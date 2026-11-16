"""Парсеры сайтов. Каждый источник умеет:
  page_url(page)          — адрес страницы списка
  parse(html) -> (items, total_pages)
items — словари с полями таблицы listings (или complexes для elitka_complexes).

Все пять сайтов отдают данные JSON-ом прямо внутри HTML, поэтому браузер не нужен.
"""
from __future__ import annotations

import math
import re
from datetime import datetime

from selectolax.lexbor import LexborHTMLParser

from .util import (
    DEFAULT_USD_KGS, next_data, next_flight, norm_condition, norm_material,
    norm_commercial, norm_series, norm_seller, objects_with, parse_dt, parse_title, to_float, to_int,
)


def _usd(kgs, rate):
    return round(kgs / rate, 2) if kgs else None


def _m2(price_usd, area):
    return round(price_usd / area, 1) if price_usd and area else None


class Source:
    name = ""
    base = ""

    def __init__(self, rate: float = DEFAULT_USD_KGS):
        self.rate = rate

    def page_url(self, page: int) -> str:
        raise NotImplementedError

    def parse(self, html: str) -> tuple[list[dict], int | None]:
        raise NotImplementedError


# ---------------------------------------------------------------- lalafo.kg
class Lalafo(Source):
    """Доска объявлений. Данные в __NEXT_DATA__ → listingFeed."""
    name = "lalafo"
    base = "https://lalafo.kg"
    path = "/bishkek/kvartiry/prodazha-kvartir"
    commercial = False

    SERIES = ("элитка", "индивидуалка", "104 серия", "105 серия", "106 серия", "хрущевка",
              "сталинка", "малосемейка", "102 серия", "107 серия", "108 серия")

    def page_url(self, page):
        return f"{self.base}{self.path}" + (f"?page={page}" if page > 1 else "")

    def parse(self, html):
        d = next_data(html)
        qs = d.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", [])
        feed = next((q["state"]["data"] for q in qs if q["queryKey"][0] == "listingFeed"), None)
        if not feed:
            return [], None
        page = feed["pages"][0]
        items = []
        for it in page["items"]:
            title = it.get("title") or ""
            t = parse_title(title)
            tl = title.lower()
            params = {p["name"]: p.get("value") for p in it.get("params", [])}
            cur = it.get("currency")
            price = to_float(it.get("price"))
            nat = (it.get("national_price") or {}).get("price")
            price_usd = price if cur == "USD" else _usd(price, self.rate)
            price_kgs = to_float(nat) or (price * self.rate if cur == "USD" and price else price)
            old = to_float(it.get("old_price"))
            old_usd = (old if cur == "USD" else _usd(old, self.rate)) if old else None
            labels = " ".join([it.get("ad_label") or ""] + [c.get("name", "") for c in it.get("card_labels", [])])
            series = next((s for s in self.SERIES if s in tl), None)
            ptype = norm_commercial(it.get("ad_label"), title) if self.commercial else "flat"
            items.append(dict(
                source_id=str(it["id"]),
                url=self.base + it["url"] if it.get("url") else None,
                title=title[:300], property_type=ptype,
                rooms=None if self.commercial else t.get("rooms"), area_total=t.get("area_total"), floor=t.get("floor"),
                series=norm_series(series), condition=norm_condition(title),
                is_new_building=True if series in ("элитка",) or "жк" in tl or "псо" in tl else None,
                price_usd=price_usd, price_kgs=price_kgs, old_price_usd=old_usd,
                price_m2_usd=_m2(price_usd, t.get("area_total")),
                city=it.get("city"), district=params.get("Район Бишкека"),
                lat=it.get("lat"), lng=it.get("lng"),
                seller_type=norm_seller(labels),
                mortgage=True if "ипотек" in tl else None,
                installment=True if "рассроч" in tl else None,
                views=it.get("impressions"),
                published_at=parse_dt(it.get("created_time")),
                updated_at=parse_dt(it.get("updated_time")),
                raw={k: it.get(k) for k in ("id", "title", "price", "old_price", "currency", "ad_label",
                                             "is_vip", "favorite_count", "callers_count", "params")},
            ))
        return items, page["_meta"].get("pageCount")


# ---------------------------------------------------------------- house.kg
class House(Source):
    """Крупнейшая доска по недвижимости. Данные в потоке Next.js (self.__next_f)."""
    name = "house"
    base = "https://www.house.kg"
    path = "/kupit-kvartiru"
    commercial = False

    def page_url(self, page):
        return f"{self.base}{self.path}" + (f"?page={page}" if page > 1 else "")

    def parse(self, html):
        s = next_flight(html)
        dicts = objects_with(s, '"serie":[{', ("serie", "condition"), id_key="serie")
        dic = {}
        if dicts:
            for k, v in dicts[0].items():
                if isinstance(v, list) and v and isinstance(v[0], dict) and "id" in v[0]:
                    dic[k] = {o["id"]: o.get("name") for o in v}
        rooms_map = {1: 1, 2: 2, 3: 3, 5: 4, 6: 5, 7: 6}

        # Город и роль продавца берём из карточки (в JSON только коды)
        tree = LexborHTMLParser(html)
        cards = {}
        for art in tree.css("article"):
            a = art.css_first('a[href^="/details/"]')
            if not a:
                continue
            slug = a.attributes.get("href", "").split("/details/")[-1]
            text = art.text(separator=" ")
            role = ("developer" if "Застройщик" in text else
                    "agency" if re.search(r"Агентство|Компания|Риелтор|Риэлтор|Агент\b", text) else
                    "owner" if "Собственник" in text else None)
            cards[slug] = dict(address=(a.attributes.get("aria-label") or "").replace("​", ""), role=role)

        items = []
        for o in objects_with(s, '"price_deviation"', ("slug", "prices", "rooms")):
            prices = {p["id"]: p for p in o.get("prices", [])}
            kgs = to_float(prices.get(1, {}).get("price"))
            usd = to_float(prices.get(2, {}).get("price"))
            m2_usd = to_float(prices.get(2, {}).get("m2_price"))
            card = cards.get(o["slug"], {})
            addr = card.get("address") or ""
            city = addr.split(",")[0].strip() if "," in addr else None
            prev = to_float(o.get("previous_price"))
            old_usd = (prev if o.get("currency_id") == 2 else _usd(prev, self.rate)) if prev else None
            serie = dic.get("serie", {}).get(o.get("serie"))
            is_com = o.get("category") == 3
            if self.commercial != is_com:
                continue
            ctype = dic.get("commercial_type", {}).get(o.get("commercial_type"))
            items.append(dict(
                source_id=str(o["id"]),
                url=f"{self.base}/details/{o['slug']}",
                title=ctype, property_type=norm_commercial(ctype) if is_com else "flat",
                rooms=None if is_com else rooms_map.get(o.get("rooms")), area_total=to_float(o.get("square")),
                area_kitchen=to_float(o.get("kitchen_square")),
                floor=to_int(o.get("floor")), floors_total=to_int(o.get("floors")),
                series=norm_series(serie),
                condition=norm_condition(dic.get("condition", {}).get(o.get("condition"))),
                wall_material=norm_material(dic.get("building_type", {}).get(o.get("building_type"))
                                            or dic.get("material", {}).get(o.get("material"))),
                year_built=to_int(o.get("year")),
                is_new_building=bool(o.get("building_id")) or serie == "элитка",
                price_kgs=kgs, price_usd=usd, price_m2_usd=m2_usd, old_price_usd=old_usd,
                market_deviation_pct=to_float(o.get("price_deviation")),
                # house.kg отдаёт район только кодом; перекрёсток добавляем к адресу, район назначается по координатам
                city=city, district=None,
                address=" / ".join(filter(None, [" ".join(filter(None, [o.get("address"), o.get("house_number")])),
                                                 o.get("crossing")])).replace("​", "").strip() or None,
                lat=to_float(o.get("latitude")), lng=to_float(o.get("longitude")),
                seller_type=card.get("role"),
                # имя частного продавца не храним — только название компании/агентства
                seller_name=o.get("user_name") if card.get("role") in ("agency", "developer") else None,
                complex_source_id=str(o["building_id"]) if o.get("building_id") else None,
                mortgage=bool(o.get("mortgage")) or None, installment=bool(o.get("installment")) or None,
                views=to_int(o.get("counter")),
                published_at=parse_dt(o.get("created_at")), updated_at=parse_dt(o.get("upped_at")),
                raw={k: o.get(k) for k in ("id", "owner_type", "building_id", "district", "micro_district",
                                            "serie", "condition", "document", "prices", "previous_price",
                                            "favourite_counter", "call_counter", "is_premium", "is_vip")},
            ))
        m = re.search(r"Найдено\s*([\d\s\xa0]+)\s*объявл", html)
        if m:
            return items, math.ceil(int(re.sub(r"\D", "", m.group(1))) / 20)
        pages = [int(x) for x in re.findall(r'href="%s\?page=(\d+)"' % self.path, html)]
        return items, max(pages) if pages else None


# ---------------------------------------------------------------- stroka.kg
class Stroka(Source):
    """Новая доска объявлений. Данные в потоке Next.js."""
    name = "stroka"
    base = "https://stroka.kg"
    path = "/kupit-kvartiry"
    commercial = False

    def page_url(self, page):
        sep = "&" if "?" in self.path else "?"
        return f"{self.base}{self.path}" + (f"{sep}page={page}" if page > 1 else "")

    def parse(self, html):
        s = next_flight(html)
        items = []
        objs = objects_with(s, '"buildingTypeId":', ("id", "cost", "square"))
        regular = [o for o in objs]
        for o in regular:
            if o.get("category") != "Sale":
                continue
            is_com = "оммерч" in (o.get("buildingType") or "")
            if is_com != self.commercial or (not is_com and o.get("buildingTypeId") != 1):
                continue
            kgs = to_float(o.get("cost"))
            usd = _usd(kgs, self.rate)
            area = to_float(o.get("square"))
            lat = lng = None
            if o.get("coordinats") and "," in o["coordinats"]:
                lat, lng = (to_float(x) for x in o["coordinats"].split(",", 1))
            items.append(dict(
                source_id=o["id"], url=f"{self.base}/ad/{o['id']}", title=o.get("title"),
                property_type=norm_commercial(o.get("title"), o.get("text", "")[:200]) if is_com else "flat",
                rooms=None if is_com else to_int(o.get("rooms")), area_total=area,
                floor=to_int(o.get("floor")), floors_total=to_int(o.get("totalFloors")),
                series=norm_series(o.get("series")),
                is_new_building=True if norm_series(o.get("series")) == "elitka" else None,
                price_kgs=kgs, price_usd=usd, price_m2_usd=_m2(usd, area),
                city=o.get("region"), district=o.get("district"), address=o.get("address"),
                lat=lat, lng=lng,
                seller_type=norm_seller(o.get("ownerType") or o.get("userType")),
                views=to_int(o.get("viewCount")),
                published_at=parse_dt(o.get("createdAt")), updated_at=parse_dt(o.get("updatedAt")),
                raw={k: o.get(k) for k in ("id", "ownerType", "payedService", "showCount", "districtId",
                                            "availabilityConfirmedAt", "isNegotiable")},
            ))
        m = re.search(r'"regularCount":(\d+)', s) or re.search(r'"totalCount":(\d+)', s)
        per_page = max(1, len([o for o in objs if o.get("category") == "Sale"]) - 1)
        total = math.ceil(int(m.group(1)) / per_page) if m else None
        return items, total


# ---------------------------------------------------------------- bishkek.etagi.com
class Etagi(Source):
    """Сайт агентства «Этажи». Данные в <script>var data=...</script> → lists.flats."""
    name = "etagi"
    base = "https://bishkek.etagi.com"
    path, list_key = "/realty/", "flats"
    ETAGI_TYPES = {"office": "office", "commercial": "retail", "office_storeroom": "warehouse",
                   "space_base": "warehouse", "busines": "business", "space_land": "land", "other": "commercial_other"}

    def page_url(self, page):
        return f"{self.base}{self.path}" + (f"?page={page}" if page > 1 else "")

    def parse(self, html):
        import json
        i = html.find('"lists":{"%s":[' % self.list_key)
        if i < 0:
            return [], None
        arr, _ = json.JSONDecoder().raw_decode(html, html.find("[", i))

        tree = LexborHTMLParser(html)
        labels = {}
        for card in tree.css('[data-testid="object_card"]'):
            a = card.css_first('a[href^="/realty/"]')
            lab = card.css_first("#chp-label")
            if a and lab:
                labels[a.attributes["href"].strip("/").split("/")[-1]] = lab.text(strip=True)

        items = []
        for o in arr:
            oid = str(o.get("object_id"))
            meta = o.get("meta") or {}
            kgs = to_float(o.get("price"))
            usd = _usd(kgs, self.rate)
            area = to_float(o.get("square"))
            label = labels.get(oid)
            is_com = self.list_key != "flats"
            ptype = self.ETAGI_TYPES.get(o.get("type"), "commercial_other") if is_com else "flat"
            if ptype == "land":
                continue
            items.append(dict(
                source_id=oid, url=f"{self.base}{self.path}{oid}/",
                title=label, property_type=ptype, year_built=to_int(o.get("building_year")),
                rooms=None if is_com else (0 if o.get("studio") else to_int(o.get("rooms"))),
                area_total=area, floor=to_int(o.get("floor")), floors_total=to_int(o.get("floors")),
                condition=norm_condition(label),
                wall_material=norm_material(meta.get("walls")),
                is_new_building=bool(o.get("newhouses_id")),
                complex_name=meta.get("newcomplex"),
                complex_source_id=str(o["newhouses_id"]) if o.get("newhouses_id") else None,
                price_kgs=kgs, price_usd=usd, price_m2_usd=_m2(usd, area),
                city=meta.get("city"), district=meta.get("district"),
                address=" ".join(filter(None, [meta.get("street"), o.get("house_num")])) or None,
                lat=to_float(o.get("la")), lng=to_float(o.get("lo")),
                seller_type="agency", seller_name="Этажи",
                installment=o.get("hasInstallment") or None,
                raw={k: o.get(k) for k in ("object_id", "price", "price_m2", "newhouses_id", "ondeadline",
                                            "district_id", "is_exclusive", "status")},
            ))
        m = re.search(r'"count":(\d+),"aggFields"', html)
        total = math.ceil(int(m.group(1)) / max(1, len(arr))) if m else None
        return items, total


# ---------------------------------------------------------------- elitka.kg — объявления
class ElitkaAds(Source):
    """Объявления частных лиц и риелторов внутри ЖК на elitka.kg."""
    name = "elitka"
    base = "https://elitka.kg"

    def page_url(self, page):
        return f"{self.base}/ads/index" + (f"?page={page}" if page > 1 else "")

    def parse(self, html):
        s = next_flight(html)
        items = []
        for o in objects_with(s, '"ad_from":', ("id", "price_usd", "indoor_area")):
            if o.get("is_rent") or o.get("realty_type") not in (None, "APARTMENT"):
                continue
            usd = to_float(o.get("price_usd"))
            area = to_float(o.get("indoor_area"))
            ob = o.get("obyekt") or {}
            items.append(dict(
                source_id=str(o["id"]), url=f"{self.base}/ads/{o['id']}", title=o.get("title"),
                # флаг is_studio на сайте бывает ошибочным (61 м² «студия»), поэтому главное — room_count
                rooms=to_int(o.get("room_count")) or (0 if o.get("is_studio") else None),
                area_total=area, floor=to_int(o.get("floor")), floors_total=to_int(o.get("floor_total")),
                series="elitka", condition=norm_condition(o.get("condition")), is_new_building=True,
                price_kgs=to_float(o.get("price_som")), price_usd=usd, price_m2_usd=_m2(usd, area),
                address=o.get("address"),
                seller_type=norm_seller(o.get("ad_from")),
                complex_name=ob.get("title"),
                complex_source_id=str(o["object_id"]) if o.get("object_id") else None,
                mortgage=o.get("ipoteka_avail"), installment=o.get("rassrochka_avail"),
                views=to_int(o.get("views_count")),
                published_at=parse_dt(o.get("created_at")), updated_at=parse_dt(o.get("updated_at")),
                raw={k: o.get(k) for k in ("id", "object_id", "company_id", "ad_from", "condition",
                                            "is_urgent", "bargain_avail", "barter_avail")},
            ))
        m = re.search(r'"totalPages":(\d+)', s)
        return items, int(m.group(1)) if m else None


# ---------------------------------------------------------------- elitka.kg — ЖК
ELITKA_CITIES = {1: "Бишкек", 2: "Ош", 3: "Манас"}
ELITKA_CLASSES = {"ECONOM": "econom", "COMFORT": "comfort", "COMFORT_PLUS": "comfort_plus",
                  "BUSINESS": "business", "PREMIUM": "premium"}


class ElitkaComplexes(Source):
    """Каталог ЖК. Обходим каждый класс жилья отдельно, чтобы знать класс ЖК."""
    name = "elitka_complexes"
    base = "https://elitka.kg"

    def __init__(self, rate=DEFAULT_USD_KGS, city: int = 1, housing_class: str | None = None):
        super().__init__(rate)
        self.city, self.housing_class = city, housing_class

    def page_url(self, page):
        q = f"city={self.city}"
        if self.housing_class:
            q += f"&class={self.housing_class}"
        if page > 1:
            q += f"&page={page}"
        return f"{self.base}/object/index?{q}"

    def parse(self, html):
        s = next_flight(html)
        items = []
        now = datetime.utcnow()
        for o in objects_with(s, '"rooms_data":', ("id", "slug", "title")):
            b = o.get("builder") or {}
            fin = parse_dt(o.get("construction_finish_date"))
            items.append(dict(
                source_id=str(o["id"]), name=o["title"], slug=o.get("slug"),
                url=f"{self.base}/object/{o.get('slug')}",
                city=ELITKA_CITIES.get(self.city), address=o.get("address"),
                housing_class=ELITKA_CLASSES.get(self.housing_class) if self.housing_class else None,
                developer_name=b.get("title"), developer_id=str(b["id"]) if b.get("id") else None,
                developer_plan=b.get("subscription_plan"),
                # у распроданных/сданных ЖК сайт пишет цену 0 — храним как «нет цены»
                price_m2_usd=to_float(o.get("price_usd")) or None, price_m2_kgs=to_float(o.get("price_kgs")) or None,
                rooms_data=o.get("rooms_data"), finish_date=fin,
                is_finished=(fin < now) if fin else None,
                rating=to_float(o.get("rating")), reviews_count=to_int(o.get("reviews_count")),
                labels=o.get("labels"), gosstroy_url=o.get("gosstroy_registry"),
                private_ads_count=to_int(o.get("private_ads_count")),
                raw={k: o.get(k) for k in ("id", "is_promoted", "quality_score", "main_img")},
            ))
        m = re.search(r'"totalPages":(\d+)', s)
        return items, int(m.group(1)) if m else None


# ---------------------------------------------------------------- коммерческая недвижимость (продажа)
class LalafoCommercial(Lalafo):
    name, path, commercial = "lalafo_com", "/bishkek/kommercheskaya-nedvizhimost/prodazha", True


class HouseCommercial(House):
    name, path, commercial = "house_com", "/kupit-kommercheskaia-nedvijimost", True


class StrokaCommercial(Stroka):
    name, path, commercial = "stroka_com", "/prodazha?buildingTypeId=5", True


class EtagiCommercial(Etagi):
    name, path, list_key = "etagi_com", "/commerce/", "commerce"


LISTING_SOURCES = {c.name: c for c in (Lalafo, House, Stroka, Etagi, ElitkaAds,
                                       LalafoCommercial, HouseCommercial, StrokaCommercial, EtagiCommercial)}
