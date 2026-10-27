"""The Flows tab: cross-exchange order flow, laid out like Aggr / Velo charts.

Two stacked chart panels share the time axis:
  1. Order flow by exchange: price, total volume, then one row per exchange
     with taker buys above zero and taker sells below.
  2. Positioning: liquidations, open interest, CVD (spot vs perps and per
     spot exchange), funding, Coinbase premium.
Then an estimated liquidation heatmap (Binance perps) with price overlaid.

Times are shown in the configured time zone; tick labels adapt to the bar
size and to how far you zoom (seconds -> minutes -> days -> months).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from . import flows, storage

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


# ---- liquidation heatmap ------------------------------------------------- #
# Coinglass-style look: deep purple background, viridis scale (purple -> teal ->
# green -> yellow), small red / green candles on top.
LIQ_BG = "#0b0b16"
LIQ_SCALE = [[0.0, "#2a0f45"], [0.08, "#3b1c6e"], [0.25, "#30508c"], [0.45, "#21878d"], [0.65, "#3bba6f"],
             [0.85, "#a6d93a"], [1.0, "#f9e721"]]
LIQ_UP, LIQ_DOWN = "#2ebd85", "#f6465d"
LIQ_WINDOWS = {"12 hours": ("5m", 144), "24 hours": ("5m", 288), "3 days": ("15m", 288),
               "7 days": ("30m", 336), "30 days": ("2h", 360)}


LIQ_WINDOWS = {"12 hours": ("5m", 144), "24 hours": ("5m", 288), "3 days": ("15m", 288),
               "7 days": ("30m", 336), "30 days": ("1h", 720)}


@st.cache_data(ttl=300, show_spinner="Fetching open interest from Binance, Bybit and OKX...")
def _liq_inputs(asset: str, period: str, n: int):
    from .collectors import derivatives
    # twice the window: the first half only warms the model up, so levels opened
    # before the window starts are already on the chart at its left edge
    return derivatives.liq_model_inputs(asset, period, 2 * n)


def _hl_layer(con, asset: str, times: pd.DatetimeIndex, candles: pd.DataFrame, prices: np.ndarray, step: float,
              period_td: pd.Timedelta) -> tuple[np.ndarray, np.ndarray, np.ndarray, pd.Timestamp | None]:
    """Exact Hyperliquid levels on the chart grid: for each bar, the latest scan
    snapshot, minus levels price has traded through since that scan."""
    from .sources import liquidation_feeds as lf
    n = len(prices)
    z = np.zeros((n, len(times)))
    last_long, last_short = np.zeros(n), np.zeros(n)
    snaps = lf.hl_levels(con, asset, times[0] - pd.Timedelta(hours=1))
    if snaps.empty:
        return z, last_long, last_short, None
    b0 = int(round(prices[0] / step))
    by_ts = {t: g for t, g in snaps.groupby("ts")}
    snap_times = pd.DatetimeIndex(sorted(by_ts))
    first = snap_times[0]
    for j, t in enumerate(times):
        end = t + period_td
        k = snap_times.searchsorted(end, side="right") - 1
        if k < 0 or end - snap_times[k] > pd.Timedelta(hours=1):
            continue
        g = by_ts[snap_times[k]]
        idx = np.clip(np.floor(g["price"].to_numpy() / step + 1e-9).astype(int) - b0, 0, n - 1)
        lng, sht = np.zeros(n), np.zeros(n)
        is_long = (g["side"] == "long").to_numpy()
        np.add.at(lng, idx[is_long], g["usd"].to_numpy()[is_long])
        np.add.at(sht, idx[~is_long], g["usd"].to_numpy()[~is_long])
        since = candles[(candles.index + period_td > snap_times[k]) & (candles.index <= t)]
        if not since.empty:
            lng[prices >= since["l"].min()] = 0.0
            sht[prices <= since["h"].max()] = 0.0
        z[:, j] = lng + sht
        last_long, last_short = lng, sht
    return z, last_long, last_short, first


def liquidation_figure(con, asset: str, candles: pd.DataFrame, venues: dict, n: int, period: str, tz: str,
                       threshold: float, show_map: bool, show_actual: bool) -> tuple[go.Figure, dict]:
    from .indicators import liquidations as lq
    from .sources import liquidation_feeds as lf
    weights, cal = lf.load_weights(asset)
    start = candles.index[-n] if len(candles) > n else candles.index[0]
    step = lq.price_step(float(candles["c"].iloc[-1]), 0.05)
    out = lq.combined_levels(candles, venues, start=start, step=step, leverage=weights)
    shown = candles[candles.index >= start]
    period_td = pd.Timedelta(period.replace("m", "min"))
    hl_z, hl_long, hl_short, hl_first = _hl_layer(con, asset, pd.DatetimeIndex(out["times"]), candles,
                                                  out["prices"], step, period_td)
    z_all = out["z"] + hl_z
    lo, hi = shown["l"].min(), shown["h"].max()
    pad = max((hi - lo) * 0.6, hi * 0.03)
    keep = (out["prices"] >= lo - pad) & (out["prices"] <= hi + pad)
    prices, z, zh = out["prices"][keep], z_all[keep], hl_z[keep]
    nz = z[z > 0]
    # threshold 0.85 -> about the top 1.5% of levels show as full yellow (0.5 -> top 5%, 1.0 -> only the largest)
    cap = max(float(np.quantile(nz, 0.9 + 0.1 * threshold)) if len(nz) else 1.0, 1.0)
    x = local(pd.DatetimeIndex(out["times"]), tz)

    cols = 2 if show_map else 1
    fig = make_subplots(rows=1, cols=cols, shared_yaxes=True, horizontal_spacing=0.008,
                        column_widths=[0.85, 0.15] if show_map else [1.0])
    fig.add_trace(go.Heatmap(
        x=x, y=prices, z=z, zmin=0, zmax=cap, colorscale=LIQ_SCALE, zsmooth=False, customdata=zh,
        colorbar=dict(title=dict(text="USD", side="top", font=dict(color=TEXT, size=11)), x=-0.075, xanchor="left",
                      thickness=12, len=0.92, y=0.5, tickformat="$.2s", tickfont=dict(color=TEXT, size=10),
                      outlinewidth=0),
        hovertemplate="%{x|%d %b %H:%M}<br>$%{y:,.0f}: %{z:$,.3s} would be liquidated"
                      "<br>of which Hyperliquid (exact): %{customdata:$,.3s}<extra></extra>",
        name="Liquidation levels"), row=1, col=1)
    fig.add_trace(go.Candlestick(
        x=local(shown.index, tz), open=shown["o"], high=shown["h"], low=shown["l"], close=shown["c"], name="Price",
        increasing=dict(line=dict(color=LIQ_UP, width=1), fillcolor=LIQ_UP),
        decreasing=dict(line=dict(color=LIQ_DOWN, width=1), fillcolor=LIQ_DOWN)), row=1, col=1)
    actual = lf.actual_liquidations(con, asset, start) if show_actual else pd.DataFrame()
    if not actual.empty:
        g = actual.assign(t=actual["ts"].dt.floor(period_td)).groupby(["t", "side"], as_index=False).agg(
            usd=("usd", "sum"), price=("price", "median"))
        for side, color in (("long", "#ff8a9a"), ("short", "#7ff0c0")):
            d = g[g["side"] == side]
            fig.add_trace(go.Scatter(
                x=local(pd.DatetimeIndex(d["t"]), tz), y=d["price"], mode="markers", name=f"{side}s liquidated",
                marker=dict(size=np.clip(np.sqrt(d["usd"]) / 60, 4, 22), color=color, opacity=0.85,
                            line=dict(color="#0b0b16", width=1)),
                customdata=d["usd"], hovertemplate="%{x|%d %b %H:%M}: %{customdata:$,.3s} of " + side +
                "s liquidated near $%{y:,.0f}<extra>actual</extra>"), row=1, col=1)
    if show_map:
        last = float(shown["c"].iloc[-1])
        lng = (out["long"] + hl_long)[keep]
        sht = (out["short"] + hl_short)[keep]
        fig.add_trace(go.Bar(y=prices, x=lng, orientation="h", marker_color=LIQ_DOWN, name="Longs liquidated below",
                             hovertemplate="$%{y:,.0f}: %{x:$,.3s} of longs<extra></extra>"), row=1, col=2)
        fig.add_trace(go.Bar(y=prices, x=sht, orientation="h", marker_color=LIQ_UP, name="Shorts liquidated above",
                             hovertemplate="$%{y:,.0f}: %{x:$,.3s} of shorts<extra></extra>"), row=1, col=2)
        fig.add_hline(y=last, line_color="rgba(255,255,255,0.5)", line_dash="dot", row=1, col=2)
        fig.update_xaxes(row=1, col=2, showgrid=False, tickformat="$.2s", tickfont=dict(color=TEXT, size=10),
                         title=dict(text="Now", font=dict(color=TEXT, size=11)))
    fig.update_layout(height=640, paper_bgcolor=LIQ_BG, plot_bgcolor="#2a0f45", bargap=0, barmode="overlay",
                      margin=dict(l=70, r=64, t=30, b=40), showlegend=False, hovermode="closest",
                      font=dict(color=TEXT), xaxis_rangeslider_visible=False,
                      hoverlabel=dict(bgcolor="#1a1a2e", font=dict(color="#f7f8fa")))
    fig.update_xaxes(row=1, col=1, showgrid=False, tickfont=dict(color=TEXT, size=10), rangeslider_visible=False,
                     range=[x[0], x[-1] + period_td])
    fig.update_yaxes(range=[lo - pad, hi + pad], side="right", showgrid=False, tickformat=",.0f",
                     tickfont=dict(color=TEXT, size=10), row=1, col=cols)
    fig.update_yaxes(showticklabels=cols == 1, row=1, col=1, showgrid=False)
    if show_map:
        fig.update_yaxes(showticklabels=True, row=1, col=2)
    info = {"venues": list(out["venues"]), "hl_first": hl_first, "cal": cal, "weights": weights or lq.LEVERAGE,
            "actual": len(actual)}
    return fig, info


def liquidation_section(con, cfg: dict, asset: str, tz: str, c2, c3, c4, c5) -> None:
    win = c2.selectbox("Window", list(LIQ_WINDOWS), index=1, key="liq_win")
    threshold = c3.slider("Liquidity threshold", 0.50, 1.00, 0.85, 0.01, key="liq_thr",
                          help="Colour scale. Higher = only the biggest clusters reach yellow; lower = more levels light up "
                               "(at 0.85 about the top 1.5% of levels show as full yellow).")
    with c4:
        show_map = st.toggle("Liquidation map", value=True, key="liq_map",
                             help="Side panel: USD that would be liquidated at each price right now "
                                  "(longs below price in red, shorts above in green).")
        show_actual = st.toggle("Actual liquidations", value=True, key="liq_actual",
                                help="Dots: liquidations that really happened (Bybit, Binance, OKX feeds), "
                                     "recorded while the dashboard is open.")
    if c5.button("Refresh", width="stretch", key="liq_refresh"):
        _liq_inputs.clear()
    period, n = LIQ_WINDOWS[win]
    try:
        candles, venues, errors = _liq_inputs(asset, period, n)
    except Exception as e:  # noqa: BLE001
        st.warning(f"Couldn't fetch Binance data for {asset}: {type(e).__name__}: {str(e)[:160]}")
        return
    if len(candles) < 10:
        st.caption(f"Binance doesn't have enough open-interest history for {asset} yet.")
        return
    fig, info = liquidation_figure(con, asset, candles, venues, n, period, tz, threshold, show_map, show_actual)
    st.plotly_chart(fig, width="stretch", config={"scrollZoom": True, "displaylogo": False})

    cov = storage.load_series(con, f"hl_liq_coverage:{asset}")
    hl_txt = (f"Hyperliquid: exact liquidation prices read from its accounts' positions (covering "
              f"{cov.iloc[-1]:.0%} of Hyperliquid {asset} open interest in the latest scan, rescanned every 15 min"
              + (f"; on the chart since {local(pd.DatetimeIndex([info['hl_first']]), tz)[0]:%d %b %H:%M}" if info["hl_first"] is not None else "")
              + ").") if not cov.empty else ("Hyperliquid: exact levels appear once the first account scan "
                                              "finishes (about 15 minutes after the dashboard starts).")
    w = ", ".join(f"{k}x {v:.0%}" for k, v in sorted(info["weights"].items()))
    cal = info["cal"]
    cal_txt = (f"Leverage mix fitted to {cal['events']:,} recorded liquidations ({w}; match {cal['score']:.2f} vs "
               f"{cal['default_score']:.2f} for the default mix)." if cal.get("weights") else
               f"Leverage mix: default ({w}) until enough real liquidations are recorded to fit it "
               f"(3+ days, 300+ events; then refitted daily).")
    st.caption(
        f"{', '.join(info['venues'])} USDT perpetuals ({period} bars, times in {tz.replace('_', ' ')}), modelled from "
        "open interest: when it rises, the new positions are placed at that bar's price, split long/short by taker "
        "buy/sell volume (Binance's for Bybit) and spread over the leverage mix; when it falls, every level shrinks "
        "by the same share; a level disappears once price trades through it. " + cal_txt + " " + hl_txt
        + (f" Dots: {info['actual']:,} actual liquidations recorded from the exchanges' feeds." if info["actual"] else "")
        + (" Unavailable now: " + "; ".join(f"{k} ({v})" for k, v in errors.items()) + "." if errors else ""))


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
    sections = ["Order flow by exchange", "Liquidations, open interest & funding",
                "Spot CVD & Coinbase premium", "Liquidation heatmap (estimated)"]
    section = st.selectbox("Chart", sections, key="flow_section")
    c1, c2, c3, c4, c5 = st.columns([1, 1, 1, 1, 1], vertical_alignment="bottom")
    asset = c1.selectbox("Asset", cfg["assets"], key="flow_asset")

    if section.startswith("Liquidation heatmap"):     # Binance public data: no Coinalyze key needed
        liquidation_section(con, cfg, asset, tz, c2, c3, c4, c5)
        return
    if not (cfg.get("api_keys") or {}).get("coinalyze"):
        st.info("This chart needs a free Coinalyze API key: sign up at coinalyze.net, copy the key from "
                "your account's API page, and paste it into config.yaml under api_keys > coinalyze. "
                "(The liquidation heatmap works without it.)")
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
