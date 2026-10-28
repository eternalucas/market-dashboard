"""Trade review tab: Top 100 leaderboard and the per-trader review.

Everything shown is computed from the venue's raw data in data/trades.db
(fills, funding, account-value history); the venue's own leaderboard only
decides whom to look at. Limitations are listed on the page, not hidden.
"""
from __future__ import annotations

import json
import threading

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from . import pipeline, storage, venues, views
from .indicators import trades as tr
from .review import leaderboard as lbm
from .review import store
from .review.reconstruct import reconstruct, to_legacy

BLUE, ORANGE = "#2a78d6", "#eb6834"
WINDOW_LABELS = {"7d": "7 days", "30d": "30 days", "90d": "90 days", "all": "All time"}


def money(x, dp=0):
    if x is None or (isinstance(x, float) and not np.isfinite(x)) or pd.isna(x):
        return "n/a"
    if round(abs(x), dp) == 0:
        x = 0.0
    return f"{'-' if x < 0 else ''}${abs(x):,.{dp}f}"


def chart_layout(fig, height=420):
    from .charts import fixed_layout
    return fixed_layout(fig, height)


@st.cache_resource
def _rcon():
    return store.connect()


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def render(con, cfg) -> None:
    rcon = _rcon()
    qp_trader = st.query_params.get("trader")
    default = "Trader review" if qp_trader else "Top 100"
    mode = st.segmented_control("View", ["Top 100", "Trader review"], default=default, key="tr-mode",
                                label_visibility="collapsed") or default
    if mode == "Top 100":
        render_board(rcon, cfg)
    else:
        render_trader_page(con, rcon, cfg, qp_trader)


# --------------------------------------------------------------------------- #
# Top 100
# --------------------------------------------------------------------------- #

def _start_refresh(budget_s: int = 600) -> bool:
    t = st.session_state.get("_tl_thread")
    if t is not None and t.is_alive():
        return False

    def run():
        c = store.connect()
        try:
            lbm.refresh(c, "hyperliquid", budget_s=budget_s, say=lambda *_: None)
        finally:
            c.close()
    t = threading.Thread(target=run, daemon=True, name="tl-manual")
    t.start()
    st.session_state["_tl_thread"] = t
    return True


def render_board(rcon, cfg) -> None:
    st.subheader("Top 100 on-chain perp traders")
    st.caption("Ranked from each trader's own fills, funding and account history, recomputed here. The venue's "
               "leaderboard only decides whom to look at. Market makers, vaults and protocol accounts are excluded "
               "by behaviour; every filter is adjustable below.")
    stt = lbm.status(rcon, "hyperliquid")
    kinds = stt["accounts_by_kind"]
    c = st.columns([1.2, 1, 1, 1, 1.3], vertical_alignment="center")
    c[0].metric("Candidates", f"{stt['pool'] or 0:,}", help="Top 1,000 by PnL in each of Hyperliquid's week, month "
                "and all-time lists (union). Refreshed every 6 hours.")
    c[1].metric("Analysed", f"{stt['with_metrics']:,}")
    c[2].metric("Market makers", f"{kinds.get('market_maker', 0):,}")
    c[3].metric("Vaults", f"{kinds.get('vault', 0):,}")
    with c[4]:
        running = st.session_state.get("_tl_thread") is not None and st.session_state["_tl_thread"].is_alive()
        if st.button("Refresh now (10 min)", disabled=running, width="stretch",
                     help="The recorder also refreshes every hour while the dashboard is open."):
            _start_refresh()
            st.rerun()
        if running:
            st.caption("Refreshing in the background...")

    a, b, v = st.columns([2, 2, 1.4], vertical_alignment="bottom")
    window = a.segmented_control("Window", list(WINDOW_LABELS), default="30d",
                                 format_func=lambda w: WINDOW_LABELS[w], key="tl-window") or "30d"
    sort = b.selectbox("Sort by", list(lbm.SORTS), key="tl-sort")
    v.selectbox("Venue", ["Hyperliquid"], key="tl-venue",
                help="Other venues are added once their public data is checked (docs/venues.md). "
                     "Nado and Variational were checked and excluded: no open per-trader history.")

    el0 = lbm.Eligibility.from_cfg(cfg)
    with st.expander("Eligibility filters"):
        e = st.columns(4)
        el = lbm.Eligibility(
            min_trips=e[0].number_input("Min round trips (all history)", 0, 10_000, el0.min_trips, 10),
            min_active_days=e[1].number_input("Min active days", 0, 3650, el0.min_active_days, 1),
            max_fills_per_day=e[2].number_input("Market maker: fills per active day above", 10, 1_000_000,
                                                int(el0.max_fills_per_day), 100),
            max_maker_share=e[3].number_input("Market maker: maker share above", 0.0, 1.0, el0.max_maker_share, 0.05,
                                              help="Only together with a median hold under the minute threshold."),
        )
        e2 = st.columns(4)
        el.min_median_hold_min = e2[0].number_input("...and median hold under (minutes)", 0.0, 1440.0,
                                                    el0.min_median_hold_min, 0.5)
        el.max_two_sided_share = e2[1].number_input("Market maker: two-sided fills above", 0.0, 1.0,
                                                    el0.max_two_sided_share, 0.05,
                                                    help="Share of fills with an opposite-side fill on the same coin within 1 s.")
        el.single_trade_flag = e2[2].number_input("Flag when one trade is above this share of PnL", 0.0, 1.0,
                                                  el0.single_trade_flag, 0.05)
        st.caption("Defaults live in config.yaml under trade_review > eligibility.")

    top, allrows = lbm.board(rcon, window, sort, venue="hyperliquid", el=el)
    if allrows.empty:
        st.info("No traders analysed yet. The first build runs in the background (about an hour at the venue's "
                "rate limit); click **Refresh now** to start it straight away.")
        return
    if len(top) < 100:
        st.warning(f"{len(top)} eligible traders so far for this window: the background sync is still working "
                   f"through the {stt['pool'] or 0} candidates ({stt['with_metrics']} analysed).")
    st.dataframe(_board_table(top), hide_index=True, width="stretch", height=min(36 * (len(top) + 1) + 3, 720),
                 column_config=_board_columns())
    with st.expander(f"Excluded candidates ({int((~allrows['eligible']).sum())})"):
        ex = allrows[~allrows["eligible"]][["account_id", "pnl", "exclusion"]].sort_values("pnl", ascending=False)
        st.dataframe(ex, hide_index=True, width="stretch", column_config={
            "account_id": "Account", "pnl": st.column_config.NumberColumn("PnL in window", format="$%,.0f"),
            "exclusion": st.column_config.TextColumn("Why excluded", width="large")})
    with st.expander("How this is calculated, and its limits"):
        st.markdown(METHOD_NOTES)


def _board_table(top: pd.DataFrame) -> pd.DataFrame:
    m = top["metrics_json"].map(json.loads)
    get = lambda k: m.map(lambda d: d.get(k))          # noqa: E731
    return pd.DataFrame({
        "Rank": top["rank"].values,
        "Venue": "HL",
        "Trader": [f"?trader={a}" for a in top["account_id"]],
        "Account value": get("account_value").values,
        "PnL": top["pnl"].values,
        "Return on equity": top["roe"].values,
        "Sharpe": top["sharpe"].values,
        "Sortino": top["sortino"].values,
        "Max drawdown": top["max_dd_pct"].values,
        "Max drawdown ($)": top["max_dd_usd"].values,
        "Profit factor": top["profit_factor"].values,
        "% weeks profitable": top["pct_weeks_profitable"].values,
        "Round trips": top["trips"].values,
        "Win rate": get("trip_win_rate").values,
        "Expectancy": get("trip_expectancy").values,
        "Avg win": get("trip_avg_win").values,
        "Avg loss": get("trip_avg_loss").values,
        "Fees": get("fees_paid").values,
        "Funding": get("funding").values,
        "Hold, winners (h)": get("trip_median_hold_win_h").values,
        "Hold, losers (h)": get("trip_median_hold_loss_h").values,
        "Longest losing streak": get("trip_longest_losing_streak").values,
        "Flags": top["flags"].values,
    })


def _board_columns() -> dict:
    N = st.column_config.NumberColumn
    return {
        "Trader": st.column_config.LinkColumn("Trader", display_text=r"\?trader=(0x[0-9a-f]+)",
                                              help="Opens the trader's review"),
        "Account value": N(format="$%,.0f"), "PnL": N(format="dollar"),
        "Return on equity": N(format="percent"), "Max drawdown": N(format="percent"),
        "Max drawdown ($)": N(format="dollar"), "Sharpe": N(format="%.2f"), "Sortino": N(format="%.2f"),
        "Profit factor": N(format="%.2f"), "% weeks profitable": N(format="percent"), "Win rate": N(format="percent"),
        "Expectancy": N(format="dollar"), "Avg win": N(format="dollar"), "Avg loss": N(format="dollar"),
        "Fees": N(format="$%,.0f"), "Funding": N(format="dollar"),
        "Hold, winners (h)": N(format="%.1f"), "Hold, losers (h)": N(format="%.1f"),
        "Flags": st.column_config.TextColumn(width="large"),
    }


METHOD_NOTES = """
- **Daily P&L** is mark-to-market: positions carried into a day are marked at the venue's daily close (00:00 UTC
  boundaries), plus each fill against that close, minus fees, plus funding. Over any period this equals realized
  P&L − fees + funding + the change in unrealized P&L.
- **Windows are whole UTC days** ending today. Hyperliquid's own week/month figures are rolling from the current
  minute, so short windows can differ from the venue's numbers by a day's price move; on a real wallet checked in the
  tests, 30-day P&L matches Hyperliquid's within 1%.
- **Return on equity** is time-weighted: each day's P&L divided by the account value at the end of the previous day
  (Hyperliquid's account-value history), compounded. Deposits and withdrawals change equity but are not returns.
  **Sharpe / Sortino** use those daily returns, annualized with √365; under ~20 days they are noisy.
- **History truncation:** Hyperliquid's free API keeps only each wallet's 10,000 most recent fills. Wallets that hit
  it (or whose fills cover less than 98% of the venue's all-time volume) are flagged "history truncated", and their
  long windows only cover the period the fills cover ("window only partly covered").
- **Position gaps:** if a position changes without a fill in the history (seen on a delisted builder perp), the trip
  is closed as "exit not observed" and counted on the trader page; its P&L can't be reconstructed.
- **Eligibility** uses the whole available history (at least 50 round trips and 10 active days), so a 7-day board
  still requires an established trader. Market makers: more than 2,000 fills per active day, or more than 80% maker
  fills with a sub-minute median hold, or more than 30% of fills quoted on both sides within a second. Vaults are
  identified from Hyperliquid's vault records.
- **Candidates** come from Hyperliquid's unofficial stats file (top 1,000 by PnL in each of its windows, accounts with no trading volume dropped). A trader
  outside those lists can't appear here; that's a selection bias of this board, shown here rather than hidden.
"""


# --------------------------------------------------------------------------- #
# Trader page
# --------------------------------------------------------------------------- #

@st.cache_data(ttl=300, show_spinner=False)
def _positions(address: str) -> pd.DataFrame:
    try:   # one quick attempt: this is a nice-to-have on a page that must load fast
        from .venues.hyperliquid import INFO
        r = venues.get("hyperliquid").session.post(INFO, json={"type": "clearinghouseState", "user": address}, timeout=8)
        r.raise_for_status()
        s = {"positions": [p["position"] for p in r.json().get("assetPositions", [])]}
    except Exception:  # noqa: BLE001
        return pd.DataFrame()
    rows = [{"coin": p["coin"], "size": float(p["szi"]), "entry_px": float(p.get("entryPx") or 0),
             "value_usd": float(p["positionValue"]), "unrealized_pnl": float(p["unrealizedPnl"]),
             "leverage": (p.get("leverage") or {}).get("value"),
             "liquidation_px": float(p["liquidationPx"]) if p.get("liquidationPx") else None} for p in s["positions"]]
    return pd.DataFrame(rows)


def render_trader_page(con, rcon, cfg, qp_trader: str | None) -> None:
    analysed = pd.read_sql_query("SELECT DISTINCT account_id FROM tr_metrics WHERE venue='hyperliquid' AND eligible=1",
                                 rcon)["account_id"].tolist()
    sources = ["Leaderboard trader", "Random draw (original rule)"]
    src = st.radio("Trader", sources, horizontal=True, index=0 if (qp_trader or analysed) else 1,
                   label_visibility="collapsed", key="tr-src")
    if src == sources[1]:
        render_random_draw(con, cfg)
        return
    options = list(dict.fromkeys(([qp_trader.lower()] if qp_trader else []) + analysed))
    if not options:
        st.info("No leaderboard trader analysed yet; see Top 100.")
        return
    a, b = st.columns([3, 1], vertical_alignment="bottom")
    addr = a.selectbox("Account", options, key="tr-acct")
    if b.button("Sync this trader now", width="stretch"):
        with st.spinner("Fetching fills, funding and account history..."):
            lbm.sync_account(rcon, addr, "hyperliquid")
            lbm.compute_account(rcon, addr, "hyperliquid", lbm.Eligibility.from_cfg(cfg))
        st.rerun()
    fills = store.load(rcon, "tr_fills", "hyperliquid", addr)
    if fills.empty:
        st.info("This account hasn't been synced yet: click **Sync this trader now**.")
        return
    funding = store.load(rcon, "tr_funding", "hyperliquid", addr)
    trips, rep = reconstruct(fills, funding)
    sync = store.get_sync(rcon, "hyperliquid", addr, "fills")["notes"]
    snaps = store.load(rcon, "tr_account_snapshots", "hyperliquid", addr)
    acct_now = float(snaps.sort_values("ts")["account_value"].iloc[-1]) if not snaps.empty else np.nan
    st.markdown(f"Trader: `{addr}` ([view on Hyperliquid](https://app.hyperliquid.xyz/explorer/address/{addr}))")
    view = st.segmented_control("Trader view", ["Review", "Diagnosis", "Backtest & Improve", "Data quality"],
                                default="Review",
                                key="tr-view", label_visibility="collapsed") or "Review"
    if view == "Review":
        render_review(to_legacy(trips) if not trips.empty else pd.DataFrame(), fills["ts"], acct_now, _positions(addr),
                      note=f"Includes funding ({money(trips['funding'].sum() if not trips.empty else 0)} net). "
                           "Actual results only; simulated ones are on Backtest & Improve, labelled.")
    elif view == "Diagnosis":
        from . import ui_diagnosis
        ui_diagnosis.render(rcon, addr, trips)
    elif view == "Backtest & Improve":
        from . import ui_backtest, ui_diagnosis
        from .review import costs
        closed = trips[~trips["is_open"] & ~trips["exit_unobserved"]] if not trips.empty else trips
        ui_backtest.render(rcon, addr, len(closed), costs.own_fee_rates(fills), ui_diagnosis.result(rcon, addr, trips))
    else:
        from . import ui_diagnosis
        render_data_quality(fills, funding, trips, rep, sync, ui_diagnosis.result(rcon, addr, trips))


def render_data_quality(fills, funding, trips, rep, sync, diag: dict | None = None) -> None:
    from . import ui_diagnosis
    with st.container():
        cov = sync.get("volume_coverage")
        rows = [
            ("Fills stored", f"{len(fills):,}", "Hyperliquid's free API keeps the 10,000 most recent per wallet."),
            ("History", f"{fills['ts'].min():%d %b %Y} to {fills['ts'].max():%d %b %Y}", ""),
            ("History truncated", "Yes" if sync.get("history_truncated") else "No",
             "Yes when the wallet hit the 10,000-fill cap or our fills cover < 98% of its all-time volume."),
            ("Volume covered", f"{cov:.1%}" if isinstance(cov, (int, float)) and cov == cov else "n/a",
             "Our fills' notional / Hyperliquid's all-time volume for the account."),
            ("Fills of a position opened before the history", f"{rep['excluded_fills']:,}",
             f"Realized P&L {money(rep['excluded_realized_pnl'])} on them is excluded from trips."),
            ("Positions that changed without a fill", f"{len(rep.get('position_gaps', [])):,}",
             "The open trip is closed as 'exit not observed'; its P&L can't be reconstructed."),
            ("Funding payments", f"{len(funding):,}",
             f"{rep['unattributed_funding_n']} ({money(rep['unattributed_funding_usd'])}) outside any reconstructed trip."),
            ("Liquidations (as the liquidated account)", f"{rep['liquidations']:,}", ""),
            ("Spot fills skipped", f"{rep['spot_fills_skipped']:,}", "This review covers perps only."),
            ("Order-book history", "Not available",
             "No free historical book: slippage is a proxy (taker fill vs the previous 1-minute close)."),
        ]
        if diag:
            rows += ui_diagnosis.quality_rows(diag)
        else:
            rows.append(("Market data behind the diagnosis", "Not loaded yet", "Opening the Diagnosis view loads it."))
        st.table(pd.DataFrame(rows, columns=["Check", "Value", "Note"]).set_index("Check"))
        if rep.get("position_gaps"):
            st.caption("Positions that changed without a fill (trips ending here are left out of the statistics):")
            st.dataframe(pd.DataFrame(rep["position_gaps"]).rename(columns={
                "coin": "Market", "before": "Noticed at the fill of", "expected": "Position expected",
                "found": "Position the venue reported"}), hide_index=True, width="stretch")


def render_random_draw(con, cfg) -> None:
    """The original single-trader review, unchanged in behaviour."""
    st.caption("A profitable Hyperliquid trader with a small account, picked by a fixed rule, reviewed trade by "
               "trade: round trips rebuilt from fills, performance stats, and recurring mistakes turned into rules.")
    tri = views.trade_review_inputs()
    with st.expander("Choose the trader"):
        seed = st.number_input("Draw number (seed)", value=int(cfg["trade_review"]["seed"]), step=1,
                               help="Same number = same trader every time. Change it to draw someone else from the same pool.")
        if st.button("Draw and review"):
            cfg2 = {**cfg, "trade_review": {**cfg["trade_review"], "seed": int(seed), "address": ""}}
            with st.spinner("Fetching the leaderboard and the trader's fills..."):
                pipeline.update_trade_review(con, cfg2)
            st.rerun()
        st.caption("To keep a trader permanently, paste their address under trade_review > address in config.yaml.")
    if tri is None or tri["fills"].empty:
        st.info("No trader reviewed yet. Run **Update all data**, or use **Choose the trader** above.")
        return
    sel, fills = tri["selection"], tri["fills"]
    addr = sel["address"]
    st.markdown(f"Trader: `{addr}` ([view on Hyperliquid](https://app.hyperliquid.xyz/explorer/address/{addr}))")
    how = str(sel.get("how", "n/a")).replace("$", "\\$")
    st.caption(f"Selected by {how}.")
    acct = storage.load_series(con, f"hl_account_value:{addr}")
    acct_hist = storage.load_series(con, f"hl_value_hist:{addr}")
    acct_now = next((v for v in (acct.iloc[-1] if not acct.empty else 0, acct_hist.iloc[-1] if not acct_hist.empty else 0,
                                 sel.get("account_value") or 0) if v and v > 0), np.nan)
    render_review(tr.round_trips(fills), fills["time"], acct_now, tri["positions"],
                  note="Funding isn't included for this trader (the original review stores fills only).")


def render_review(trips: pd.DataFrame, fill_times: pd.Series, acct_now: float, positions: pd.DataFrame,
                  note: str = "") -> None:
    """The per-trader review (metrics, equity, rules, breakdowns, trips)."""
    closed = trips[~trips["open"]] if not trips.empty else trips
    if "exit_unobserved" in closed:          # P&L unknown: listed in Data quality, kept out of the stats
        closed = closed[~closed["exit_unobserved"]]
    stt = tr.stats(closed)
    m = st.columns(6)
    m[0].metric("Account value", money(acct_now))
    m[1].metric("Closed trades", f"{stt.get('trades', 0):,}")
    m[2].metric("Win rate", f"{stt.get('win_rate', np.nan):.0%}" if stt else "n/a")
    m[3].metric("Profit factor", f"{stt.get('profit_factor', np.nan):.2f}" if stt else "n/a")
    m[4].metric("Net P&L (after fees)", money(stt.get("net_pnl", 0)))
    m[5].metric("Max drawdown", money(stt.get("max_drawdown", 0)))
    st.caption(f"{len(fill_times):,} fills from {fill_times.min():%d %b %Y} to {fill_times.max():%d %b %Y} "
               f"(Hyperliquid keeps each account's 10,000 most recent fills). {note}")
    if stt:
        m2 = st.columns(6)
        m2[0].metric("Expectancy / trade", money(stt["expectancy"], 2))
        m2[1].metric("Average win", money(stt["avg_win"], 2))
        m2[2].metric("Average loss", money(stt["avg_loss"], 2))
        m2[3].metric("Fees", money(stt["fees"]),
                     help="Share of gross profit: " + (f"{stt['fee_share_of_gross_profit']:.0%}" if pd.notna(stt['fee_share_of_gross_profit']) else "n/a"))
        m2[4].metric("Median hold, winners", tr.duration(stt["median_hold_win_h"]))
        m2[5].metric("Median hold, losers", tr.duration(stt["median_hold_loss_h"]))
        st.caption(f"Longest losing streak: {stt['longest_losing_streak']} trades. Markets named 'xyz:...' are "
                   "Hyperliquid's builder-deployed perps on stocks, indices and commodities (e.g. xyz:SP500).")
        eqc = closed.sort_values("close_time")
        curve = eqc["net"].cumsum()
        dd = curve - curve.cummax()
        f = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.05,
                          subplot_titles=["Cumulative net P&L ($)", "Drawdown ($)"])
        f.add_trace(go.Scatter(x=eqc["close_time"], y=curve, name="Net P&L", line=dict(color=BLUE, width=2)), 1, 1)
        f.add_trace(go.Scatter(x=eqc["close_time"], y=dd, name="Drawdown", fill="tozeroy", line=dict(color=ORANGE, width=2)), 2, 1)
        f.update_annotations(font_size=13, x=0, xanchor="left")
        chart_layout(f, 420).update_layout(showlegend=False)
        st.plotly_chart(f, width="stretch")

        st.markdown("**Recurring errors and the rule that fixes each**")
        errs = tr.recurring_errors(closed)
        if errs.empty:
            st.caption("No recurring pattern crossed the thresholds.")
        else:
            st.dataframe(errs, hide_index=True, width="stretch", column_config={
                "pattern": "Pattern", "finding": "What the data shows", "rule": st.column_config.TextColumn("Rule", width="large"),
                "impact_usd": st.column_config.NumberColumn("P&L of flagged trades ($)", format="%+,.2f",
                                                            help="Negative = what following the rule would have saved."),
                "trades_flagged": st.column_config.NumberColumn("Trades")})
        b = tr.add_buckets(closed)
        tables = []
        for key, title in [("coin", "By market"), ("entry_hour_utc", "By entry time (UTC)"),
                           ("hold", "By holding time"), ("size_quintile", "By position size")]:
            bd = tr.breakdown(b, key)
            tables.append((title, bd if key == "coin" else bd.sort_index()))
        for r in range(0, 4, 2):                      # two per row, same height, so the pair lines up
            pair = tables[r:r + 2]
            h = 36 * (max(len(t) for _, t in pair) + 1) + 3
            cols = st.columns(2)
            for col, (title, bd) in zip(cols, pair):
                col.markdown(f"**{title}**")
                col.dataframe(bd.rename_axis(title.replace("By ", "").capitalize()), width="stretch",
                              height=min(h, 400), column_config={
                                  "trades": "Trades", "win_rate": st.column_config.NumberColumn("Win rate", format="%.2f"),
                                  "expectancy": st.column_config.NumberColumn("Expectancy ($)", format="%+,.2f"),
                                  "net": st.column_config.NumberColumn("Net ($)", format="%+,.2f")})
        sc = go.Figure()
        for name, part, color in (("Winners", closed[closed["net"] > 0], BLUE), ("Losers", closed[closed["net"] <= 0], ORANGE)):
            sc.add_trace(go.Scatter(x=part["hold_hours"], y=part["return_on_notional"] * 100, mode="markers",
                                    name=name, marker=dict(color=color, size=8, line=dict(width=1, color="white")),
                                    text=part["coin"], hovertemplate="%{text}<br>%{x:.1f}h, %{y:.2f}%<extra></extra>"))
        sc.update_xaxes(type="log", title_text="Holding time (hours, log scale)")
        sc.update_yaxes(title_text="Return on position size (%)")
        st.markdown("**Holding time vs result**")
        st.plotly_chart(chart_layout(sc, 360), width="stretch")
    if positions is not None and not positions.empty:
        st.markdown("**Open positions now**")
        st.dataframe(positions, hide_index=True, width="stretch")
    if not trips.empty:
        st.markdown("**Recent round trips**")
        show_cols = [c for c in ["coin", "side", "open_time", "close_time", "hold_hours", "avg_entry", "avg_exit",
                                 "max_notional", "net", "fees", "funding", "adds", "added_while_losing", "liquidated",
                                 "exit_unobserved"] if c in trips]
        st.dataframe(trips.sort_values("open_time", ascending=False)[show_cols].head(100), hide_index=True, width="stretch",
                     column_config={"coin": "Market", "side": "Side",
                                    "open_time": st.column_config.DatetimeColumn("Opened (UTC)", format="YYYY-MM-DD HH:mm"),
                                    "close_time": st.column_config.DatetimeColumn("Closed (UTC)", format="YYYY-MM-DD HH:mm"),
                                    "avg_entry": st.column_config.NumberColumn("Avg entry", format="%,.4g"),
                                    "avg_exit": st.column_config.NumberColumn("Avg exit", format="%,.4g"),
                                    "fees": st.column_config.NumberColumn("Fees ($)", format="%,.2f"),
                                    "funding": st.column_config.NumberColumn("Funding ($)", format="%+,.2f"),
                                    "adds": "Adds", "added_while_losing": "Added while losing",
                                    "liquidated": "Liquidated", "exit_unobserved": "Exit not observed",
                                    "net": st.column_config.NumberColumn("Net ($)", format="%+,.2f"),
                                    "max_notional": st.column_config.NumberColumn("Max size", format="$%,.0f"),
                                    "hold_hours": st.column_config.NumberColumn("Hold (h)", format="%.2f")})
