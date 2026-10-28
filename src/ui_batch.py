"""Trade review > Across the Top 100: the backtest run on every trader on
the board, summarised as the most common mistakes, the rules that helped
most often (simulated) and the persistence test."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .review import batch, jobs, store
from .review import costs as cst

GREEN, RED, BLUE, GREY = "#2fbf71", "#e5484d", "#2a78d6", "#8a8f98"
CHART_CFG = {"displayModeBar": False}
LABEL = "Hyperliquid"
BADGE = ('<span style="background:#3a2a5c;color:#cbb6ff;border:1px solid #6b4fb3;border-radius:4px;'
         'padding:1px 7px;font-size:12px;font-weight:600;letter-spacing:.04em">SIMULATED</span>')


def _md(t: str) -> str:
    return t.replace("$", "\\$")


def _layout(fig, h):
    from .charts import card_layout
    return card_layout(fig, h)


def _card(key, title, desc, source, paired=True, expandable=True):
    from .ui_studio import card
    return card(key, title, desc=desc, source=source, paired=paired, expandable=expandable)


def _latest(rcon, venue):
    r = rcon.execute("SELECT run_id FROM tr_backtest_runs WHERE account_id='*' AND venue=? ORDER BY started DESC LIMIT 1",
                     (venue,)).fetchone()
    return (r[0], jobs.get(rcon, r[0])) if r else (None, None)


def render(rcon, el) -> None:
    from . import venues
    global LABEL
    st.subheader("Across the Top 100")
    st.caption("The backtest from each trader's page, run on every trader on the 30-day board, then summarised: "
               "which mistakes are most common among top traders, which rules would have helped most often "
               "(simulated, out of sample), and whether last month's top traders stayed on top.")
    venue = st.segmented_control("Venue", list(venues.VENUES), default="hyperliquid", key="batch-venue",
                                 format_func=lambda v: venues.LABELS.get(v, v)) or "hyperliquid"
    LABEL = venues.LABELS.get(venue, venue)
    bid, run = _latest(rcon, venue)
    c1, c2 = st.columns([1, 3], vertical_alignment="center")
    running = bool(run and run["status"] == "running")
    if c1.button("Run on the Top 100", type="primary", disabled=running, width="stretch", key="batch-run"):
        batch.start(venue, "30d", cst.Assumptions(), el)
        st.rerun()
    if running:
        c2.caption("Running in the background; you can leave this page. A first run takes about 30-90 minutes "
                   "(prices for every trader's markets are loaded once); later runs reuse stored results.")
        _progress(bid)
    elif run and run["status"] == "done":
        c2.caption(f"Last run finished {pd.Timestamp(run['finished']):%d %b %H:%M} UTC. Click to run again (a new run "
                   "at most once a day; each trader's own result is reused while their data is unchanged).")
    elif run and run["status"] == "error":
        c2.error(f"The last run stopped: {run['message']}")
    done = run if run and run["status"] == "done" else None
    if not done:
        prev = rcon.execute("SELECT run_id FROM tr_backtest_runs WHERE account_id='*' AND status='done' AND venue=? "
                            "ORDER BY finished DESC LIMIT 1", (venue,)).fetchone()
        done = jobs.get(rcon, prev[0]) if prev else None
    if not done or not done.get("result"):
        if not running:
            st.info("Not run yet. Click **Run on the Top 100**.")
        return
    _results(done["result"])


@st.fragment(run_every=5)
def _progress(bid: str) -> None:
    con = store.connect()
    try:
        r = jobs.get(con, bid)
    finally:
        con.close()
    if not r:
        return
    if r["status"] != "running":
        st.rerun(scope="app")
    st.progress(float(r["progress"] or 0), text=r["message"])


def _results(r: dict) -> None:
    m = st.columns(5)
    m[0].metric("Traders", f"{r['traders']}")
    m[1].metric("Backtested", f"{r['backtested']}", help=f"{r['too_few_trips']} had fewer than 30 round trips "
                f"(diagnosis only); {r['errors']} failed.")
    m[2].metric("Replay check passed", f"{r['golden_pass']} of {r['backtested']}",
                help=f"No-change replay within 1% of actual P&L. Median difference {r['golden_median_error'] or 0:.3%}.")
    m[3].metric("Entries fit a simple rule", f"{r['rule_reproduces']} of {r['backtested']}",
                help="Experimental rule extraction: traders whose entries a one- or two-threshold rule matched "
                     "and whose return it reproduced on the test window.")
    p = r.get("persistence") or {}
    m[4].metric("Top 100 still profitable",
                f"{p['top_profitable_next']:.0%}" if p.get("status") == "done" else "n/a",
                help="Share of the previous 30 days' top 100 that made money in the last 30 days (persistence "
                     "test, below).")
    n = max(r["backtested"], 1)
    a, b = st.columns(2)
    with a:
        with _card("bt-batch-mistakes", "Most common mistakes among top traders",
                   "Share of backtested traders whose actual trades show each recurring mistake (one count per "
                   "trader). Hover for the combined P&L of the flagged trades.",
                   f"{LABEL} fills and funding (actual trades)") as cc:
            mk = pd.DataFrame(r["mistakes"]).head(10)
            f = go.Figure()
            if len(mk):
                f.add_trace(go.Bar(y=mk["pattern"], x=mk["share"] * 100, orientation="h", marker_color=RED,
                                   customdata=np.c_[mk["traders"], [f"${v:,.0f}" for v in mk["impact"]]],
                                   hovertemplate="%{y}: %{x:.0f}% of traders (%{customdata[0]})<br>flagged trades' "
                                                 "P&L %{customdata[1]}<extra></extra>"))
                f.update_yaxes(autorange="reversed", automargin=True)
            f.update_xaxes(ticksuffix="%", range=[0, 100])
            cc.chart(_layout(f, 340).update_layout(showlegend=False, margin=dict(l=170)), width="stretch",
                     config=CHART_CFG)
    with b:
        with _card("bt-batch-rules", "Rules that helped most often (simulated)",
                   "For each rule family: share of backtested traders for whom it was adopted on the training "
                   "window and also improved the test window (bright green = Medium/High confidence, faded = low). Hover for "
                   "the median out-of-sample change.", f"Simulated from {LABEL} fills, funding and candles") as cc:
            ru = pd.DataFrame(r["rules"]).head(12)
            f = go.Figure()
            if len(ru):
                f.add_trace(go.Bar(y=ru["family"], x=ru["confident"] / n * 100, orientation="h", marker_color=GREEN,
                                   name="Helped, Medium/High confidence", hovertemplate="%{y}: %{x:.0f}%<extra></extra>"))
                f.add_trace(go.Bar(y=ru["family"], x=(ru["helped"] - ru["confident"]) / n * 100, orientation="h",
                                   marker_color="rgba(47,191,113,0.4)", name="Helped, low confidence",
                                   customdata=[f"${v:,.0f}" if v is not None and v == v else "n/a" for v in ru["median_gain"]],
                                   hovertemplate="%{y}: +%{x:.0f}%<br>median test change when adopted %{customdata}"
                                                 "<extra></extra>"))
                f.update_yaxes(autorange="reversed", automargin=True)
            f.update_layout(barmode="stack")
            f.update_xaxes(ticksuffix="%")
            cc.chart(_layout(f, 340).update_layout(margin=dict(l=170)), width="stretch", config=CHART_CFG)
    st.markdown(BADGE + "&nbsp; The right-hand chart is simulated; the left-hand chart describes actual trades.",
                unsafe_allow_html=True)
    a, b = st.columns(2)
    with a:
        with _card("bt-batch-right", "What top traders do right",
                   "Share of backtested traders showing each strength in their actual trades.",
                   f"{LABEL} fills and funding (actual trades)") as cc:
            sk = pd.DataFrame(r["strengths"]).head(10)
            f = go.Figure()
            if len(sk):
                f.add_trace(go.Bar(y=sk["title"], x=sk["share"] * 100, orientation="h", marker_color=BLUE,
                                   hovertemplate="%{y}: %{x:.0f}% of traders<extra></extra>"))
                f.update_yaxes(autorange="reversed", automargin=True)
            f.update_xaxes(ticksuffix="%", range=[0, 100])
            cc.chart(_layout(f, 320).update_layout(showlegend=False, margin=dict(l=270)), width="stretch",
                     config=CHART_CFG)
    with b:
        with _card("bt-batch-persist", "Do top traders stay on top?",
                   "Persistence test: every analysed account ranked by its P&L over the 30 days before the last 30; "
                   "the top 100 compared with the rest over the last 30 days.",
                   f"{LABEL} fills and funding (actual trades)", expandable=False) as cc:
            if p.get("status") != "done":
                st.caption("Not enough accounts whose stored history covers both windows yet.")
            else:
                st.markdown(_md(
                    f"Of the previous window's top {p['top_n']} (out of {p['pool']} accounts with history covering "
                    f"both windows): **{p['top_profitable_next']:.0%} were profitable in the next 30 days** (rest of "
                    f"the pool: {p['rest_profitable_next']:.0%}); **{p['top_stayed_top']:.0%} stayed in the top "
                    f"{p['top_n']}**. Median next-window P&L: ${p['top_median_next']:,.0f} (rest: "
                    f"${p['rest_median_next']:,.0f}). Rank correlation between the two windows: "
                    f"{p['rank_corr']:+.2f}."))
                st.caption(p["bias"])
            cc.fig = None
    with st.expander(f"Every trader ({r['traders']})"):
        df = pd.DataFrame(r["per_trader"])
        df["Trader"] = "?trader=" + df["account_id"].astype(str) + "&venue=" + r.get("venue", "hyperliquid")
        st.dataframe(df[["Trader", "status", "trips", "actual_test", "golden_error", "best_rule", "best_gain",
                         "best_label", "mistakes"]], hide_index=True, width="stretch", column_config={
            "Trader": st.column_config.LinkColumn("Trader", display_text=r"\?trader=([0-9a-zx]+)"),
            "status": "Status", "trips": "Round trips",
            "actual_test": st.column_config.NumberColumn("Actual P&L, test window", format="$%,.0f"),
            "golden_error": st.column_config.NumberColumn("Replay error", format="percent"),
            "best_rule": "Best adopted rule (simulated)",
            "best_gain": st.column_config.NumberColumn("Its test change (simulated)", format="$%,.0f"),
            "best_label": "Confidence", "mistakes": "Top mistakes"})
