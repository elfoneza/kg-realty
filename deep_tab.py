"""Вкладка «Углублённый анализ».

1. Цена застройщика и перепродажи в том же ЖК
2. Наценка за ремонт
3. Классы жилья
4. Активность застройщиков
5. Этаж и этажность
6. Ипотека и рассрочка
7. Собственники и агентства по районам
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
CLASS_ORDER = ["Эконом", "Комфорт", "Комфорт+", "Бизнес", "Премиум"]
CLASS_COLORS = dict(zip(CLASS_ORDER, ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b"]))
COND_RU = {"pso": "ПСО", "good": "Хороший / косметика", "euro": "Евроремонт", "designer": "Дизайнерский",
           "average": "Средний / старый"}
ROOMS = ["Студия", "1-комн.", "2-комн.", "3-комн.", "4+ комн."]


def _usd(v):
    return "—" if pd.isna(v) else f"${v:,.0f}".replace(",", " ")


def _pct(v):
    return "—" if pd.isna(v) else f"{v:+.0f}%"


def _style(fig, height=340, legend=True):
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=8, b=8), showlegend=legend, font=dict(size=13),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None), bargap=0.25)
    fig.update_xaxes(showgrid=False, title=None)
    fig.update_yaxes(gridcolor="rgba(128,128,128,.18)", zeroline=False, title=None)
    fig.update_traces(marker_line_width=0, selector=dict(type="bar"))
    return fig


def _hbar(s: pd.Series, fmt: str, height=None, color=BLUE, custom=None, hover=None):
    fig = go.Figure(go.Bar(y=s.index, x=s.values, orientation="h", marker_color=color, marker_cornerradius=3,
                           customdata=custom, hovertemplate=hover or ("<b>%{y}</b><br>%{x:" + fmt + "}<extra></extra>")))
    return _style(fig, height or max(300, 24 * len(s)), False)


def cell_ratio(df: pd.DataFrame, cols=("district", "segment", "rooms_g")) -> pd.Series:
    """Цена за м² относительно медианы «похожих» квартир (тот же район, сегмент, комнатность)."""
    grp = df.groupby(list(cols)).price_m2_usd
    med, n = grp.transform("median"), grp.transform("size")
    return (df.price_m2_usd / med - 1).where(n >= 8) * 100


def render(L: pd.DataFrame, C: pd.DataFrame, city: str, sites: list[str], now) -> None:
    flats = L[L.is_flat & L.site.isin(sites) & (L.is_active == 1)]
    if city != "Все":
        flats = flats[flats.city == city]
    priced = flats[flats.price_ok].sort_values("last_seen").drop_duplicates("flat_key", keep="last")
    CB = C if city == "Все" else C[C.city == city]

    st.caption("Цены — по объявлениям о продаже (цены предложения). Сравнения внутри «похожих» квартир: тот же "
               "район, сегмент (новостройка/вторичка) и комнатность — так различия не объясняются местом или размером.")

    # ============================================================ 1
    st.header("1. Цена застройщика и перепродажи в том же ЖК")
    ads = L[(L.source == "elitka") & (L.is_active == 1) & L.price_ok & L.complex_source_id.notna()]
    cx = CB[CB.price_m2_usd.notna()].set_index("source_id")
    ads = ads[ads.complex_source_id.isin(cx.index)].copy()
    if len(ads) < 10:
        st.info("Мало объявлений о перепродаже в ЖК с известной ценой застройщика.")
    else:
        ads["dev_m2"] = ads.complex_source_id.map(cx.price_m2_usd)
        ads["zhk"] = ads.complex_source_id.map(cx.name)
        ads["dev"] = ads.complex_source_id.map(cx.developer_name)
        ads["cls"] = ads.complex_source_id.map(cx.class_ru)
        ads["finish"] = ads.complex_source_id.map(cx.finish_date)
        ads["gap"] = (ads.price_m2_usd / ads.dev_m2 - 1) * 100
        months = (ads.finish - now).dt.days / 30.4
        ads["stage"] = pd.cut(months, [-1e9, 0, 6, 12, 24, 1e9],
                              labels=["Сдан", "До сдачи < 6 мес", "6–12 мес", "1–2 года", "> 2 лет"])
        k = st.columns(4)
        k[0].metric("Перепродаж в ЖК", len(ads))
        k[1].metric("Медианная разница с застройщиком", _pct(ads.gap.median()))
        k[2].metric("Дороже застройщика", f"{(ads.gap > 0).mean() * 100:.0f}%")
        k[3].metric("ЖК с перепродажами", ads.complex_source_id.nunique())
        st.warning("Застройщики на elitka указывают цену «от» — минимальную за м², а перепродают обычно не самые "
                   "дешёвые квартиры. Поэтому если перепродажа дешевле даже этой цены, переуступка заметно выгоднее "
                   "покупки у застройщика. Главное — как разница меняется со стадией стройки.", icon="⚠️")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Разница с ценой застройщика по стадии стройки**")
            g = ads.groupby("stage", observed=True).agg(gap=("gap", "median"), n=("id", "size"))
            fig = go.Figure(go.Bar(x=g.index.astype(str), y=g.gap, marker_color=BLUE, marker_cornerradius=4,
                                   customdata=g.n, hovertemplate="<b>%{x}</b><br>%{y:+.0f}%<br>"
                                                                 "Перепродаж: %{customdata}<extra></extra>"))
            fig.add_hline(y=0, line_color="rgba(128,128,128,.6)")
            st.plotly_chart(_style(fig, 300, False), width="stretch")
        with c2:
            st.markdown("**По классу жилья**")
            g = ads.groupby("cls").agg(gap=("gap", "median"), n=("id", "size")).reindex(CLASS_ORDER).dropna()
            fig = go.Figure(go.Bar(x=g.index, y=g.gap, marker_color=[CLASS_COLORS[c] for c in g.index],
                                   marker_cornerradius=4, customdata=g.n,
                                   hovertemplate="<b>%{x}</b><br>%{y:+.0f}%<br>Перепродаж: %{customdata}<extra></extra>"))
            fig.add_hline(y=0, line_color="rgba(128,128,128,.6)")
            st.plotly_chart(_style(fig, 300, False), width="stretch")
        t = ads.groupby(["zhk", "dev", "cls"]).agg(n=("id", "size"), dev_m2=("dev_m2", "first"),
                                                   resale=("price_m2_usd", "median"), gap=("gap", "median"),
                                                   finish=("finish", "first")) \
            .reset_index().sort_values("n", ascending=False)
        st.dataframe(t, width="stretch", hide_index=True, height=320, column_config={
            "zhk": "ЖК", "dev": "Застройщик", "cls": "Класс", "n": "Перепродаж",
            "dev_m2": st.column_config.NumberColumn("Застройщик, от $/м²", format="$%.0f"),
            "resale": st.column_config.NumberColumn("Перепродажа, медиана $/м²", format="$%.0f"),
            "gap": st.column_config.NumberColumn("Разница", format="%+.0f%%"),
            "finish": st.column_config.DateColumn("Сдача", format="MM.YYYY")})

    # ============================================================ 2
    st.header("2. Наценка за ремонт")
    base = priced[priced.condition.isin(COND_RU) & priced.district.notna()]
    cells = base.groupby(["district", "segment", "rooms_g", "condition"]).price_m2_usd.agg(["median", "size"])
    cells = cells[cells["size"] >= 3]["median"].unstack("condition")
    if "pso" not in cells or cells["pso"].notna().sum() < 5:
        st.info("Мало данных для сравнения с ПСО.")
    else:
        rows = []
        for cond in ["good", "euro", "designer"]:
            if cond in cells:
                both = cells[["pso", cond]].dropna()
                if len(both) >= 5:
                    rows.append(dict(cond=COND_RU[cond], pct=((both[cond] / both["pso"] - 1) * 100).median(),
                                     usd=(both[cond] - both["pso"]).median(), cells=len(both)))
        prem = pd.DataFrame(rows).set_index("cond")
        if not prem.empty:
            eu = prem.loc["Евроремонт"] if "Евроремонт" in prem.index else prem.iloc[0]
            st.success(f"Квартира с евроремонтом в среднем на **{eu.pct:+.0f}%** ({_usd(eu.usd)}/м²) дороже такой же "
                       f"под самоотделку. Для квартиры 60 м² это около **{_usd(eu.usd * 60)}** — сравните со "
                       f"стоимостью ремонта.")
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Наценка к ПСО, %** (медиана по похожим квартирам)")
                st.plotly_chart(_hbar(prem.pct, "+.0f", 220, custom=prem[["usd", "cells"]].values,
                                      hover="<b>%{y}</b><br>%{x:+.0f}% · $%{customdata[0]:,.0f}/м²<br>"
                                            "Групп сравнения: %{customdata[1]}<extra></extra>"), width="stretch")
            with c2:
                st.markdown("**Наценка к ПСО, $/м²**")
                st.plotly_chart(_hbar(prem.usd, ",.0f", 220), width="stretch")
        # по районам: евроремонт vs ПСО
        if "euro" in cells:
            d = cells[["pso", "euro"]].dropna().reset_index()
            d["pct"] = (d.euro / d.pso - 1) * 100
            byd = d.groupby("district").pct.median()
            cnt = d.groupby("district").size()
            byd = byd[cnt >= 2].sort_values()
            if len(byd) >= 3:
                st.markdown("**Наценка за евроремонт по районам, %**")
                st.plotly_chart(_hbar(byd, "+.0f"), width="stretch")

    # ============================================================ 3
    st.header("3. Классы жилья")
    cb = CB[CB.class_ru.isin(CLASS_ORDER)].copy()
    cb["inst"] = cb.labels.str.contains("ассроч|взнос", case=False, na=False)
    cb["one_room"] = cb.rooms_data.map(_min_price_1room)
    resale_inst = L[(L.source == "elitka") & (L.is_active == 1)].assign(
        cls=lambda x: x.complex_source_id.map(C.set_index("source_id").class_ru))
    tab = cb.groupby("class_ru").agg(
        zhk=("id", "size"), building=("is_finished", lambda s: (s == 0).sum()),
        m2=("price_m2_usd", "median"), m2_mean=("price_m2_usd", "mean"), one=("one_room", "median"),
        inst=("inst", "mean"), resale=("private_ads_count", "sum"),
        finish=("finish_year", lambda s: s[s >= now.year].median())).reindex(CLASS_ORDER)
    tab["inst_ads"] = resale_inst.groupby("cls").installment.mean().reindex(CLASS_ORDER) * 100
    tab["inst"] *= 100
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Медиана цены застройщика за м²**")
        fig = go.Figure(go.Bar(x=tab.index, y=tab.m2, marker_color=[CLASS_COLORS[c] for c in tab.index],
                               marker_cornerradius=4, text=tab.m2.map(_usd), textposition="outside",
                               hovertemplate="<b>%{x}</b><br>$%{y:,.0f}/м²<extra></extra>"))
        st.plotly_chart(_style(fig, 300, False), width="stretch")
    with c2:
        st.markdown("**Строится и сдано**")
        sb = cb.assign(st=np.where(cb.is_finished == 1, "Сдан", "Строится")).groupby(["class_ru", "st"]).size() \
            .reset_index(name="n")
        fig = px.bar(sb, x="class_ru", y="n", color="st", category_orders={"class_ru": CLASS_ORDER},
                     color_discrete_map={"Строится": BLUE, "Сдан": "#9ec5f4"})
        fig.update_traces(hovertemplate="%{x}: %{y} ЖК<extra>%{fullData.name}</extra>")
        st.plotly_chart(_style(fig, 300), width="stretch")
    st.dataframe(tab, width="stretch", column_config={
        "zhk": "ЖК всего", "building": "Строится",
        "m2": st.column_config.NumberColumn("Медиана $/м²", format="$%.0f"),
        "m2_mean": st.column_config.NumberColumn("Средняя $/м²", format="$%.0f"),
        "one": st.column_config.NumberColumn("1-комн. от, медиана $", format="$%.0f"),
        "inst": st.column_config.NumberColumn("Рассрочка в метках ЖК", format="%.0f%%"),
        "inst_ads": st.column_config.NumberColumn("Рассрочка в объявлениях", format="%.0f%%"),
        "resale": "Перепродаж (объявл. частников)",
        "finish": st.column_config.NumberColumn("Типичный год сдачи", format="%.0f")})
    st.caption("Рассрочку указывают не все застройщики: метки на карточке ЖК заполнены редко, поэтому доля занижена.")

    # ============================================================ 4
    st.header("4. Активность застройщиков")
    dv = CB.assign(new=(CB.is_finished == 0) & ((CB.finish_year >= now.year + 2) |
                                               CB.labels.str.contains("Старт", na=False))) \
        .groupby("developer_name").agg(
            total=("id", "size"), building=("is_finished", lambda s: (s == 0).sum()), new=("new", "sum"),
            resale=("private_ads_count", "sum"), m2=("price_m2_usd", "median"),
            classes=("class_ru", lambda s: ", ".join(sorted(set(s) & set(CLASS_ORDER)))),
            plan=("developer_plan", "first"))
    dv["resale_per"] = dv.resale / dv.building.replace(0, np.nan)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Больше всего новых проектов** (в стройке, сдача через 2+ года или «старт продаж»)")
        s = dv.new.sort_values().tail(15)
        st.plotly_chart(_hbar(s[s > 0], ",.0f", 420), width="stretch")
    with c2:
        st.markdown("**Больше всего перепродаж** (объявления частников в их ЖК)")
        s = dv.resale.sort_values().tail(15)
        st.plotly_chart(_hbar(s[s > 0], ",.0f", 420, color=ORANGE), width="stretch")
    st.caption("Много перепродаж в строящихся ЖК — признак инвестиционного спроса: покупают на котловане, чтобы "
               "продать дороже ближе к сдаче.")
    dv.index.name = "Застройщик"
    st.dataframe(dv.sort_values(["building", "resale"], ascending=False), width="stretch", height=380,
                 column_config={"total": "ЖК всего", "building": "Строится", "new": "Новых проектов",
                                "resale": "Перепродаж", "resale_per": st.column_config.NumberColumn(
                                    "Перепродаж на строящийся ЖК", format="%.1f"),
                                "m2": st.column_config.NumberColumn("Медиана $/м²", format="$%.0f"),
                                "classes": "Классы", "plan": "Тариф на elitka"})

    # ============================================================ 5
    st.header("5. Этаж и этажность")
    fl = priced[priced.floor.notna() & priced.floors_total.between(3, 60) &
                (priced.floor >= 1) & (priced.floor <= priced.floors_total)].copy()
    fl["ratio"] = cell_ratio(fl)
    fl = fl[fl.ratio.notna()]
    fl["pos"] = np.select(
        [fl.floor == 1, fl.floor == fl.floors_total, fl.floor <= 3, fl.floor >= fl.floors_total - 2],
        ["1-й этаж", "Последний", "2–3 этаж", "Верхние (не последний)"], "Средние")
    order = ["1-й этаж", "2–3 этаж", "Средние", "Верхние (не последний)", "Последний"]
    g = fl.groupby(["pos", "segment"]).ratio.median().unstack("segment").reindex(order)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Цена за м² относительно похожих квартир, %**")
        fig = go.Figure()
        for sg, col in (("Новостройка", BLUE), ("Вторичка", ORANGE)):
            if sg in g:
                fig.add_bar(x=g.index, y=g[sg], name=sg, marker_color=col, marker_cornerradius=4,
                            hovertemplate="%{x}: %{y:+.1f}%<extra>" + sg + "</extra>")
        fig.add_hline(y=0, line_color="rgba(128,128,128,.6)")
        fig.update_layout(barmode="group")
        st.plotly_chart(_style(fig, 320), width="stretch")
    with c2:
        st.markdown("**Медиана $/м² по этажности дома**")
        fl["height"] = pd.cut(fl.floors_total, [0, 5, 9, 12, 16, 25, 100],
                              labels=["до 5", "6–9", "10–12", "13–16", "17–25", "26+"])
        h = fl.groupby(["height", "segment"], observed=True).price_m2_usd.median().reset_index()
        fig = px.bar(h[h.segment != "Не указано"], x="height", y="price_m2_usd", color="segment", barmode="group",
                     color_discrete_map={"Новостройка": BLUE, "Вторичка": ORANGE})
        fig.update_traces(marker_cornerradius=4, hovertemplate="%{x} эт.: $%{y:,.0f}/м²<extra>%{fullData.name}</extra>")
        st.plotly_chart(_style(fig, 320), width="stretch")
    first = fl[fl.pos == "1-й этаж"].ratio.median()
    mid = fl[fl.pos == "Средние"].ratio.median()
    last = fl[fl.pos == "Последний"].ratio.median()
    st.markdown(f"Первый этаж: **{first:+.1f}%**, средние этажи: **{mid:+.1f}%**, последний: **{last:+.1f}%** к цене "
                f"похожих квартир. Основа — {len(fl):,} объявлений с указанной этажностью (house.kg, etagi, stroka, elitka)."
                .replace(",", " "))

    # ============================================================ 6
    st.header("6. Ипотека и рассрочка")
    rep = flats[flats.source.isin(["house", "elitka"])].drop_duplicates("flat_key")
    rep = rep.assign(mortgage=rep.mortgage.fillna(0).astype(bool), installment=rep.installment.fillna(0).astype(bool))
    k = st.columns(4)
    k[0].metric("С ипотекой", f"{rep.mortgage.mean() * 100:.0f}%")
    k[1].metric("С рассрочкой", f"{rep.installment.mean() * 100:.0f}%")
    nb = rep[rep.segment == "Новостройка"]
    k[2].metric("Рассрочка в новостройках", f"{nb.installment.mean() * 100:.0f}%")
    k[3].metric("Ипотека во вторичке", f"{rep[rep.segment == 'Вторичка'].mortgage.mean() * 100:.0f}%")
    st.caption("Только house.kg и elitka — остальные сайты не указывают условия сделки в списке объявлений. "
               "Отсутствие отметки не значит, что ипотека невозможна: продавец мог её просто не указать.")
    dd = rep[rep.district.notna()].groupby("district").agg(n=("id", "size"), mort=("mortgage", "mean"),
                                                             inst=("installment", "mean")).query("n >= 40")
    if len(dd) >= 3:
        dd = dd.sort_values("mort").tail(25) * [1, 100, 100]
        fig = go.Figure()
        fig.add_bar(y=dd.index, x=dd.mort, name="Ипотека", orientation="h", marker_color=BLUE, marker_cornerradius=3,
                    hovertemplate="<b>%{y}</b><br>Ипотека: %{x:.0f}%<extra></extra>")
        fig.add_bar(y=dd.index, x=dd.inst, name="Рассрочка", orientation="h", marker_color=ORANGE,
                    marker_cornerradius=3, hovertemplate="<b>%{y}</b><br>Рассрочка: %{x:.0f}%<extra></extra>")
        fig.update_layout(barmode="group")
        st.markdown("**Доля объявлений с ипотекой и рассрочкой по районам, %**")
        st.plotly_chart(_style(fig, max(380, 28 * len(dd))), width="stretch")
    ec = L[(L.source == "elitka") & (L.is_active == 1)].assign(
        cls=lambda x: x.complex_source_id.map(C.set_index("source_id").class_ru))
    ecg = ec.groupby("cls").agg(n=("id", "size"), mort=("mortgage", "mean"), inst=("installment", "mean")) \
        .reindex(CLASS_ORDER).dropna(how="all")
    if not ecg.empty:
        ecg[["mort", "inst"]] *= 100
        st.markdown("**По классам жилья** (перепродажи на elitka)")
        st.dataframe(ecg, width="stretch", column_config={
            "n": "Объявлений", "mort": st.column_config.NumberColumn("Ипотека", format="%.0f%%"),
            "inst": st.column_config.NumberColumn("Рассрочка", format="%.0f%%")})

    # ============================================================ 7
    st.header("7. Собственники и агентства по районам")
    sl = flats[flats.source.isin(["house", "stroka", "elitka"]) & flats.seller_type.notna() &
               flats.district.notna()].drop_duplicates("flat_key")
    sh = sl.groupby("district").seller_type.value_counts(normalize=True).unstack(fill_value=0) * 100
    sh["n"] = sl.groupby("district").size()
    sh = sh[sh.n >= 30]
    for c in ("owner", "agency", "developer"):
        if c not in sh:
            sh[c] = 0.0
    if len(sh) < 3:
        st.info("Мало данных о продавцах по районам.")
    else:
        sh = sh.sort_values("owner")
        k = st.columns(3)
        k[0].metric("Собственники", f"{(sl.seller_type == 'owner').mean() * 100:.0f}%")
        k[1].metric("Агентства и риелторы", f"{(sl.seller_type == 'agency').mean() * 100:.0f}%")
        k[2].metric("Застройщики", f"{(sl.seller_type == 'developer').mean() * 100:.0f}%")
        fig = go.Figure()
        for c, name, col in (("owner", "Собственник", BLUE), ("agency", "Агентство / риелтор", ORANGE),
                             ("developer", "Застройщик", AQUA)):
            fig.add_bar(y=sh.index, x=sh[c], name=name, orientation="h", marker_color=col,
                        customdata=sh.n, hovertemplate="<b>%{y}</b><br>" + name + ": %{x:.0f}%<br>"
                                                       "Объявлений: %{customdata}<extra></extra>")
        fig.update_layout(barmode="stack", bargap=0.2)
        fig.update_traces(marker_line_width=1, marker_line_color="rgba(255,255,255,.9)")
        fig.update_xaxes(range=[0, 100], ticksuffix="%")
        st.markdown("**Кто продаёт: доля объявлений по районам** (сверху — где больше собственников)")
        st.plotly_chart(_style(fig, max(420, 22 * len(sh))), width="stretch")
        top_own, top_ag = sh.owner.idxmax(), sh.agency.idxmax()
        st.markdown(f"Самый «живой» рынок (больше всего собственников): **{top_own}** ({sh.owner.max():.0f}%). "
                    f"Больше всего посредников: **{top_ag}** ({sh.agency.max():.0f}% агентств).")
    st.caption("По house.kg, stroka и elitka — там тип продавца указан явно. etagi — всегда агентство, "
               "lalafo отмечает только собственников, поэтому они не входят в расчёт.")


def _min_price_1room(v):
    try:
        rd = json.loads(v) if isinstance(v, str) else (v or [])
        p = [r.get("price_usd") for r in rd if r.get("rooms_count") == 1 and r.get("price_usd")]
        return min(p) if p else np.nan
    except (ValueError, TypeError, AttributeError):
        return np.nan
