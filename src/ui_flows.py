"""The Flows tab: cross-exchange order flow, laid out like Aggr / Velo charts.

Two stacked chart panels share the time axis:
  1. Order flow by exchange: price, total volume, then one row per exchange
     with taker buys above zero and taker sells below.
  2. Positioning: liquidations, open interest, CVD (spot vs perps and per
     spot exchange), funding, Coinbase premium.
Then the order-book heatmap with price overlaid.

Times are shown in the configured time zone; tick labels adapt to the bar
size and to how far you zoom (seconds -> minutes -> days -> months).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from . import flows, pipeline, storage

# ---- look ---------------------------------------------------------------- #
BG = "#0e1320"
GRID = "rgba(255,255,255,0.06)"
TEXT = "#c8ccd6"
BUY = "#f2f4f8"        # taker buys: white (matches the TradingView candle template)
SELL = "#2962ff"       # taker sells: deep blue (TradingView blue)
SPOT_TAG = "#f2f4f8"   # exchange tags: white for spot venues
PERP_TAG = "#f6b8d1"   # light pink for perp venues
LIQ_SHORT, LIQ_LONG = "#8fb8ff", "#f2994a"   # liquidations keep their own colours
UP, DOWN = "#26a69a", "#ef5350"
LINE_A, LINE_B, LINE_C, LINE_D = "#5b8def", "#ef5350", "#2fbf71", "#e0b341"
EXCHANGE_COLORS = {"Coinbase": LINE_A, "Binance": LINE_B, "Bybit": LINE_C, "Bitfinex": LINE_D,
                   "OKX": "#b07cf0", "Hyperliquid": "#46c7c7"}

# Default and allowed look-back windows per bar size (hours)
WINDOWS = {
    "1 min": [1, 3, 6, 12, 24],
    "5 min": [6, 12, 24, 72, 120],
    "15 min": [24, 72, 168, 336],
    "30 min": [72, 168, 336, 720],
    "1 hour": [168, 336, 720, 1440],
    "4 hours": [720, 2160, 4320],
    "12 hours": [2160, 4320, 8760],
    "1 day": [4320, 8760, 17520, 35040],
    "1 week": [8760, 17520, 35040],
}
DEFAULT_WINDOW = {"1 min": 6, "5 min": 24, "15 min": 72, "30 min": 168, "1 hour": 336,
                  "4 hours": 2160, "12 hours": 4320, "1 day": 8760, "1 week": 17520}

# Tick labels change with zoom level (plotly picks the rule by tick spacing, in ms)
TICK_STOPS = [
    dict(dtickrange=[None, 60_000], value="%H:%M:%S"),
    dict(dtickrange=[60_000, 3_600_000], value="%H:%M"),
    dict(dtickrange=[3_600_000, 86_400_000], value="%H:%M\n%d %b"),
    dict(dtickrange=[86_400_000, 604_800_000], value="%d %b"),
    dict(dtickrange=[604_800_000, "M1"], value="%d %b\n%Y"),
    dict(dtickrange=["M1", "M12"], value="%b %Y"),
    dict(dtickrange=["M12", None], value="%Y"),
]
HOVER_FMT = {"1 min": "%d %b %H:%M", "5 min": "%d %b %H:%M", "15 min": "%d %b %H:%M", "30 min": "%d %b %H:%M",
             "1 hour": "%d %b %H:%M", "4 hours": "%d %b %H:%M", "12 hours": "%d %b %Y %H:%M",
             "1 day": "%a %d %b %Y", "1 week": "week of %d %b %Y"}


def window_label(hours: int) -> str:
    if hours < 24:
        return f"{hours} hours" if hours > 1 else "1 hour"
    days = hours // 24
    if days < 60:
        return f"{days} days" if days > 1 else "1 day"
    if days < 365:
        return f"{round(days / 30)} months"
    return f"{round(days / 365)} year{'s' if days >= 730 else ''}"


def local(idx: pd.DatetimeIndex, tz: str) -> pd.DatetimeIndex:
    """UTC index -> naive local time, so plotly labels it as wall-clock time."""
    return idx.tz_convert(tz).tz_localize(None)


def style(fig: go.Figure, height: int, bar_size: str, right_margin: int = 8) -> go.Figure:
    fig.update_layout(
        height=height, template="plotly_dark", paper_bgcolor=BG, plot_bgcolor=BG,
        font=dict(color=TEXT, size=11), margin=dict(l=8, r=right_margin, t=24, b=8),
        hovermode="x unified", showlegend=False, bargap=0.15, barmode="relative",
        xaxis_rangeslider_visible=False,
    )
    fig.update_xaxes(showgrid=True, gridcolor=GRID, tickformatstops=TICK_STOPS,
                     hoverformat=HOVER_FMT[bar_size], showspikes=True, spikemode="across",
                     spikecolor="rgba(255,255,255,0.35)", spikethickness=1, spikedash="dot")
    fig.update_yaxes(side="right", showgrid=True, gridcolor=GRID, zeroline=True,
                     zerolinecolor="rgba(255,255,255,0.18)", tickfont=dict(size=10))
    return fig


def row_label(fig: go.Figure, row: int, text: str, color: str) -> None:
    """Exchange tag in the right margin, outside the plot area, so it never
    covers the latest bars."""
    axis = fig.layout["yaxis" if row == 1 else f"yaxis{row}"]
    y0, y1 = axis.domain
    fig.add_annotation(xref="paper", yref="paper", x=1.008, y=(y0 + y1) / 2, xanchor="left",
                       text=f" {text} ", showarrow=False, font=dict(color="#0e1320", size=11),
                       bgcolor=color, borderpad=3)


def row_title(fig: go.Figure, row: int, text: str) -> None:
    axis = fig.layout["yaxis" if row == 1 else f"yaxis{row}"]
    fig.add_annotation(xref="paper", yref="paper", x=0.005, y=axis.domain[1], xanchor="left", yanchor="top",
                       text=text, showarrow=False, font=dict(color=TEXT, size=11), bgcolor="rgba(14,19,32,0.7)")


def row_legend(fig: go.Figure, row: int, items: list[tuple[str, str]]) -> None:
    axis = fig.layout["yaxis" if row == 1 else f"yaxis{row}"]
    txt = "   ".join(f"<span style='color:{c}'>■</span> {n}" for n, c in items)
    fig.add_annotation(xref="paper", yref="paper", x=0.995, y=axis.domain[1], xanchor="right", yanchor="top",
                       text=txt, showarrow=False, font=dict(color=TEXT, size=10), bgcolor="rgba(14,19,32,0.7)")


def add_price(fig: go.Figure, fl: dict, tz: str, row: int = 1) -> None:
    p = fl["price"]
    fig.add_trace(go.Candlestick(x=local(p.index, tz), open=p["o"], high=p["h"], low=p["l"], close=p["c"],
                                 name="Price", increasing=dict(line=dict(color=UP, width=1), fillcolor=UP),
                                 decreasing=dict(line=dict(color=DOWN, width=1), fillcolor=DOWN)), row, 1)
    row_title(fig, row, "Price (main perp)")


def finish(fig: go.Figure, n_rows: int) -> None:
    fig.update_xaxes(showticklabels=False)
    fig.update_xaxes(showticklabels=True, row=n_rows, col=1)


# ---- sections ------------------------------------------------------------ #

def orderflow_figure(fl: dict, tz: str, bar_size: str, usd: bool) -> go.Figure:
    spot, perp = fl["spot_volume"], fl["perp_volume"]
    order = [("Coinbase", spot, "Coinbase", "spot"), ("Binance", spot, "Binance Spot", "spot"),
             ("Bybit", spot, "Bybit Spot", "spot"), ("Bitfinex", spot, "Bitfinex Spot", "spot"),
             ("OKX", spot, "OKX Spot", "spot"),
             ("Binance", perp, "Binance Perps", "perp"), ("Bybit", perp, "Bybit Perps", "perp"),
             ("OKX", perp, "OKX Perps", "perp"), ("Hyperliquid", perp, "HL Perps", "perp")]
    rows = [(src[ex], label, kind) for ex, src, label, kind in order if ex in src]
    n = 2 + len(rows)
    fig = make_subplots(rows=n, cols=1, shared_xaxes=True, vertical_spacing=0.012,
                        row_heights=[3.2, 1.2] + [1.0] * len(rows))
    add_price(fig, fl, tz)
    b_col, s_col = ("buy_usd", "sell_usd") if usd else ("buy", "sell")
    allv = pd.concat([v[[b_col, s_col]] for v, _, _ in rows]).groupby(level=0).sum() if rows else pd.DataFrame()
    if not allv.empty:
        xv = local(allv.index, tz)
        fig.add_trace(go.Bar(x=xv, y=allv[b_col], marker_color=BUY, name="All buys"), 2, 1)
        fig.add_trace(go.Bar(x=xv, y=-allv[s_col], marker_color=SELL, name="All sells"), 2, 1)
    row_title(fig, 2, f"Total volume, all venues ({'USD' if usd else 'coins'}): buys up, sells down")
    for i, (v, label, _) in enumerate(rows, start=3):
        xv = local(v.index, tz)
        fig.add_trace(go.Bar(x=xv, y=v[b_col], marker_color=BUY, name=f"{label} buys"), i, 1)
        fig.add_trace(go.Bar(x=xv, y=-v[s_col], marker_color=SELL, name=f"{label} sells"), i, 1)
        fig.update_yaxes(showticklabels=False, row=i, col=1)
    style(fig, 330 + 70 * n, bar_size, right_margin=112)
    for i, (_, label, kind) in enumerate(rows, start=3):
        row_label(fig, i, label, PERP_TAG if kind == "perp" else SPOT_TAG)
    finish(fig, n)
    return fig


def derivatives_figure(fl: dict, tz: str, bar_size: str, asset: str, oi_split: bool) -> go.Figure:
    """Price, liquidations, open interest, funding."""
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[1.6, 0.9, 1.2, 0.9])
    add_price(fig, fl, tz)
    lq = fl["liquidations"]
    if not lq.empty:
        x = local(lq.index, tz)
        fig.add_trace(go.Bar(x=x, y=lq["shorts"], marker_color=LIQ_SHORT, name="Shorts liquidated"), 2, 1)
        fig.add_trace(go.Bar(x=x, y=-lq["longs"], marker_color=LIQ_LONG, name="Longs liquidated"), 2, 1)
    if oi_split and fl["oi_by_exchange"]:
        for ex, s in fl["oi_by_exchange"].items():
            fig.add_trace(go.Scatter(x=local(s.index, tz), y=s, name=f"{ex} OI", stackgroup="oi",
                                     line=dict(width=0.5, color=EXCHANGE_COLORS.get(ex, LINE_D))), 3, 1)
    elif not fl["oi_coins"].empty:
        s = fl["oi_coins"]
        fig.add_trace(go.Scatter(x=local(s.index, tz), y=s, name="Open interest", line=dict(color=LINE_A, width=1.6)), 3, 1)
    f = fl["funding"]
    if not f.empty:
        fig.add_trace(go.Bar(x=local(f.index, tz), y=f, name="Funding",
                             marker_color=[UP if v >= 0 else DOWN for v in f.fillna(0)]), 4, 1)
    style(fig, 980, bar_size)
    row_title(fig, 2, "Liquidations (USD): shorts liquidated up, longs down")
    row_title(fig, 3, f"Open interest ({asset} perps, in {asset})")
    row_title(fig, 4, "Funding rate, OI-weighted (% per 8h)")
    row_legend(fig, 2, [("Shorts", LIQ_SHORT), ("Longs", LIQ_LONG)])
    if oi_split:
        row_legend(fig, 3, [(ex, EXCHANGE_COLORS.get(ex, LINE_D)) for ex in fl["oi_by_exchange"]])
    finish(fig, 4)
    return fig


def spot_figure(fl: dict, tz: str, bar_size: str, asset: str) -> go.Figure:
    """Price, CVD spot vs perps, spot CVD per exchange, Coinbase premium."""
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[1.6, 1.1, 1.1, 0.9])
    add_price(fig, fl, tz)
    for s, name, color in ((fl["cvd_spot"], "CVD spot", LINE_A), (fl["cvd_perp"], "CVD perps", LINE_B)):
        if not s.empty:
            fig.add_trace(go.Scatter(x=local(s.index, tz), y=s, name=name, line=dict(color=color, width=1.6)), 2, 1)
    for ex, s in fl["cvd_by_spot_exchange"].items():
        fig.add_trace(go.Scatter(x=local(s.index, tz), y=s, name=f"{ex} spot CVD",
                                 line=dict(color=EXCHANGE_COLORS.get(ex, LINE_D), width=1.6)), 3, 1)
    prem = fl["coinbase_premium"]
    if not prem.empty:
        fig.add_trace(go.Bar(x=local(prem.index, tz), y=prem, name="Coinbase premium",
                             marker_color=[UP if v >= 0 else DOWN for v in prem.fillna(0)]), 4, 1)
    style(fig, 980, bar_size)
    row_title(fig, 2, f"CVD: spot vs perps ({asset})")
    row_title(fig, 3, f"Spot CVD by exchange ({asset})")
    row_title(fig, 4, "Coinbase premium (%): Coinbase USD vs Binance USDT")
    row_legend(fig, 2, [("Spot", LINE_A), ("Perps", LINE_B)])
    row_legend(fig, 3, [(ex, EXCHANGE_COLORS.get(ex, LINE_D)) for ex in fl["cvd_by_spot_exchange"]])
    finish(fig, 4)
    return fig


HEAT_SCALE = [[0.0, "rgba(14,19,32,0)"], [0.12, "#1d1145"], [0.3, "#4a1d7a"], [0.5, "#9c2f8f"],
              [0.7, "#e8613c"], [0.88, "#f7c65a"], [1.0, "#ffffff"]]
def heatmap_figure(lv: pd.DataFrame, candles: pd.DataFrame | None, tz: str, sensitivity: float) -> go.Figure:
    """Resting liquidity per price bucket over time. Colour is linear in USD,
    capped at a high percentile so a few huge walls don't wash out the rest;
    thin levels are left transparent so the walls stand out."""
    lv = lv.assign(usd=lv["bid_usd"] + lv["ask_usd"])
    grid = lv.pivot_table(index="price", columns="ts", values="usd", aggfunc="sum")
    vals = grid.to_numpy().ravel()
    vals = vals[np.isfinite(vals) & (vals > 0)]
    cap = np.percentile(vals, 99.5 - sensitivity * 4) if len(vals) else 1
    floor = np.percentile(vals, 40 + sensitivity * 8) if len(vals) else 0
    z = grid.clip(upper=cap).where(grid >= floor)
    fig = go.Figure(go.Heatmap(
        x=local(pd.DatetimeIndex(grid.columns), tz), y=grid.index, z=z.to_numpy(), zmin=floor, zmax=cap,
        colorscale=HEAT_SCALE, showscale=False, zsmooth=False,
        hovertemplate="%{x}<br>%{y:,.0f}: $%{z:,.0f} resting<extra></extra>"))
    if candles is not None and not candles.empty:
        x = local(candles.index, tz)
        fig.add_trace(go.Candlestick(x=x, open=candles["o"], high=candles["h"], low=candles["l"], close=candles["c"],
                                     increasing=dict(line=dict(color=UP, width=1), fillcolor=UP),
                                     decreasing=dict(line=dict(color=DOWN, width=1), fillcolor=DOWN), name="Price"))
    else:
        mids = lv.groupby("ts").apply(lambda g: (g.loc[g["bid_usd"] > 0, "price"].max()
                                                 + g.loc[g["ask_usd"] > 0, "price"].min()) / 2, include_groups=False)
        fig.add_trace(go.Scatter(x=local(pd.DatetimeIndex(mids.index), tz), y=mids, name="Mid",
                                 line=dict(color="#ffffff", width=1.5)))
    style(fig, 560, "1 min")
    mid = float(lv["price"].median())
    fig.update_yaxes(range=[mid * 0.975, mid * 1.025], tickformat=",.0f")
    return fig


# ---- tab ----------------------------------------------------------------- #

@st.cache_data(ttl=120, show_spinner="Fetching new bars from Coinalyze...")
def _fetch(asset: str, interval: str, _cfg: dict) -> str:
    c = storage.connect()
    try:
        r = flows.update_flow(c, _cfg, asset, interval)
        storage.log_fetch(c, f"coinalyze:{interval}:{asset}", True, r["rows"])
        return ""
    except Exception as e:  # noqa: BLE001
        storage.log_fetch(c, f"coinalyze:{interval}:{asset}", False, 0, f"{type(e).__name__}: {e}")
        return f"{type(e).__name__}: {e}"
    finally:
        c.close()


def render(con, cfg: dict) -> None:
    tz = (cfg.get("display") or {}).get("timezone", "UTC")
    if not (cfg.get("api_keys") or {}).get("coinalyze"):
        st.info("The Flows tab needs a free Coinalyze API key: sign up at coinalyze.net, copy the key from "
                "your account's API page, and paste it into config.yaml under api_keys > coinalyze.")
        return

    sections = ["Order flow by exchange", "Liquidations, open interest & funding",
                "Spot CVD & Coinbase premium", "Order-book heatmap (liquidity levels)"]
    section = st.selectbox("Chart", sections, key="flow_section")
    c1, c2, c3, c4, c5 = st.columns([1, 1, 1, 1, 1])
    asset = c1.selectbox("Asset", cfg["assets"], key="flow_asset")

    # The order book keeps recording in the background whichever chart is showing
    rec = st.session_state.get("hm_rec", True)

    @st.fragment(run_every="60s" if rec else None)
    def recorder():
        if rec:
            c = storage.connect()
            try:
                pipeline.record_book(c, cfg, asset)
            finally:
                c.close()

    recorder()

    if section.startswith("Order-book"):
        heatmap_section(con, cfg, asset, tz, c2, c3, c4)
        return

    bar_size = c2.selectbox("Bar size", list(flows.BAR_SIZES), index=1, key="flow_bar")
    opts = WINDOWS[bar_size]
    window = c3.selectbox("Window", opts, index=opts.index(DEFAULT_WINDOW[bar_size]),
                          format_func=window_label, key=f"flow_win_{bar_size}")
    if section == sections[0]:
        unit = c4.radio("Volume in", ["USD", "Coins"], horizontal=True, key="flow_unit")
    elif section == sections[1]:
        oi_split = c4.toggle("Split OI by exchange", key="oi_split")
    if c5.button("Fetch latest", width="stretch"):
        _fetch.clear()
    err = _fetch(asset, flows.BAR_SIZES[bar_size][0], cfg)
    if err:
        st.warning(f"Coinalyze fetch failed, showing stored data. {err[:160]} (full error in Data health)")

    since = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=window)
    fl = flows.load_flow(con, cfg, asset, bar_size, since)
    if fl["price"].empty:
        st.caption("No bars stored for this asset and bar size yet.")
        return
    mk = fl["markets"]
    n_bars = len(fl["price"])
    st.caption(f"{n_bars:,} bars of {bar_size} · times in {tz.replace('_', ' ')} · "
               f"spot: {', '.join(mk['spot'])} · perps (stablecoin-margined): {', '.join(mk['perp'])}")
    if n_bars < 0.5 * window * 3600 / flows.BAR_SIZES[bar_size][1]:
        st.caption("Coinalyze only keeps about 1,500 bars per series for intraday sizes, so longer windows fill "
                   "in as the dashboard keeps storing new bars.")
    chart_cfg = {"scrollZoom": True, "displaylogo": False}
    if section == sections[0]:
        st.plotly_chart(orderflow_figure(fl, tz, bar_size, unit == "USD"), width="stretch", config=chart_cfg)
        st.caption("Each row: taker buys (white) above the line, taker sells (blue) below, per bar. "
                   "White tags are spot venues, pink tags are perps.")
    elif section == sections[1]:
        st.plotly_chart(derivatives_figure(fl, tz, bar_size, asset, oi_split), width="stretch", config=chart_cfg)
        st.caption(f"Open interest is {asset} only: the sum of the {asset} stablecoin-margined perpetuals on "
                   f"{', '.join(mk['perp'])}, converted to {asset} so price moves don't look like new positions. "
                   "Coin-margined (inverse) perps, dated futures and CME are not included.")
    else:
        st.plotly_chart(spot_figure(fl, tz, bar_size, asset), width="stretch", config=chart_cfg)
        st.caption("CVD = running total of taker buys minus taker sells from the left edge of the window.")


def heatmap_section(con, cfg: dict, asset: str, tz: str, c2, c3, c4) -> None:
    hm_hours = c2.selectbox("Window", [1, 3, 6, 12, 24, 72], index=2, format_func=window_label, key="hm_win")
    sens = c3.slider("Show thinner levels", 0, 5, 2, key="hm_sens",
                     help="Higher shows more of the smaller resting orders; lower keeps only the big walls.")
    c4.toggle("Record", value=True, key="hm_rec",
              help="Adds one order-book snapshot a minute while the dashboard is open, whichever chart is showing.")

    @st.fragment(run_every="60s")
    def heat():
        lv = storage.load_book_levels(con, asset, (pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=hm_hours)).to_pydatetime())
        if lv["ts"].nunique() < 3:
            st.caption("Building up: the heatmap needs a few minutes of snapshots. Leave the dashboard open with "
                       "recording on; each minute adds one column.")
            return
        candles = None
        venue = {"coinbase": "Coinbase", "kraken": "Kraken", "bybit": "Bybit"}.get(cfg["positioning"]["spot_books"][0])
        for hm_bar in ("1 min", "5 min", "15 min", "1 hour"):
            try:
                fl_h = flows.load_flow(con, cfg, asset, hm_bar, lv["ts"].min())
            except Exception:  # noqa: BLE001 - candles are optional
                continue
            c_ = fl_h["spot_ohlc"].get(venue)
            c_ = c_ if c_ is not None and not c_.empty else fl_h["price"]
            if len(c_) >= 3:
                candles = c_
                break
        st.plotly_chart(heatmap_figure(lv, candles, tz, sens), width="stretch",
                        config={"scrollZoom": True, "displaylogo": False})
        st.caption(f"Resting bids and asks on {cfg['positioning']['spot_books'][0].title()} {asset} spot, "
                   "summed per price level each minute. Bright bands are large resting orders (walls); "
                   "bands that vanish as price approaches were pulled. Gaps = times the dashboard wasn't open.")

    heat()
