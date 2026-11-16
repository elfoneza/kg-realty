"""Командная строка.

  uv run python -m kg_realty collect               # свежие объявления, первые 3 стр. каждого сайта
  uv run python -m kg_realty collect --mode full   # полный обход всех сайтов
  uv run python -m kg_realty collect -s house -p 10
  uv run python -m kg_realty complexes             # каталог ЖК elitka.kg с классами жилья
  uv run python -m kg_realty dedup                 # склейка дублей между сайтами
  uv run python -m kg_realty stats                 # что лежит в базе
  uv run python -m kg_realty schedule              # работать по расписанию (не закрывайте терминал)
"""
from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import Integer, cast, func, select

from .collector import assign_districts, collect, collect_complexes, current_rate, dedup, link_complexes
from .db import Complex, Listing, PriceHistory, Session, init_db
from .sources import LISTING_SOURCES

log = logging.getLogger("kg_realty")


def run_all(sources, mode, pages):
    rate = current_rate()
    log.info("Курс для пересчёта сом→$: %s", rate)
    # Сайты обходятся параллельно, но внутри одного сайта — строго по очереди с паузой
    with ThreadPoolExecutor(max_workers=len(sources)) as ex:
        runs = list(ex.map(lambda s: collect(s, mode, pages, rate), sources))
    link_complexes()
    groups = dedup()
    log.info("Районы назначены: %s объявлений", assign_districts())
    for r in runs:
        status = "OK" if r.ok else f"ОШИБКА: {r.error}"
        log.info("%-7s %s: стр. %s, объявл. %s, новых %s, смен цены %s, снято %s — %s",
                 r.source, r.mode, r.pages, r.items, r.new_items, r.price_changes, r.removed, status)
    log.info("Групп дублей между сайтами: %s", groups)


def stats():
    with Session() as s:
        print("\nОбъявления (активные / всего), медиана $/м²:")
        for src, act, tot in s.execute(
                select(Listing.source, func.sum(cast(Listing.is_active, Integer)), func.count())
                .group_by(Listing.source)):
            vals = sorted(v for (v,) in s.execute(
                select(Listing.price_m2_usd).where(Listing.source == src, Listing.is_active.is_(True),
                                                   Listing.price_m2_usd.between(300, 10000))))
            med = vals[len(vals) // 2] if vals else None
            print(f"  {src:8} {act:>6} / {tot:<6}  медиана {med} $/м²")
        dup = s.scalar(select(func.count(func.distinct(Listing.dup_group))).where(Listing.dup_group.is_not(None)))
        dup_l = s.scalar(select(func.count()).where(Listing.dup_group.is_not(None)))
        print(f"\nДубли: {dup_l} объявлений склеено в {dup} квартир")
        print(f"Записей истории цен: {s.scalar(select(func.count()).select_from(PriceHistory))}")
        print("\nЖК (elitka) по классам:")
        for cls, n, avg in s.execute(select(Complex.housing_class, func.count(), func.avg(Complex.price_m2_usd))
                                     .group_by(Complex.housing_class)):
            print(f"  {cls or 'класс не указан':16} {n:>4} ЖК, средняя {round(avg or 0)} $/м²")


def export(path: str):
    """VACUUM INTO + gzip: компактная копия базы для ветки `data` на GitHub."""
    import gzip
    import shutil
    import sqlite3
    from pathlib import Path

    from .db import DEFAULT_URL
    src = DEFAULT_URL.removeprefix("sqlite:///")
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix("")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(src)
    con.execute("VACUUM INTO ?", (str(tmp),))
    con.close()
    with open(tmp, "rb") as f, gzip.open(out, "wb", compresslevel=9) as g:
        shutil.copyfileobj(f, g)
    tmp.unlink()
    log.info("Экспорт: %s (%.1f МБ)", out, out.stat().st_size / 1e6)


def schedule(pages):
    from apscheduler.schedulers.blocking import BlockingScheduler
    sources = list(LISTING_SOURCES)
    sch = BlockingScheduler(timezone="Asia/Bishkek")
    sch.add_job(run_all, "interval", minutes=15, args=[sources, "fresh", pages],
                id="fresh", max_instances=1, coalesce=True, next_run_time=__import__("datetime").datetime.now())
    sch.add_job(run_all, "cron", hour=3, minute=0, args=[sources, "full", 0], id="full",
                max_instances=1, coalesce=True)
    sch.add_job(collect_complexes, "cron", hour=5, minute=30, id="complexes", max_instances=1)
    log.info("Расписание: свежие объявления каждые 15 мин, полный обход в 03:00, ЖК в 05:30 (Бишкек)")
    sch.start()


def main():
    p = argparse.ArgumentParser(prog="kg_realty")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("-s", "--source", default="all", help="all или " + ", ".join(LISTING_SOURCES))
    c.add_argument("-m", "--mode", choices=["fresh", "full"], default="fresh")
    c.add_argument("-p", "--pages", type=int, default=3, help="сколько страниц в режиме fresh")
    sub.add_parser("complexes")
    sub.add_parser("dedup")
    sub.add_parser("districts")
    ex = sub.add_parser("export", help="сжатая копия базы для публикации")
    ex.add_argument("path", nargs="?", default="out/realty.db.gz")
    sub.add_parser("stats")
    sc = sub.add_parser("schedule")
    sc.add_argument("-p", "--pages", type=int, default=3)
    a = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    init_db()

    if a.cmd == "collect":
        srcs = list(LISTING_SOURCES) if a.source == "all" else a.source.split(",")
        run_all(srcs, a.mode, a.pages)
    elif a.cmd == "complexes":
        print("ЖК собрано:", collect_complexes())
    elif a.cmd == "dedup":
        print("Групп дублей:", dedup())
    elif a.cmd == "export":
        export(a.path)
    elif a.cmd == "districts":
        print("Районы назначены:", assign_districts())
    elif a.cmd == "stats":
        stats()
    elif a.cmd == "schedule":
        schedule(a.pages)


if __name__ == "__main__":
    main()
