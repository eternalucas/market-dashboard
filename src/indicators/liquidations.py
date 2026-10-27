"""Estimated liquidation levels (a liquidation heatmap, as on Coinglass).

Exchanges don't publish where positions would be liquidated, so the levels
are modelled from public data, bar by bar:

1. Open interest rises by dOI -> new positions of dOI coins opened during the
   bar, at about the bar's typical price p = (high + low + close) / 3.
2. The split between new longs and new shorts follows that bar's taker flow:
   longs = buy volume / (buy + sell volume).
3. Each side is spread over common leverage tiers (LEVERAGE, weights summing
   to 1). Liquidation price at leverage L with maintenance margin m:
       long  p * (1 - 1/L + m)        short  p * (1 + 1/L - m)
4. Open interest falls -> positions closed: every tracked level shrinks by
   the same share (|dOI| / OI before the bar).
5. A level is liquidated, and removed, once price trades through it: long
   levels at or above the bar's low, short levels at or below its high.

The heatmap shows, after each bar, the USD that would be liquidated at each
price level. It is an estimate: real positions are opened at many prices and
leverages, and some are closed or topped up before price gets there.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

LEVERAGE = {10: 0.30, 25: 0.30, 50: 0.25, 100: 0.15}
MMR = 0.004            # Binance's tier-1 maintenance margin on the majors


def price_step(price: float, target_pct: float = 0.1) -> float:
    """A round bucket size near target_pct of price ($100 at BTC $80k)."""
    raw = price * target_pct / 100
    mag = 10 ** math.floor(math.log10(raw))
    return min((m * mag for m in (1, 2, 2.5, 5, 10)), key=lambda x: abs(x - raw))


def make_grid(bars: pd.DataFrame, step: float, max_lev_gap: float = 0.11) -> tuple[int, int]:
    """(first bucket, number of buckets) covering every level the bars can create."""
    lo = bars["l"].min() * (1 - max_lev_gap)
    hi = bars["h"].max() * (1 + max_lev_gap)
    b0 = math.floor(lo / step)
    return b0, math.ceil(hi / step) - b0 + 1


def liquidation_levels(bars: pd.DataFrame, step: float | None = None, start: pd.Timestamp | None = None,
                       grid: tuple[int, int] | None = None, leverage: dict | None = None,
                       track_removed: bool = False) -> dict:
    """bars: index = bar open time; columns h, l, c, oi (coins), buy, sell
    (taker volumes; optional, 50/50 if missing). Bars before `start` only
    warm the model up; the grid covers start onwards.

    Returns {"times", "prices", "z" (prices x times, USD), "long", "short"
    (USD per price level after the last bar), "step", "removed" (with
    track_removed: rows of bar time, price, side, USD the model says were
    liquidated)}."""
    leverage = leverage or LEVERAGE
    bars = bars.dropna(subset=["h", "l", "c"])
    if len(bars) < 3:
        raise ValueError("not enough bars")
    step = step or price_step(float(bars["c"].iloc[-1]))
    b0, n = grid or make_grid(bars, step, 1 / min(leverage) + 0.01)
    prices = (np.arange(n) + b0) * step
    longs, shorts = np.zeros(n), np.zeros(n)
    idx = lambda px: int(np.clip(math.floor(px / step) - b0, 0, n - 1))  # noqa: E731

    h, l, c = (bars[k].to_numpy(float) for k in ("h", "l", "c"))
    oi = bars["oi"].to_numpy(float) if "oi" in bars else np.full(len(bars), np.nan)
    buy = bars["buy"].to_numpy(float) if "buy" in bars else np.ones(len(bars))
    sell = bars["sell"].to_numpy(float) if "sell" in bars else np.ones(len(bars))
    keep = np.ones(len(bars), bool) if start is None else (bars.index >= start)
    cols, removed = [], []
    for t in range(len(bars)):
        # 5. price trades through levels -> liquidated
        il, ih = idx(l[t]), idx(h[t]) + 1
        if track_removed:
            for side, arr, sl in (("long", longs, slice(il, None)), ("short", shorts, slice(None, ih))):
                hit = np.nonzero(arr[sl] > 0)[0] + (sl.start or 0)
                removed += [(bars.index[t], prices[i], side, arr[i]) for i in hit]
        longs[il:] = 0.0
        shorts[:ih] = 0.0
        if t > 0 and np.isfinite(oi[t - 1]) and np.isfinite(oi[t]) and oi[t - 1] > 0:
            d = oi[t] - oi[t - 1]
            if d < 0:                                 # 4. positions closed
                f = max(0.0, 1 + d / oi[t - 1])
                longs *= f
                shorts *= f
            elif d > 0:                               # 1-3. positions opened
                p = (h[t] + l[t] + c[t]) / 3
                b, s_ = buy[t], sell[t]
                share = b / (b + s_) if np.isfinite(b) and np.isfinite(s_) and b + s_ > 0 else 0.5
                usd = d * p
                for lev, w in leverage.items():
                    longs[idx(p * (1 - 1 / lev + MMR))] += usd * share * w
                    shorts[idx(p * (1 + 1 / lev - MMR))] += usd * (1 - share) * w
        if keep[t]:
            cols.append(longs + shorts)
    z = np.column_stack(cols) if cols else np.zeros((n, 0))
    out = {"times": bars.index[keep], "prices": prices, "z": z, "long": longs.copy(), "short": shorts.copy(),
           "step": step}
    if track_removed:
        out["removed"] = pd.DataFrame(removed, columns=["ts", "price", "side", "usd"])
    return out


def combined_levels(candles: pd.DataFrame, venues: dict[str, pd.DataFrame], start: pd.Timestamp,
                    step: float, leverage: dict | None = None) -> dict:
    """Sum of the modelled levels of several venues on one price grid.
    candles: h, l, c (and taker buy/sell used for venues without their own);
    venues: name -> DataFrame with oi (coins) and optional buy/sell, on the
    same bar index."""
    leverage = leverage or LEVERAGE
    grid = make_grid(candles, step, 1 / min(leverage) + 0.01)
    total, per_venue = None, {}
    for name, v in venues.items():
        bars = candles[["h", "l", "c"]].join(v[["oi"]], how="left")
        own = {"buy", "sell"} <= set(v.columns) and v[["buy", "sell"]].notna().any().all()
        flow = v[["buy", "sell"]] if own else candles[["buy", "sell"]]
        bars = bars.join(flow, how="left")
        if bars["oi"].notna().sum() < 3:
            continue
        out = liquidation_levels(bars, step=step, start=start, grid=grid, leverage=leverage)
        per_venue[name] = out
        total = out if total is None else {**total, "z": total["z"] + out["z"], "long": total["long"] + out["long"],
                                           "short": total["short"] + out["short"]}
    if total is None:
        raise ValueError("no venue had open-interest data")
    total["venues"] = per_venue
    return total
