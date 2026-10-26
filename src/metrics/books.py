"""Order-book metrics read from the recorder's 1-minute samples
(book_metrics) and 10-minute fixed-grid heatmap (heatmap). Definitions are
in src/indicators/orderbook.py; here samples are rolled up to the chart
resolution (Glassnode: values are time-weighted within each bucket; the
mean of 1-minute samples approximates that)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import orderbook as ob
from .core import Ctx


def samples(c: Ctx, market: str, venue: str | None = None, since: pd.Timestamp | None = None) -> pd.DataFrame:
    sql, params = "SELECT * FROM book_metrics WHERE market = ?", [market]
    if venue:
        sql, params = sql + " AND venue = ?", params + [venue]
    if since is not None:
        sql, params = sql + " AND ts >= ?", params + [since.isoformat()]
    df = pd.read_sql_query(sql, c.con, params=tuple(params))
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    return df


def venue_metrics(c: Ctx, market: str, venue: str, rule: str = "1h", since=None) -> pd.DataFrame:
    """Per-bucket metrics for one venue: spread (mean / min / max / close) [bps],
    depth per side per band [USD], imbalance and depth delta per band, slope
    per side for 100 / 500 / 1000 / 2000 bps vs 25 bps, price impact [bps]."""
    s = samples(c, market, venue, since)
    if s.empty:
        return s
    a = ob.aggregate(s.set_index("ts").drop(columns=["market", "venue", "asset"]), rule)
    for b in ob.DEPTH_BANDS_BPS:
        a[f"imbalance_{b}"] = ob.imbalance(a[f"bid_{b}"], a[f"ask_{b}"])
        a[f"delta_{b}"] = a[f"bid_{b}"] - a[f"ask_{b}"]
        if b != 25:
            a[f"slope_bid_{b}"] = ob.slope(a[f"bid_{b}"], a["bid_25"])
            a[f"slope_ask_{b}"] = ob.slope(a[f"ask_{b}"], a["ask_25"])
    return a


def depth_to_oi(c: Ctx, venue: str, band: int = 100, rule: str = "1h", since=None) -> pd.Series:
    """Perpetual Orderbook Depth to Open Interest [ratio] = (bid + ask depth
    within `band` bps) / that venue's open interest (Coinalyze daily OI,
    forward-filled to the bucket)."""
    from .futures import open_interest_by_exchange
    a = venue_metrics(c, "perp", venue, rule, since)
    if a.empty:
        return pd.Series(dtype=float)
    oi = open_interest_by_exchange(c)
    name = {"binance": "Binance", "bybit": "Bybit", "okx": "OKX", "hyperliquid": "Hyperliquid"}[venue]
    if name not in oi:
        return pd.Series(dtype=float)
    o = oi[name].reindex(a.index, method="ffill")
    return (a[f"bid_{band}"] + a[f"ask_{band}"]) / o


def heatmap(c: Ctx, market: str = "spot", since: pd.Timestamp | None = None) -> pd.DataFrame:
    """Orderbook Heatmap: resting notional [USD] per fixed $50 price bucket,
    both sides combined, venues summed (spot: Coinbase + Binance), every 10
    minutes. Rows = time, columns = bucket price."""
    sql, params = "SELECT ts, price, usd FROM heatmap WHERE market = ?", [market]
    if since is not None:
        sql, params = sql + " AND ts >= ?", params + [since.isoformat()]
    df = pd.read_sql_query(sql, c.con, params=tuple(params))
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    return df.pivot_table(index="ts", columns="price", values="usd", aggfunc="sum").sort_index(axis=1)
