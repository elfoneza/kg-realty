"""Дашборд рынка недвижимости Кыргызстана.

Запуск:  uv run streamlit run dashboard.py
"""
from __future__ import annotations

import gzip
import json
import os
import sqlite3
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import analysis_tab

LOCAL_DB = Path(__file__).parent / "data" / "realty.db"
# В облаке (Streamlit Community Cloud) локальной базы нет: свежая база лежит в ветке `data`
# репозитория и обновляется GitHub Actions. Адрес задаётся в DATA_URL (переменная окружения или secrets).
DATA_URL = os.environ.get("DATA_URL", "https://raw.githubusercontent.com/elfoneza/kg-realty/data/realty.db.gz")

# Палитра: категориальные цвета в фиксированном порядке (не по рангу),
# классы жилья — упорядоченная одноцветная шкала от светлого к тёмному.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SOURCE_COLORS = dict(zip(["lalafo", "house", "stroka", "etagi", "elitka"], SERIES))
CLASS_ORDER = ["econom", "comfort", "comfort_plus", "business", "premium"]
CLASS_RU = {"econom": "Эконом", "comfort": "Комфорт", "comfort_plus": "Комфорт+",
            "business": "Бизнес", "premium": "Премиум"}
CLASS_COLORS = dict(zip([CLASS_RU[c] for c in CLASS_ORDER], ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b"]))
COND_RU = {"pso": "ПСО", "euro": "Евроремонт", "designer": "Дизайнерский", "good": "Хороший/косметика",
           "average": "Средний/старый", "unfinished": "Не достроено"}
SELLER_RU = {"owner": "Собственник", "agency": "Агентство/риелтор", "developer": "Застройщик"}
ROOMS_RU = {0: "Студия", 1: "1-комн.", 2: "2-комн.", 3: "3-комн.", 4: "4+ комн."}
SOURCE_RU = {"lalafo": "lalafo.kg (данные на 16.11.2026)", "house": "house.kg", "stroka": "stroka.kg",
             "etagi": "etagi (агентство)", "elitka": "elitka.kg"}
BLUE = SERIES[0]

st.set_page_config(page_title="Рынок недвижимости КР", page_icon="🏗️", layout="wide")
st.markdown("""<style>
  [data-testid="stMetricValue"] { font-size: 1.7rem; }
  .block-container { padding-top: 1.5rem; }
  .insight { padding: .6rem .9rem; border-left: 3px solid #2a78d6; margin-bottom: .5rem;
             background: rgba(42,120,214,.06); border-radius: 4px; }
</style>""", unsafe_allow_html=True)


# ------------------------------------------------------------------ данные
@st.cache_data(ttl=900, show_spinner="Загружаю свежую базу…")
def fetch_db(url: str) -> str:
    """Скачивает сжатую базу из репозитория (кэш 15 минут) и возвращает путь к файлу."""
    out = Path(tempfile.gettempdir()) / "kg_realty.db"
    with urllib.request.urlopen(url, timeout=60) as r:
        data = r.read()
    out.write_bytes(gzip.decompress(data) if url.endswith(".gz") else data)
    return str(out)


def db_path() -> Path | None:
    if LOCAL_DB.exists():
        return LOCAL_DB
    url = DATA_URL
    try:
        url = url or st.secrets.get("DATA_URL", "")
    except Exception:  # noqa: BLE001 — secrets не настроены
        pass
    return Path(fetch_db(url)) if url else None


def now_utc():
    return pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))


def demand_score(L: pd.DataFrame) -> pd.Series:
    """Внимание к объявлению в день, ранжированное внутри сайта и типа объекта (0..1).

    house/stroka/elitka — просмотры; lalafo — звонки + избранное (просмотры lalafo — это показы в ленте).
    Платно продвигаемые объявления исключаются: показы им покупаются.
    Ранг считается внутри сайта, типа объекта и возраста объявления.
    """
    raw = L["raw"].map(lambda v: json.loads(v) if isinstance(v, str) else (v or {}))
    eng = L["views"].astype(float)
    lal = L["source"].str.startswith("lalafo")
    eng[lal] = raw[lal].map(lambda r: (r.get("callers_count") or 0) + (r.get("favorite_count") or 0)).astype(float)
    eng[L["source"].str.startswith("etagi")] = np.nan
    promoted = raw.map(lambda r: bool(r.get("is_vip") or r.get("is_premium") or r.get("payedService")))
    eng[promoted] = np.nan
    rate = eng / L["days_on"]
    # Свежие объявления всегда набирают больше просмотров в день, поэтому сравниваем
    # только с объявлениями того же возраста на том же сайте.
    age = pd.cut(L["days_on"], [0, 3, 7, 14, 30, 60, 120, np.inf], labels=False)
    grp = [L["source"], L["property_type"].fillna("flat") == "flat", age]
    return rate.groupby(grp).rank(pct=True)


@st.cache_data(ttl=120)
def load(db: str, stamp: float):
    """stamp — время изменения файла базы: новая база сбрасывает кэш."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    L = pd.read_sql("""select id, source, property_type, rooms, area_total, floor, floors_total, series, condition,
                              is_new_building, price_usd, price_m2_usd, old_price_usd, market_deviation_pct,
                              city, district, district_norm, lat, lng, views, raw, seller_type, complex_name, developer_name, mortgage, installment,
                              published_at, first_seen, last_seen, is_active, removed_at, dup_group, url, title, address
                       from listings""", con,
                    parse_dates=["published_at", "first_seen", "last_seen", "removed_at"])
    H = pd.read_sql("select listing_id, seen_at, price_usd from price_history", con, parse_dates=["seen_at"])
    C = pd.read_sql("""select id, name, url, city, address, housing_class, developer_name, developer_plan,
                              price_m2_usd, finish_date, is_finished, rating, labels, gosstroy_url,
                              private_ads_count, rooms_data, first_seen
                       from complexes""", con, parse_dates=["finish_date", "first_seen"])
    R = pd.read_sql("select * from runs order by id desc limit 50", con, parse_dates=["started_at", "finished_at"])
    con.close()
    # чистка: выбросы и пустые цены не участвуют в ценовой статистике
    # Единое название района (назначается парсером, см. collector.assign_districts)
    L["district_site"] = L["district"]
    L["district"] = L["district_norm"]
    L["site"] = L["source"].str.split("_").str[0]
    L["is_flat"] = L["property_type"].fillna("flat") == "flat"
    L["days_on"] = ((now_utc() - L["published_at"].fillna(L["first_seen"])).dt.total_seconds() / 86400).clip(lower=1)
    L["demand_pct"] = demand_score(L)
    L = L.drop(columns=["raw"])
    # Прайс-лист застройщика: десятки лотов одного продавца в одном месте по одной цене за м².
    # В ценовой статистике такой прайс-лист считается одним объявлением, иначе он перекашивает район.
    loc = L["address"].fillna(L["lat"].round(4).astype(str) + "," + L["lng"].round(4).astype(str))
    L["pricelist_dup"] = L.assign(loc=loc).duplicated(["source", "loc", "price_m2_usd"]) & L["price_m2_usd"].notna()
    L["price_ok"] = L["price_m2_usd"].between(300, 10000) & ~L["pricelist_dup"]
    L["rooms_g"] = L["rooms"].clip(upper=4).map(ROOMS_RU).fillna("Не указано")
    # Сегмент: советские серии — вторичка; элитка или флаг сайта — новостройка
    soviet = L["series"].fillna("").str.match(r"^(\d+|khrushchevka|stalinka|malosemeyka)")
    L["segment"] = "Не указано"
    L.loc[(L["is_new_building"] == 1) | (L["series"] == "elitka"), "segment"] = "Новостройка"
    L.loc[soviet | ((L["is_new_building"] == 0) & (L["series"] != "elitka")), "segment"] = "Вторичка"
    L["flat_key"] = L["dup_group"].fillna(-L["id"]).astype(int)   # одна квартира на нескольких сайтах = 1
    C["labels"] = C["labels"].map(lambda v: ", ".join(json.loads(v)) if isinstance(v, str) and v.startswith("[") else (v or ""))
    C["class_ru"] = C["housing_class"].map(CLASS_RU).fillna("Не указан")
    C["finish_year"] = C["finish_date"].dt.year
    return L, H, C, R


DB = db_path()
if DB is None or not DB.exists():
    st.error("База не найдена. Локально запустите `uv run python -m kg_realty collect`, "
             "в облаке задайте DATA_URL в настройках приложения.")
    st.stop()

L, H, C, R = load(str(DB), DB.stat().st_mtime)
now = datetime.now(timezone.utc).replace(tzinfo=None)


def fig_style(fig, height=340, legend=True):
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=8, b=8), hovermode="closest",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None) if legend else None,
        showlegend=legend, bargap=0.25, font=dict(size=13),
    )
    fig.update_xaxes(showgrid=False, title=None)
    fig.update_yaxes(gridcolor="rgba(128,128,128,.18)", zeroline=False, title=None)
    fig.update_traces(marker_line_width=0, selector=dict(type="bar"))
    return fig


def fmt_usd(v):
    return "—" if pd.isna(v) else f"${v:,.0f}".replace(",", " ")


# ------------------------------------------------------------------ шапка и фильтры
last_run = R["finished_at"].max()
st.title("Рынок недвижимости Кыргызстана")
st.caption(f"Источники: house.kg, stroka.kg, etagi, elitka.kg (lalafo.kg — архив на 16.11.2026) · последнее обновление базы: "
           f"{(last_run + timedelta(hours=6)).strftime('%d.%m.%Y %H:%M') if pd.notna(last_run) else '—'} (Бишкек)")

f1, f2, f3, f4, f5, f6 = st.columns([1.2, 1.4, 1.3, 1.2, 1.3, .6])
cities = L.loc[L.is_active == 1, "city"].value_counts()
city = f1.selectbox("Город", ["Все"] + cities.index[:30].tolist(),
                    index=1 + cities.index[:30].tolist().index("Бишкек") if "Бишкек" in cities.index[:30] else 0)
# lalafo.kg больше не обновляется (блокирует серверы GitHub), поэтому по умолчанию выключен:
# его объявления никогда не помечаются снятыми и искажали бы картину текущего рынка.
srcs = f2.multiselect("Источники", list(SOURCE_RU), default=[s for s in SOURCE_RU if s != "lalafo"],
                      format_func=SOURCE_RU.get,
                      help="lalafo.kg не обновляется с 16.11.2026: сайт блокирует облачные серверы")
ROOM_OPTS = list(ROOMS_RU.values()) + ["Не указано"]
rooms = f3.multiselect("Комнатность", ROOM_OPTS, default=ROOM_OPTS)
segment = f4.selectbox("Сегмент", ["Все", "Новостройка", "Вторичка"])
seller = f5.selectbox("Продавец", ["Все"] + list(SELLER_RU.values()))
if f6.button("Обновить", width="stretch"):
    st.cache_data.clear()
    st.rerun()

m = L["site"].isin(srcs) & L["is_flat"] & L["rooms_g"].isin(rooms)
if city != "Все":
    m &= L["city"] == city
if segment != "Все":
    m &= L["segment"] == segment
if seller != "Все":
    m &= L["seller_type"].map(SELLER_RU) == seller
F = L[m]
A = F[F.is_active == 1]                                   # активные
AP = A[A.price_ok]                                        # с корректной ценой
AU = AP.sort_values("last_seen").drop_duplicates("flat_key", keep="last")   # уникальные квартиры

# изменения цен за 7 дней
H7 = H[H.seen_at > now - timedelta(days=7)]
chg = (H.sort_values("seen_at").groupby("listing_id")["price_usd"].agg(["first", "last", "count"]))
chg = chg[chg["count"] > 1]
chg["delta_pct"] = (chg["last"] / chg["first"] - 1) * 100
chg = chg.join(L.set_index("id"), how="inner")
chg = chg[chg.index.isin(F.id)]
drops7 = chg[(chg.delta_pct < -0.5) & chg.index.isin(H7.listing_id)]

# ------------------------------------------------------------------ KPI
k = st.columns(6)
k[0].metric("Квартир в продаже", f"{A.flat_key.nunique():,}".replace(",", " "),
            help="Уникальные квартиры: одна и та же квартира на нескольких сайтах считается один раз")
k[1].metric("Медиана цены за м²", fmt_usd(AU.price_m2_usd.median()))
k[2].metric("Медиана цены квартиры", fmt_usd(AU.price_usd.median()))
new7 = F[F.first_seen > now - timedelta(days=7)].flat_key.nunique()
k[3].metric("Новых за 7 дней", f"{new7:,}".replace(",", " "),
            help="Впервые замечены парсером. В первые дни работы сюда попадает вся база")
rem7 = F[(F.removed_at > now - timedelta(days=7))].flat_key.nunique()
k[4].metric("Снято за 7 дней", f"{rem7:,}".replace(",", " "),
            help="Пропали с сайта при полном обходе — косвенный признак продажи")
k[5].metric("Снижений цены за 7 дней", f"{len(drops7):,}".replace(",", " "))

tabs = st.tabs(["Аналитика", "Обзор рынка", "Районы", "Новостройки и застройщики", "Динамика", "Источники", "Выгодные предложения"])

# ================================================================== Аналитика
with tabs[0]:
    analysis_tab.render(L, C, city, srcs, now)

# ================================================================== Обзор
with tabs[1]:
    # Авто-выводы
    ins = []
    if len(AU) > 30:
        nb = AU[AU.segment == "Новостройка"].price_m2_usd.median()
        sb = AU[AU.segment == "Вторичка"].price_m2_usd.median()
        if pd.notna(nb) and pd.notna(sb):
            ins.append(f"Новостройки продаются по медиане <b>{fmt_usd(nb)}/м²</b>, вторичка — <b>{fmt_usd(sb)}/м²</b> "
                       f"({(nb / sb - 1) * 100:+.0f}%).")
        share = AU.rooms_g.value_counts(normalize=True)
        if len(share):
            ins.append(f"Больше всего предложений — <b>{share.index[0]}</b> ({share.iloc[0] * 100:.0f}% рынка).")
        own = (AU.seller_type == "owner").mean()
        ins.append(f"Доля объявлений от собственников: <b>{own * 100:.0f}%</b> (по тем, где продавец указан, — "
                   f"{(AU.seller_type == 'owner').sum() / max(1, AU.seller_type.notna().sum()) * 100:.0f}%).")
        pso = AU[AU.condition.notna()]
        if len(pso) > 20:
            ins.append(f"Под самоотделку (ПСО) продаётся <b>{(pso.condition == 'pso').mean() * 100:.0f}%</b> квартир "
                       f"с указанным состоянием.")
        dist = AU.groupby("district").price_m2_usd.agg(["median", "size"]).query("size >= 15")
        if len(dist) >= 3:
            ins.append(f"Самый дорогой район: <b>{dist['median'].idxmax()}</b> ({fmt_usd(dist['median'].max())}/м²), "
                       f"самый доступный: <b>{dist['median'].idxmin()}</b> ({fmt_usd(dist['median'].min())}/м²).")
        dup = L[L.is_active == 1].dup_group.notna().mean()
        ins.append(f"{dup * 100:.0f}% активных объявлений — дубли одной квартиры на разных сайтах.")
    for t in ins:
        st.markdown(f'<div class="insight">{t}</div>', unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Предложение по комнатности")
        g = AU.groupby("rooms_g", observed=True).agg(n=("id", "size"), med=("price_m2_usd", "median"),
                                                     med_p=("price_usd", "median")).reindex(list(ROOMS_RU.values())).dropna(how="all")
        fig = go.Figure(go.Bar(x=g.index, y=g.n, marker_color=BLUE, marker_cornerradius=4,
                               customdata=g[["med", "med_p"]],
                               hovertemplate="<b>%{x}</b><br>Квартир: %{y}<br>Медиана: $%{customdata[0]:,.0f}/м²"
                                             "<br>Медиана цены: $%{customdata[1]:,.0f}<extra></extra>"))
        st.plotly_chart(fig_style(fig, legend=False), width="stretch")
    with c2:
        st.subheader("Медиана $/м² по комнатности")
        fig = go.Figure(go.Bar(x=g.index, y=g.med, marker_color=BLUE, marker_cornerradius=4,
                               text=g.med.map(lambda v: f"${v:,.0f}"), textposition="outside",
                               hovertemplate="<b>%{x}</b><br>$%{y:,.0f}/м²<extra></extra>"))
        st.plotly_chart(fig_style(fig, legend=False), width="stretch")

    c3, c4 = st.columns(2)
    with c3:
        st.subheader("Цена за м² по состоянию")
        cc = AU[AU.condition.notna()].groupby("condition").price_m2_usd.agg(["median", "size"]).query("size >= 5")
        cc.index = cc.index.map(COND_RU)
        cc = cc.sort_values("median")
        fig = go.Figure(go.Bar(y=cc.index, x=cc["median"], orientation="h", marker_color=BLUE,
                               marker_cornerradius=4, customdata=cc["size"],
                               hovertemplate="<b>%{y}</b><br>$%{x:,.0f}/м²<br>Объявлений: %{customdata}<extra></extra>"))
        st.plotly_chart(fig_style(fig, legend=False), width="stretch")
    with c4:
        st.subheader("Цена за м² по серии дома")
        sr = AU[AU.series.notna() & (AU.series != "other")].groupby("series").price_m2_usd.agg(["median", "size"]) \
            .query("size >= 5").sort_values("median")
        sr.index = sr.index.str.replace(r"^(\d+)", r"\1 серия", regex=True).str.replace("_improved", " улучш.").str.replace("elitka", "элитка") \
            .str.replace("khrushchevka", "хрущёвка").str.replace("stalinka", "сталинка").str.replace("malosemeyka", "малосемейка")
        fig = go.Figure(go.Bar(y=sr.index, x=sr["median"], orientation="h", marker_color=BLUE,
                               marker_cornerradius=4, customdata=sr["size"],
                               hovertemplate="<b>%{y}</b><br>$%{x:,.0f}/м²<br>Объявлений: %{customdata}<extra></extra>"))
        st.plotly_chart(fig_style(fig, legend=False), width="stretch")

    st.subheader("Распределение цены за м²: новостройка и вторичка")
    seg = AU[AU.segment != "Не указано"]
    fig = px.histogram(seg, x="price_m2_usd", color="segment", nbins=60, barmode="overlay", opacity=.75,
                       color_discrete_map={"Новостройка": SERIES[0], "Вторичка": SERIES[1]},
                       labels={"price_m2_usd": "$/м²", "segment": ""})
    fig.update_traces(hovertemplate="$%{x}/м²: %{y} квартир<extra></extra>")
    st.plotly_chart(fig_style(fig, 300), width="stretch")

# ================================================================== Районы
with tabs[2]:
    st.subheader("Медиана цены за м² по районам")
    minn = st.slider("Минимум объявлений в районе", 5, 100, 15, 5)
    d = AU[AU.district.notna()].groupby("district").agg(
        med=("price_m2_usd", "median"), n=("id", "size"), med_p=("price_usd", "median"),
        own=("seller_type", lambda s: (s == "owner").mean() * 100)).query("n >= @minn").sort_values("med")
    if d.empty:
        st.info("Мало данных: уменьшите порог или дождитесь полного обхода.")
    else:
        fig = go.Figure(go.Bar(y=d.index, x=d.med, orientation="h", marker_color=BLUE, marker_cornerradius=4,
                               customdata=d[["n", "med_p", "own"]],
                               hovertemplate="<b>%{y}</b><br>$%{x:,.0f}/м²<br>Объявлений: %{customdata[0]}"
                                             "<br>Медиана цены: $%{customdata[1]:,.0f}<br>От собственников: %{customdata[2]:.0f}%<extra></extra>"))
        st.plotly_chart(fig_style(fig, max(300, 22 * len(d)), legend=False), width="stretch")

    st.subheader("Где больше всего предложений")
    top = AU[AU.district.notna()].district.value_counts().head(15)
    fig = go.Figure(go.Bar(x=top.index, y=top.values, marker_color=BLUE, marker_cornerradius=4,
                           hovertemplate="<b>%{x}</b><br>%{y} квартир<extra></extra>"))
    st.plotly_chart(fig_style(fig, 320, legend=False), width="stretch")

# ================================================================== Новостройки
with tabs[3]:
    CB = C if city == "Все" else C[C.city == city]
    building = CB[CB.is_finished == 0]
    k2 = st.columns(4)
    k2[0].metric("ЖК строится", f"{len(building):,}".replace(",", " "))
    k2[1].metric("Застройщиков с ЖК в стройке", f"{building.developer_name.nunique():,}".replace(",", " "))
    k2[2].metric("Медиана $/м² (строящиеся)", fmt_usd(building.price_m2_usd.median()))
    with_inst = building.labels.fillna("").str.contains("ассроч", case=False).mean() * 100
    k2[3].metric("ЖК с рассрочкой в метках", f"{with_inst:.0f}%",
                 help="По меткам на карточке ЖК («Рассрочка до 35 мес»). Не все застройщики их заполняют")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Строящиеся ЖК по классу жилья")
        g = building.groupby("class_ru").agg(n=("id", "size"), med=("price_m2_usd", "median")) \
            .reindex([CLASS_RU[c] for c in CLASS_ORDER]).dropna(how="all")
        fig = go.Figure(go.Bar(x=g.index, y=g.n, marker_color=[CLASS_COLORS[c] for c in g.index],
                               marker_cornerradius=4, customdata=g.med,
                               hovertemplate="<b>%{x}</b><br>ЖК: %{y}<br>Медиана: $%{customdata:,.0f}/м²<extra></extra>"))
        st.plotly_chart(fig_style(fig, legend=False), width="stretch")
    with c2:
        st.subheader("Медиана $/м² по классу")
        fig = go.Figure(go.Bar(x=g.index, y=g.med, marker_color=[CLASS_COLORS[c] for c in g.index],
                               marker_cornerradius=4, text=g.med.map(lambda v: "—" if pd.isna(v) else f"${v:,.0f}"),
                               textposition="outside", hovertemplate="<b>%{x}</b><br>$%{y:,.0f}/м²<extra></extra>"))
        st.plotly_chart(fig_style(fig, legend=False), width="stretch")

    st.subheader("Плановая сдача ЖК по годам")
    yrs = building[(building.finish_year >= now.year) & (CB.finish_year <= now.year + 5)]
    yy = yrs.groupby(["finish_year", "class_ru"]).size().reset_index(name="n")
    fig = px.bar(yy, x="finish_year", y="n", color="class_ru", category_orders={"class_ru": list(CLASS_COLORS)},
                 color_discrete_map=CLASS_COLORS, labels={"finish_year": "", "n": "ЖК", "class_ru": ""})
    fig.update_traces(marker_line_width=2, marker_line_color="rgba(0,0,0,0)",
                      hovertemplate="%{x}: %{y} ЖК<extra>%{fullData.name}</extra>")
    fig.update_xaxes(dtick=1)
    st.plotly_chart(fig_style(fig, 320), width="stretch")

    st.subheader("Застройщики: кто сколько строит")
    dv = building.groupby("developer_name").agg(
        ЖК=("id", "size"), med=("price_m2_usd", "median"),
        классы=("class_ru", lambda s: ", ".join(sorted(set(s)))),
        тариф=("developer_plan", "first"),
        ближайшая_сдача=("finish_date", "min")).sort_values("ЖК", ascending=False).head(25)
    dv["med"] = dv["med"].map(fmt_usd)
    dv["ближайшая_сдача"] = dv["ближайшая_сдача"].dt.strftime("%m.%Y")
    dv.index.name = "Застройщик"
    st.dataframe(dv.rename(columns={"med": "медиана $/м²", "ближайшая_сдача": "ближайшая сдача"}), width="stretch", height=420)

    st.subheader("Все ЖК")
    show = CB[["name", "class_ru", "developer_name", "price_m2_usd", "finish_date", "labels", "address", "rating", "url", "gosstroy_url"]] \
        .sort_values("finish_date", ascending=False)
    st.dataframe(show, width="stretch", height=380, hide_index=True, column_config={
        "name": "ЖК", "class_ru": "Класс", "developer_name": "Застройщик",
        "price_m2_usd": st.column_config.NumberColumn("$/м²", format="$%d"),
        "finish_date": st.column_config.DateColumn("Сдача", format="MM.YYYY"),
        "labels": "Условия", "address": "Адрес", "rating": "Рейтинг",
        "url": st.column_config.LinkColumn("elitka", display_text="открыть"),
        "gosstroy_url": st.column_config.LinkColumn("Минстрой", display_text="паспорт")})

# ================================================================== Динамика
with tabs[4]:
    days = (L.first_seen.max() - L.first_seen.min()).days if len(L) else 0
    if days < 7:
        st.info(f"История копится с {L.first_seen.min():%d.%m.%Y}. Графики динамики станут информативными "
                f"через 1–2 недели работы по расписанию.")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Новые и снятые объявления по дням")
        nd = F.groupby(F.first_seen.dt.normalize()).flat_key.nunique().rename("Новые")
        rd = F[F.removed_at.notna()].groupby(F.removed_at.dt.normalize()).flat_key.nunique().rename("Снятые")
        dd = pd.concat([nd, rd], axis=1).fillna(0).reset_index(names="day").melt("day", var_name="тип", value_name="n")
        fig = px.bar(dd, x="day", y="n", color="тип", barmode="group",
                     color_discrete_map={"Новые": SERIES[0], "Снятые": SERIES[1]}, labels={"day": "", "n": ""})
        fig.update_traces(hovertemplate="%{x|%d.%m}: %{y}<extra>%{fullData.name}</extra>")
        st.plotly_chart(fig_style(fig, 300), width="stretch")
    with c2:
        st.subheader("Медиана $/м² активных объявлений по дням")
        # для каждого дня: объявления, видимые в этот день (first_seen <= день <= last_seen)
        rng = pd.date_range(L.first_seen.min().normalize(), now, freq="D")
        rows = []
        for day in rng:
            vis = F[F.price_ok & (F.first_seen <= day + timedelta(days=1)) & (F.last_seen >= day)]
            vis = vis.drop_duplicates("flat_key")
            for sg in ("Новостройка", "Вторичка"):
                v = vis[vis.segment == sg].price_m2_usd
                if len(v) >= 10:
                    rows.append(dict(day=day, segment=sg, med=v.median(), n=len(v)))
        ts = pd.DataFrame(rows)
        if not ts.empty:
            fig = px.line(ts, x="day", y="med", color="segment", markers=True, custom_data=["n"],
                          color_discrete_map={"Новостройка": SERIES[0], "Вторичка": SERIES[1]},
                          labels={"day": "", "med": ""})
            fig.update_traces(line_width=2, marker_size=8,
                              hovertemplate="%{x|%d.%m}: $%{y:,.0f}/м² (%{customdata[0]} кв.)<extra>%{fullData.name}</extra>")
            fig.update_layout(hovermode="x unified")
            st.plotly_chart(fig_style(fig, 300), width="stretch")

    st.subheader("Последние изменения цен")
    if chg.empty:
        st.caption("Пока нет: цена должна измениться между двумя обходами.")
    else:
        t = chg.sort_values("delta_pct")[["source", "rooms_g", "area_total", "district", "first", "last", "delta_pct", "url"]]
        st.dataframe(t, width="stretch", hide_index=True, column_config={
            "source": "Сайт", "rooms_g": "Комн.", "area_total": "м²", "district": "Район",
            "first": st.column_config.NumberColumn("Было, $", format="%d"),
            "last": st.column_config.NumberColumn("Стало, $", format="%d"),
            "delta_pct": st.column_config.NumberColumn("Изменение", format="%.1f%%"),
            "url": st.column_config.LinkColumn("Ссылка", display_text="открыть")})

# ================================================================== Источники
with tabs[5]:
    st.subheader("Сравнение источников")
    srcs_df = A.groupby("source").agg(
        активных=("id", "size"),
        медиана_м2=("price_m2_usd", lambda s: s[s.between(300, 10000)].median()),
        собственники=("seller_type", lambda s: (s == "owner").mean() * 100),
        новостройки=("segment", lambda s: (s == "Новостройка").mean() * 100),
        дубли=("dup_group", lambda s: s.notna().mean() * 100),
    )
    srcs_df.index = srcs_df.index.map(SOURCE_RU)
    st.dataframe(srcs_df, width="stretch", column_config={
        "медиана_м2": st.column_config.NumberColumn("медиана $/м²", format="$%d"),
        "собственники": st.column_config.NumberColumn("от собственников", format="%.0f%%"),
        "новостройки": st.column_config.NumberColumn("доля новостроек", format="%.0f%%"),
        "дубли": st.column_config.NumberColumn("есть на других сайтах", format="%.0f%%")})

    st.subheader("Медиана $/м² по комнатности на разных сайтах")
    sc = AP.groupby(["rooms_g", "source"]).price_m2_usd.median().reset_index()
    sc["source"] = sc["source"].map(SOURCE_RU)
    fig = px.bar(sc, x="rooms_g", y="price_m2_usd", color="source", barmode="group",
                 category_orders={"rooms_g": list(ROOMS_RU.values()), "source": [SOURCE_RU[s] for s in SOURCE_COLORS]},
                 color_discrete_map={SOURCE_RU[k]: v for k, v in SOURCE_COLORS.items()},
                 labels={"rooms_g": "", "price_m2_usd": "", "source": ""})
    fig.update_traces(marker_cornerradius=4, hovertemplate="%{x}: $%{y:,.0f}/м²<extra>%{fullData.name}</extra>")
    st.plotly_chart(fig_style(fig, 340), width="stretch")

    st.subheader("Журнал запусков парсеров")
    st.dataframe(R[["source", "mode", "started_at", "pages", "items", "new_items", "price_changes", "removed", "ok", "error"]],
                 width="stretch", hide_index=True, height=300)

# ================================================================== Выгодные
with tabs[6]:
    st.subheader("Дешевле медианы своего района")
    st.caption("Сравнение с медианой $/м² по тому же району, комнатности и сегменту (группы от 8 объявлений).")
    grp = ["district", "rooms_g", "segment"]
    base = AU[AU.district.notna() & (AU.rooms_g != "Не указано")].copy()
    base["seller_type"] = base["seller_type"].map(SELLER_RU)
    base["grp_med"] = base.groupby(grp).price_m2_usd.transform("median")
    base["grp_n"] = base.groupby(grp).price_m2_usd.transform("size")
    base = base[base.grp_n >= 8]
    base["vs_pct"] = (base.price_m2_usd / base.grp_med - 1) * 100
    lim = st.slider("Насколько дешевле, %", 5, 50, 15)
    deals = base[base.vs_pct <= -lim].sort_values("vs_pct")
    st.dataframe(deals[["source", "rooms_g", "area_total", "floor", "floors_total", "district", "segment",
                        "price_usd", "price_m2_usd", "grp_med", "vs_pct", "seller_type", "url"]],
                 width="stretch", hide_index=True, height=480, column_config={
                     "source": "Сайт", "rooms_g": "Комн.", "area_total": "м²", "floor": "Этаж",
                     "floors_total": "Этажн.", "district": "Район", "segment": "Сегмент",
                     "price_usd": st.column_config.NumberColumn("Цена, $", format="%d"),
                     "price_m2_usd": st.column_config.NumberColumn("$/м²", format="%d"),
                     "grp_med": st.column_config.NumberColumn("Медиана района, $/м²", format="%d"),
                     "vs_pct": st.column_config.NumberColumn("Отклонение", format="%.0f%%"),
                     "seller_type": "Продавец", "url": st.column_config.LinkColumn("Ссылка", display_text="открыть")})
    st.caption("Сильно заниженная цена бывает и ошибкой в объявлении (цена за м² вместо полной, опечатка) — проверяйте.")
