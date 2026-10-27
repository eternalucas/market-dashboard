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
from src.config import load_config
from src.indicators import positioning as pos

st.set_page_config(page_title="Market Dashboard", layout="wide")
# Every chart: billions as "B", never Plotly's SI "G" (see src/numfmt.py)
from src.numfmt import fix_si as _fix_si  # noqa: E402

if not getattr(st.plotly_chart, "_fix_si", False):
    _plotly_chart = st.plotly_chart

    def _plotly_chart_b(fig, *a, **k):
        return _plotly_chart(_fix_si(fig), *a, **k)
    _plotly_chart_b._fix_si = True
    st.plotly_chart = _plotly_chart_b
from src.running_icons import CSS as _RUNNING_CSS  # noqa: E402
st.markdown(_RUNNING_CSS, unsafe_allow_html=True)

# Categorical colours, fixed order (validated colour-blind-safe palette).
BLUE, ORANGE, AQUA, YELLOW, RED = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e34948"
SERIES = [BLUE, ORANGE, AQUA, YELLOW]
# Exchange colours stay fixed whichever exchanges are on screen.
EXCHANGE_COLORS = {"Coinbase": BLUE, "Binance": RED, "Bybit": AQUA, "OKX": YELLOW, "Hyperliquid": ORANGE}
# Release labels: hotter / stronger / hike in warm colours, cooler / weaker / cut in cool ones
LABEL_ORDER = ["Hotter", "Cooler", "Strong", "Weak", "Rising", "Falling", "Hike", "Hold", "Cut"]
LABEL_COLORS = {"All releases": "#f7f8fa", "Hotter": "#e34948", "Cooler": "#2a78d6", "Strong": "#eb6834",
                "Weak": "#1baf7a", "Rising": "#e34948", "Falling": "#2a78d6", "Hike": "#e34948", "Hold": "#eda100",
                "Cut": "#2a78d6"}
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
try:
    pipeline.fix_funding_intervals(con)   # one-off repair, no-op once it has run
    pipeline.fix_bybit_oi(con, cfg["assets"])
except Exception:  # noqa: BLE001
    pass


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
    from src.charts import fixed_layout
    return fixed_layout(fig, height)


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
from src import ui_studio  # noqa: E402
ui_studio.global_search()   # sits at the right end of the tab bar
tab_studio, tab_pos, tab_flow, tab_chain, tab_events, tab_review, tab_health = st.tabs(
    ["Studio", "Positioning", "Flows", "On-chain", "Macro events", "Trade review", "Data health"],
    key="main-tabs", on_change="rerun")

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


EX_NAMES = {"okx": "OKX", "bybit": "Bybit", "binance": "Binance", "hyperliquid": "Hyperliquid"}


def price_str(x):
    """Prices with enough decimals for any coin: 80,477.70 / 100.77 / 1.3202 / 0.078010."""
    if x is None or pd.isna(x) or x <= 0:
        return "n/a"
    dp = max(2, 5 - int(np.floor(np.log10(x))) - 1)
    return f"{x:,.{dp}f}"


def table_height(n_rows: int) -> int:
    """Exact height for n rows, so a table never shows empty rows."""
    return 35 * n_rows + 38


def pos_chart(fig, height):
    from src.charts import card_layout
    return card_layout(fig, height)


with tab_pos:
    try:
        from src.ui_studio import card
        top = st.columns([1, 1, 4], vertical_alignment="bottom")
        asset = top[0].selectbox("Asset", cfg["assets"])
        if top[1].button("Refresh live", width="stretch"):
            live_positioning.clear()
        live = live_positioning(asset)
        summ = live.get("summary") or {}
        skew = live.get("skew") or {}

        st.subheader(f"{asset} spot vs futures positioning")
        m = st.columns(5)
        m[0].metric("Funding (OI-weighted, 8h)", pct(summ.get("oi_weighted_funding_8h"), 4),
                    help="What longs pay shorts per 8 hours, averaged across exchanges by open interest. Rates from "
                         "contracts that pay hourly or every 4 hours are scaled to 8 hours first. 0.01% is the "
                         "standard rate exchanges charge when the perp trades close to spot.")
        m[1].metric("Funding, annualised", pct(summ.get("oi_weighted_funding_annual"), 1),
                    help="The 8-hour rate x 3 payments a day x 365 (simple, not compounded). 0.01% per 8h = 10.95% a year.")
        m[2].metric("Open interest (total)", usd_bn(summ.get("total_oi_usd")),
                    help="Sum of the open interest on the exchanges in the table below (one side, in USD).")
        m[3].metric("Perp basis vs spot", pct(summ.get("oi_weighted_basis"), 3),
                    help="Perp price premium over the spot index, OI-weighted. Positive = leverage demand on the long side.")
        m[4].metric("25d risk reversal", pct(skew.get("risk_reversal"), 1) if skew else "n/a",
                    help="IV of 25-delta calls minus 25-delta puts (Deribit). Negative = paying up for downside protection.")

        if live["snaps"]:
            snaps = pd.DataFrame(live["snaps"])
            tbl = pd.DataFrame({
                "Exchange": snaps["exchange"].map(EX_NAMES).fillna(snaps["exchange"]),
                "Mark": snaps["mark"].map(price_str),
                "Spot index": snaps["index"].map(price_str),
                "basis_bps": (snaps["mark"] / snaps["index"] - 1) * 1e4,
                "interval": snaps["interval_h"].map(lambda h: f"{h:g}h"),
                "rate_pct": snaps["funding_rate"] * 100,
                "funding_8h_pct": snaps["funding_8h"] * 100,
                "funding_annual_pct": pos.annualize_funding(snaps["funding_8h"]) * 100,
                "oi_bn": snaps["oi_usd"] / 1e9,
            })
            with card("pos-perps", f"{asset} perpetuals by exchange",
                      desc="Live snapshot. Exchanges quote funding per payment, and the payment interval differs "
                           "(Hyperliquid pays hourly; some contracts pay every 4h), so each rate is also shown scaled "
                           "to 8 hours and annualised for a like-for-like comparison. Spot index is each exchange's own "
                           "index (Hyperliquid: its oracle price). OI is one side, in USD.",
                      source="Binance, Bybit, OKX and Hyperliquid public APIs"):
                st.dataframe(tbl, hide_index=True, width="stretch", height=table_height(len(tbl)), column_config={
                    "basis_bps": st.column_config.NumberColumn("Basis (bps)", format="%.1f",
                                                               help="(mark / spot index - 1) in basis points."),
                    "interval": st.column_config.TextColumn("Pays every"),
                    "rate_pct": st.column_config.NumberColumn("Funding per payment %", format="%.5f",
                                                              help="The rate as the exchange quotes it, per payment."),
                    "funding_8h_pct": st.column_config.NumberColumn("Funding 8h %", format="%.4f",
                                                                    help="Per-payment rate x 8 / hours between payments."),
                    "funding_annual_pct": st.column_config.NumberColumn("Funding ann. %", format="%.2f",
                                                                        help="8h rate x 3 x 365."),
                    "oi_bn": st.column_config.NumberColumn("OI ($bn)", format="%.3f"),
                })
        if live["errors"]:
            st.caption("Unavailable right now: " + ", ".join(sorted(live["errors"])) + " (details in Data health).")

        left, right = st.columns(2)
        with left:
            hist = storage.load_prefix(con, "funding_hist_8h:")
            hist = {k.split(":")[1]: v for k, v in hist.items() if k.endswith(f":{asset}")}
            note = ""
            if hist and "binance" in hist:
                z = pos.zscore_latest(hist["binance"])
                if not pd.isna(z):
                    note = f" Latest Binance payment: {z:+.1f} SD vs its last 90."
            with card("pos-funding-hist", "Funding history (8h-equivalent rate)", paired=True,
                      desc="Each payment scaled to an 8-hour rate, last 60 days (OKX ~1 month, Hyperliquid 20 days). "
                           "Dotted line: the standard 0.01%." + note,
                      source="Binance, Bybit, OKX, Hyperliquid funding history", expandable=True) as cc:
                if hist:
                    f = go.Figure()
                    for i, (ex, s) in enumerate(sorted(hist.items())):
                        s = s[s.index >= s.index.max() - pd.Timedelta(days=60)]
                        f.add_trace(go.Scatter(x=s.index, y=s * 100, name=EX_NAMES.get(ex, ex),
                                               line=dict(color=SERIES[i % 4], width=1.6)))
                    f.add_hline(y=0.01, line_dash="dot", line_color="gray")
                    f.update_yaxes(title_text="% per 8h")
                    cc.chart(pos_chart(f, 320), width="stretch", config={"displayModeBar": False})
                else:
                    st.caption("No funding history yet. Run **Update all data**.")

        with right:
            oi = storage.load_prefix(con, "oi_coin_hist:")
            oi = {k.split(":")[1]: v for k, v in oi.items() if k.endswith(f":{asset}")}
            with card("pos-oi-hist", f"Open interest history ({asset})", paired=True,
                      desc="Daily open interest in coins rather than dollars, so a price rally alone doesn't look like "
                           "new positioning. Binance serves the last 30 days, Bybit about 6 months.",
                      source="Binance, Bybit open-interest history", expandable=True) as cc:
                if oi:
                    f = go.Figure()
                    for i, (ex, s) in enumerate(sorted(oi.items())):
                        f.add_trace(go.Scatter(x=s.index, y=s, name=EX_NAMES.get(ex, ex),
                                               line=dict(color=SERIES[i % 4], width=1.6)))
                    f.update_yaxes(title_text=f"OI ({asset})")
                    cc.chart(pos_chart(f, 320), width="stretch", config={"displayModeBar": False})
                else:
                    st.caption("No open-interest history yet.")

        if live.get("depth") is not None:
            meta = live["book_meta"]
            d = live["depth"].assign(bid=lambda x: x["bid_usd"].map(usd_m), ask=lambda x: x["ask_usd"].map(usd_m))
            h = table_height(len(d))
            with card("pos-book", "Spot order book: depth and imbalance",
                      desc=f"{meta['venue'].title()} {asset}-USD · mid {price_str(meta['mid'])} · spread "
                           f"{meta['spread_bps']:.2f} bps · the book returned reaches {min(meta['bid_reach_pct'], 100):.1f}% "
                           f"below and {meta['ask_reach_pct']:,.0f}% above mid. USD resting within each distance of mid; "
                           "imbalance = (bids - asks) / (bids + asks).",
                      source=f"{meta['venue'].title()} public order book"):
                bl, br = st.columns([2, 3])
                bl.dataframe(d[["band_pct", "bid", "ask", "imbalance", "complete"]], hide_index=True, width="stretch",
                             height=h,
                             column_config={"band_pct": st.column_config.NumberColumn("Within ±%", format="%g"),
                                            "bid": st.column_config.TextColumn("Bids"),
                                            "ask": st.column_config.TextColumn("Asks"),
                                            "imbalance": st.column_config.NumberColumn("Imbalance", format="%+.2f",
                                                help="(bids - asks) / (bids + asks). Negative = more resting supply."),
                                            "complete": st.column_config.CheckboxColumn("Full band",
                                                help="Unticked = the exchange's book snapshot doesn't reach this far.")})
                bids, asks = live["book"]
                curve = pos.depth_curve(bids, asks, 2.0)
                f = go.Figure()
                for side, color in (("bid", BLUE), ("ask", ORANGE)):
                    c = curve[curve["side"] == side]
                    f.add_trace(go.Scatter(x=c["dist_pct"], y=c["cum_usd"] / 1e6, name=f"{side}s",
                                           line=dict(color=color, width=1.6), fill="tozeroy", line_shape="hv"))
                f.update_xaxes(title_text="Distance from mid (%)")
                f.update_yaxes(title_text="Cumulative depth ($m)")
                br.plotly_chart(pos_chart(f, h), width="stretch", config={"displayModeBar": False})
        else:
            st.caption("No order book available right now.")

        rr = storage.load_series(con, f"skew_rr25:{asset}")
        if skew or asset in ("BTC", "ETH"):
            with card("pos-skew", "Options skew (Deribit)",
                      desc=(f"Expiry closest to 30 days. IVs at exactly 25 delta are interpolated between neighbouring "
                            f"strikes (Black-76 on Deribit's forward). The chart shows the 25d risk reversal "
                            f"(call IV - put IV) from each refresh." if skew else "Options data unavailable right now."),
                      source="Deribit public API", expandable=True) as cc:
                if skew:
                    s1, s2, s3, s4 = st.columns(4)
                    s1.metric("Expiry used", f"{skew['expiry']:%d %b %y} ({skew['days_to_expiry']:.0f}d)",
                              help="The listed expiry closest to 30 days out (at least 3 days away).")
                    s2.metric("ATM IV", pct(skew["atm_iv"], 1),
                              help="Implied volatility of the at-the-money options (strikes nearest the forward price) "
                                   "for this expiry: the market's expected annualised move. Divide by about 19 for a "
                                   "one-day move (e.g. 38% ≈ ±2% a day).")
                    s3.metric("25d call IV", pct(skew["call25_iv"], 1),
                              help="Implied volatility of the out-of-the-money call with a 25% delta (roughly a 1-in-4 "
                                   "chance of finishing in the money): the price of upside exposure.")
                    s4.metric("25d put IV", pct(skew["put25_iv"], 1),
                              help="Implied volatility of the out-of-the-money put with a 25% delta: the price of "
                                   "downside protection. Higher than the 25d call IV = puts are in demand.")
                if len(rr) > 1:
                    f = go.Figure(go.Scatter(x=rr.index, y=rr * 100, name="25d risk reversal",
                                             line=dict(color=BLUE, width=1.6)))
                    f.add_hline(y=0, line_color="gray")
                    f.update_yaxes(title_text="vol points")
                    cc.chart(pos_chart(f, 260), width="stretch", config={"displayModeBar": False})
        else:
            st.caption("Deribit's SOL and other alt options are thin or not listed, so skew covers BTC and ETH only.")

        opts = live.get("options")
        if opts is not None and not opts.empty:
            greeks = pos.options_with_delta(opts, pd.Timestamp.now(tz="UTC"))
            by_exp = pos.net_delta_by_expiry(greeks)
            with card("pos-options-oi", "Options open interest: net delta and strikes (Deribit)",
                      desc=f"Open interest in {asset}. Net delta = sum of open interest x delta: the exposure option "
                           "holders carry in aggregate (writers carry the opposite); who is long or short isn't public. "
                           "Table: the 10 nearest expiries. Chart: calls above zero, puts below, for the expiry picked.",
                      source="Deribit public API"):
                o1, o2, o3 = st.columns(3)
                o1.metric("Net delta, all expiries", f"{greeks['delta_oi'].sum():+,.0f} {asset}",
                          help="Positive = holders net positioned for upside.")
                o2.metric("Put/call OI ratio",
                          f"{greeks.loc[greeks['type']=='P','oi'].sum() / max(greeks.loc[greeks['type']=='C','oi'].sum(), 1e-9):.2f}")
                o3.metric("Open interest", f"{greeks['oi'].sum():,.0f} {asset}")
                exp_pick = st.selectbox("Expiry for strike chart", by_exp.index,
                                        index=int(by_exp["call_oi"].add(by_exp["put_oi"]).values.argmax()),
                                        format_func=lambda e: f"{e:%d %b %y}")
                el, er = st.columns([2, 3])
                show = by_exp.head(10).reset_index()
                show["expiry"] = show["expiry"].dt.strftime("%d %b %y")
                h = table_height(len(show))
                el.dataframe(show, hide_index=True, width="stretch", height=h, column_config={
                    "expiry": st.column_config.TextColumn("Expiry"),
                    "call_oi": st.column_config.NumberColumn("Call OI", format="%,.0f"),
                    "put_oi": st.column_config.NumberColumn("Put OI", format="%,.0f"),
                    "net_delta": st.column_config.NumberColumn("Net delta", format="%+,.0f"),
                    "put_call_ratio": st.column_config.NumberColumn("P/C", format="%.2f")})
                strikes = pos.oi_by_strike(greeks, exp_pick)
                fwd = float(greeks.loc[greeks["expiry"] == exp_pick, "forward"].median())
                strikes = strikes[(strikes.index > fwd * 0.6) & (strikes.index < fwd * 1.4)]
                f = go.Figure()
                f.add_trace(go.Bar(x=strikes.index, y=strikes.get("C", 0), name="Calls", marker_color=BLUE))
                f.add_trace(go.Bar(x=strikes.index, y=-strikes.get("P", 0), name="Puts (shown below zero)",
                                   marker_color=ORANGE))
                f.add_vline(x=fwd, line_dash="dot", line_color="gray", annotation_text="forward")
                f.update_layout(barmode="relative", bargap=0.15)
                f.update_yaxes(title_text=f"Open interest ({asset})")
                er.plotly_chart(pos_chart(f, h), width="stretch", config={"displayModeBar": False})
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
        from src import ui_onchain
        ui_onchain.render(con, cfg, CATEGORY_NAMES, CATEGORY_COLORS, (BLUE, ORANGE))
    except Exception as e:  # noqa: BLE001 - one broken tab must not blank the others
        tab_error("tab_chain", e)

# --------------------------------------------------------------------------- #
# Macro events tab
# --------------------------------------------------------------------------- #

with tab_events:
    try:
        st.subheader("Macro event study")
        st.caption("How each asset moved in the days and weeks after past releases, to gauge the tone of the period "
                   "ahead. Day 1 = the event day, measured from the previous close. 'vs a normal period' = the event's "
                   "average move minus the asset's normal move over the same number of days (any start day since 2018).")
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
            ev_h = c3.selectbox("Horizon", horizons, index=min(2, len(horizons) - 1),
                                format_func=lambda h: f"{h} day{'s' if h > 1 else ''}")
            in_bp = ev_asset in bp_assets
            unit = "bp" if in_bp else "%"
            fmt = (lambda v: f"{v:+.0f}bp") if in_bp else (lambda v: f"{v * 100:+.2f}%")
            past = events_df[events_df["date"] <= pd.Timestamp.now().normalize()]
            px = ev_prices[ev_asset]
            rt = ev.reaction_table(past, {ev_asset: px}, horizons, bp_assets)
            typical = {(ev_asset, h): ev.typical_move(px, h, in_bp) for h in horizons}

            today = pd.Timestamp.now().normalize()
            hc1, hc2 = st.columns([4, 1], vertical_alignment="bottom")
            cal_view = hc2.toggle("Calendar view", key="ev_cal_view", help="Show the events on a monthly calendar.")
            hc1.markdown("**Calendar**" if cal_view else "**Coming up (next 30 days)**")
            upcoming = events_df[(events_df["date"] >= today) & (events_df["date"] <= today + pd.Timedelta(days=30))]
            if cal_view:
                from src import ui_calendar
                months = [today + pd.DateOffset(months=k) for k in range(0, 3)]
                pick = st.segmented_control("Month", months, default=months[0], key="ev_cal_month",
                                            format_func=lambda d: f"{d:%B %Y}", label_visibility="collapsed")
                pick = pick or months[0]
                st.markdown(ui_calendar.month_html(events_df, pick.year, pick.month, today), unsafe_allow_html=True)
            elif upcoming.empty:
                st.caption("Nothing scheduled in the calendar for the next 30 days.")
            else:
                summ_all = ev.summarize(rt, typical, by_label=False) if not rt.empty else pd.DataFrame()
                def hist_for(e, col):
                    r = summ_all[(summ_all["event"] == e) & (summ_all["h"] == ev_h)] if not summ_all.empty else summ_all
                    return r[col].iloc[0] if len(r) else np.nan
                up = upcoming.assign(
                    Day=lambda d: d["date"].dt.strftime("%a %d %b"),
                    **{f"Avg {ev_asset} move ({ev_h}d)": lambda d: d["event"].map(lambda e: hist_for(e, "avg")),
                       "vs a normal period": lambda d: d["event"].map(lambda e: hist_for(e, "excess")),
                       "Past events": lambda d: d["event"].map(lambda e: hist_for(e, "n"))})
                show = up.drop(columns=["date", "label"]).rename(columns={"event": "Event"})
                for col in (f"Avg {ev_asset} move ({ev_h}d)", "vs a normal period"):
                    show[col] = show[col].map(lambda v: fmt(v) if pd.notna(v) else "n/a")
                st.dataframe(show[["Day", "Event", f"Avg {ev_asset} move ({ev_h}d)", "vs a normal period", "Past events"]],
                             hide_index=True, width="stretch", column_config={
                                 "vs a normal period": st.column_config.TextColumn(
                                     "vs a normal period", help=f"Average move after this event minus the average "
                                     f"{ev_h}-day {ev_asset} move starting on any day since 2018. Positive = "
                                     f"{ev_asset} has done better than usual after this event.")})

            if not rt.empty:
                from src.charts import card_layout
                from src.ui_studio import card
                one = rt[rt["event"] == ev_type]
                evs = past[past["event"] == ev_type]
                labels = [x for x in LABEL_ORDER if x in set(one["label"])] + \
                    sorted(set(one["label"]) - set(LABEL_ORDER) - {"n/a"})
                groups = [("All releases", one)] + [(lb, one[one["label"] == lb]) for lb in labels]
                scale = 1 if in_bp else 100
                label_note = ("Labels compare each release with the previous reading (consensus forecasts aren't free), "
                              "using FRED's current, revised data. FOMC: hike / cut / hold.") if labels else ""

                # 1. average path (kept): the whole 28 days at a glance
                path = ev.average_path(evs, px, days=max(horizons), in_bp=in_bp)
                base = [ev.typical_move(px, h, in_bp) * scale for h in path.index]
                with card("ev-path", f"{ev_asset} after {ev_type}: average path",
                          desc=f"Average cumulative {ev_asset} move from the close before the release (day 1 = release "
                               f"day). The dotted line is a normal {ev_asset} move over the same number of days, starting "
                               f"on any day since 2018: a line above it means {ev_asset} did better than usual after the "
                               f"release. {label_note}",
                          source="FRED release dates and data, Yahoo Finance prices", expandable=True) as cc:
                    f = go.Figure()
                    for i, c in enumerate(path.columns):
                        name = c.split(" (n=")[0]
                        f.add_trace(go.Scatter(x=path.index, y=path[c] * scale, name=c,
                                               line=dict(color=LABEL_COLORS.get(name, SERIES[i % 4]), width=2)))
                    f.add_trace(go.Scatter(x=path.index, y=base, name="Normal (any day)",
                                           line=dict(color="#8b8b8b", width=2, dash="dot")))
                    f.add_hline(y=0, line_color="#555", line_width=1)
                    f.update_xaxes(title_text="Days after the release (1 = release day)")
                    f.update_yaxes(title_text=f"Average move ({unit})", ticksuffix="" if in_bp else "%")
                    cc.chart(card_layout(f, 340), width="stretch", config={"displayModeBar": False})

                left, right = st.columns(2)
                # 2. how often it was up, against how often it is up on any day
                with left:
                    with card("ev-hit", f"How often {ev_asset} was higher", paired=True,
                              desc=f"Share of past releases after which {ev_asset} was higher than the close before the "
                                   f"release, at each horizon. Grey bars: how often {ev_asset} is higher over the same "
                                   f"number of days starting on any day. Far above grey = a real tilt; near it = a coin flip.",
                              source="FRED, Yahoo Finance", expandable=True) as cc:
                        f = go.Figure()
                        xs = [f"{h}d" for h in horizons]
                        f.add_trace(go.Bar(x=xs, y=[ev.typical_up_share(px, h) * 100 for h in horizons],
                                           name="Any day", marker_color="rgba(139,139,139,0.45)"))
                        for i, (lb, g) in enumerate(groups):
                            hit = g.groupby("h")["move"].apply(lambda x: (x > 0).mean() * 100)
                            cnt = g.groupby("h")["move"].size()
                            f.add_trace(go.Bar(x=xs, y=[hit.get(h, np.nan) for h in horizons], name=lb,
                                               marker_color=LABEL_COLORS.get(lb, SERIES[i % 4]),
                                               customdata=[cnt.get(h, 0) for h in horizons],
                                               hovertemplate="%{y:.0f}% of %{customdata} releases<extra>" + lb + "</extra>"))
                        f.add_hline(y=50, line_color="#555", line_width=1, line_dash="dot")
                        f.update_layout(barmode="group", bargap=0.25, bargroupgap=0.05)
                        f.update_yaxes(title_text="% of releases up", range=[0, 100], ticksuffix="%")
                        cc.chart(card_layout(f, 320), width="stretch", config={"displayModeBar": False})

                # 3. range of outcomes at the chosen horizon
                with right:
                    typ_h = ev.typical_move(px, ev_h, in_bp) * scale
                    with card("ev-range", f"Range of {ev_asset} moves after {ev_type} ({ev_h}d)", paired=True,
                              desc=f"Every past release as a dot, by label. The box covers the middle half of outcomes, "
                                   f"the line inside it is the median. Dotted line: a normal {ev_h}-day {ev_asset} move. "
                                   f"Averages hide how wide the outcomes are; this shows it.",
                              source="FRED, Yahoo Finance", expandable=True) as cc:
                        f = go.Figure()
                        sub = one[one["h"] == ev_h]
                        for i, (lb, g) in enumerate(groups):
                            gh = g[g["h"] == ev_h]
                            f.add_trace(go.Box(y=gh["move"] * scale, name=f"{lb} ({len(gh)})", boxpoints="all",
                                               jitter=0.45, pointpos=0, marker=dict(size=4, opacity=0.6),
                                               line=dict(width=1.4), fillcolor="rgba(0,0,0,0)",
                                               marker_color=LABEL_COLORS.get(lb, SERIES[i % 4]),
                                               customdata=gh["date"].dt.strftime("%d %b %Y"),
                                               hovertemplate="%{customdata}: %{y:+.2f}" + ("bp" if in_bp else "%") +
                                                             "<extra></extra>"))
                        f.add_hline(y=typ_h, line_color="#8b8b8b", line_dash="dot")
                        f.add_hline(y=0, line_color="#555", line_width=1)
                        f.update_layout(showlegend=False, hovermode="closest")
                        f.update_yaxes(title_text=f"Move over {ev_h}d ({unit})", ticksuffix="" if in_bp else "%")
                        cc.chart(card_layout(f, 320), width="stretch", config={"displayModeBar": False})

                # 4. release by release over time
                sub = one[one["h"] == ev_h].sort_values("date")
                with card("ev-history", f"Every {ev_type} release: {ev_asset} move over the next {ev_h} days",
                          desc=f"One dot per release, coloured by label; hover for the date and move. The white line is "
                               f"the average of the last 12 releases, to show whether the reaction has changed over time. When two events share a day (e.g. CPI and jobless "
                               f"claims), both get the same move. Change the horizon with the selector above.",
                          source="FRED, Yahoo Finance", expandable=True) as cc:
                    f = go.Figure()
                    sfx = "bp" if in_bp else "%"
                    for i, (lb, g) in enumerate(groups[1:] or groups):
                        g = sub if lb == "All releases" else sub[sub["label"] == lb]
                        f.add_trace(go.Scatter(x=g["date"], y=g["move"] * scale, name=lb, mode="markers",
                                               marker=dict(color=LABEL_COLORS.get(lb, SERIES[i % 4]), size=7,
                                                           line=dict(width=0)),
                                               hovertemplate="%{x|%d %b %Y}: %{y:+.2f}" + sfx + "<extra>" + lb + "</extra>"))
                    roll = sub.set_index("date")["move"].rolling(12, min_periods=6).mean() * scale
                    f.add_trace(go.Scatter(x=roll.index, y=roll, name="Average of the last 12 releases",
                                           line=dict(color="#f7f8fa", width=1.6),
                                           hovertemplate="%{x|%d %b %Y}: %{y:+.2f}" + sfx + "<extra>12-release avg</extra>"))
                    f.update_layout(hovermode="closest")
                    f.update_yaxes(title_text=f"Move over {ev_h}d ({unit})", ticksuffix="" if in_bp else "%")
                    cc.chart(card_layout(f, 320), width="stretch", config={"displayModeBar": False})
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
