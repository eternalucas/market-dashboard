"""Order-book metrics, following Glassnode's published definitions
(docs: studio.glassnode.com metric descriptions for market.Orderbook* and
derivatives.PerpetualOrderbook*, read 2026-10-21).

One call to `snapshot_metrics` turns one book snapshot into one row. The
recorder samples every venue once a minute; Glassnode's "time-weighted"
bucket values are then approximated by the mean of the minute samples in each
bucket, and Spread (Min / Max / Close) by the min / max / last sample.

Definitions (bps = basis points of the mid-price, mid = (best bid + best ask) / 2):
  spread_bps        = (best ask - best bid) / mid * 1e4
  depth (per side)  = sum(price * size) of resting orders within B bps of mid, in USD
                      for B in DEPTH_BANDS_BPS
  imbalance(B)      = (bid depth - ask depth) / (bid depth + ask depth), range [-1, 1]
  depth delta(B)    = bid depth - ask depth, USD
  slope(B, side)    = depth within B bps / depth within 25 bps, for B in 100/500/1000/2000
  price impact(N)   = |VWAP fill price of a market order of N USD - mid| / mid * 1e4,
                      walking the book from the best level; NaN when the visible
                      book is too thin to fill N
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEPTH_BANDS_BPS = [25, 100, 500, 1000, 2000]
IMPACT_NOTIONALS = [100_000, 1_000_000, 10_000_000]


def mid_price(bids: pd.DataFrame, asks: pd.DataFrame) -> float:
    return (bids["price"].max() + asks["price"].min()) / 2


def spread_bps(bids: pd.DataFrame, asks: pd.DataFrame) -> float:
    bb, ba = bids["price"].max(), asks["price"].min()
    return (ba - bb) / ((ba + bb) / 2) * 1e4


def depth_usd(book: pd.DataFrame, mid: float, band_bps: float, side: str) -> float:
    """USD resting within band_bps of mid on one side ('bid' or 'ask')."""
    lim = mid * band_bps / 1e4
    sel = book["price"] >= mid - lim if side == "bid" else book["price"] <= mid + lim
    d = book[sel]
    return float((d["price"] * d["size"]).sum())


def price_impact_bps(book: pd.DataFrame, mid: float, notional_usd: float) -> float:
    """Cost in bps of a market order of notional_usd that walks `book`
    (asks for a buy, bids for a sell). `book` must be sorted best price first."""
    usd = book["price"] * book["size"]
    cum = usd.cumsum()
    if cum.empty or cum.iloc[-1] < notional_usd:
        return float("nan")
    full = cum < notional_usd
    filled_usd = usd[full].sum()
    filled_coins = book.loc[full, "size"].sum()
    nxt = book[~full].iloc[0]
    rest_usd = notional_usd - filled_usd
    coins = filled_coins + rest_usd / nxt["price"]
    vwap = notional_usd / coins
    return abs(vwap - mid) / mid * 1e4


def snapshot_metrics(bids: pd.DataFrame, asks: pd.DataFrame) -> dict:
    """All metrics for one snapshot, as a flat dict of floats."""
    bids = bids[bids["size"] > 0].sort_values("price", ascending=False).reset_index(drop=True)
    asks = asks[asks["size"] > 0].sort_values("price").reset_index(drop=True)
    if bids.empty or asks.empty:
        raise ValueError("empty book")
    mid = mid_price(bids, asks)
    out = {"mid": mid, "spread_bps": spread_bps(bids, asks)}
    for b in DEPTH_BANDS_BPS:
        out[f"bid_{b}"] = depth_usd(bids, mid, b, "bid")
        out[f"ask_{b}"] = depth_usd(asks, mid, b, "ask")
    for n in IMPACT_NOTIONALS:
        tag = f"{n // 1000}k" if n < 1_000_000 else f"{n // 1_000_000}m"
        out[f"impact_buy_{tag}"] = price_impact_bps(asks, mid, n)
        out[f"impact_sell_{tag}"] = price_impact_bps(bids, mid, n)
    return out


def imbalance(bid: pd.Series | float, ask: pd.Series | float):
    tot = bid + ask
    return (bid - ask) / (tot.replace(0, np.nan) if isinstance(tot, pd.Series) else (tot or np.nan))


def slope(depth_wide, depth_25):
    return depth_wide / (depth_25.replace(0, np.nan) if isinstance(depth_25, pd.Series) else (depth_25 or np.nan))


def aggregate(samples: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Roll minute samples (indexed by UTC time, one venue) up to bucket values:
    mean for depth / impact / spread, plus spread min / max / close."""
    g = samples.resample(rule, label="left", closed="left")
    out = g.mean(numeric_only=True)
    out["spread_min"] = g["spread_bps"].min()
    out["spread_max"] = g["spread_bps"].max()
    out["spread_close"] = g["spread_bps"].last()
    return out.dropna(how="all")


def heatmap_buckets(bids: pd.DataFrame, asks: pd.DataFrame, step: float, range_pct: float = 10.0) -> pd.DataFrame:
    """Notional resting per price bucket on a FIXED absolute grid (Glassnode:
    'a constant step per pair, so a standing wall reads as a straight line'),
    both sides combined, within range_pct of mid."""
    mid = mid_price(bids, asks)
    lo, hi = mid * (1 - range_pct / 100), mid * (1 + range_pct / 100)
    both = pd.concat([bids, asks])
    both = both[(both["price"] >= lo) & (both["price"] <= hi)]
    usd = (both["price"] * both["size"]).groupby((both["price"] // step) * step).sum()
    return usd.rename("usd").rename_axis("price").reset_index()
