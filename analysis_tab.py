"""Вкладка «Аналитика»: ответы на ключевые вопросы о рынке.

1. Средняя цена по районам
2. Какая площадь квартир и офисов продаётся лучше всего
3. Вторичка и новостройки
4. В каком году сдаёт объекты большинство застройщиков
5. Что ещё можно извлечь из данных
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

BLUE, ORANGE = "#2a78d6", "#eb6834"
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SEG_COLORS = {"Новостройка": BLUE, "Вторичка": ORANGE}

FLAT_BINS = [0, 30, 40, 50, 60, 70, 80, 100, 120, 150, np.inf]
FLAT_LABELS = ["до 30", "30–40", "40–50", "50–60", "60–70", "70–80", "80–100", "100–120", "120–150", "150+"]
COM_BINS = [0, 20, 40, 60, 100, 200, 500, np.inf]
COM_LABELS = ["до 20", "20–40", "40–60", "60–100", "100–200", "200–500", "500+"]
COM_RU = {"office": "Офисы", "retail": "Торговые помещения", "free_purpose": "Свободного назначения",
          "warehouse": "Склады", "catering": "Общепит", "hotel": "Гостиницы, гостевые дома",
          "production": "Производство", "business": "Готовый бизнес", "commercial_other": "Прочее"}


def _usd(v):
    return "—" if pd.isna(v) else f"${v:,.0f}".replace(",", " ")


def _style(fig, height=340, legend=True):
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=8, b=8), showlegend=legend, font=dict(size=13),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None), bargap=0.25)
    fig.update_xaxes(showgrid=False, title=None)
    fig.update_yaxes(gridcolor="rgba(128,128,128,.18)", zeroline=False, title=None)
    fig.update_traces(marker_line_width=0, selector=dict(type="bar"))
    return fig


def demand_table(df: pd.DataFrame, by: str, order: list[str]) -> pd.DataFrame:
    """Сводка спроса по группе: предложение, индекс спроса, срок экспозиции, доля снятых."""
    g = df.groupby(by, observed=True)
    out = pd.DataFrame({
        "n": g.size(),
        "demand": g["demand_pct"].mean() * 100,
        "demand_n": g["demand_pct"].count(),
        "days": g["days_on"].median(),
        "price_m2": g["price_m2_usd"].median(),
        "price": g["price_usd"].median(),
    })
    return out.reindex(order).dropna(how="all")


def render(L: pd.DataFrame, C: pd.DataFrame, city: str, sites: list[str], now) -> None:
    flats = L[L.is_flat & L.site.isin(sites) & L.price_ok]
    com = L[~L.is_flat & L.site.isin(sites)]
    if city != "Все":
        flats, com = flats[flats.city == city], com[com.city == city]
    fa = flats[flats.is_active == 1].sort_values("last_seen").drop_duplicates("flat_key", keep="last")
    ca = com[(com.is_active == 1)].sort_values("last_seen").drop_duplicates("flat_key", keep="last")

    st.caption("Все цены — по объявлениям о продаже (цены предложения, не сделок). Квартира, выставленная на "
               "нескольких сайтах, считается один раз; одинаковые лоты застройщика (тот же адрес и цена за м²) — тоже "
               "один раз. Цены за м² вне диапазона $300–10 000 отброшены как ошибки.")

    # ============================================================ 1. Районы
    st.header("1. Средняя цена по районам")
    c0, c1, _ = st.columns([1, 1, 2])
    stat = c0.radio("Показатель", ["Средняя", "Медиана"], horizontal=True,
                    help="Средняя чувствительна к редким дорогим объектам, медиана показывает «типичную» квартиру")
    minn = c1.number_input("Минимум объявлений в районе", 5, 200, 20, 5)
    agg = "mean" if stat == "Средняя" else "median"

    d = fa[fa.district.notna()]
    seg = d[d.segment != "Не указано"].pivot_table(index="district", columns="segment", values="price_m2_usd",
                                                    aggfunc=agg)
    tab = d.groupby("district").agg(n=("id", "size"), m2=("price_m2_usd", agg), price=("price_usd", agg),
                                    area=("area_total", agg)).join(seg).query("n >= @minn") \
        .sort_values("m2", ascending=False)
    if tab.empty:
        st.info("Мало данных по районам — уменьшите порог.")
    else:
        top = tab.head(25).iloc[::-1]
        fig = go.Figure()
        for sg, col in SEG_COLORS.items():
            if sg in top:
                fig.add_bar(y=top.index, x=top[sg], name=sg, orientation="h", marker_color=col,
                            marker_cornerradius=3,
                            hovertemplate="<b>%{y}</b><br>" + sg + ": $%{x:,.0f}/м²<extra></extra>")
        fig.update_layout(barmode="group", bargroupgap=0.08)
        st.markdown(f"**{stat} цена за м² — 25 самых дорогих районов** (новостройка и вторичка)")
        st.plotly_chart(_style(fig, max(420, 26 * len(top))), width="stretch")

        show = tab.rename(columns={"n": "Объявлений", "m2": f"{stat} $/м²", "price": f"{stat} цена кв., $",
                                   "area": f"{stat} площадь, м²", "Новостройка": "Новостройка, $/м²",
                                   "Вторичка": "Вторичка, $/м²"})
        show.index.name = "Район"
        st.dataframe(show, width="stretch", height=360, column_config={
            c: st.column_config.NumberColumn(c, format="%.0f") for c in show.columns})

        # карта
        geo = fa[fa.lat.between(42.6, 43.0) & fa.lng.between(74.3, 74.9) &
                 ~((fa.lat.sub(42.8746).abs() < 0.0005) & (fa.lng.sub(74.5698).abs() < 0.0005))]
        if len(geo) > 50:
            st.markdown("**Карта предложений: цвет — цена за м²**")
            geo = geo.sample(min(len(geo), 6000), random_state=1)
            fig = px.scatter_map(geo, lat="lat", lon="lng", color="price_m2_usd",
                                 color_continuous_scale=SEQ, range_color=(900, 2800), zoom=11.3, opacity=.7,
                                 center=dict(lat=42.86, lon=74.6), map_style="carto-positron",
                                 custom_data=["district", "rooms_g", "area_total", "price_usd"],
                                 labels={"price_m2_usd": "$/м²"})
            fig.update_traces(marker_size=7, hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}, "
                                                           "%{customdata[2]} м²<br>$%{customdata[3]:,.0f} · "
                                                           "$%{marker.color:,.0f}/м²<extra></extra>")
            fig.update_layout(height=520, margin=dict(l=0, r=0, t=0, b=0))
            st.plotly_chart(fig, width="stretch")

    # ============================================================ 2. Спрос по площади
    st.header("2. Какая площадь продаётся лучше всего")
    with st.expander("Как считается спрос", expanded=False):
        st.markdown("""
- **Индекс спроса** — сколько внимания объявление получает в день: просмотры (house.kg, stroka, elitka) или
  звонки + добавления в избранное (lalafo), делённые на число дней на сайте. Каждое объявление ранжируется
  среди объявлений того же сайта и того же возраста (свежие всегда набирают больше просмотров в день),
  затем ранги усредняются по группе. **50 — средний уровень**, выше — спрос выше среднего.
  Платно продвигаемые объявления (VIP, премиум) исключены: им показы покупаются.
- **Срок экспозиции** — медиана дней с публикации. Долго висящие объявления — признак слабого спроса или завышенной цены.
- **Доля снятых** — какая часть объявлений исчезла с сайтов (косвенный признак продажи). Появится после нескольких
  полных обходов: сейчас база собрана впервые.
""")
    removed_any = L.removed_at.notna().sum() > 50

    fa["area_g"] = pd.cut(fa.area_total, FLAT_BINS, labels=FLAT_LABELS, right=False)
    dt = demand_table(fa, "area_g", FLAT_LABELS)
    best = dt[dt.demand_n >= 30].demand.idxmax() if (dt.demand_n >= 30).any() else None

    st.subheader("Квартиры")
    if best is not None:
        b = dt.loc[best]
        st.success(f"Больше всего спроса на квартиры **{best} м²**: индекс {b.demand:.0f} при среднем 50, "
                   f"медиана {_usd(b.price)} ({_usd(b.price_m2)}/м²), в среднем {b.days:.0f} дн. на сайте.")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Индекс спроса по площади**")
        fig = go.Figure(go.Bar(x=dt.index.astype(str), y=dt.demand, marker_color=BLUE, marker_cornerradius=4,
                               customdata=dt[["n", "price", "price_m2"]],
                               hovertemplate="<b>%{x} м²</b><br>Индекс: %{y:.0f}<br>Объявлений: %{customdata[0]}"
                                             "<br>Медиана цены: $%{customdata[1]:,.0f}<br>$%{customdata[2]:,.0f}/м²<extra></extra>"))
        fig.add_hline(y=50, line_dash="dot", line_color="rgba(128,128,128,.7)",
                      annotation_text="средний уровень", annotation_position="top left")
        st.plotly_chart(_style(fig, 320, False), width="stretch")
    with c2:
        st.markdown("**Предложение: сколько квартир каждой площади**")
        allf = L[L.is_flat & L.site.isin(sites) & (L.is_active == 1)]
        if city != "Все":
            allf = allf[allf.city == city]
        supply = pd.cut(allf.drop_duplicates("flat_key").area_total, FLAT_BINS, labels=FLAT_LABELS, right=False) \
            .value_counts().reindex(FLAT_LABELS)
        fig = go.Figure(go.Bar(x=supply.index.astype(str), y=supply.values, marker_color=BLUE, marker_cornerradius=4,
                               hovertemplate="<b>%{x} м²</b><br>%{y} квартир<extra></extra>"))
        st.plotly_chart(_style(fig, 320, False), width="stretch")
    c3, c4 = st.columns(2)
    with c3:
        st.markdown("**Срок экспозиции, медиана дней**")
        fig = go.Figure(go.Bar(x=dt.index.astype(str), y=dt.days, marker_color=BLUE, marker_cornerradius=4,
                               hovertemplate="<b>%{x} м²</b><br>%{y:.0f} дн.<extra></extra>"))
        st.plotly_chart(_style(fig, 300, False), width="stretch")
    with c4:
        st.markdown("**Индекс спроса: площадь × комнатность**")
        hm = fa[fa.rooms_g != "Не указано"].pivot_table(index="rooms_g", columns="area_g", values="demand_pct",
                                                         aggfunc=lambda s: s.mean() * 100 if s.count() >= 15 else np.nan,
                                                         observed=True)
        hm = hm.reindex(["Студия", "1-комн.", "2-комн.", "3-комн.", "4+ комн."]).dropna(how="all")
        fig = go.Figure(go.Heatmap(z=hm.values, x=[str(c) for c in hm.columns], y=hm.index, colorscale=SEQ,
                                   zmin=30, zmax=70, xgap=2, ygap=2, colorbar=dict(title="индекс"),
                                   hovertemplate="%{y}, %{x} м²: индекс %{z:.0f}<extra></extra>"))
        st.plotly_chart(_style(fig, 300, False), width="stretch")
    if removed_any:
        rm = flats.sort_values("last_seen").drop_duplicates("flat_key", keep="last").copy()
        rm["area_g"] = pd.cut(rm.area_total, FLAT_BINS, labels=FLAT_LABELS, right=False)
        rm["gone"] = rm.removed_at.notna()
        st.markdown("**Доля снятых с продажи по площади** (из всех объявлений, замеченных парсером)")
        r = rm.groupby("area_g", observed=True).gone.mean().mul(100).reindex(FLAT_LABELS)
        fig = go.Figure(go.Bar(x=r.index, y=r.values, marker_color=BLUE, marker_cornerradius=4,
                               hovertemplate="%{x} м²: %{y:.1f}% снято<extra></extra>"))
        st.plotly_chart(_style(fig, 280, False), width="stretch")

    st.subheader("Офисы и коммерческая недвижимость")
    if ca.empty:
        st.info("Коммерческая недвижимость ещё не собрана. Запустите: "
                "`uv run python -m kg_realty collect -s lalafo_com,house_com,stroka_com,etagi_com --mode full`")
    else:
        ca = ca[ca.price_m2_usd.between(100, 20000) | ca.price_m2_usd.isna()].copy()
        ca["area_g"] = pd.cut(ca.area_total, COM_BINS, labels=COM_LABELS, right=False)
        ca["type_ru"] = ca.property_type.map(COM_RU).fillna("Прочее")
        offices = ca[ca.property_type == "office"]
        ot = demand_table(offices, "area_g", COM_LABELS)
        obest = ot[ot.demand_n >= 8].demand.idxmax() if (ot.demand_n >= 8).any() else None
        if obest is not None:
            b = ot.loc[obest]
            st.success(f"Офисы: больше всего спроса на площади **{obest} м²** (индекс {b.demand:.0f}), "
                       f"медиана {_usd(b.price)}, {_usd(b.price_m2)}/м².")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Офисы: индекс спроса по площади**")
            fig = go.Figure(go.Bar(x=ot.index.astype(str), y=ot.demand, marker_color=BLUE, marker_cornerradius=4,
                                   customdata=ot[["n", "price_m2"]],
                                   hovertemplate="<b>%{x} м²</b><br>Индекс: %{y:.0f}<br>Объявлений: %{customdata[0]}"
                                                 "<br>$%{customdata[1]:,.0f}/м²<extra></extra>"))
            fig.add_hline(y=50, line_dash="dot", line_color="rgba(128,128,128,.7)")
            st.plotly_chart(_style(fig, 300, False), width="stretch")
        with c2:
            st.markdown("**Офисы: медиана цены за м² по площади**")
            fig = go.Figure(go.Bar(x=ot.index.astype(str), y=ot.price_m2, marker_color=BLUE, marker_cornerradius=4,
                                   customdata=ot.n, hovertemplate="<b>%{x} м²</b><br>$%{y:,.0f}/м²<br>"
                                                                  "Объявлений: %{customdata}<extra></extra>"))
            st.plotly_chart(_style(fig, 300, False), width="stretch")

        st.markdown("**Коммерция по типам**")
        tt = ca.groupby("type_ru").agg(n=("id", "size"), demand=("demand_pct", lambda s: s.mean() * 100),
                                       area=("area_total", "median"), price_m2=("price_m2_usd", "median"),
                                       price=("price_usd", "median"), days=("days_on", "median")) \
            .sort_values("n", ascending=False)
        tt.index.name = "Тип"
        st.dataframe(tt, width="stretch", column_config={
            "n": "Объявлений", "demand": st.column_config.NumberColumn("Индекс спроса", format="%.0f"),
            "area": st.column_config.NumberColumn("Медиана площади, м²", format="%.0f"),
            "price_m2": st.column_config.NumberColumn("Медиана $/м²", format="$%.0f"),
            "price": st.column_config.NumberColumn("Медиана цены", format="$%.0f"),
            "days": st.column_config.NumberColumn("Дней на сайте", format="%.0f")})

    # ============================================================ 3. Вторичка vs новостройки
    st.header("3. Вторичное жильё и новостройки")
    nb, sb = fa[fa.segment == "Новостройка"], fa[fa.segment == "Вторичка"]
    k = st.columns(4)
    k[0].metric("Новостройки: средняя $/м²", _usd(nb.price_m2_usd.mean()), help=f"медиана {_usd(nb.price_m2_usd.median())}, {len(nb)} объявл.")
    k[1].metric("Вторичка: средняя $/м²", _usd(sb.price_m2_usd.mean()), help=f"медиана {_usd(sb.price_m2_usd.median())}, {len(sb)} объявл.")
    k[2].metric("Новостройки: средняя цена квартиры", _usd(nb.price_usd.mean()), help=f"средняя площадь {nb.area_total.mean():.0f} м²")
    k[3].metric("Вторичка: средняя цена квартиры", _usd(sb.price_usd.mean()), help=f"средняя площадь {sb.area_total.mean():.0f} м²")
    st.caption("Новостройка — элитка/ЖК или пометка сайта; вторичка — советские серии (104–108, хрущёвка, сталинка, "
               "малосемейка) или пометка сайта. Объявления без серии в сравнение не попадают.")
    c1, c2 = st.columns(2)
    order = ["Студия", "1-комн.", "2-комн.", "3-комн.", "4+ комн."]
    with c1:
        st.markdown("**Средняя цена за м² по комнатности**")
        g = fa[fa.segment != "Не указано"].groupby(["rooms_g", "segment"]).price_m2_usd.mean().reset_index()
        fig = px.bar(g, x="rooms_g", y="price_m2_usd", color="segment", barmode="group",
                     category_orders={"rooms_g": order, "segment": list(SEG_COLORS)}, color_discrete_map=SEG_COLORS)
        fig.update_traces(marker_cornerradius=4, hovertemplate="%{x}: $%{y:,.0f}/м²<extra>%{fullData.name}</extra>")
        st.plotly_chart(_style(fig, 320), width="stretch")
    with c2:
        st.markdown("**Средняя цена квартиры по комнатности**")
        g = fa[fa.segment != "Не указано"].groupby(["rooms_g", "segment"]).price_usd.mean().reset_index()
        fig = px.bar(g, x="rooms_g", y="price_usd", color="segment", barmode="group",
                     category_orders={"rooms_g": order, "segment": list(SEG_COLORS)}, color_discrete_map=SEG_COLORS)
        fig.update_traces(marker_cornerradius=4, hovertemplate="%{x}: $%{y:,.0f}<extra>%{fullData.name}</extra>")
        st.plotly_chart(_style(fig, 320), width="stretch")
    st.markdown("**Новостройки по состоянию: ПСО и с ремонтом**")
    cond = nb[nb.condition.isin(["pso", "euro", "designer", "good"])].assign(
        c=lambda x: np.where(x.condition == "pso", "ПСО (под самоотделку)", "С ремонтом"))
    if len(cond) > 30:
        g = cond.groupby("c").agg(n=("id", "size"), m2=("price_m2_usd", "mean"), price=("price_usd", "mean"))
        st.dataframe(g.rename(columns={"n": "Объявлений", "m2": "Средняя $/м²", "price": "Средняя цена, $"}),
                     width="stretch", column_config={c: st.column_config.NumberColumn(c, format="%.0f")
                                                     for c in ["Средняя $/м²", "Средняя цена, $"]})

    # ============================================================ 4. Год сдачи
    st.header("4. В каком году сдаёт объекты большинство застройщиков")
    CB = C if city == "Все" else C[C.city == city]
    fut = CB[(CB.finish_date >= now) & (CB.finish_year <= now.year + 6)]   # только предстоящая сдача
    by_year = fut.groupby("finish_year").agg(ЖК=("id", "size"), Застройщиков=("developer_name", "nunique"))
    last_year = CB[CB.is_finished == 0].groupby("developer_name").finish_year.max()
    ly = last_year[last_year.between(now.year, now.year + 6)].value_counts().sort_index()
    if not by_year.empty:
        peak = by_year["Застройщиков"].idxmax()
        k = st.columns(3)
        k[0].metric("Пик сдачи", f"{int(peak)} год",
                    help="Год, в котором больше всего застройщиков сдают хотя бы один ЖК (считаются только сроки, которые ещё не наступили)")
        k[1].metric(f"Застройщиков сдают в {int(peak)}", int(by_year.loc[peak, "Застройщиков"]))
        k[2].metric(f"ЖК сдаётся в {int(peak)}", int(by_year.loc[peak, "ЖК"]))
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Сколько застройщиков и ЖК сдаётся по годам**")
            bb = by_year.reset_index().melt("finish_year", var_name="что", value_name="n")
            fig = px.bar(bb, x="finish_year", y="n", color="что", barmode="group",
                         color_discrete_map={"Застройщиков": BLUE, "ЖК": ORANGE})
            fig.update_traces(marker_cornerradius=4, hovertemplate="%{x}: %{y}<extra>%{fullData.name}</extra>")
            fig.update_xaxes(dtick=1)
            st.plotly_chart(_style(fig, 320), width="stretch")
        with c2:
            st.markdown("**В каком году застройщики завершат все текущие стройки**")
            fig = go.Figure(go.Bar(x=ly.index, y=ly.values, marker_color=BLUE, marker_cornerradius=4,
                                   hovertemplate="%{x}: %{y} застройщиков<extra></extra>"))
            fig.update_xaxes(dtick=1)
            st.plotly_chart(_style(fig, 320, False), width="stretch")
        st.caption("По плановым срокам сдачи из каталога elitka.kg. Сроки в новостройках часто переносятся — "
                   "переносы будут видны в истории, когда парсер поработает несколько месяцев.")
        yr = st.selectbox("Кто сдаёт в году", sorted(fut.finish_year.dropna().astype(int).unique()),
                          index=list(sorted(fut.finish_year.dropna().astype(int).unique())).index(int(peak)))
        lst = fut[fut.finish_year == yr].groupby("developer_name").agg(
            ЖК=("name", lambda s: ", ".join(s)), классы=("class_ru", lambda s: ", ".join(sorted(set(s)))),
            цена=("price_m2_usd", "median")).sort_values("ЖК")
        lst.index.name = "Застройщик"
        st.dataframe(lst, width="stretch", height=300,
                     column_config={"цена": st.column_config.NumberColumn("медиана $/м²", format="$%.0f")})

    # ============================================================ 5. Что ещё
    st.header("5. Что ещё можно извлечь из этих данных")
    st.markdown("""
**Уже можно (данные собираются):**
- **Разрыв цены застройщика и перепродажи** — цена за м² в ЖК на elitka против объявлений частников в том же ЖК (переуступки). Показывает, растёт ли объект в цене во время стройки.
- **Наценка за ремонт** — ПСО против евроремонта в одном районе и классе: сколько рынок реально платит за отделку.
- **Классы жилья** — средняя цена, доля рассрочки и сроки сдачи по классам эконом → премиум (каталог 842 ЖК).
- **Активность застройщиков** — кто запускает больше всего новых ЖК, у кого больше всего перепродаж (признак инвестиционного спроса).
- **Этаж и этажность** — премия за средние этажи, скидка за первый и последний.
- **Ипотека и рассрочка** — доля объявлений с этими условиями по районам и классам.
- **Доля собственников и агентств** по районам — где рынок «живой», а где его держат перекупщики.

**Через 2–4 недели работы по расписанию:**
- **Скорость продаж** — сколько дней объявление висит до снятия, по районам, площади и цене (самый точный показатель спроса).
- **Динамика цен** — недельный индекс цены за м² по районам и сегментам, снижения цен и торг (насколько снижают до продажи).
- **Ликвидность** — доля проданных за 30 дней от выставленных, по сегментам.
- **Переносы сроков сдачи** ЖК и изменения цен застройщиков.

**Если добавить источники:**
- **Аренда** (lalafo, house.kg, stroka уже имеют разделы) → доходность сдачи: годовая аренда / цена покупки по районам. Ключевой показатель для инвесторов.
- **Паспорта объектов Минстроя** (ссылки уже собраны из elitka) → разрешения, площадь застройки, этажность, реальный статус стройки.
- **Цены стройматериалов** (сайты поставщиков и строительных рынков) → себестоимость стройки против цены продажи.
- **Курс сома, ставки ипотеки банков, НСК (статистика ввода жилья)** → связь макро-показателей и цен.
- **Тендеры и госзакупки** (zakupki.gov.kg) → государственные стройки и подрядчики.
""")
