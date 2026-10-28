"""Trade review > trader page > Backtest & Improve.

Every number on this view is simulated (the trader's real entries replayed
with one thing changed) and is labelled "Simulated". Results on the test
window (the last 30% of trips) come first. Actual results are never
overwritten: each run is stored on its own (tr_backtest_runs).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .review import backtest as bt
from .review import costs as cst
from .review import jobs
from .review import store

BLUE, ORANGE, GREEN, RED, GREY, PURPLE = "#2a78d6", "#eb6834", "#2fbf71", "#e5484d", "#8a8f98", "#9b6dff"
LINE_COLORS = [GREEN, PURPLE, "#f2c94c", "#56ccf2", ORANGE]
CHART_CFG = {"displayModeBar": False}
LABEL_COLORS = {"High": GREEN, "Medium": "#f2c94c", "Low": GREY}
BADGE = ('<span style="background:#3a2a5c;color:#cbb6ff;border:1px solid #6b4fb3;border-radius:4px;'
         'padding:1px 7px;font-size:12px;font-weight:600;letter-spacing:.04em">SIMULATED</span>')
FAMILIES = {"exits": "Exits (stops, take profit, time stop)", "sizing": "Sizing",
            "execution": "Execution (limit entries, funding)", "selection": "Selection (markets, hours, regimes)"}


def _md(text: str) -> str:
    return text.replace("$", "\\$")


def money(x, dp=0, signed=False):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    if round(abs(x), dp) == 0:
        x = 0.0
    sign = "+" if signed and x > 0 else "-" if x < 0 else ""
    return f"{sign}${abs(x):,.{dp}f}"


def _layout(fig, height=360):
    from .charts import card_layout
    return card_layout(fig, height)


def _card(key, title, desc, paired=False, expandable=True, source="Simulated from Hyperliquid fills, funding and candles"):
    from .ui_studio import card
    return card(key, title, desc=desc, source=source, paired=paired, expandable=expandable)


def render(rcon, addr: str, n_closed: int, own_fees: dict, diag: dict | None) -> None:
    st.markdown(f"{BADGE}&nbsp; Every number on this view is a simulation: the trader's real entries replayed on "
                "historical prices with one thing changed. Parameters are chosen on the first 70% of trips and "
                "results are shown on the last 30% (out of sample) first.", unsafe_allow_html=True)
    if n_closed < bt.MIN_TRIPS:
        st.info(f"This trader has {n_closed} closed round trips; at least {bt.MIN_TRIPS} are needed before any "
                "improvement is claimed. The Diagnosis view still applies.")
        return
    a, window = _assumptions(own_fees)
    rid = jobs.run_id("hyperliquid", addr, window, a, store.data_version(rcon, "hyperliquid", addr))
    run = jobs.get(rcon, rid)
    c1, c2 = st.columns([1, 3], vertical_alignment="center")
    go_ = c1.button("Run backtest", type="primary", width="stretch", key="bt-run",
                    disabled=bool(run and run["status"] == "running"))
    if run is None:
        prev = jobs.latest(rcon, "hyperliquid", addr)
        c2.caption("Not run yet with these assumptions." + (
            " Showing the last finished run (different assumptions or older data) below." if prev else ""))
    elif run["status"] == "done":
        c2.caption(f"Cached result from {pd.Timestamp(run['finished']):%d %b %H:%M} UTC for these assumptions and "
                   "this data. It reruns automatically when new fills are synced or an assumption changes.")
    if go_:
        jobs.start("hyperliquid", addr, window, a)
        st.session_state["_bt_poll"] = rid
        st.rerun()
    if run and run["status"] == "running":
        _progress(rid)
        return
    if run and run["status"] == "error":
        st.error(f"The backtest stopped: {run['message']}")
        return
    shown = run if (run and run["status"] == "done") else jobs.latest(rcon, "hyperliquid", addr)
    if not shown or not shown.get("result"):
        st.caption("Click **Run backtest**. A full run takes from a few seconds to a few minutes (prices are "
                   "loaded the first time).")
        return
    _results(shown["result"], diag)


def _assumptions(own: dict) -> tuple[cst.Assumptions, str]:
    with st.expander("Assumptions", expanded=False):
        c = st.columns(4)
        window = c[0].selectbox("Trips from", list(jobs.WINDOWS), format_func=lambda w: {
            "all": "All stored history", "90d": "Last 90 days", "30d": "Last 30 days"}[w], key="bt-window")
        own_t, own_m = own.get("taker_bps", cst.BASE_TAKER_BPS), own.get("maker_bps", cst.BASE_MAKER_BPS)
        taker = c[1].number_input("Taker fee (bps)", 0.0, 50.0, round(float(own_t), 2), 0.1, key="bt-taker",
                                  help=f"Default: {own.get('source', 'base tier')}.")
        maker = c[2].number_input("Maker fee (bps)", -5.0, 50.0, round(float(own_m), 2), 0.1, key="bt-maker")
        spread = c[3].number_input("Half spread (bps)", 0.0, 50.0, 1.0, 0.5, key="bt-spread",
                                   help="Paid on every simulated market order (stops, time exits).")
        c = st.columns(4)
        k = c[0].number_input("Impact k (bps)", 0.0, 200.0, 10.0, 1.0, key="bt-k",
                              help="Slippage = half spread + k x sqrt(order size / average 1-minute traded value). "
                                   "No free order-book history exists to calibrate k; 10 bps is a cautious default.")
        cap = c[1].number_input("Leverage cap for sizing rules (x)", 0.5, 50.0, 5.0, 0.5, key="bt-cap",
                                help="A sizing rule never makes a position bigger than this multiple of the account "
                                     "(or the trader's own size, if larger).")
        fams = c[2].multiselect("Rule families", list(FAMILIES), default=list(FAMILIES),
                                format_func=lambda f: FAMILIES[f], key="bt-fams")
        st.caption("Stops are measured from the first entry price; ATR is the 14-bar average true range of the "
                   "hourly candles before the entry. If a stop and a target are both inside one candle, the stop is "
                   "assumed to fill first. Funding is the account's actual hourly funding, scaled to the simulated "
                   "position. Liquidation is checked (isolated-equivalent, an approximation for cross margin) only "
                   "when a rule makes a position bigger than the trader's.")
    a = cst.Assumptions(taker_bps=None if abs(taker - round(float(own_t), 2)) < 1e-6 else taker,
                        maker_bps=None if abs(maker - round(float(own_m), 2)) < 1e-6 else maker,
                        half_spread_bps=spread, impact_k_bps=k, families=tuple(sorted(fams or FAMILIES)),
                        max_leverage_cap=cap)
    return a, window


@st.fragment(run_every=2)
def _progress(rid: str) -> None:
    con = store.connect()
    try:
        run = jobs.get(con, rid)
    finally:
        con.close()
    if not run:
        return
    if run["status"] != "running":
        st.rerun(scope="app")
    st.progress(float(run["progress"] or 0), text=f"Backtest running: {run['message']}")


def _results(r: dict, diag: dict | None) -> None:
    if r.get("status") == "too_few_trips":
        st.info(f"{r.get('trips', 0)} closed round trips in this window; at least {r.get('min_trips', 30)} are "
                "needed. Pick a longer window under Assumptions.")
        return
    g = r["golden"]
    sp = r["split"]
    ok = "passes" if g["pass"] else "FAILS"
    st.caption(_md(
        f"Check: the no-change replay gives {money(g['replay'])} vs the actual {money(g['actual'])} "
        f"(difference {g['error']:.3%}; it must be under 1%: {ok}). {r['trips']:,} round trips: "
        f"{sp['train_trips']:,} to choose parameters (train), {sp['test_trips']:,} to test them, from "
        f"{pd.Timestamp(sp['test_from']):%d %b %Y}. {r['variants_tried']} variants tried. Fees: taker "
        f"{r['rates']['taker_bps']:.2f} / maker {r['rates']['maker_bps']:.2f} bps ({r['rates']['fee_source']}). "
        f"Candles found for {r['bars_coverage']:.0%} of trips (exit rules can't change the others)."))
    if not g["pass"]:
        st.error("The no-change replay doesn't reproduce the actual result within 1%, so the simulated results "
                 "below are not reliable for this trader.")
    _summary(r)
    _equity(r)
    _plan(r, diag)
    _all_variants(r)
    _rules(r.get("rules"))


def _ci(ci, dp=0):
    if not ci or ci[0] is None:
        return "n/a"
    return f"{money(ci[0], dp, True)} to {money(ci[1], dp, True)}"


def _summary(r: dict) -> None:
    top = bt.summary(r["findings"])
    with _card("bt-summary", "Highest-impact findings (simulated, out of sample)",
               "Only rules that improved the training window are listed (a rule that only looks good on the test "
               "window would be picked with hindsight). Change in net P&L on the test window if the rule had been followed, with its 90% interval from a "
               "paired block bootstrap of daily P&L, and a confidence label: High = the interval excludes zero, "
               "deflated Sharpe at least 0.95 and 50+ test trips; Medium = the interval excludes zero; Low = "
               "otherwise.", expandable=False) as cc:
        if not top:
            st.markdown(_md(f"No rule that improved the training window also improved the test window. The trader's "
                            f"own exits, sizing and selection held up against all {r['variants_tried']} variants tried."))
        for f in top:
            col = LABEL_COLORS[f["label"]]
            st.markdown(
                f'<div style="display:flex;gap:12px;align-items:baseline;margin:6px 0 2px">'
                f'<span style="font-weight:600;font-size:15px">{f["name"]}</span>'
                f'<span style="color:{col};border:1px solid {col};border-radius:4px;padding:0 6px;font-size:12px">'
                f'{f["label"]} confidence</span></div>', unsafe_allow_html=True)
            sh = f["sharpe_delta_ci"]
            st.markdown(_md(
                f"Test window: **{money(f['test_delta'], 0, True)}** (90% interval {_ci(f['test_delta_ci'])}); "
                f"train: {money(f['train_delta'], 0, True)}. Sharpe {_num(f['sharpe_actual'])} → "
                f"{_num(f['sharpe_variant'])} (change {_num(sh[0], True)} to {_num(sh[1], True)}). Deflated Sharpe "
                f"{_num(f['dsr'])}. {f['test_trips_affected']} test trips changed. *Rule: {f['describe']}.*"))
            if (f.get("test_skipped_share") or 0) > 0.5:
                st.caption(f"This rule skips {f['test_skipped_share']:.0%} of the test trips: the gain mostly says the "
                           "trader lost money in that period, not that the rule found a better way to trade.")
        cc.fig = None


def _num(x, signed=False):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{x:+.2f}" if signed else f"{x:.2f}"


def _equity(r: dict) -> None:
    days = pd.to_datetime(r["days"])
    test_start = pd.Timestamp(r["test_start"])
    top = bt.summary(r["findings"], 4)
    with _card("bt-equity", "Cumulative P&L: actual vs simulated variants",
               "Closed round trips' net P&L by the day they closed. Grey line: the trader's actual trades. Coloured "
               "lines: simulated variants (parameters chosen on the unshaded training period). The shaded region is "
               "the test period, where the comparison is out of sample.") as cc:
        f = go.Figure()
        f.add_vrect(x0=test_start, x1=days[-1], fillcolor="rgba(155,109,255,0.08)", line_width=0,
                    annotation_text="Test (out of sample)", annotation_position="top left",
                    annotation_font=dict(size=11, color="#cbb6ff"))
        f.add_trace(go.Scatter(x=days, y=np.cumsum(r["actual_daily"]), name="Actual", line=dict(color="#d0d3d9", width=2.5),
                               hovertemplate="%{x|%d %b %Y}: %{y:$,.0f} actual<extra></extra>"))
        for i, fd in enumerate(top):
            f.add_trace(go.Scatter(x=days, y=np.cumsum(fd["daily"]), name=f"{fd['name']} (simulated)",
                                   line=dict(color=LINE_COLORS[i % len(LINE_COLORS)], width=1.6, dash="dot" if i else None),
                                   hovertemplate="%{x|%d %b %Y}: %{y:$,.0f}<extra>" + fd["name"][:30] + "</extra>"))
        f.update_yaxes(tickformat="$~s")
        cc.chart(_layout(f, 420), width="stretch",
                 config=CHART_CFG)


def _plan(r: dict, diag: dict | None) -> None:
    st.subheader("Right / Wrong / Improve")
    left, mid, right = st.columns(3)
    with left:
        with _card("bt-right", "Right: keep doing", "From the Diagnosis of the same trips (actual trades).",
                   paired=True, expandable=False, source="Hyperliquid (actual fills and funding)") as cc:
            s = r.get("right") or (diag or {}).get("strengths") or []
            st.markdown(_md("\n".join(f"- **{x['title']}.** {x['evidence']}" for x in s[:5])) if s else
                        "Nothing stands out with enough evidence.")
            cc.fig = None
    with mid:
        with _card("bt-wrong", "Wrong: recurring mistakes", "From the Diagnosis of the same trips (actual trades).",
                   paired=True, expandable=False, source="Hyperliquid (actual fills and funding)") as cc:
            e = r.get("wrong")
            if e is None:
                d = (diag or {}).get("errors")
                e = d.to_dict("records") if d is not None and len(d) else []
            if e:
                st.markdown(_md("\n".join(f"- **{x['pattern']}** ({money(x['impact_usd'])} on {x['trades_flagged']} "
                                          f"trades)" for x in e[:5])))
            else:
                st.markdown("No recurring mistake crossed the thresholds.")
            cc.fig = None
    with right:
        with _card("bt-improve", "Improve (simulated)",
                   "Ranked by confidence, then by the out-of-sample gain.", paired=True, expandable=False) as cc:
            top = bt.summary(r["findings"])
            if top:
                st.markdown(_md("\n".join(
                    f"{i + 1}. **{f['describe'][0].upper() + f['describe'][1:]}** - "
                    f"{money(f['test_delta'], 0, True)} on the test window ({f['label'].lower()} confidence)."
                    for i, f in enumerate(top))))
            else:
                st.markdown("Nothing tested beat the trader's own decisions out of sample.")
            worse = [f for f in r["findings"] if f["train_delta"] > 0 and f["test_delta"] < 0][:3]
            if worse:
                st.caption(_md("Looked good on train but hurt on test: " + "; ".join(f"{f['name'].lower()} ({money(f['test_delta'], 0, True)})"
                                                     for f in worse) + "."))
            cc.fig = None


def _all_variants(r: dict) -> None:
    with st.expander(f"Every variant family ({len(r['findings'])} shown; best setting of each, chosen on train)"):
        rows = [{"Family": f["family"], "Setting (chosen on train)": f["name"],
                 "Adopted": "yes" if f["train_delta"] > 0 else "no (didn't help on train)", "Train change": f["train_delta"],
                 "Test change": f["test_delta"], "Test 90% low": (f["test_delta_ci"] or [None])[0],
                 "Test 90% high": (f["test_delta_ci"] or [None, None])[1], "Sharpe actual": f["sharpe_actual"],
                 "Sharpe simulated": f["sharpe_variant"], "Deflated Sharpe": f["dsr"], "Confidence": f["label"],
                 "Test trips changed": f["test_trips_affected"],
                 "What happened": ", ".join(f"{k}: {v}" for k, v in f["reasons"].items())} for f in r["findings"]]
        N = st.column_config.NumberColumn
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
            "Train change": N(format="$%,.0f"), "Test change": N(format="$%,.0f"), "Test 90% low": N(format="$%,.0f"),
            "Test 90% high": N(format="$%,.0f"), "Sharpe actual": N(format="%.2f"), "Sharpe simulated": N(format="%.2f"),
            "Deflated Sharpe": N(format="%.2f")})
        st.caption(_md(
            f"All simulated. Sharpe is annualised from daily P&L / account value ({money(r['equity_ref'])}, "
            f"{r.get('equity_ref_source', '')}). The deflated Sharpe accounts for all {r['variants_tried']} variants "
            "tried (Bailey & Lopez de Prado). Exit rules overlay the trader's own exits: the earlier one wins."))


def _rules(rr: dict | None) -> None:
    if not rr:
        return
    with _card("bt-rules", "Rule extraction (experimental)",
               "Can the trader's entries be written as a simple rule (up to two thresholds on recent returns, "
               "volatility, funding and hour, learned on the training window) that makes money on its own? The "
               "rule is traded alone on the test window: next bar's open in, held for the trader's median holding "
               "time, taker fee and half spread each way.", expandable=False) as cc:
        if rr.get("status") != "done":
            st.markdown(rr.get("message", "No result."))
        else:
            for x in rr["rules"]:
                st.markdown(_md(
                    f"- **{x['text']}.** Matches their {x['side'].lower()} entries with F1 {x['train_f1']:.2f} "
                    f"(train) / {_num(x['test_f1'])} (test). Traded alone on the test window: {x['test_trades']} "
                    f"trades, {_pct(x['test_ron'])} per trade (simulated)."))
            st.markdown(_md(f"Trader's own trips on the test window: {_pct(rr['trader_test_ron'])} per dollar traded "
                            f"({rr['trader_test_trips']} trips). **{rr['verdict']}** "
                            f"({rr['rules_tried']} rules tried; candles: {rr.get('interval')})."))
        cc.fig = None


def _pct(x):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{x:+.2%}"
