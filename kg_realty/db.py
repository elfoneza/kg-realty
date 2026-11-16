"""Единая схема данных.

Таблицы:
  listings          — объявления о продаже со всех сайтов, в одном формате
  price_history     — история цен (строка пишется только когда цена изменилась)
  complexes         — жилые комплексы (основной источник — elitka.kg)
  runs              — журнал запусков парсеров

По умолчанию SQLite-файл data/realty.db. Для PostgreSQL задайте
переменную окружения DATABASE_URL=postgresql+psycopg://user:pass@host/db
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint,
    create_engine, ForeignKey, Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URL = f"sqlite:///{ROOT / 'data' / 'realty.db'}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_source_id"),
        Index("ix_listing_match", "city", "rooms", "floor", "floors_total"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(20), index=True)       # lalafo / house / stroka / etagi / elitka
    source_id: Mapped[str] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(String(500))

    # Объект
    property_type: Mapped[str] = mapped_column(String(20), default="flat")
    rooms: Mapped[int | None] = mapped_column(Integer)                # 0 = студия
    area_total: Mapped[float | None] = mapped_column(Float)
    area_kitchen: Mapped[float | None] = mapped_column(Float)
    floor: Mapped[int | None] = mapped_column(Integer)
    floors_total: Mapped[int | None] = mapped_column(Integer)
    series: Mapped[str | None] = mapped_column(String(40))            # нормализовано: elitka, 104, 105, 106, khrushchevka...
    condition: Mapped[str | None] = mapped_column(String(20))         # нормализовано: pso, euro, designer, good, average, unfinished
    wall_material: Mapped[str | None] = mapped_column(String(20))     # brick, panel, monolith, other
    year_built: Mapped[int | None] = mapped_column(Integer)
    is_new_building: Mapped[bool | None] = mapped_column(Boolean)

    # Цена (на момент последнего обхода)
    price_kgs: Mapped[float | None] = mapped_column(Float)
    price_usd: Mapped[float | None] = mapped_column(Float)
    price_m2_usd: Mapped[float | None] = mapped_column(Float, index=True)
    old_price_usd: Mapped[float | None] = mapped_column(Float)        # если сайт сам показывает старую цену
    market_deviation_pct: Mapped[float | None] = mapped_column(Float) # «выше/ниже рынка на X%» (house, stroka)

    # Где
    city: Mapped[str | None] = mapped_column(String(60), index=True)
    district: Mapped[str | None] = mapped_column(String(120), index=True)          # как на сайте
    district_norm: Mapped[str | None] = mapped_column(String(120), index=True)     # единое название (см. collector.assign_districts)
    address: Mapped[str | None] = mapped_column(String(300))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)

    # Кто и где
    seller_type: Mapped[str | None] = mapped_column(String(20), index=True)  # owner / agency / developer
    seller_name: Mapped[str | None] = mapped_column(String(200))
    complex_name: Mapped[str | None] = mapped_column(String(200))
    complex_source_id: Mapped[str | None] = mapped_column(String(64))
    developer_name: Mapped[str | None] = mapped_column(String(200))

    # Условия сделки
    mortgage: Mapped[bool | None] = mapped_column(Boolean)
    installment: Mapped[bool | None] = mapped_column(Boolean)

    views: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(300))

    # Время
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Склейка дублей между сайтами: одинаковый dup_group = одна и та же квартира
    dup_group: Mapped[int | None] = mapped_column(Integer, index=True)

    raw: Mapped[dict | None] = mapped_column(JSON)


class PriceHistory(Base):
    __tablename__ = "price_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    price_kgs: Mapped[float | None] = mapped_column(Float)
    price_usd: Mapped[float | None] = mapped_column(Float)


class Complex(Base):
    __tablename__ = "complexes"
    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_complex_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(20))
    source_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str | None] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(500))
    city: Mapped[str | None] = mapped_column(String(60), index=True)
    address: Mapped[str | None] = mapped_column(String(300))
    housing_class: Mapped[str | None] = mapped_column(String(20), index=True)  # econom / comfort / comfort_plus / business / premium
    developer_name: Mapped[str | None] = mapped_column(String(200), index=True)
    developer_id: Mapped[str | None] = mapped_column(String(64))
    developer_plan: Mapped[str | None] = mapped_column(String(30))     # платный тариф застройщика на площадке
    price_m2_usd: Mapped[float | None] = mapped_column(Float)
    price_m2_kgs: Mapped[float | None] = mapped_column(Float)
    rooms_data: Mapped[list | None] = mapped_column(JSON)              # минимальная площадь и цена по комнатности
    finish_date: Mapped[datetime | None] = mapped_column(DateTime)
    is_finished: Mapped[bool | None] = mapped_column(Boolean)
    rating: Mapped[float | None] = mapped_column(Float)
    reviews_count: Mapped[int | None] = mapped_column(Integer)
    labels: Mapped[list | None] = mapped_column(JSON)                  # «Рассрочка до 35 мес», «Старт продаж»...
    gosstroy_url: Mapped[str | None] = mapped_column(String(300))     # паспорт объекта в реестре Минстроя
    private_ads_count: Mapped[int | None] = mapped_column(Integer)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    raw: Mapped[dict | None] = mapped_column(JSON)


class ComplexPriceHistory(Base):
    __tablename__ = "complex_price_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    complex_id: Mapped[int] = mapped_column(ForeignKey("complexes.id"), index=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    price_m2_usd: Mapped[float | None] = mapped_column(Float)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(10))          # fresh / full
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    items: Mapped[int] = mapped_column(Integer, default=0)
    new_items: Mapped[int] = mapped_column(Integer, default=0)
    price_changes: Mapped[int] = mapped_column(Integer, default=0)
    removed: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)


_engine = None
Session = sessionmaker(expire_on_commit=False)


def _add_missing_columns(engine):
    """Простая миграция: добавляет новые колонки моделей в уже существующие таблицы."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    with engine.begin() as con:
        for table in Base.metadata.sorted_tables:
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    con.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}'))


def init_db(url: str | None = None):
    global _engine
    url = url or os.environ.get("DATABASE_URL", DEFAULT_URL)
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    kw = {"connect_args": {"timeout": 60, "check_same_thread": False}} if url.startswith("sqlite") else {}
    _engine = create_engine(url, future=True, **kw)
    Base.metadata.create_all(_engine)
    _add_missing_columns(_engine)
    Session.configure(bind=_engine)
    return _engine
