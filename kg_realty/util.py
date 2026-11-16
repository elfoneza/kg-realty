"""HTTP-клиент, извлечение JSON из страниц и нормализация значений."""
from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime, timezone

import httpx

log = logging.getLogger("kg_realty")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# Курс по умолчанию, если на странице не нашлось пары сом/$ для пересчёта
DEFAULT_USD_KGS = 87.45


class Fetcher:
    """Вежливый клиент: пауза между запросами к одному сайту и повторы при ошибках.

    Ходим только по обычным HTML-страницам; разделы, закрытые в robots.txt
    (/api/, ?pageSize=, ajax), не используем.
    """

    def __init__(self, delay: float = 2.0, retries: int = 3):
        self.delay = delay
        self.retries = retries
        self._last = 0.0
        self.client = httpx.Client(
            headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"},
            timeout=30, follow_redirects=True, http2=False,
        )

    def get(self, url: str) -> str:
        for attempt in range(1, self.retries + 1):
            wait = self.delay - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait + random.uniform(0, 0.5))
            self._last = time.monotonic()
            try:
                r = self.client.get(url)
                if r.status_code == 200:
                    return r.text
                if r.status_code in (403, 404):
                    raise httpx.HTTPStatusError(f"{r.status_code} {url}", request=r.request, response=r)
                log.warning("%s -> %s, попытка %s", url, r.status_code, attempt)
            except httpx.TransportError as e:
                log.warning("%s -> %s, попытка %s", url, e, attempt)
            time.sleep(5 * attempt)
        raise RuntimeError(f"Не удалось загрузить {url}")

    def close(self):
        self.client.close()


# ---------- извлечение JSON из страниц ----------

_dec = json.JSONDecoder()


def next_flight(html: str) -> str:
    """Склеивает данные Next.js App Router (self.__next_f.push) в одну строку."""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', html, re.S)
    return "".join(json.loads('"' + c + '"') for c in chunks)


def next_data(html: str) -> dict:
    """Данные Next.js Pages Router (<script id="__NEXT_DATA__">)."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else {}


def objects_with(s: str, marker: str, required: tuple[str, ...], id_key: str = "id") -> list[dict]:
    """Находит в тексте все JSON-объекты, содержащие marker и ключи required.

    Для каждого вхождения marker берётся ближайший охватывающий объект с нужными ключами.
    Повторы (одно объявление встречается в данных несколько раз) убираются по id_key.
    """
    out, seen = [], set()
    for m in re.finditer(re.escape(marker), s):
        idx, p = m.start(), m.start()
        while True:
            p = s.rfind("{", 0, p)
            if p < 0:
                break
            try:
                obj, end = _dec.raw_decode(s, p)
            except ValueError:
                continue
            if end > idx and isinstance(obj, dict) and all(k in obj for k in required):
                key = obj.get(id_key)
                if not isinstance(key, (str, int)):
                    key = ("pos", p)
                if key not in seen:
                    seen.add(key)
                    out.append(obj)
                break
    return out


# ---------- нормализация ----------

def to_float(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.,]", "", str(v)).replace(",", ".")
    try:
        return float(s) if s else None
    except ValueError:
        return None


def to_int(v) -> int | None:
    f = to_float(v)
    return int(f) if f is not None else None


def parse_dt(v) -> datetime | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, timezone.utc).replace(tzinfo=None)
    s = str(v).replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d


CONDITION_MAP = [  # порядок важен: более конкретное раньше
    ("pso", ("псо", "самоотдел", "pso", "черновая")),
    ("unfinished", ("не достро", "недостро")),
    ("designer", ("дизайн", "designer")),
    ("euro", ("евро", "euro")),
    ("good", ("хорош", "свеж", "космет", "отличн", "заходи и живи", "good", "cosmetic")),
    ("average", ("средн", "стар", "требует", "average", "old")),
]


def norm_condition(v) -> str | None:
    if not v:
        return None
    s = str(v).lower()
    for code, keys in CONDITION_MAP:
        if any(k in s for k in keys):
            return code
    return None


def norm_series(v) -> str | None:
    if not v:
        return None
    s = str(v).lower()
    if "элит" in s or "индивид" in s or "пентхаус" in s or "новостр" in s:
        return "elitka"
    m = re.search(r"\b(10[2-8]|77)\b", s)
    if m:
        return m.group(1) + ("_improved" if "улучш" in s else "")
    for key, code in (("хрущ", "khrushchevka"), ("сталин", "stalinka"), ("малосем", "malosemeyka"),
                      ("общежит", "malosemeyka")):
        if key in s:
            return code
    return "other"


def norm_material(v) -> str | None:
    if not v:
        return None
    s = str(v).lower()
    for key, code in (("кирп", "brick"), ("панел", "panel"), ("монол", "monolith")):
        if key in s:
            return code
    return "other"


def norm_seller(v) -> str | None:
    if not v:
        return None
    s = str(v).lower()
    if any(k in s for k in ("собств", "owner", "sobstvennik")):
        return "owner"
    if any(k in s for k in ("застрой", "developer", "zastroy")):
        return "developer"
    if any(k in s for k in ("агент", "риел", "риэл", "agency", "realtor", "agentstvo", "rieltor")):
        return "agency"
    return None


COMMERCIAL_MAP = [  # тип коммерческой недвижимости по тексту (метка сайта, заголовок)
    ("office", ("офис", "office")),
    ("warehouse", ("склад", "storeroom", "space_base", "ангар")),
    ("catering", ("кафе", "ресторан", "общепит", "столов")),
    ("hotel", ("гостиниц", "отел", "хостел", "гостев", "зоны отдыха")),
    ("production", ("цех", "завод", "фабрик", "мастерск", "производ")),
    ("business", ("готовый бизнес", "готового бизнеса", "busines")),
    ("free_purpose", ("свободного назнач", "своб. назнач", "помещение")),
    ("retail", ("магазин", "бутик", "павильон", "контейнер", "торгов", "салон", "commercial", "точк")),
]


def norm_commercial(*texts) -> str:
    s = " ".join(t for t in texts if t).lower()
    for code, keys in COMMERCIAL_MAP:
        if any(k in s for k in keys):
            return code
    return "commercial_other"


ROOMS_RE = re.compile(r"(\d+)\s*[- ]?(?:комн|ком\.|к\.?\s?кв|-?х\s*комн|kомн)", re.I)
AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:м²|м2|кв\.?\s?м)", re.I)
FLOOR_RE = re.compile(r"(\d+)\s*(?:этаж|эт\.)", re.I)


def parse_title(title: str) -> dict:
    """Вытаскивает комнатность, площадь и этаж из заголовка вида
    «3 комнаты, 100 м², Джал Элитка, 7 этаж, Дизайнерский ремонт»."""
    t = title or ""
    out: dict = {}
    if re.search(r"студи", t, re.I):
        out["rooms"] = 0
    elif (m := ROOMS_RE.search(t)):
        out["rooms"] = int(m.group(1))
    if (m := AREA_RE.search(t)):
        out["area_total"] = float(m.group(1).replace(",", "."))
    if (m := FLOOR_RE.search(t)):
        out["floor"] = int(m.group(1))
    return out


ADMIN_DISTRICTS = {"Октябрьский", "Ленинский", "Свердловский", "Первомайский"}
DISTRICT_ALIASES = {
    "джал-23 (нижний джал)": "Нижний Джал (Джал-23)", "нижний джал": "Нижний Джал (Джал-23)",
    "джал-23": "Нижний Джал (Джал-23)", "южн. магистраль": "Южная Магистраль", "магистраль": "Южная Магистраль",
    "южная магистраль": "Южная Магистраль", "аламединский рынок / базар": "Аламединский рынок",
}


def norm_district(v) -> str | None:
    """«Асанбай мкр», «Асанбай м-н», «Микрорайон Асанбай» → «Асанбай»."""
    if not v:
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    s = re.sub(r"(?i)^(микрорайон|мкрн?\.?|м-н)\s+", "", s)
    s = re.sub(r"(?i)\s+(мкрн?\.?|м-н|микрорайон)(?=$|\s|\))", "", s).strip(" -,")
    if not s:
        return None
    if s.isdigit():                      # «7 мкр» остаётся «7 мкр»
        return f"{s} мкр"
    key = s.lower()
    if key in DISTRICT_ALIASES:
        return DISTRICT_ALIASES[key]
    return s[0].upper() + s[1:]
