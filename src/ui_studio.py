"""Studio tab: Glassnode-style dashboards and single-chart views over the
metrics in src/catalog.py. Every chart goes through src/charts.render."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from . import catalog, charts, explanations, storage
from .metrics import Ctx

CSS = f"""
<style>
.st-key-gn-root {{ background: {charts.BG}; border-radius: 10px; padding: 14px 14px 4px 14px; }}
.st-key-gn-root p, .st-key-gn-root label, .gn-title, .gn-meta, .gn-explain {{ font-family: {charts.FONT}; }}
.st-key-gn-root [data-testid="stMarkdownContainer"] p {{ color: {charts.TEXT}; }}
.st-key-gn-root [data-testid="stCaptionContainer"] p {{ color: {charts.AXIS}; font-size: 12px; }}
.st-key-gn-root label p {{ color: {charts.AXIS} !important; font-size: 12px; }}
div[class*="st-key-gn-panel-"] {{ background: {charts.BG}; border: 1px solid {charts.BORDER}; border-radius: 8px;
    padding: 10px 12px 10px 12px; }}
.gn-title {{ color: {charts.TEXT}; font-weight: 600; font-size: 14px; margin: 0; }}
.gn-latest {{ color: {charts.TEXT}; font-size: 13px; float: right; font-variant-numeric: tabular-nums; }}
.gn-meta {{ color: {charts.AXIS}; font-size: 11.5px; line-height: 1.4; margin: 2px 0 0 0; }}
/* Streamlit gives every markdown block a -16px bottom margin; inside a panel that made the
   description spill past the panel's border. Reset it so text stays inside the pane. */
div[class*="st-key-gn-panel-"] [data-testid="stMarkdownContainer"] {{ margin-bottom: 0 !important; }}
div[class*="st-key-gn-panel-"] [data-testid="stMarkdownContainer"] p {{ margin-bottom: 0; }}
/* "About this chart" toggle: small, quiet, same panel */
div[class*="st-key-gn-panel-"] [data-testid="stExpander"] details {{ border: 1px solid {charts.BORDER}; border-radius: 6px;
    background: {charts.PANEL}; }}
div[class*="st-key-gn-panel-"] [data-testid="stExpander"] summary {{ padding: 4px 10px; min-height: 0; }}
div[class*="st-key-gn-panel-"] [data-testid="stExpander"] summary p {{ font-size: 12px; color: {charts.AXIS}; }}
div[class*="st-key-gn-panel-"] [data-testid="stExpanderDetails"] {{ padding: 2px 12px 10px 12px; }}
.gn-explain {{ color: {charts.TEXT}; font-size: 12.5px; line-height: 1.5; }}
.gn-explain h6 {{ color: {charts.AXIS}; font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
    margin: 8px 0 2px 0; padding: 0; font-weight: 600; }}
.gn-partial {{ color: #c9a227; }}
@media (max-width: 640px) {{ .gn-latest {{ float: none; display: block; }} }}
</style>
"""


@st.cache_data(ttl=600, show_spinner=False)
def _data(spec_id: str, _con, nonce: str):
    """Compute one metric (cached 10 minutes; nonce = latest fetch time, so
    new data invalidates the cache)."""
    return catalog.BY_ID[spec_id].fn(Ctx(_con))


@st.cache_data(ttl=600, show_spinner=False)
def _price(_con, nonce: str) -> pd.Series:
    return Ctx(_con).price()


def _nonce(con) -> str:
    row = con.execute("SELECT MAX(ts) FROM fetch_log WHERE ok = 1").fetchone()
    return (row[0] or "")[:15]   # changes at most every ~10 minutes


def controls(key: str, show_resolution: bool = True) -> charts.Opts:
    c1, c2, c3, c4, c5 = st.columns([2.3, 1.7, 1.8, 1.2, 0.8])
    rng = c1.segmented_control("Range", list(charts.RANGES), default="1Y", key=f"{key}-rng")
    scale = c2.segmented_control("Scale", ["Default", "Lin", "Log"], default="Default", key=f"{key}-scale")
    ma = c3.segmented_control("Moving avg", list(charts.MAS), default="None", key=f"{key}-ma")
    res = c4.segmented_control("Resolution", ["24h", "1h"], default="24h", key=f"{key}-res") if show_resolution else "24h"
    price = c5.toggle("Price", value=True, key=f"{key}-price")
    return charts.Opts(range=rng or "1Y", log={"Lin": False, "Log": True}.get(scale or "Default"), ma=ma or "None",
                       resolution=res or "24h", price=price)


def panel(con, spec: charts.Spec, opts: charts.Opts, nonce: str, key: str) -> None:
    with st.container(key=f"gn-panel-{key}"):
        try:
            data = _data(spec.id, con, nonce)
            many = isinstance(data, pd.DataFrame) and spec.kind == "multi" and data.shape[1] > 4
            latest = charts.latest_value(data, spec.unit, spec.kind) \
                if isinstance(getattr(data, "index", None), pd.DatetimeIndex) and spec.kind not in ("heatmap", "xy", "overlay") and not many else ""
            st.markdown(f'<p class="gn-title">{spec.title}<span class="gn-latest">{latest}</span></p>', unsafe_allow_html=True)
            fig = charts.render(spec, data, _price(con, nonce), opts)
        except Exception as e:  # noqa: BLE001 - one broken chart must not blank the dashboard
            st.markdown(f'<p class="gn-title">{spec.title}</p>', unsafe_allow_html=True)
            fig = charts.empty_figure(opts.height, "Not available yet")
            spec_err = f"{type(e).__name__}: {e}"
        else:
            spec_err = ""
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key=f"chart-{key}")
        meta = " · ".join(x for x in (spec.desc, f"Source: {spec.source}") if x)
        if spec.partial:
            meta += f' · <span class="gn-partial">Partial: {spec.partial}</span>'
        if spec_err:
            meta += f' · <span class="gn-partial">{spec_err[:160]}</span>'
        st.markdown(f'<div class="gn-meta">{meta}</div>', unsafe_allow_html=True)
        explain(spec)


def explain(spec: charts.Spec) -> None:
    """Collapsed 'About this chart' section with the in-depth explanation."""
    calc, read = explanations.E.get(spec.id, ("", ""))
    if not (calc or read):
        return
    data = f"Source: {spec.source}."
    if spec.partial:
        data += f" Limitation: {spec.partial}"
    body = "".join(f"<h6>{h}</h6><div>{t}</div>" for h, t in
                   (("How it's calculated", calc), ("How to read it", read), ("Data", data)) if t)
    with st.expander("About this chart", expanded=False):
        st.markdown(f'<div class="gn-explain">{body}</div>', unsafe_allow_html=True)


def social_extras(con, cfg: dict) -> None:
    """Under the Social Risk dashboard: today's reading, every input's status,
    the evidence tables, and the X impressions log."""
    from datetime import date

    from . import views
    from .indicators import social_risk as sr

    df, raw = views.social_risk_frame(con, cfg)
    bands = (cfg.get("social_risk") or {}).get("bands") or {"low": 0.30, "elevated": 0.70, "extreme": 0.85}
    price = raw["price"]
    with st.container(key="gn-panel-social-extras"):
        st.markdown('<p class="gn-title">Social Risk: reading and evidence</p>', unsafe_allow_html=True)
        score = df["score"].dropna() if "score" in df else pd.Series(dtype=float)
        if score.empty:
            st.caption("No score yet: it needs at least three inputs with history.")
        else:
            latest, as_of = score.iloc[-1], score.index[-1]
            prior = score[: as_of - pd.Timedelta(days=30)]
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Social Risk", f"{latest:.2f}",
                      delta=f"{latest - prior.iloc[-1]:+.2f} vs 30d ago" if not prior.empty else None, delta_color="inverse")
            c2.metric("Band", sr.band(latest, bands))
            c3.metric("Attention", f"{df['attention'].dropna().iloc[-1]:.2f}" if df["attention"].notna().any() else "-")
            c4.metric("Speculation", f"{df['speculation'].dropna().iloc[-1]:.2f}" if df["speculation"].notna().any() else "-")
            c5.metric("Inputs live", f"{int(df['n_components'].iloc[-1])} of {len(sr.COMPONENTS)}")
        st.markdown('<p class="gn-meta">Inputs</p>', unsafe_allow_html=True)
        status = views.component_status(df, raw, cfg)
        status["Score"] = status["Score"].map(lambda v: "-" if v is None or pd.isna(v) else f"{v:.2f}")
        status["As of"] = status["As of"].map(lambda v: "-" if v is None or pd.isna(v) else str(v))
        st.dataframe(status, hide_index=True, width="stretch", height=36 * (len(status) + 1) + 2,
                     column_config={"Weight": st.column_config.NumberColumn(format="%.2f")})
        with st.expander("Does it work? Score versus what BTC did next", expanded=False):
            if score.empty or price.empty:
                st.caption("Needs both the score and BTC price history.")
            else:
                st.markdown('<div class="gn-explain">Average BTC return over the following days, grouped by the score\'s '
                            'band on the day. If the score flags froth, returns after <b>Extreme</b> readings should be '
                            'worse than <b>All days</b>.</div>', unsafe_allow_html=True)
                fwd = sr.forward_returns_by_band(df["score"], price, bands)
                fwd.columns = [c.replace("avg_next_", "BTC next ").replace("days", "Days") for c in fwd.columns]
                st.dataframe(fwd.style.format({c: "{:.1%}" for c in fwd.columns if c.startswith("BTC")}), width="stretch")
                st.markdown('<div class="gn-explain">Each input on its own. <b>Rank corr.</b> below 0 means high readings '
                            'were followed by weaker 90-day returns, which is what a risk gauge should show. Inputs with '
                            'short histories are judged on few cycles, so treat their numbers as anecdotes.</div>',
                            unsafe_allow_html=True)
                sig = sr.component_signal(df, price)
                fmt = {c: "{:.1%}" for c in sig.columns if c.startswith("Next")}
                fmt["Rank corr. with next 90d"] = "{:+.2f}"
                st.dataframe(sig.style.format(fmt, na_rep="-"), hide_index=True, width="stretch")
                st.markdown('<div class="gn-explain">Major BTC tops (highest close within six months either side) and when '
                            'the score peaked around them. <b>Lead days</b> above 0 mean the score peaked first.</div>',
                            unsafe_allow_html=True)
                leads = sr.peak_lead_times(df["score"], price).rename(columns={
                    "price_top": "BTC top", "top_price": "Top price", "score_peak": "Score peaked",
                    "score_at_peak": "Score at peak", "score_on_top_day": "Score on top day", "lead_days": "Lead days",
                    "confirmed": "Confirmed"})
                st.dataframe(leads, hide_index=True, width="stretch",
                             column_config={"Top price": st.column_config.NumberColumn(format="$%.0f"),
                                            "Score at peak": st.column_config.NumberColumn(format="%.2f"),
                                            "Score on top day": st.column_config.NumberColumn(format="%.2f")})
        with st.expander("Log X impressions", expanded=False):
            st.caption("X's API is paid, so impressions are entered by hand, e.g. weekly from the analytics page of the "
                       "accounts or searches you follow. Eight entries per label start that input.")
            with st.form("x_form", clear_on_submit=True):
                a, b, c = st.columns(3)
                label = a.text_input("Label", placeholder="e.g. crypto-timeline")
                when = b.date_input("Date", value=date.today())
                value = c.number_input("Impressions", min_value=0, step=1000)
                if st.form_submit_button("Save") and label.strip():
                    storage.save_point(con, f"x_impr:{label.strip()}", float(value),
                                       ts=pd.Timestamp(when, tz="UTC").to_pydatetime())
                    storage.log_fetch(con, "x_impressions", True, 1)   # new entry -> charts recompute
                    st.success(f"Saved {value:,.0f} for {label.strip()} on {when}")


def render(con, cfg: dict | None = None) -> None:
    if cfg is None:
        from .config import load_config
        cfg = load_config()
    st.markdown(CSS, unsafe_allow_html=True)
    nonce = _nonce(con)
    with st.container(key="gn-root"):
        mode = st.segmented_control("View", ["Dashboards", "Charts"], default="Dashboards", key="gn-mode",
                                    label_visibility="collapsed")
        groups = list(dict.fromkeys(g for g, _, _ in catalog.DASHBOARDS))
        if (mode or "Dashboards") == "Dashboards":
            a, b = st.columns([1, 2])
            group = a.selectbox("Section", groups, key="gn-group")
            names = [n for g, n, _ in catalog.DASHBOARDS if g == group]
            name = b.selectbox("Dashboard", names, key="gn-dash")
            ids = next(lst for g, n, lst in catalog.DASHBOARDS if g == group and n == name)
            opts = controls("gn-d")
            opts.height = 300
            cols = st.columns(2)
            for i, sid in enumerate(ids):
                with cols[i % 2]:
                    panel(con, catalog.BY_ID[sid], opts, nonce, f"{group}-{name}-{sid}")
            if name == "Social Risk":
                try:
                    social_extras(con, cfg)
                except Exception as e:  # noqa: BLE001
                    st.caption(f"Social Risk tables unavailable: {type(e).__name__}: {e}")
        else:
            by_group: dict[str, list[str]] = {}
            for g, _, lst in catalog.DASHBOARDS:
                for sid in lst:
                    by_group.setdefault(g, [])
                    if sid not in by_group[g]:
                        by_group[g].append(sid)
            a, b = st.columns([1, 2])
            group = a.selectbox("Category", list(by_group), key="gn-cgroup")
            sid = b.selectbox("Metric", by_group[group], format_func=lambda x: catalog.BY_ID[x].title, key="gn-metric")
            opts = controls("gn-c")
            opts.height = 520
            panel(con, catalog.BY_ID[sid], opts, nonce, f"chart-{sid}")
