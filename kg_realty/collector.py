"""Сбор: обход страниц, запись в базу, история цен, снятые объявления, склейка дублей."""
from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select, update

from .db import Complex, ComplexPriceHistory, Listing, PriceHistory, Run, Session, utcnow
from .sources import ELITKA_CLASSES, LISTING_SOURCES, ElitkaComplexes
from .util import DEFAULT_USD_KGS, Fetcher

log = logging.getLogger("kg_realty")

SKIP_UPDATE = {"source_id"}


def current_rate() -> float:
    """Курс сом/$ по свежим объявлениям, где сайт сам показывает обе цены."""
    with Session() as s:
        rows = s.execute(
            select(Listing.price_kgs, Listing.price_usd)
            .where(Listing.source.in_(("house", "lalafo", "elitka")),
                   Listing.price_usd > 0, Listing.price_kgs > 0,
                   Listing.last_seen > utcnow() - timedelta(days=2))
            .limit(2000)
        ).all()
    ratios = [k / u for k, u in rows if 60 < k / u < 120]
    return round(statistics.median(ratios), 3) if len(ratios) > 20 else DEFAULT_USD_KGS


def _changed(old: float | None, new: float | None) -> bool:
    if not old or not new:
        return False
    return abs(new - old) / old > 0.005


def upsert_listings(source: str, items: list[dict], run: Run, s) -> None:
    now = utcnow()
    ids = [i["source_id"] for i in items]
    existing = {l.source_id: l for l in s.scalars(
        select(Listing).where(Listing.source == source, Listing.source_id.in_(ids)))}
    for it in items:
        # «Студия» больше 45 м² — это лот со свободной планировкой (так помечает etagi), комнатность неизвестна
        if it.get("rooms") == 0 and (it.get("area_total") or 0) > 45:
            it["rooms"] = None
        l = existing.get(it["source_id"])
        if l is None:
            l = Listing(source=source, first_seen=now, last_seen=now, is_active=True, **it)
            s.add(l)
            s.flush()
            s.add(PriceHistory(listing_id=l.id, seen_at=now, price_kgs=l.price_kgs, price_usd=l.price_usd))
            run.new_items += 1
            existing[it["source_id"]] = l
            continue
        if _changed(l.price_usd, it.get("price_usd")):
            s.add(PriceHistory(listing_id=l.id, seen_at=now, price_kgs=it.get("price_kgs"),
                               price_usd=it.get("price_usd")))
            run.price_changes += 1
        for k, v in it.items():
            if k not in SKIP_UPDATE and v is not None:
                setattr(l, k, v)
        l.last_seen, l.is_active, l.removed_at = now, True, None
    run.items += len(items)


def collect(source: str, mode: str = "fresh", max_pages: int = 3, rate: float | None = None) -> Run:
    """mode=fresh — первые max_pages страниц (новые и поднятые объявления);
    mode=full — весь каталог; после полного обхода непросмотренные объявления помечаются снятыми."""
    rate = rate or current_rate()
    src = LISTING_SOURCES[source](rate=rate)
    f = Fetcher()
    with Session() as s:
        run = Run(source=source, mode=mode)
        s.add(run)
        s.commit()
        started = run.started_at
        try:
            page, total = 1, None
            while True:
                html = f.get(src.page_url(page))
                items, t = src.parse(html)
                total = total or t
                run.pages = page
                if not items:
                    log.info("%s: стр. %s пустая, стоп", source, page)
                    break
                upsert_listings(source, items, run, s)
                s.commit()
                log.info("%s стр. %s/%s: %s объявл. (новых всего %s)", source, page, total, len(items), run.new_items)
                if mode == "fresh" and page >= max_pages:
                    break
                if total and page >= total:
                    break
                page += 1
            if mode == "full":
                res = s.execute(
                    update(Listing)
                    .where(Listing.source == source, Listing.is_active.is_(True), Listing.last_seen < started)
                    .values(is_active=False, removed_at=utcnow()))
                run.removed = res.rowcount or 0
            run.ok = True
        except Exception as e:  # noqa: BLE001 — один упавший сайт не должен ронять остальные
            log.exception("%s: ошибка", source)
            run.error = repr(e)[:2000]
        finally:
            run.finished_at = utcnow()
            s.commit()
            f.close()
        s.expunge(run)
        return run


def collect_complexes(cities=(1,), rate: float | None = None) -> int:
    """Каталог ЖК elitka.kg: сначала по каждому классу жилья (чтобы знать класс), затем без фильтра."""
    f = Fetcher()
    n = 0
    now = utcnow()
    with Session() as s:
        run = Run(source="elitka_complexes", mode="full")
        s.add(run)
        s.commit()
        try:
            for city in cities:
                for cls in [*ELITKA_CLASSES, None]:
                    src = ElitkaComplexes(rate=rate or DEFAULT_USD_KGS, city=city, housing_class=cls)
                    page, total = 1, None
                    while True:
                        items, t = src.parse(f.get(src.page_url(page)))
                        total = total or t
                        run.pages += 1
                        for it in items:
                            c = s.scalar(select(Complex).where(Complex.source == "elitka",
                                                               Complex.source_id == it["source_id"]))
                            if c is None:
                                c = Complex(source="elitka", first_seen=now, **it)
                                s.add(c)
                                s.flush()
                                s.add(ComplexPriceHistory(complex_id=c.id, seen_at=now, price_m2_usd=c.price_m2_usd))
                                run.new_items += 1
                            else:
                                if _changed(c.price_m2_usd, it.get("price_m2_usd")):
                                    s.add(ComplexPriceHistory(complex_id=c.id, seen_at=now,
                                                              price_m2_usd=it["price_m2_usd"]))
                                    run.price_changes += 1
                                for k, v in it.items():
                                    if v is not None and k != "source_id":
                                        setattr(c, k, v)
                            c.last_seen = now
                            n += 1
                        s.commit()
                        log.info("elitka ЖК city=%s class=%s стр. %s/%s: %s", city, cls, page, total, len(items))
                        if not items or not total or page >= total:
                            break
                        page += 1
            run.ok = True
        except Exception as e:  # noqa: BLE001
            log.exception("elitka_complexes: ошибка")
            run.error = repr(e)[:2000]
        finally:
            run.items, run.finished_at = n, utcnow()
            s.commit()
            f.close()
    link_complexes()
    return n


def link_complexes() -> None:
    """Объявлениям elitka проставляем город, застройщика и название ЖК из каталога."""
    with Session() as s:
        cx = {c.source_id: c for c in s.scalars(select(Complex).where(Complex.source == "elitka"))}
        for l in s.scalars(select(Listing).where(Listing.source == "elitka", Listing.complex_source_id.is_not(None))):
            c = cx.get(l.complex_source_id)
            if c:
                l.city = l.city or c.city
                l.developer_name = c.developer_name
                l.complex_name = l.complex_name or c.name
        s.commit()


def dedup() -> int:
    """Склейка одной и той же квартиры, выставленной на разных сайтах.

    Кандидаты: тот же город, комнатность, этаж, этажность и площадь (±1.5 м²),
    цена отличается не более чем на 7%. Если у обоих есть координаты — не дальше ~300 м.
    Объявления одного сайта между собой не склеиваются.
    """
    with Session() as s:
        rows = s.execute(select(
            Listing.id, Listing.source, Listing.city, Listing.rooms, Listing.floor, Listing.floors_total,
            Listing.area_total, Listing.price_usd, Listing.lat, Listing.lng,
        ).where(Listing.is_active.is_(True), Listing.area_total.is_not(None), Listing.floor.is_not(None),
                Listing.price_usd.is_not(None))).all()

        buckets = defaultdict(list)
        for r in rows:
            buckets[(r.rooms, r.floor, r.floors_total)].append(r)

        parent = {r.id: r.id for r in rows}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for group in buckets.values():
            group.sort(key=lambda r: r.area_total)
            for i, a in enumerate(group):
                for b in group[i + 1:]:
                    if b.area_total - a.area_total > 1.5:
                        break
                    if a.source == b.source:
                        continue
                    if a.city and b.city and a.city != b.city:
                        continue
                    if abs(a.price_usd - b.price_usd) / max(a.price_usd, b.price_usd) > 0.07:
                        continue
                    if a.lat and b.lat and a.lng and b.lng:
                        if abs(a.lat - b.lat) > 0.003 or abs(a.lng - b.lng) > 0.004:
                            continue
                    parent[find(a.id)] = find(b.id)

        clusters = defaultdict(list)
        for r in rows:
            clusters[find(r.id)].append(r.id)
        s.execute(update(Listing).values(dup_group=None))
        n = 0
        for ids in clusters.values():
            if len(ids) > 1:
                s.execute(update(Listing).where(Listing.id.in_(ids)).values(dup_group=min(ids)))
                n += 1
        s.commit()
        return n


CITY_CENTER_DEFAULTS = [(42.8746, 74.5698)]   # lalafo ставит центр Бишкека, если адрес не указан


def assign_districts(k: int = 9, max_km: float = 1.2) -> int:
    """Единое название района для всех сайтов.

    1. Где район указан текстом (lalafo, stroka, elitka через каталог ЖК) — нормализуем написание.
    2. Где не указан или указан крупный административный район (house.kg, etagi) — берём район,
       который чаще всего встречается у k ближайших подписанных объявлений в радиусе max_km.
    """
    import math
    from collections import Counter

    from .util import ADMIN_DISTRICTS, norm_district

    with Session() as s:
        rows = s.execute(select(Listing.id, Listing.source, Listing.district, Listing.lat, Listing.lng)).all()

        def bad_coord(lat, lng):
            return (lat is None or lng is None or
                    any(abs(lat - a) < 0.0005 and abs(lng - b) < 0.0005 for a, b in CITY_CENTER_DEFAULTS))

        named, result = [], {}
        for r in rows:
            nd = norm_district(r.district)
            if nd and nd not in ADMIN_DISTRICTS:
                result[r.id] = nd
                if not bad_coord(r.lat, r.lng):
                    named.append((r.lat, r.lng, nd))

        # 2а. Район прямо в адресе: «микрорайон Джал-23 18/2», «11 микрорайон 19»
        import re
        freq = Counter(result.values())
        known = sorted((n for n, c in freq.items() if c >= 5 and len(n) >= 4), key=len, reverse=True)
        addr = {r.id: a for r in s.execute(select(Listing.id, Listing.address, Listing.complex_name))
                for a in [" ".join(filter(None, [r.address, r.complex_name]))] if a}
        for r in rows:
            a = addr.get(r.id)
            if r.id in result or not a:
                continue
            if re.search(r"(?i)чолпон|\bош\b|каракол|иссык|бостери|тамчы|балыкчы|кара-ой|токмок|кант\b", a):
                continue  # названия районов — бишкекские
            m = re.search(r"(?i)(?:^|\s)(\d{1,2})\s*(?:-?й\s*)?(?:мкр|микрорайон|м-н)", a) or \
                re.search(r"(?i)(?:мкр\.?|микрорайон|м-н)\s*(\d{1,2})(?:\s|,|$)", a)
            if m and f"{m.group(1)} мкр" in freq:
                result[r.id] = f"{m.group(1)} мкр"
                continue
            al = a.lower()
            hit = next((n for n in known if re.search(r"(?<![\w-])" + re.escape(n.lower()) + r"(?![\w-])", al)), None)
            if hit:
                result[r.id] = hit

        cell = 0.01  # ~1 км
        grid = defaultdict(list)
        for lat, lng, nd in named:
            grid[(int(lat / cell), int(lng / cell))].append((lat, lng, nd))

        for r in rows:
            if r.id in result or bad_coord(r.lat, r.lng):
                continue
            ci, cj = int(r.lat / cell), int(r.lng / cell)
            cand = []
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for lat, lng, nd in grid.get((ci + di, cj + dj), ()):
                        d = math.hypot((lat - r.lat) * 111, (lng - r.lng) * 82)
                        if d <= max_km:
                            cand.append((d, nd))
            if len(cand) >= 3:
                cand.sort()
                result[r.id] = Counter(nd for _, nd in cand[:k]).most_common(1)[0][0]

        s.execute(update(Listing).values(district_norm=None))
        for lid, nd in result.items():
            s.execute(update(Listing).where(Listing.id == lid).values(district_norm=nd))
        s.commit()
        return len(result)
