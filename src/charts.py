"""One shared chart component for every Studio metric, in Glassnode's design
language (not its branding): near-black canvas, faint gridlines, muted axis
text, one sans-serif face, the metric in an accent colour on the left axis
and BTC price as a thin light line on a log-scale right axis.

`render(spec, data, price, opts)` returns a Plotly figure. `spec` comes from
src/catalog.py and says what kind of chart the metric is; `opts` carries the
control-bar state (range, resolution, scale, moving average, price overlay).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import plotly.graph_objects as go

# ---------------------------------------------------------------- design tokens
BG = "#1a1a1a"
PANEL = "#242424"
GRID = "rgba(255,255,255,0.06)"
AXIS = "#8b8b8b"
TEXT = "#f7f8fa"
BORDER = "#333333"
PRICE = "rgba(247,248,250,0.85)"
ACCENT = "#f7931a"            # bitcoin orange: the default metric colour
POS, NEG = "#2fbf71", "#e5484d"
FONT = "Inter, 'Helvetica Neue', system-ui, -apple-system, sans-serif"
# categorical palette for multi-series / stacked charts (ordered for contrast on dark)
PALETTE = ["#f7931a", "#4c8dff", "#2fbf71", "#e5484d", "#b07cff", "#18c6c6", "#f2c94c", "#ff7ab6",
           "#9aa4b2", "#ff9f43", "#7bd88f", "#6c7cff"]

TOP_N = 6                      # stacked charts: largest 6 series + "Other"
RANGES = {"1M": 30, "3M": 90, "1Y": 365, "2Y": 730, "All": None}
MAS = {"None": 0, "7D": 7, "30D": 30}


@dataclass
class Opts:
    range: str = "All"
    resolution: str = "24h"       # '24h' or '1h' (only where the metric has intraday data)
    log: bool | None = None       # None = the metric's default scale
    ma: str = "None"
    price: bool = True
    height: int = 360


@dataclass
class Spec:
    id: str
    title: str
    desc: str
    source: str
    unit: str = "num"              # usd, btc, pct, frac_pct (0.12 -> 12%), ratio, bps, count, num, hs (hash/s)
    kind: str = "line"             # line, bars, bars_pn, area, stacked, multi, band, heatmap, xy, overlay
    fn: object = None              # callable(ctx) -> Series | DataFrame
    columns: list | None = None    # subset / order of DataFrame columns to draw
    log: bool = False              # default metric-axis scale
    price: bool = True             # draw BTC price by default
    zones: list = field(default_factory=list)   # [(y0, y1, colour, label)]
    hlines: list = field(default_factory=list)  # [(y, label)]
    intraday: bool = False         # supports 1h resolution
    partial: str = ""              # note shown under the chart when the item is partial
    xlabel: str = ""               # for xy / overlay charts
    reverse: bool = False          # flip the metric axis (ranks: 1 at the top)
    yrange: tuple | None = None    # fixed metric-axis range, e.g. (0, 1) for scores


# ---------------------------------------------------------------- formatting
def fmt(v: float, unit: str) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "-"
    a = abs(v)
    def si(x):
        for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
            if abs(x) >= div:
                return f"{x / div:,.2f}{suf}"
        return f"{x:,.2f}" if abs(x) < 100 else f"{x:,.0f}"
    if unit == "usd":
        return ("-" if v < 0 else "") + "$" + si(a)
    if unit == "btc":
        return si(v) + " BTC"
    if unit == "pct":
        return f"{v:,.2f}%"
    if unit == "frac_pct":
        return f"{v * 100:,.2f}%"
    if unit == "bps":
        return f"{v:,.2f} bps"
    if unit == "hs":
        return f"{v / 1e18:,.1f} EH/s"
    if unit == "ratio":
        return f"{v:,.3f}" if a < 10 else si(v)
    return si(v)


HOVER = {"usd": "$%{y:,.4s}", "btc": "%{y:,.4s} BTC", "pct": "%{y:,.2f}%", "frac_pct": "%{y:.2%}",
         "bps": "%{y:,.2f} bps", "ratio": "%{y:,.3f}", "count": "%{y:,.0f}", "num": "%{y:,.4s}", "hs": "%{y:.3s}H/s"}
# "" = Plotly's automatic labels with exponentformat "B" (1.2B, 300M, 20k: never the SI "G")
TICK = {"usd": "", "btc": "", "pct": "", "frac_pct": ".3~%", "bps": "", "ratio": "", "count": "",
        "num": "", "hs": ""}
PREFIX = {"usd": "$"}
SUFFIX = {"pct": "%", "btc": " BTC", "hs": "H/s"}


# ---------------------------------------------------------------- data shaping
def _frame(data, name: str = "value") -> pd.DataFrame:
    if data is None:
        return pd.DataFrame()
    if isinstance(data, pd.Series):
        return data.to_frame(name)
    return data


def window(df: pd.DataFrame, opts: Opts) -> pd.DataFrame:
    if df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return df
    days = RANGES.get(opts.range)
    if days:
        df = df[df.index >= df.index.max() - pd.Timedelta(days=days)]
    n = MAS.get(opts.ma, 0)
    if n:
        step = df.index.to_series().diff().median()
        per_day = max(1, int(round(pd.Timedelta(days=1) / step))) if pd.notna(step) and step > pd.Timedelta(0) else 1
        df = df.rolling(n * per_day, min_periods=1).mean()
    return df


def resample(df: pd.DataFrame, opts: Opts, spec: Spec) -> pd.DataFrame:
    """24h: one point per UTC day (last); 1h: hourly where the data is intraday."""
    if df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return df
    if opts.resolution == "1h" and spec.intraday:
        return df.resample("1h").last().dropna(how="all")
    step = df.index.to_series().diff().median()
    if pd.notna(step) and step < pd.Timedelta(hours=23):
        how = "sum" if spec.kind in ("bars", "bars_pn") and spec.unit in ("usd", "btc", "count") else "last"
        return getattr(df.resample("1D"), how)().dropna(how="all")
    return df


# ---------------------------------------------------------------- figure
def base_layout(fig: go.Figure, height: int, log: bool, unit: str, has_price: bool) -> go.Figure:
    fig.update_layout(
        height=height, paper_bgcolor=BG, plot_bgcolor=BG, margin=dict(l=8, r=8 if not has_price else 4, t=34, b=8),
        font=dict(family=FONT, size=11, color=AXIS), hovermode="x unified",
        hoverlabel=dict(bgcolor=PANEL, bordercolor=BORDER, font=dict(family=FONT, size=12, color=TEXT)),
        legend=dict(orientation="h", x=0, y=1.0, yanchor="bottom", xanchor="left", font=dict(size=11, color=TEXT),
                    bgcolor="rgba(0,0,0,0)", itemsizing="constant", entrywidth=0, entrywidthmode="pixels",
                    tracegroupgap=0),
        bargap=0.05, barmode="relative",
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor=BORDER, tickfont=dict(color=AXIS), showspikes=True,
                     spikemode="across", spikesnap="cursor", spikecolor="rgba(247,248,250,0.35)", spikethickness=1,
                     spikedash="solid")
    fig.update_yaxes(gridcolor=GRID, zeroline=False, linecolor=BORDER, tickfont=dict(color=AXIS), side="left",
                     tickformat=TICK.get(unit, ""), exponentformat="B", tickprefix=PREFIX.get(unit, ""),
                     ticksuffix="%" if unit == "pct" else "", type="log" if log else "linear")
    if has_price:
        fig.update_layout(yaxis2=dict(overlaying="y", side="right", type="log", showgrid=False, zeroline=False,
                                      tickfont=dict(color=AXIS), tickprefix="$", tickformat=".3~s", linecolor=BORDER))
    return fig


def render(spec: Spec, data, price: pd.Series | None, opts: Opts) -> go.Figure:
    df = _frame(data, spec.title)
    if spec.columns:
        df = df[[c for c in spec.columns if c in df.columns]]
    df = window(resample(df, opts, spec), opts)
    log = spec.log if opts.log is None else opts.log
    timeseries = isinstance(df.index, pd.DatetimeIndex) and spec.kind not in ("xy", "heatmap", "overlay")
    show_price = bool(opts.price and spec.price and timeseries and price is not None and not price.empty and not df.empty)
    fig = go.Figure()
    hov = HOVER.get(spec.unit, "%{y:,.4s}")

    if df.empty or df.dropna(how="all").empty:
        return empty_figure(opts.height, "No data yet")
    if spec.kind in ("stacked", "bars") and df.shape[1] > TOP_N + 1 and isinstance(df.index, pd.DatetimeIndex):
        order = df.iloc[-30:].abs().mean().sort_values(ascending=False).index
        df = pd.concat([df[order[:TOP_N]], df[order[TOP_N:]].sum(axis=1, min_count=1).rename("Other")], axis=1)

    k = spec.kind
    if k == "heatmap":
        z = df.T.values
        diverging = np.nanmin(z) < 0 < np.nanmax(z)
        lim = np.nanpercentile(np.abs(z), 99) if diverging else None
        fig.add_trace(go.Heatmap(x=df.index, y=df.columns.astype(float), z=z,
                                 colorscale=DIVERGING if diverging else HEAT, zmid=0 if diverging else None,
                                 zmin=-lim if diverging else None, zmax=lim if diverging else np.nanpercentile(z, 99),
                                 showscale=False, xgap=1 if len(df) < 60 else 0,
                                 hovertemplate="%{x|%d %b %Y %H:%M}<br>%{y:$,.0f}<br>" + hov.replace("{y", "{z") + "<extra></extra>"))
    elif k == "candles" and {"open", "high", "low", "close"} <= set(df.columns):
        fig.add_trace(go.Candlestick(x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
                                     name=spec.title, increasing=dict(line=dict(color=POS, width=1), fillcolor=POS),
                                     decreasing=dict(line=dict(color=NEG, width=1), fillcolor=NEG)))
        fig.update_layout(xaxis_rangeslider_visible=False)
    elif k in ("xy", "overlay"):
        for i, col in enumerate(df.columns):
            fig.add_trace(go.Scatter(x=df.index, y=df[col], name=str(col), mode="lines",
                                     line=dict(width=1.6, color=PALETTE[i % len(PALETTE)]),
                                     hovertemplate=hov + "<extra>" + str(col) + "</extra>"))
    elif k == "bars_pn":
        col = df.columns[0]
        s = df[col]
        fig.add_trace(go.Bar(x=s.index, y=s.where(s >= 0), name="Positive", marker_color=POS, marker_line_width=0,
                             hovertemplate=hov + "<extra></extra>", showlegend=False))
        fig.add_trace(go.Bar(x=s.index, y=s.where(s < 0), name="Negative", marker_color=NEG, marker_line_width=0,
                             hovertemplate=hov + "<extra></extra>", showlegend=False))
    elif k == "bars":
        for i, col in enumerate(df.columns):
            fig.add_trace(go.Bar(x=df.index, y=df[col], name=str(col), marker_color=PALETTE[i % len(PALETTE)],
                                 marker_line_width=0, hovertemplate=hov + "<extra>" + str(col) + "</extra>"))
    elif k == "stacked":
        for i, col in enumerate(df.columns):
            fig.add_trace(go.Scatter(x=df.index, y=df[col], name=str(col), stackgroup="one", mode="lines",
                                     line=dict(width=0.5, color=PALETTE[i % len(PALETTE)]),
                                     hovertemplate=hov + "<extra>" + str(col) + "</extra>"))
    elif k == "area_pn":
        s = df.iloc[:, 0]
        fig.add_trace(go.Scatter(x=s.index, y=s.clip(lower=0), fill="tozeroy", mode="lines", name=f"{spec.title} (+)",
                                 line=dict(width=0.8, color=POS), fillcolor="rgba(47,191,113,0.35)", hovertemplate=hov + "<extra></extra>"))
        fig.add_trace(go.Scatter(x=s.index, y=s.clip(upper=0), fill="tozeroy", mode="lines", name=f"{spec.title} (-)",
                                 line=dict(width=0.8, color=NEG), fillcolor="rgba(229,72,77,0.35)", hovertemplate=hov + "<extra></extra>",
                                 showlegend=False))
    else:   # line / multi / band / area
        for i, col in enumerate(df.columns):
            colour = ACCENT if (len(df.columns) == 1) else PALETTE[i % len(PALETTE)]
            fill = "tozeroy" if k == "area" else None
            sparse = df[col].notna().sum() < 3
            fig.add_trace(go.Scatter(x=df.index, y=df[col], name=spec.title if len(df.columns) == 1 else str(col),
                                     mode="lines+markers" if sparse else "lines", marker=dict(size=6), line=dict(width=1.4, color=colour), fill=fill,
                                     fillcolor="rgba(247,147,26,0.15)" if fill else None,
                                     hovertemplate=hov + "<extra></extra>"))

    for y0, y1, colour, label in spec.zones:
        fig.add_hrect(y0=y0, y1=y1, fillcolor=colour, opacity=0.12, line_width=0, layer="below",
                      annotation_text=label, annotation_position="top left",
                      annotation_font=dict(size=10, color=AXIS))
    for y, label in spec.hlines:
        fig.add_hline(y=y, line=dict(color="rgba(247,248,250,0.35)", width=1, dash="dot"),
                      annotation_text=label, annotation_font=dict(size=10, color=AXIS))

    if show_price:
        p = price[(price.index >= df.index.min()) & (price.index <= df.index.max())]
        fig.add_trace(go.Scatter(x=p.index, y=p, name="BTC Price [USD]", yaxis="y2", mode="lines",
                                 line=dict(width=1, color=PRICE), hovertemplate="$%{y:,.0f}<extra>Price</extra>"))
    fig = base_layout(fig, opts.height, log, spec.unit, show_price)
    if log and k not in ("heatmap",):
        vals = df.select_dtypes("number").to_numpy(dtype=float)
        vals = vals[np.isfinite(vals) & (vals > 0)]
        if vals.size and np.log10(vals.max() / vals.min()) > 2.5:    # several decades: label powers of ten only
            fig.update_yaxes(dtick=1, selector=dict(overlaying=None))
    if spec.reverse:
        fig.update_yaxes(autorange="reversed", selector=dict(overlaying=None))
    if spec.yrange and not log:
        fig.update_yaxes(range=list(spec.yrange), autorange=False, selector=dict(overlaying=None))
    if k == "heatmap":
        fig.update_yaxes(tickformat="$~s", type="linear")
        fig.update_xaxes(tickformat="%d %b", showspikes=False)
    if timeseries and isinstance(df.index, pd.DatetimeIndex):
        span = df.index.max() - df.index.min()
        if span < pd.Timedelta(days=2):    # a day or two of data: pad the axis so it reads as dates
            fig.update_xaxes(range=[df.index.min() - pd.Timedelta(days=1), df.index.max() + pd.Timedelta(days=1)],
                             tickformat="%d %b %H:%M")
        else:
            fig.update_xaxes(tickformatstops=[dict(dtickrange=[None, 86400000], value="%d %b %H:%M"),
                                              dict(dtickrange=[86400000, 2.6e9], value="%d %b"),
                                              dict(dtickrange=[2.6e9, 3.2e10], value="%b %Y"),
                                              dict(dtickrange=[3.2e10, None], value="%Y")])
    if spec.xlabel:
        fig.update_xaxes(title=dict(text=spec.xlabel, font=dict(size=11, color=AXIS)))
    return fig


DIVERGING = [[0.0, "#e5484d"], [0.5, "#1a1a1a"], [1.0, "#2fbf71"]]
HEAT = [[0.0, "#1a1a1a"], [0.15, "#2d1b4e"], [0.4, "#6b2fa0"], [0.7, "#f7931a"], [1.0, "#fff4d6"]]


def empty_figure(height: int, text: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=text, x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False,
                       font=dict(family=FONT, size=13, color=AXIS))
    fig.update_layout(height=height, paper_bgcolor=BG, plot_bgcolor=BG, margin=dict(l=8, r=8, t=8, b=8),
                      xaxis=dict(visible=False), yaxis=dict(visible=False))
    return fig


def latest_value(data, unit: str, kind: str = "line") -> str:
    df = _frame(data)
    if df.empty:
        return "-"
    s = df.sum(axis=1, min_count=1).dropna() if kind == "stacked" else df.iloc[:, 0].dropna()
    return fmt(float(s.iloc[-1]), unit) if not s.empty else "-"
