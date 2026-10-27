"""Trade review > trader page > Diagnosis: what the trader does, how they
execute, handle risk and size, where the edge comes from, what they do
right and what they get wrong. Actual trades only (nothing simulated).

Market data (candles, funding) is fetched in a background thread the
first time a trader is opened and stored in data/trades.db; the page shows
what's ready and refreshes itself until loading is done.
"""
from __future__ import annotations

import threading
import time

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from . import venues
from .indicators import trades as tr
from .review import diagnosis as dg
from .review import equity as eq
from .review import market as mk
from .review import store

BLUE, ORANGE, GREEN, RED, GREY = "#2a78d6", "#eb6834", "#2fbf71", "#e5484d", "#8a8f98"
CHART_CFG = {"displayModeBar": False}
SOURCE = "Hyperliquid (account fills and funding; candles; funding history)"
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _layout(fig, height=300):
    from .charts import card_layout
    return card_layout(fig, height)


def _md(text: str) -> str:
    """Dollar signs would start LaTeX in Streamlit markdown."""
    return text.replace("$", "\\$")


def money(x, dp=0):
    if x is None or (isinstance(x, float) and not np.isfinite(x)) or pd.isna(x):
        return "n/a"
    if round(abs(x), dp) == 0:
        x = 0.0
    return f"{'-' if x < 0 else ''}${abs(x):,.{dp}f}"


def pct(x, dp=0, signed=False):
    if x is None or (isinstance(x, float) and not np.isfinite(x)) or pd.isna(x):
        return "n/a"
    return f"{x:+.{dp}%}" if signed else f"{x:.{dp}%}"


def num(x, fmt="{:.2f}"):
    if x is None or (isinstance(x, float) and not np.isfinite(x)) or pd.isna(x):
        return "n/a"
    return fmt.format(x)


# --------------------------------------------------------------------------- #
# Background loading of market data
# --------------------------------------------------------------------------- #

def _start_loader(addr: str, trips: pd.DataFrame, coins: list[str]) -> dict:
    with _LOCK:
        job = _JOBS.get(addr)
        if job and job["thread"].is_alive():
            return job
        job = {"progress": 0.0, "message": "Starting", "report": {}, "done": False, "started": time.time()}

        def run():
            con = store.connect()
            try:
                ad = venues.get("hyperliquid")
                now = pd.Timestamp.now(tz="UTC")
                needs = mk.plan(trips, now, coins)

                def prog(p, m):
                    job["progress"], job["message"] = 0.8 * p, m
                rep = mk.ensure_candles(con, ad, needs, budget_s=420, progress=prog, now=now)
                job["message"] = "Funding history"
                rep["funding"] = mk.ensure_market_funding(con, ad, trips, coins, budget_s=150, now=now)
                job["report"] = rep
            except Exception as ex:  # noqa: BLE001 - shown on the page
                job["report"] = {"errors": {"loader": f"{type(ex).__name__}: {ex}"}}
            finally:
                job["progress"], job["message"], job["done"] = 1.0, "Done", True
                con.close()
        job["thread"] = threading.Thread(target=run, daemon=True, name=f"diag-{addr[:8]}")
        job["thread"].start()
        _JOBS[addr] = job
        return job


def _price_version(rcon, coins) -> str:
    if not coins:
        return "none"
    q = ",".join("?" * len(coins))
    a = rcon.execute(f"SELECT COUNT(*), MAX(ts) FROM tr_candles WHERE venue='hyperliquid' AND coin IN ({q})", coins).fetchone()
    b = rcon.execute(f"SELECT COUNT(*), MAX(ts) FROM tr_market_funding WHERE venue='hyperliquid' AND coin IN ({q})",
                     coins).fetchone()
    return f"{a[0]}-{a[1]}-{b[0]}-{b[1]}"


@st.cache_data(ttl=900, show_spinner=False, max_entries=20)
def _diagnose(addr: str, data_version: str, price_version: str) -> dict:
    con = store.connect()
    try:
        fills = store.load(con, "tr_fills", "hyperliquid", addr)
        funding = store.load(con, "tr_funding", "hyperliquid", addr)
        from .review.reconstruct import reconstruct
        trips, rep = reconstruct(fills, funding)
        if trips.empty:
            return {}
        coins = sorted(trips["coin"].unique())
        prices = mk.Prices.load(con, coins, account_funding=funding)
        perp = fills[fills["coin"].map(lambda c: not (c.startswith("@") or "/" in c))]
        closes = {c: store.candles(con, "hyperliquid", c, "1d")["c"] for c in sorted(perp["coin"].unique())}
        now = pd.Timestamp.now(tz="UTC")
        daily = eq.daily_pnl(perp, funding, closes, now)
        snaps = store.load(con, "tr_account_snapshots", "hyperliquid", addr)
        avs = [g.set_index("ts")["account_value"] for _, g in snaps.groupby("source")] if not snaps.empty else []
        equity = eq.daily_equity(avs, daily.index) if not daily.empty else pd.Series(dtype=float)
        res = dg.run(trips, fills, funding, prices, daily, equity, now)
        res["coverage"] = dg.coverage(res["trips"])
        return res
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #

def render(rcon, addr: str, trips: pd.DataFrame) -> dict:
    """Draws the Diagnosis view; returns the result (for the data-quality panel)."""
    if trips.empty:
        st.info("No round trips to diagnose yet.")
        return {}
    coins, skipped = mk.chosen_coins(trips)
    now = pd.Timestamp.now(tz="UTC")
    pending = mk.missing(rcon, mk.plan(trips, now, coins), now=now)
    job = _JOBS.get(addr)
    loading = bool(job and not job["done"])
    if pending and not loading and not (job and job["done"] and time.time() - job["started"] < 600):
        job = _start_loader(addr, trips, coins)
        st.session_state[f"_diag_rerun_{addr}"] = False
        loading = True
    if loading:
        _progress(addr)
    res = _diagnose(addr, store.data_version(rcon, "hyperliquid", addr), _price_version(rcon, coins))
    if not res:
        st.info("No closed round trips to diagnose yet.")
        return {}
    res["skipped_coins"] = skipped
    res["loader"] = (job or {}).get("report", {})
    _summary(res)
    _fingerprint(res)
    _execution(res)
    _risk(res)
    _sizing(res)
    _edge(res)
    return res


def result(rcon, addr: str, trips: pd.DataFrame) -> dict:
    """The diagnosis from whatever market data is stored (no fetching)."""
    if trips.empty:
        return {}
    coins, skipped = mk.chosen_coins(trips)
    res = _diagnose(addr, store.data_version(rcon, "hyperliquid", addr), _price_version(rcon, coins))
    if res:
        res = {**res, "skipped_coins": skipped, "loader": (_JOBS.get(addr) or {}).get("report", {})}
    return res


@st.fragment(run_every=3)
def _progress(addr: str) -> None:
    job = _JOBS.get(addr)
    if not job:
        return
    if job["done"]:
        st.caption("Price data loaded.")
        if not st.session_state.get(f"_diag_rerun_{addr}"):
            st.session_state[f"_diag_rerun_{addr}"] = True
            st.rerun(scope="app")
        return
    st.progress(job["progress"], text=f"Loading price data for the excursion and entry-context measures "
                                      f"({job['message']}). The page updates when it's done; numbers that need "
                                      f"prices show n/a until then.")


def _metrics(items, n=6) -> None:
    cols = st.columns(n)
    for col, (label, value, help_) in zip(cols, items):
        col.metric(label, value, help=help_ or None)


def _card(key, title, desc, paired=True):
    from .ui_studio import card
    return card(key, title, desc=desc, source=SOURCE, paired=paired, expandable=True)


def _bar(x, y, color=BLUE, height=280, fmt="%{y:.0f}", horizontal=False, colors=None, suffix=""):
    f = go.Figure()
    if horizontal:
        f.add_trace(go.Bar(y=x, x=y, orientation="h", marker_color=colors or color,
                           hovertemplate="%{y}: " + fmt.replace("%{y", "%{x") + suffix + "<extra></extra>"))
        f.update_yaxes(autorange="reversed")
    else:
        f.add_trace(go.Bar(x=x, y=y, marker_color=colors or color, hovertemplate="%{x}: " + fmt + suffix + "<extra></extra>"))
    return _layout(f, height).update_layout(showlegend=False)


def _summary(res) -> None:
    st.markdown(_md(f"**{res['summary']}**"))
    st.caption("Diagnosis of the trader's actual round trips: nothing on this view is simulated. Trips whose exit "
               "wasn't observed are left out (see Data quality).")
    left, right = st.columns(2)
    with left:
        with _card("dg-right", "What they do right",
                   "Strengths backed by the data, strongest first. Each needs enough trips to count; with few trips "
                   "this list stays short on purpose.", paired=True) as cc:
            items = res["strengths"]
            if items:
                st.markdown(_md("\n".join(f"- **{s['title']}.** {s['evidence']}" for s in items)))
            else:
                st.caption("Nothing stands out with enough evidence yet.")
            cc.fig = None
    with right:
        with _card("dg-wrong", "What they get wrong, and the rule for each",
                   "Recurring patterns that cost money, with a rule that would address each. P&L = total of the "
                   "flagged trades (negative = what following the rule would have saved, before any knock-on "
                   "effects; the backtest measures those).", paired=True) as cc:
            errs = res["errors"]
            if errs is None or errs.empty:
                st.caption("No recurring mistake crossed the thresholds.")
            else:
                st.markdown(_md("\n".join(f"- **{r.pattern}** ({r.trades_flagged} trades, {money(r.impact_usd)}): "
                                          f"{r.finding}. *Rule:* {r.rule}." for r in errs.head(6).itertuples())))
            cc.fig = None


def _fingerprint(res) -> None:
    fp, t = res["fingerprint"], res["trips"]
    st.subheader("Strategy fingerprint")
    _metrics([
        ("Largest market", f"{fp['top_asset']} {pct(fp['top_asset_share'])}",
         f"Share of total position size. Effective number of markets: {fp['effective_assets']:.1f}."),
        ("Long share", pct(fp["long_share_notional"]),
         f"Of total position size; {pct(fp['long_share_trips'])} of trips are longs."),
        ("Median hold", tr.duration(fp["median_hold_h"]), None),
        ("Median leverage", num(fp["leverage_median"], "{:.2f}x" if (fp["leverage_median"] or 0) < 1 else "{:.1f}x"),
         f"Position size / account value at the start of the day. 90th percentile: "
         f"{num(fp['leverage_p90'], '{:.1f}x')}. Known for {fp['leverage_known']:,} trips."),
        ("Scales in", pct(fp["scale_in_share"]),
         f"Share of trips with at least one add. {pct(fp['scale_out_share'])} of trips take a partial exit before "
         f"the final exit (scaling out)."),
        ("Style", fp["style"],
         "From the 24h price move before each entry, in units of that day's volatility: 'with the move' above "
         "+0.5, 'against' below -0.5. Uses only bars that closed before the entry."),
    ])
    a, b = st.columns(2)
    with a:
        with _card("dg-assets", "Where the size goes",
                   "Share of total position size (largest size of each trip) by market; top 12.") as cc:
            s = fp["asset_share"].head(12)
            cc.chart(_bar(list(s.index), s.values * 100, horizontal=True, fmt="%{y:.1f}", suffix="%").update_xaxes(
                ticksuffix="%"), width="stretch", config=CHART_CFG)
    with b:
        with _card("dg-hold", "Holding time",
                   "Number of closed trips by how long they were held.") as cc:
            h = fp["hold_dist"]
            cc.chart(_bar(list(h.index), h.values), width="stretch", config=CHART_CFG)
    a, b = st.columns(2)
    with a:
        with _card("dg-hours", "When they enter (hour, UTC)",
                   "Trips opened in each hour of the day (UTC); bars coloured by whether trips opened in that hour "
                   "made money overall.") as cc:
            c = dg.closed_trips(t)
            by = c.groupby(c["open_ts"].dt.hour)["net"].sum().reindex(range(24), fill_value=0)
            cols = [GREEN if v > 0 else RED if v < 0 else GREY for v in by.values]
            f = _bar([f"{h:02d}" for h in range(24)], fp["hour_dist"].values, colors=cols)
            f.update_traces(customdata=[money(v) for v in by.values],
                            hovertemplate="%{x}:00 UTC: %{y} trips, net %{customdata}<extra></extra>")
            cc.chart(f, width="stretch", config=CHART_CFG)
    with b:
        with _card("dg-ctx", "Entry context",
                   "Share of trips entered with the prior 24h move, against it, or after a flat day, and the "
                   "volatility regime at entry (24h volatility vs its 30-day median: below 0.75x low, above "
                   "1.33x high).") as cc:
            f = go.Figure()
            for name, s, colors in (("Prior move", fp["context_share"], [BLUE, ORANGE, GREY]),
                                    ("Volatility", fp["vol_share"], [GREEN, GREY, RED])):
                order = ["With the move", "Against the move", "Flat"] if name == "Prior move" else ["Low", "Normal", "High"]
                s = s.reindex(order).fillna(0)
                for k, (lab, v) in enumerate(s.items()):
                    f.add_trace(go.Bar(y=[name], x=[v * 100], name=lab, orientation="h", marker_color=colors[k],
                                       hovertemplate=f"{lab}: %{{x:.0f}}%<extra></extra>",
                                       text=f"{lab} {v:.0%}" if v >= 0.12 else "", textposition="inside"))
            f.update_layout(barmode="stack", showlegend=False)
            f.update_xaxes(ticksuffix="%", range=[0, 100])
            cc.chart(_layout(f, 280).update_layout(showlegend=False), width="stretch", config=CHART_CFG)
            st.caption(f"Known for {fp['context_known']:,} of {fp['trips']:,} trips.")


def _execution(res) -> None:
    ex = res["execution"]
    st.subheader("Execution")
    _metrics([
        ("Taker share", pct(ex.get("taker_share_notional")),
         f"Of traded volume ({pct(ex.get('taker_share_fills'))} of fills). Taker = crossed the spread."),
        ("Fees", f"{money(ex.get('fees'))}", f"{num(ex.get('fee_bps'), '{:.1f}')} bps of volume (taker "
         f"{num(ex.get('fee_bps_taker'), '{:.1f}')}, maker {num(ex.get('fee_bps_maker'), '{:.1f}')})."),
        ("Fees / gross profit", pct(ex.get("fees_share_of_gross")), "Fees paid on closed trips over their gross profit."),
        ("Funding, net", money(ex.get("funding_net")),
         f"Received {money(ex.get('funding_received'))}, paid {money(ex.get('funding_paid'))}."),
        ("Slippage (proxy)", f"{num(ex.get('slippage_bps_median'), '{:+.1f}')} bps",
         f"Median taker fill price vs the previous 1-minute close, in the trade's direction (positive = paid up). "
         f"No historical order book exists, so this is a proxy; measured on {ex.get('slippage_n', 0):,} taker fills "
         f"from the last ~3.5 days (1-minute candles)."),
        ("Chasing", pct(ex.get("chased_share")),
         f"Trips entered after a 4h move of more than 2 sigma in the trade's direction. Expectancy "
         f"{money(ex.get('chased_expectancy'))} vs {money(ex.get('not_chased_expectancy'))} for the rest."),
    ])
    fb = ex.get("funding_by_coin", pd.Series(dtype=float))
    if len(fb):
        fb = pd.concat([fb.head(6), fb.tail(6)])
        fb = fb[~fb.index.duplicated()]
        with _card("dg-funding", "Funding paid and received, by market",
                   "Net funding over the stored history: positive = received. The six largest payments and receipts.",
                   paired=False) as cc:
            cc.chart(_bar(list(fb.index), fb.values, colors=[GREEN if v > 0 else RED for v in fb.values],
                          fmt="%{y:$,.0f}", height=260), width="stretch", config=CHART_CFG)


def _risk(res) -> None:
    rk = res["risk"]
    st.subheader("Risk")
    _metrics([
        ("Liquidations", f"{rk.get('liquidations', 0):,}", "Fills where this account was the one liquidated."),
        ("Deep losers", f"{rk.get('losers_past_winner_cut', 'n/a')}",
         f"90% of winners never went further against their first entry than {pct(rk.get('winner_mae_p10'), 1)}. "
         f"Losers that did: {pct(rk.get('losers_past_winner_cut_share'))} of losers, {money(rk.get('losers_past_winner_cut_pnl'))}."),
        ("Losers' worst move", pct(rk.get("mae_losers_median"), 1),
         f"Median of how far price went against the average entry during losing trips; winners: "
         f"{pct(rk.get('mae_winners_median'), 1)}."),
        ("Winners keep", pct(rk.get("winner_capture_median")),
         "Median share of the best move during the trip (MFE) that winners kept at exit."),
        ("Max open at once", f"{rk.get('max_concurrent', 'n/a')}",
         f"Positions open at the same time; {pct(rk.get('time_multi_share'))} of time in the market had 2+ open, "
         f"{pct(rk.get('time_same_dir_share'))} all in the same direction. Gross leverage peak: "
         f"{num(rk.get('max_gross_leverage'), '{:.1f}x')}."),
        ("Account drawdown", money(rk.get("dd_usd")),
         f"Whole account, daily mark-to-market (includes positions opened before the stored history, unlike the "
         f"trip-based drawdown on Review); {pct(rk.get('dd_pct_of_equity'))} of the account value at the peak; longest time "
         f"under water {rk.get('longest_underwater_days', 'n/a')} days; recovered in "
         f"{rk.get('dd_recovery_days') if rk.get('dd_recovery_days') is not None else 'not yet'}"
         f"{' days' if rk.get('dd_recovery_days') is not None else ''}."),
    ])
    a, b = st.columns(2)
    with a:
        with _card("dg-mae", "Excursions: how far each trip went for and against",
                   "Each dot is a closed trip: maximum adverse move (x) and maximum favourable move (y) from the "
                   "average entry, measured on the finest candles still available (1 minute for the last ~3.5 days, "
                   "then 15 minutes, 1 hour). The dashed line is where 90% of winners stayed.") as cc:
            mm = rk.get("mae_mfe")
            f = go.Figure()
            if mm is not None and len(mm):
                for name, part, color in (("Winners", mm[mm["net"] > 0], BLUE), ("Losers", mm[mm["net"] <= 0], ORANGE)):
                    f.add_trace(go.Scattergl(x=part["mae_pct"] * 100, y=part["mfe_pct"] * 100, mode="markers", name=name,
                                             marker=dict(color=color, size=6, opacity=0.75),
                                             customdata=np.c_[part["coin"], [money(v) for v in part["net"]],
                                                              part["excursion_interval"]],
                                             hovertemplate="%{customdata[0]}: against %{x:.2f}%, for %{y:.2f}%<br>net "
                                                           "%{customdata[1]} (%{customdata[2]} candles)<extra></extra>"))
                if rk.get("winner_mae_p10") == rk.get("winner_mae_p10") and rk.get("winner_mae_p10") is not None:
                    f.add_vline(x=rk["winner_mae_p10"] * 100, line=dict(color=GREY, dash="dash", width=1))
            f.update_xaxes(title_text="Max move against (%)", ticksuffix="%")
            f.update_yaxes(title_text="Max move in favour (%)", ticksuffix="%")
            cc.chart(_layout(f, 320).update_layout(legend=dict(orientation="h", y=1.08)), width="stretch",
                     config=CHART_CFG)
    with b:
        with _card("dg-dd", "Account drawdown (daily)",
                   "Daily mark-to-market P&L below its running peak (USD). Days are UTC.") as cc:
            dd = rk.get("drawdown")
            f = go.Figure()
            if dd is not None and len(dd):
                f.add_trace(go.Scatter(x=dd.index, y=dd.values, fill="tozeroy", line=dict(color=ORANGE, width=1.5),
                                       hovertemplate="%{x|%d %b %Y}: %{y:$,.0f}<extra></extra>"))
            cc.chart(_layout(f, 320).update_layout(showlegend=False), width="stretch", config=CHART_CFG)


def _sizing(res) -> None:
    sz = res["sizing"]
    st.subheader("Sizing")
    if not sz:
        st.caption("Too few trips to judge sizing.")
        return
    _metrics([
        ("Size after a loss", num(sz.get("tilt_ratio"), "{:.2f}x"),
         "Median size of trips that follow a loss vs those that follow a win, each relative to the trader's "
         "previous 20 trips. Above ~1.2x suggests sizing up to win it back."),
        ("Size vs return", num(sz.get("size_vs_return_corr"), "{:+.2f}"),
         "Rank correlation between position size and return on size. Positive = bigger bets do better."),
        ("Size vs volatility", num(sz.get("size_vs_vol_corr"), "{:+.2f}"),
         "Rank correlation between relative size and volatility at entry. Negative = sizes down in wild markets."),
    ], n=3)
    a, b = st.columns(2)
    with a:
        with _card("dg-size", "Results by position size",
                   "Expectancy (average net P&L per trip) for each fifth of trips by size, smallest to largest.") as cc:
            bs = sz["by_size"]
            cc.chart(_bar(list(bs.index), bs["expectancy"].astype(float).values,
                          colors=[GREEN if v > 0 else RED for v in bs["expectancy"]], fmt="%{y:$,.0f}"),
                     width="stretch", config=CHART_CFG)
    with b:
        with _card("dg-tilt", "After a win vs after a loss",
                   "Median size relative to the trader's recent trips (bars) and the expectancy of those trips.") as cc:
            af = sz.get("after", pd.DataFrame())
            f = go.Figure()
            if len(af):
                af = af.reindex([k for k in ("After a win", "After a loss") if k in af.index])
                f.add_trace(go.Bar(x=list(af.index), y=af["median_size_vs_recent"],
                                   marker_color=[GREEN if k == "After a win" else RED for k in af.index],
                                   customdata=[money(v) for v in af["expectancy"]],
                                   hovertemplate="%{x}: %{y:.2f}x recent size, expectancy %{customdata}<extra></extra>"))
                f.add_hline(y=1, line=dict(color=GREY, dash="dash", width=1))
            f.update_yaxes(title_text="Size vs recent (x)")
            cc.chart(_layout(f, 280).update_layout(showlegend=False), width="stretch", config=CHART_CFG)


def _edge(res) -> None:
    ed = res["edge"]
    o = ed["overall"]
    st.subheader("Where the edge comes from")
    _metrics([
        ("Expectancy / trip", money(o.get("expectancy")), None),
        ("Win rate", pct(o.get("win_rate")), None),
        ("Payoff", num(o.get("payoff"), "{:.2f}"), "Average win / average loss."),
        ("Profit factor", num(o.get("profit_factor"), "{:.2f}"), None),
        ("Best 5 trips", pct(ed.get("top5_share")) if o.get("net", 0) > 0 else "net loss",
         f"Share of net P&L made by the 5 best trips. Without them: {money(ed.get('without_top5'))}."),
        ("Net P&L (closed trips)", money(o.get("net")), None),
    ])
    tables = ed.get("tables", {})
    a, b = st.columns(2)
    if "Market" in tables:
        with a:
            with _card("dg-edge-mkt", "Net P&L by market",
                       "Closed trips' net P&L (after fees and funding) by market: the 8 best and 8 worst.") as cc:
                m = tables["Market"]["net"].astype(float)
                m = pd.concat([m.head(8), m.tail(8)])
                m = m[~m.index.duplicated()].sort_values()
                cc.chart(_bar(list(m.index), m.values, horizontal=True, colors=[GREEN if v > 0 else RED for v in m.values],
                              fmt="%{y:$,.0f}", height=max(280, 22 * len(m) + 60)).update_xaxes(tickformat="$,.0f"),
                         width="stretch", config=CHART_CFG)
    with b:
        with _card("dg-edge-dim", "Expectancy by side, holding time and entry context",
                   "Average net P&L per trip in each group (hover for trips and win rate).") as cc:
            labels, vals, n_, wr = [], [], [], []
            for title in ("Side", "Holding time", "Entry context", "Volatility at entry", "Funding at entry"):
                df = tables.get(title)
                if df is None or df.empty:
                    continue
                short = {"Holding time": "Hold", "Entry context": "Entry", "Volatility at entry": "Vol",
                         "Funding at entry": "Funding"}.get(title, title)
                for k, r in df.iterrows():
                    labels.append(f"{short}: {k}")
                    vals.append(float(r["expectancy"]))
                    n_.append(int(r["trades"]))
                    wr.append(float(r["win_rate"]) * 100)
            f = go.Figure(go.Bar(y=labels, x=vals, orientation="h", marker_color=[GREEN if v > 0 else RED for v in vals],
                                 customdata=np.c_[n_, wr],
                                 hovertemplate="%{y}: %{x:$,.0f} per trip<br>%{customdata[0]:.0f} trips, "
                                               "win rate %{customdata[1]:.0f}%<extra></extra>"))
            f.update_yaxes(autorange="reversed", automargin=True)
            f.update_xaxes(tickformat="$,.0f")
            cc.chart(_layout(f, max(340, 22 * len(labels) + 60)).update_layout(showlegend=False, margin=dict(l=170)),
                     width="stretch", config=CHART_CFG)
    with st.expander("All breakdown tables"):
        cfg = {"trades": "Trips", "win_rate": st.column_config.NumberColumn("Win rate", format="percent"),
               "payoff": st.column_config.NumberColumn("Payoff", format="%.2f"),
               "expectancy": st.column_config.NumberColumn("Expectancy", format="dollar"),
               "net": st.column_config.NumberColumn("Net", format="dollar"),
               "profit_factor": st.column_config.NumberColumn("Profit factor", format="%.2f"),
               "share_of_pnl": st.column_config.NumberColumn("Share of P&L", format="percent")}
        for title, df in tables.items():
            st.markdown(f"**{title}**")
            st.dataframe(df.astype({"trades": int}), width="stretch", column_config=cfg,
                         height=min(36 * (len(df) + 1) + 3, 420))
        st.markdown("**Best 5 trips**")
        st.dataframe(ed["top5"], hide_index=True, width="stretch", column_config={
            "coin": "Market", "side": "Side", "open_ts": st.column_config.DatetimeColumn("Opened (UTC)", format="YYYY-MM-DD HH:mm"),
            "hold_hours": st.column_config.NumberColumn("Hold (h)", format="%.1f"),
            "max_notional": st.column_config.NumberColumn("Size", format="$%,.0f"),
            "net": st.column_config.NumberColumn("Net", format="dollar")})


def quality_rows(res: dict) -> list[tuple[str, str, str]]:
    """Rows for the data-quality panel about the market data behind the diagnosis."""
    if not res:
        return []
    cov = res.get("coverage", {})
    n = cov.get("trips", 0)
    ivs = ", ".join(f"{k}: {v:,}" for k, v in cov.get("excursion_intervals", {}).items()) or "none"
    lo = res.get("loader", {})
    errs = {**lo.get("errors", {}), **lo.get("funding", {}).get("errors", {})}
    return [
        ("Trips with excursions (MAE/MFE)", f"{cov.get('excursions', 0):,} of {n:,}",
         f"By candle interval: {ivs}. Coarser candles overstate excursions slightly."),
        ("Trips with entry context", f"{cov.get('context', 0):,} of {n:,}", "Needs 1h (else 4h) candles before the entry."),
        ("Trips with funding at entry", f"{cov.get('funding_at_entry', 0):,} of {n:,}",
         "Market funding for the last 60 days, plus the rate on the account's own funding payments."),
        ("Trips with leverage", f"{cov.get('leverage', 0):,} of {n:,}", "Needs the account value history."),
        ("Markets without price data", ", ".join(res.get("skipped_coins", [])[:15]) or "none",
         "Only the most traded markets (98% of trips, at most 24) are loaded, to stay within the venue's rate limit."),
        ("Trips without excursions in", ", ".join(cov.get("missing_excursion_coins", [])[:15]) or "none",
         "No candles fine enough: 1h candles need a trip of 3h+, 4h of 12h+, daily of 3 days+."),
        ("Price-data errors", "; ".join(f"{k}: {v}" for k, v in list(errs.items())[:5]) or "none", ""),
        ("Leverage at the time / liquidation distance", "Not available",
         "Historical margin and liquidation prices aren't public; leverage is estimated from account value, and "
         "the distance to liquidation is shown only for positions open now."),
    ]
