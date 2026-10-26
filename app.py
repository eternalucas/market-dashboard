"""Market dashboard: one screen for the attention and positioning data I used
to check by hand across a dozen websites.

Start it with the "Start Dashboard" launcher, or: streamlit run app.py
"""
from __future__ import annotations


import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src import events as ev
from src import flows, pipeline, recorder, storage, ui_flows, views  # noqa: F401
from src.collectors import onchain as onchain_api
from src.indicators import onchain as oc
from src.config import load_config
from src.indicators import positioning as pos

st.set_page_config(page_title="Market Dashboard", layout="wide")

# Categorical colours, fixed order (validated colour-blind-safe palette).
BLUE, ORANGE, AQUA, YELLOW, RED = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e34948"
SERIES = [BLUE, ORANGE, AQUA, YELLOW]
# Exchange colours stay fixed whichever exchanges are on screen.
EXCHANGE_COLORS = {"Coinbase": BLUE, "Binance": RED, "Bybit": AQUA, "OKX": YELLOW, "Hyperliquid": ORANGE}
CATEGORY_NAMES = {"exchange": "Exchange", "market_maker": "Market maker", "whale": "Whale", "government": "Government",
                  "hack_or_recovery": "Hack / recovery", "issuer_treasury": "Issuer treasury"}
CATEGORY_COLORS = {"Exchange": BLUE, "Whale": ORANGE, "Government": AQUA, "Hack / recovery": YELLOW,
                   "Issuer treasury": "#e87ba4", "Market maker": "#6250d6"}


def money(x, dp=0):
    """$1,234 / -$1,234 (sign before the dollar sign)."""
    if x is None or pd.isna(x):
        return "n/a"
    return f"{'-' if x < 0 else ''}${abs(x):,.{dp}f}"
BAND_FILL = {"Elevated": "rgba(237,161,0,0.14)", "Extreme": "rgba(227,73,72,0.16)"}

cfg = load_config()
con = storage.connect()


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #

def pct(x, dp=2):
    return "n/a" if x is None or pd.isna(x) else f"{x * 100:.{dp}f}%"


def usd_bn(x):
    return "n/a" if x is None or pd.isna(x) else f"${x / 1e9:.2f}bn"


def usd_m(x):
    return "n/a" if x is None or pd.isna(x) else f"${x / 1e6:.1f}m"


def tab_error(tab: str, e: Exception) -> None:
    """Show a short message instead of a crash, so the other tabs keep working.
    The full error is in the expander for debugging."""
    st.error(f"This section hit an error and was skipped: {type(e).__name__}: {e}")
    with st.expander("Technical details"):
        st.exception(e)


def chart_layout(fig, height=420):
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    return fig


# --------------------------------------------------------------------------- #
# Sidebar: update controls
# --------------------------------------------------------------------------- #

@st.cache_resource
def _start_recorder() -> str:
    # One background recorder per dashboard process; skipped when "Start
    # Recorder.command" is already recording (it holds the lock).
    return recorder.start_background()


_rec_state = _start_recorder()

with st.sidebar:
    st.title("Market Dashboard")
    include_trends = st.checkbox("Include Google Trends", value=True,
                                 help="Google rate-limits Trends. Untick it if updates are slow or failing.")
    full = st.checkbox("Full refresh", value=False,
                       help="Normally, sources that already updated recently are skipped (daily ones for 20 "
                            "hours, hourly ones for 55 minutes), so a repeat update takes about a minute. "
                            "Tick this to re-fetch everything (10+ minutes).")
    if st.button("Update all data", type="primary", width="stretch"):
        with st.status("Updating data...", expanded=True) as status:
            pipeline.run_all(cfg, include_trends=include_trends, progress=status.write, full=full)
            status.update(label="Update finished", state="complete")
        st.cache_data.clear()
    log = storage.fetch_log(con, limit=1)
    if not log.empty:
        st.caption(f"Last fetch: {pd.to_datetime(log['ts'].iloc[0]).strftime('%Y-%m-%d %H:%M UTC')}")
    st.caption("While the dashboard (or Start Recorder) is open, the recorder refreshes every source on its own, "
               "including the daily App Store and YouTube readings the Social Risk history builds from.")

last_full = max((t for t in (storage.last_success(con, "src:social"), storage.last_success(con, "src:coinmetrics"))
                 if t is not None), default=None)
if last_full is None:
    st.info("No data yet: click **Update all data** in the left sidebar. The first run takes several minutes.")
elif pd.Timestamp.now(tz="UTC") - last_full > pd.Timedelta(hours=20):
    days_old = (pd.Timestamp.now(tz="UTC") - last_full).days
    st.warning(f"Data was last updated {last_full:%a %d %b %H:%M} UTC"
               f"{f' ({days_old} days ago)' if days_old >= 1 else ''}. Click **Update all data** in the sidebar "
               "for today's numbers; daily updates also build the history some panels need.")

# Social Risk now lives in Studio (Indicators > Social Risk).
tab_studio, tab_pos, tab_flow, tab_chain, tab_events, tab_review, tab_health = st.tabs(
    ["Studio", "Positioning", "Flows", "On-chain", "Macro events", "Trade review", "Data health"])

with tab_studio:
    try:
        from src import ui_studio
        ui_studio.render(con, cfg)
    except Exception as e:  # noqa: BLE001 - one broken tab must not blank the others
        tab_error("tab_studio", e)

# --------------------------------------------------------------------------- #
# Positioning tab
# --------------------------------------------------------------------------- #


@st.cache_data(ttl=60, show_spinner="Fetching live exchange data...")
def live_positioning(asset: str) -> dict:
    c = storage.connect()
    try:
        return pipeline.positioning_snapshot(c, cfg, asset)
    finally:
        c.close()


with tab_pos:
    try:
        top = st.columns([1, 1, 4])
        asset = top[0].selectbox("Asset", cfg["assets"])
        if top[1].button("Refresh live", width="stretch"):
            live_positioning.clear()
        live = live_positioning(asset)
        summ = live.get("summary") or {}
        skew = live.get("skew") or {}

        st.subheader(f"{asset} spot vs futures positioning")
        m = st.columns(5)
        m[0].metric("Funding (OI-weighted, 8h)", pct(summ.get("oi_weighted_funding_8h"), 4),
                    help="What longs pay shorts every 8 hours, averaged across exchanges by open interest.")
        m[1].metric("Funding, annualised", pct(summ.get("oi_weighted_funding_annual"), 1))
        m[2].metric("Open interest (total)", usd_bn(summ.get("total_oi_usd")))
        m[3].metric("Perp basis vs spot", pct(summ.get("oi_weighted_basis"), 3),
                    help="Perp price premium over the spot index. Positive = leverage demand on the long side.")
        m[4].metric("25d risk reversal", pct(skew.get("risk_reversal"), 1) if skew else "n/a",
                    help="IV of 25-delta calls minus 25-delta puts (Deribit). Negative = paying up for downside protection.")

        if live["snaps"]:
            tbl = pd.DataFrame(live["snaps"]).assign(
                basis_bps=lambda d: (d["mark"] / d["index"] - 1) * 1e4,
                funding_8h_pct=lambda d: d["funding_8h"] * 100,
                funding_annual_pct=lambda d: pos.annualize_funding(d["funding_8h"]) * 100,
                oi_bn=lambda d: d["oi_usd"] / 1e9,
            )[["exchange", "mark", "index", "basis_bps", "funding_8h_pct", "funding_annual_pct", "oi_bn"]]
            st.dataframe(tbl, hide_index=True, width="stretch", column_config={
                "mark": st.column_config.NumberColumn("Mark", format="%.2f"),
                "index": st.column_config.NumberColumn("Spot index", format="%.2f"),
                "basis_bps": st.column_config.NumberColumn("Basis (bps)", format="%.1f"),
                "funding_8h_pct": st.column_config.NumberColumn("Funding 8h %", format="%.4f"),
                "funding_annual_pct": st.column_config.NumberColumn("Funding ann. %", format="%.1f"),
                "oi_bn": st.column_config.NumberColumn("OI ($bn)", format="%.2f"),
            })
        if live["errors"]:
            st.caption("Unavailable right now: " + ", ".join(sorted(live["errors"])) + " (details in Data health).")

        left, right = st.columns(2)

        with left:
            st.markdown("**Funding history (8h rate)**")
            hist = storage.load_prefix(con, "funding_hist_8h:")
            hist = {k.split(":")[1]: v for k, v in hist.items() if k.endswith(f":{asset}")}
            if hist:
                f = go.Figure()
                for i, (ex, s) in enumerate(sorted(hist.items())):
                    s = s[s.index >= s.index.max() - pd.Timedelta(days=60)]
                    f.add_trace(go.Scatter(x=s.index, y=s * 100, name=ex, line=dict(color=SERIES[i % 4], width=2)))
                f.add_hline(y=0.01, line_dash="dot", line_color="gray", annotation_text="0.01% baseline")
                f.update_yaxes(title_text="% per 8h")
                st.plotly_chart(chart_layout(f, 340), width="stretch")
                ref_ex = sorted(hist)[0]
                z = pos.zscore_latest(hist[ref_ex])
                if not pd.isna(z):
                    st.caption(f"Latest {ref_ex} funding is {z:+.1f} standard deviations from its last-90-payments average.")
            else:
                st.caption("No funding history yet. Run **Update all data**.")

        with right:
            st.markdown("**Open interest history (coins)**")
            oi = storage.load_prefix(con, "oi_coin_hist:")
            oi = {k.split(":")[1]: v for k, v in oi.items() if k.endswith(f":{asset}")}
            if oi:
                f = go.Figure()
                for i, (ex, s) in enumerate(sorted(oi.items())):
                    f.add_trace(go.Scatter(x=s.index, y=s, name=ex, line=dict(color=SERIES[i % 4], width=2)))
                f.update_yaxes(title_text=f"OI ({asset})")
                st.plotly_chart(chart_layout(f, 340), width="stretch")
                st.caption("In coins rather than dollars, so a price rally alone doesn't look like new positioning.")
            else:
                st.caption("No open-interest history yet.")

        st.markdown("**Spot order book: depth and imbalance**")
        if live.get("depth") is not None:
            meta = live["book_meta"]
            st.caption(f"{meta['venue'].title()} {asset}-USD | mid {meta['mid']:,.2f} | spread {meta['spread_bps']:.2f} bps | "
                       f"book returned reaches {meta['bid_reach_pct']:.2f}% below and {meta['ask_reach_pct']:.2f}% above mid")
            d = live["depth"].assign(bid=lambda x: x["bid_usd"].map(usd_m), ask=lambda x: x["ask_usd"].map(usd_m))
            bl, br = st.columns([2, 3])
            bl.dataframe(d[["band_pct", "bid", "ask", "imbalance", "complete"]], hide_index=True, width="stretch",
                         column_config={"band_pct": st.column_config.NumberColumn("Within ±%", format="%.1f"),
                                        "imbalance": st.column_config.NumberColumn("Imbalance", format="%+.2f",
                                            help="(bids - asks) / (bids + asks). Negative = more resting supply."),
                                        "complete": st.column_config.CheckboxColumn("Full band",
                                            help="Unticked = the exchange's book snapshot doesn't reach this far.")})
            bids, asks = live["book"]
            curve = pos.depth_curve(bids, asks, max(cfg["positioning"]["depth_bands_pct"]))
            f = go.Figure()
            for side, color in (("bid", BLUE), ("ask", ORANGE)):
                c = curve[curve["side"] == side]
                f.add_trace(go.Scatter(x=c["dist_pct"], y=c["cum_usd"] / 1e6, name=f"{side}s", line=dict(color=color, width=2),
                                       fill="tozeroy", line_shape="hv"))
            f.update_xaxes(title_text="Distance from mid (%)")
            f.update_yaxes(title_text="Cumulative depth ($m)")
            br.plotly_chart(chart_layout(f, 300), width="stretch")
        else:
            st.caption("No order book available right now.")

        st.markdown("**Options skew (Deribit)**")
        if skew:
            s1, s2, s3, s4 = st.columns(4)
            s1.metric("Expiry used", f"{skew['expiry']:%d %b %y} ({skew['days_to_expiry']:.0f}d)")
            s2.metric("ATM IV", pct(skew["atm_iv"], 1))
            s3.metric("25d call IV", pct(skew["call25_iv"], 1))
            s4.metric("25d put IV", pct(skew["put25_iv"], 1))
        elif asset not in ("BTC", "ETH"):
            st.caption("Deribit's SOL options are thin and USDC-settled, so skew covers BTC and ETH only.")
        else:
            st.caption("Options data unavailable right now.")
        rr = storage.load_series(con, f"skew_rr25:{asset}")
        if len(rr) > 1:
            f = go.Figure(go.Scatter(x=rr.index, y=rr * 100, name="25d RR", line=dict(color=BLUE, width=2)))
            f.add_hline(y=0, line_color="gray")
            f.update_yaxes(title_text="vol points")
            st.plotly_chart(chart_layout(f, 260), width="stretch")
            st.caption("Skew history builds from each refresh.")

        opts = live.get("options")
        if opts is not None and not opts.empty:
            st.markdown("**Options open interest: net delta and strikes (Deribit)**")
            greeks = pos.options_with_delta(opts, pd.Timestamp.now(tz="UTC"))
            by_exp = pos.net_delta_by_expiry(greeks)
            o1, o2, o3 = st.columns(3)
            o1.metric("Net delta, all expiries", f"{greeks['delta_oi'].sum():+,.0f} {asset}",
                      help="Sum of open interest x delta: the exposure option holders carry in aggregate. "
                           "Positive = holders net positioned for upside.")
            o2.metric("Put/call OI ratio", f"{greeks.loc[greeks['type']=='P','oi'].sum() / max(greeks.loc[greeks['type']=='C','oi'].sum(), 1e-9):.2f}")
            o3.metric("Open interest", f"{greeks['oi'].sum():,.0f} {asset}")
            el, er = st.columns([2, 3])
            show = by_exp.head(10).reset_index()
            show["expiry"] = show["expiry"].dt.strftime("%d %b %y")
            el.dataframe(show, hide_index=True, width="stretch", column_config={
                "call_oi": st.column_config.NumberColumn("Call OI", format="%,.0f"),
                "put_oi": st.column_config.NumberColumn("Put OI", format="%,.0f"),
                "net_delta": st.column_config.NumberColumn("Net delta", format="%+,.0f"),
                "put_call_ratio": st.column_config.NumberColumn("P/C", format="%.2f")})
            exp_pick = er.selectbox("Expiry for strike chart", by_exp.index,
                                    index=int(by_exp["call_oi"].add(by_exp["put_oi"]).values.argmax()),
                                    format_func=lambda e: f"{e:%d %b %y}")
            strikes = pos.oi_by_strike(greeks, exp_pick)
            fwd = float(greeks.loc[greeks["expiry"] == exp_pick, "forward"].median())
            strikes = strikes[(strikes.index > fwd * 0.6) & (strikes.index < fwd * 1.4)]
            f = go.Figure()
            f.add_trace(go.Bar(x=strikes.index, y=strikes.get("C", 0), name="Calls", marker_color=BLUE))
            f.add_trace(go.Bar(x=strikes.index, y=-strikes.get("P", 0), name="Puts (shown below zero)", marker_color=ORANGE))
            f.add_vline(x=fwd, line_dash="dot", line_color="gray", annotation_text="forward")
            f.update_layout(barmode="relative", bargap=0.15)
            f.update_yaxes(title_text=f"Open interest ({asset})")
            er.plotly_chart(chart_layout(f, 300), width="stretch")
    except Exception as e:  # noqa: BLE001 - one broken tab must not blank the others
        tab_error("tab_pos", e)


# --------------------------------------------------------------------------- #
# Flows tab (cross-exchange order flow, laid out like Aggr / Velo; see src/ui_flows.py)
# --------------------------------------------------------------------------- #

with tab_flow:
    try:
        ui_flows.render(con, cfg)
    except Exception as e:  # noqa: BLE001 - one broken tab must not blank the others
        tab_error("tab_flow", e)


# --------------------------------------------------------------------------- #
# On-chain tab
# --------------------------------------------------------------------------- #

with tab_chain:
    try:
        st.subheader("Watched wallets")
        st.caption("Exchanges, market makers and every Bitcoin wallet holding 10,000+ BTC, from public labels "
                   "(Etherscan name tags, the bitinfocharts rich list). Balances and transfers come from public "
                   "explorers (mempool.space, Blockscout). Edit watchlist.yaml to add wallets, e.g. ones you see on Arkham.")
        wl = onchain_api.load_watchlist()
        wl["category"] = wl["category"].map(CATEGORY_NAMES).fillna(wl["category"])
        btc_px = storage.load_series(con, "price:BTC")
        eth_px = storage.load_series(con, "price:ETH")
        px_now = {"btc": btc_px.iloc[-1] if not btc_px.empty else np.nan,
                  "eth": eth_px.iloc[-1] if not eth_px.empty else np.nan}
        k1, k2, k3 = st.columns(3)
        k1.metric("Wallets watched", f"{len(wl)}", help="Bitcoin and Ethereum")
        k2.metric("Failing checksum", f"{int((~wl['valid']).sum())}")
        k3.metric("Entities", f"{wl['entity'].nunique()}")

        rows = []
        for w in wl.itertuples():
            bal = storage.load_series(con, f"onchain_bal:{w.chain}:{w.address}")
            latest = bal.iloc[-1] if not bal.empty else np.nan
            def chg(days):
                """Change vs the latest reading at least `days` old; NaN until one exists."""
                past = bal[bal.index <= bal.index[-1] - pd.Timedelta(days=days)] if not bal.empty else bal
                return latest - past.iloc[-1] if len(past) else np.nan
            prev = latest - bal.iloc[-2] if len(bal) >= 2 else np.nan
            rows.append({"Label": w.label, "Entity": w.entity, "Category": w.category, "Chain": w.chain.upper(),
                         "Balance": latest, "USD": latest * px_now.get(w.chain, np.nan),
                         "Since last update": prev, "Change 7d": chg(7), "Change 30d": chg(30), "Address": w.address})
        bals = pd.DataFrame(rows)
        n_days = max((len(storage.load_series(con, f"onchain_bal:{w.chain}:{w.address}")) for w in wl.head(5).itertuples()), default=0)
        if n_days < 2:
            st.caption("Balance changes appear once the dashboard has two daily updates on different days. "
                       "Run **Update all data** once a day.")
        if bals["Balance"].notna().any():
            st.markdown("**Balances by cohort**")
            cat = bals.dropna(subset=["Balance"]).groupby(["Chain", "Category"]).agg(
                wallets=("Label", "size"), balance=("Balance", "sum"), usd=("USD", "sum"),
                change=("Since last update", lambda x: x.sum(min_count=1))).reset_index()
            st.dataframe(cat, hide_index=True, width="stretch", column_config={
                "wallets": "Wallets",
                "balance": st.column_config.NumberColumn("Balance (coins)", format="%,.0f"),
                "usd": st.column_config.NumberColumn("USD", format="$%,.0f"),
                "change": st.column_config.NumberColumn("Change since last update (coins)", format="%+,.0f")})
            hist = []
            for w in wl[wl["chain"] == "btc"].itertuples():
                b = storage.load_series(con, f"onchain_bal:btc:{w.address}")
                if len(b):
                    hist.append(b.rename(w.category))
            if hist and max(len(h) for h in hist) > 1:
                byc = pd.concat(hist, axis=1).T.groupby(level=0).sum().T
                f = go.Figure()
                for c in byc.columns:
                    f.add_trace(go.Scatter(x=byc.index, y=byc[c], name=c, line=dict(color=CATEGORY_COLORS.get(c, YELLOW), width=2)))
                f.update_yaxes(title_text="BTC held")
                st.plotly_chart(chart_layout(f, 320), width="stretch")
            st.markdown("**Every wallet**")
            st.dataframe(bals.sort_values("USD", ascending=False), hide_index=True, width="stretch", column_config={
                "Balance": st.column_config.NumberColumn(format="%,.2f"),
                "USD": st.column_config.NumberColumn(format="$%,.0f"),
                "Since last update": st.column_config.NumberColumn(format="%+,.2f"),
                "Change 7d": st.column_config.NumberColumn(format="%+,.2f"),
                "Change 30d": st.column_config.NumberColumn(format="%+,.2f")})
            st.caption("Blank 7-day and 30-day changes fill in once there are daily updates that far back.")
        else:
            st.info("No balances yet. Run **Update all data**.")

        labels = {a.lower(): e for a, e in zip(wl["address"], wl["entity"])}
        btc_tx = views.onchain_file("btc_txs.csv")
        if not btc_tx.empty:
            st.markdown("**Bitcoin: recent moves by watched wallets**")
            btc_tx["time"] = pd.to_datetime(btc_tx["time"], utc=True)
            meta = wl.set_index("address")
            btc_tx["Label"] = btc_tx["address"].map(meta["label"])
            btc_tx["Category"] = btc_tx["address"].map(meta["category"])
            btc_tx["USD"] = btc_tx["net_btc"] * px_now["btc"]
            btc_tx["Size"] = oc.size_cohort(btc_tx["USD"].abs())
            btc_tx["Counterparty"] = btc_tx["counterparties"].fillna("").map(
                lambda c: ", ".join(sorted({labels.get(a.lower(), "") for a in str(c).split()} - {""})) or "unlabelled")
            recent = btc_tx[btc_tx["time"] >= pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=30)]
            daily = recent.groupby([recent["time"].dt.date, "Category"])["net_btc"].sum().unstack(fill_value=0)
            if not daily.empty:
                f = go.Figure()
                for c in daily.columns:
                    f.add_trace(go.Bar(x=daily.index, y=daily[c], name=c, marker_color=CATEGORY_COLORS.get(c, YELLOW)))
                f.update_layout(barmode="relative")
                f.update_yaxes(title_text="Net BTC in (+) / out (-)")
                st.plotly_chart(chart_layout(f, 300), width="stretch")
            a1, a2 = st.columns([3, 2])
            a1.dataframe(recent.sort_values("time", ascending=False)[["time", "Label", "Category", "net_btc", "USD", "Size", "Counterparty"]].head(50),
                         hide_index=True, width="stretch", column_config={
                             "time": st.column_config.DatetimeColumn("Time (UTC)", format="YYYY-MM-DD HH:mm"),
                             "net_btc": st.column_config.NumberColumn("Net BTC", format="%+,.2f"),
                             "USD": st.column_config.NumberColumn(format="dollar")})
            size_tbl = recent.groupby("Size", observed=False).agg(moves=("txid", "size"), usd=("USD", lambda x: x.abs().sum()))
            a2.dataframe(size_tbl.rename_axis("Move size"), width="stretch", column_config={
                "moves": st.column_config.NumberColumn("Moves"),
                "usd": st.column_config.NumberColumn("USD moved", format="$%,.0f")})
            act = btc_tx.groupby("address")["time"].max()
            cohorts = (oc.wallet_activity(act, pd.Timestamp.now(tz="UTC")).value_counts()
                       .reindex(["Active (7d)", "Recent (30d)", "Quiet (1y)", "Dormant (>1y)"]).fillna(0).astype(int))
            a2.dataframe(cohorts.rename_axis("Last activity").rename("Wallets"), width="stretch")
            a2.caption("Activity cohorts use each wallet's latest transaction in the data fetched so far.")

        eth_tr = views.onchain_file("eth_transfers.csv")
        if not eth_tr.empty:
            st.markdown("**Ethereum: flows between entities (ERC-20 tokens)**")
            eth_tr["time"] = pd.to_datetime(eth_tr["time"], utc=True)
            days = st.select_slider("Window", options=[1, 7, 30, 90], value=7, format_func=lambda d: f"{d}d", key="eth_win")
            win = eth_tr[eth_tr["time"] >= pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)].copy()
            # Moves between two wallets of the same firm are internal shuffles, not flows
            same = win["from"].str.lower().map(labels).fillna("a") == win["to"].str.lower().map(labels).fillna("b")
            win = win[~same]
            st.caption(f"{int(same.sum())} internal transfers (between two wallets of the same firm) are left out.")
            flows_tbl = oc.entity_flows(win, labels).rename(columns={"from_entity": "From", "to_entity": "To", "transfers": "Transfers"})
            e1, e2 = st.columns([3, 2])
            e1.dataframe(flows_tbl, hide_index=True, width="stretch",
                         column_config={"usd": st.column_config.NumberColumn("USD", format="$%,.0f")})
            mm = flows_tbl[flows_tbl["From"].isin(["Wintermute", "Jump Trading"]) | flows_tbl["To"].isin(["Wintermute", "Jump Trading"])]
            e2.markdown("Market-maker flows")
            e2.dataframe(mm, hide_index=True, width="stretch",
                         column_config={"usd": st.column_config.NumberColumn("USD", format="$%,.0f")})
            big = win[win["usd"] >= 1_000_000].assign(
                From=lambda d: d["from"].str.lower().map(labels).fillna(d["from"].str[:10] + "..."),
                To=lambda d: d["to"].str.lower().map(labels).fillna(d["to"].str[:10] + "..."))
            st.caption(f"Transfers of $1M or more in the window: {len(big)}")
            st.dataframe(big.sort_values("time", ascending=False)[["time", "From", "To", "token", "amount", "usd"]].head(50),
                         hide_index=True, width="stretch",
                         column_config={"time": st.column_config.DatetimeColumn("Time (UTC)", format="YYYY-MM-DD HH:mm"),
                                        "token": "Token",
                                        "usd": st.column_config.NumberColumn("USD", format="$%,.0f"),
                                        "amount": st.column_config.NumberColumn("Amount", format="%,.2f")})

        st.subheader("Hyperliquid trader cohorts")
        st.caption("Every Hyperliquid account is public. Active leaderboard accounts are grouped by account size; "
                   "a sample from each group is checked for open positions, showing how small and large traders lean.")
        lbc = views.onchain_file("hl_leaderboard.csv")
        hpos = views.onchain_file("hl_cohort_positions.csv")
        if lbc.empty:
            st.caption("No cohort data yet. Run **Update all data**.")
        else:
            order = oc.HL_LABELS
            sizes = lbc.groupby("cohort").agg(accounts=("address", "size"), total_value=("account_value", "sum"),
                                              month_pnl=("month_pnl", "sum")).reindex(order).dropna(how="all").rename_axis("Cohort")
            turnover = (pd.crosstab(lbc["cohort"], lbc["turnover"]).reindex(order).dropna(how="all")
                        .reindex(columns=["Low (<2x/mo)", "Medium (2-20x)", "High (>20x)"]).rename_axis("Cohort").rename_axis(None, axis=1))
            h1, h2 = st.columns(2)
            h1.dataframe(sizes, width="stretch", column_config={
            "accounts": st.column_config.NumberColumn("Accounts", format="%,d"),
                "total_value": st.column_config.NumberColumn("Account value", format="$%,.0f"),
                "month_pnl": st.column_config.NumberColumn("PnL this month", format="dollar")})
            h2.dataframe(turnover, width="stretch")
            h2.caption("Turnover = monthly volume / account size: how often each cohort trades.")
            if not hpos.empty:
                summ = oc.cohort_positioning(hpos)
                coin = st.selectbox("Coin", [c for c in cfg["onchain"]["cohort_coins"] if c in set(summ["coin"])] or sorted(summ["coin"].unique()))
                one = summ[summ["coin"] == coin].set_index("cohort").reindex(order).dropna(how="all").reset_index()
                f = go.Figure()
                f.add_trace(go.Bar(x=one["cohort"], y=one["long_usd"] / 1e6, name="Long", marker_color=BLUE))
                f.add_trace(go.Bar(x=one["cohort"], y=-one["short_usd"] / 1e6, name="Short (below zero)", marker_color=ORANGE))
                f.update_layout(barmode="relative")
                f.update_yaxes(title_text=f"{coin} positions ($m)")
                st.plotly_chart(chart_layout(f, 320), width="stretch")
                st.dataframe(one, hide_index=True, width="stretch", column_config={
                    "cohort": "Cohort", "coin": "Coin", "accounts": "Accounts",
                    "long_usd": st.column_config.NumberColumn("Long", format="$%,.0f"),
                    "short_usd": st.column_config.NumberColumn("Short", format="$%,.0f"),
                    "net_usd": st.column_config.NumberColumn("Net", format="dollar"),
                    "long_share": st.column_config.NumberColumn("Long share", format="percent")})
                st.caption(f"Sample: up to {cfg['onchain']['hl_cohort_sample']} most active accounts per cohort.")
    except Exception as e:  # noqa: BLE001 - one broken tab must not blank the others
        tab_error("tab_chain", e)

# --------------------------------------------------------------------------- #
# Macro events tab
# --------------------------------------------------------------------------- #

with tab_events:
    try:
        st.subheader("Macro event study")
        st.caption("How each asset moved in the days and weeks after past releases, to gauge the tone of the period "
                   "ahead. Day 1 = the event day, measured from the previous close. 'Excess' = the event's average "
                   "move minus the asset's typical move over the same number of days.")
        events_df, ev_prices, bp_assets = views.event_inputs(con, cfg)
        if events_df.empty or not ev_prices:
            st.info("No events yet. Add your FRED key to config.yaml (api_keys > fred), then run **Update all data**. "
                    "Central-bank dates (BoE, BoJ, PBoC) load even without it.")
        else:
            horizons = cfg["events"]["horizons_days"]
            c1, c2, c3 = st.columns(3)
            ev_asset = c1.selectbox("Asset", list(ev_prices), key="ev_asset")
            ev_names = sorted(events_df["event"].unique())
            ev_type = c2.selectbox("Event", ev_names, key="ev_type", index=ev_names.index("CPI") if "CPI" in ev_names else 0)
            ev_h = c3.selectbox("Horizon for the calendar", horizons, index=min(2, len(horizons) - 1),
                                format_func=lambda h: f"{h} day{'s' if h > 1 else ''}")
            in_bp = ev_asset in bp_assets
            unit = "bp" if in_bp else "%"
            fmt = (lambda v: f"{v:+.0f}bp") if in_bp else (lambda v: f"{v * 100:+.2f}%")
            past = events_df[events_df["date"] <= pd.Timestamp.now().normalize()]
            px = ev_prices[ev_asset]
            rt = ev.reaction_table(past, {ev_asset: px}, horizons, bp_assets)
            typical = {(ev_asset, h): ev.typical_move(px, h, in_bp) for h in horizons}

            st.markdown("**Coming up (next 30 days)**")
            today = pd.Timestamp.now().normalize()
            upcoming = events_df[(events_df["date"] >= today) & (events_df["date"] <= today + pd.Timedelta(days=30))]
            if upcoming.empty:
                st.caption("Nothing scheduled in the calendar for the next 30 days.")
            else:
                summ_all = ev.summarize(rt, typical, by_label=False) if not rt.empty else pd.DataFrame()
                def hist_for(e, col):
                    r = summ_all[(summ_all["event"] == e) & (summ_all["h"] == ev_h)] if not summ_all.empty else summ_all
                    return r[col].iloc[0] if len(r) else np.nan
                up = upcoming.assign(
                    Day=lambda d: d["date"].dt.strftime("%a %d %b"),
                    **{f"Avg {ev_asset} move ({ev_h}d)": lambda d: d["event"].map(lambda e: hist_for(e, "avg")),
                       "Excess vs typical": lambda d: d["event"].map(lambda e: hist_for(e, "excess")),
                       "Past events": lambda d: d["event"].map(lambda e: hist_for(e, "n"))})
                show = up.drop(columns=["date", "label"]).rename(columns={"event": "Event"})
                for col in (f"Avg {ev_asset} move ({ev_h}d)", "Excess vs typical"):
                    show[col] = show[col].map(lambda v: fmt(v) if pd.notna(v) else "n/a")
                st.dataframe(show[["Day", "Event", f"Avg {ev_asset} move ({ev_h}d)", "Excess vs typical", "Past events"]],
                             hide_index=True, width="stretch")

            if not rt.empty:
                st.markdown(f"**{ev_type}: {ev_asset} after past releases**")
                one = rt[rt["event"] == ev_type]
                summ = ev.summarize(one[one["label"] != "n/a"], typical, by_label=True) if (one["label"] != "n/a").any() else pd.DataFrame()
                summ_all1 = ev.summarize(one, typical, by_label=False).assign(label="All releases")
                summ = pd.concat([summ_all1, summ])
                tbl = summ.pivot_table(index="label", columns="h", values="avg")
                exc = summ.pivot_table(index="label", columns="h", values="excess")
                hit = summ.pivot_table(index="label", columns="h", values="pct_up")
                n = summ.groupby("label")["n"].max()
                out = pd.DataFrame(index=tbl.index)
                out["Events"] = n
                for h in horizons:
                    if h in tbl:
                        out[f"{h}d avg"] = tbl[h].map(fmt)
                        out[f"{h}d excess"] = exc[h].map(fmt)
                        out[f"{h}d % up"] = hit[h].map(lambda v: f"{v:.0%}")
                st.dataframe(out.rename_axis("Releases"), width="stretch")
                st.caption("Labels compare the release with the previous reading (consensus forecasts aren't free), "
                           "using FRED's current, revised data. FOMC: hike / cut / hold.")

                evs = past[past["event"] == ev_type]
                path = ev.average_path(evs, px, days=max(horizons), in_bp=in_bp)
                base = [ev.typical_move(px, h, in_bp) for h in path.index]
                f = go.Figure()
                for i, c in enumerate(path.columns):
                    y = path[c] if in_bp else path[c] * 100
                    f.add_trace(go.Scatter(x=path.index, y=y, name=c, line=dict(color=SERIES[i % 4], width=2)))
                f.add_trace(go.Scatter(x=path.index, y=base if in_bp else [b * 100 for b in base], name="Typical (any day)",
                                       line=dict(color="gray", width=2, dash="dot")))
                f.add_hline(y=0, line_color="gray", line_width=1)
                f.update_xaxes(title_text="Days after the event (1 = event day)")
                f.update_yaxes(title_text=f"Average cumulative move ({unit})")
                st.plotly_chart(chart_layout(f, 360), width="stretch")

                last = one.pivot_table(index=["date", "label"], columns="h", values="move").sort_index(ascending=False).head(12)
                last = last.apply(lambda col: col.map(fmt))
                last.columns = [f"{h}d" for h in last.columns]
                if (one["label"] == "n/a").all():
                    last = last.droplevel("label")   # this event type has no labels
                st.markdown("Most recent releases")
                st.dataframe(last.reset_index().assign(date=lambda d: d["date"].dt.strftime("%Y-%m-%d")),
                             hide_index=True, width="stretch")
                st.caption("When two events share a day (e.g. CPI and jobless claims), both get the same move.")
    except Exception as e:  # noqa: BLE001 - one broken tab must not blank the others
        tab_error("tab_events", e)

# --------------------------------------------------------------------------- #
# Trade review tab
# --------------------------------------------------------------------------- #

with tab_review:
    try:
        from src import ui_review
        ui_review.render(con, cfg)
    except Exception as e:  # noqa: BLE001 - one broken tab must not blank the others
        tab_error("tab_review", e)

# --------------------------------------------------------------------------- #
# Data health tab
# --------------------------------------------------------------------------- #

with tab_health:
    try:
        st.subheader("Data health")
        st.caption("Every fetch is logged. A failing source never stops the others; it shows up here with the error.")
        health = views.source_health(con)
        if health.empty:
            st.caption("Nothing fetched yet.")
        else:
            ok = (health["Status"] == "OK").sum()
            st.metric("Sources working", f"{ok} of {len(health)}")
            st.dataframe(health, hide_index=True, width="stretch")
        st.subheader("Recorders")
        st.caption({"started": "Recording in the background while the dashboard is open.",
                    "already running": "Recording in the background while the dashboard is open.",
                    "running elsewhere": "Start Recorder.command is recording in its own window."}.get(_rec_state, _rec_state)
                   + " Order books every minute, option trades every 5 minutes, futures curve every 15, "
                     "option chain hourly, IBIT options after each US close.")
        rs = recorder.status(con)
        if not rs.empty:
            st.dataframe(rs, hide_index=True, width="stretch")
    except Exception as e:  # noqa: BLE001 - one broken tab must not blank the others
        tab_error("tab_health", e)
