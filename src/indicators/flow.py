"""Order-flow aggregates: CVD, open interest in coins, funding, liquidations,
Coinbase premium. Inputs are {exchange: DataFrame} from the Coinalyze
collector; outputs are single series ready to chart.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Exchanges that pay funding every hour instead of every 8 hours. Their rate
# is multiplied by 8 so it sits on the same scale as everyone else.
HOURLY_FUNDING = {"hyperliquid"}


def bar_delta(ohlcv: pd.DataFrame) -> pd.Series:
    """Net taker flow per bar: taker buys minus taker sells, in coins.
    bv is taker buy volume, so sells = v - bv and buys - sells = 2*bv - v."""
    return 2 * ohlcv["bv"] - ohlcv["v"]


def cvd(ohlcv: pd.DataFrame) -> pd.Series:
    """Cumulative volume delta: running total of net taker flow. Rising =
    aggressive buyers lifting offers; falling = sellers hitting bids."""
    return bar_delta(ohlcv).cumsum()


def aggregate_cvd(markets: dict[str, pd.DataFrame]) -> pd.Series:
    """CVD summed across exchanges. Each bar's deltas are added first, then
    accumulated, so an exchange with a missing bar doesn't distort the total."""
    deltas = [bar_delta(df).rename(ex) for ex, df in markets.items() if {"v", "bv"} <= set(df)]
    if not deltas:
        return pd.Series(dtype=float)
    return pd.concat(deltas, axis=1).sum(axis=1, min_count=1).cumsum()


def oi_in_coins(oi_usd: dict[str, pd.DataFrame], closes: dict[str, pd.Series]) -> pd.Series:
    """Total open interest in coins: each exchange's USD OI divided by its own
    price that bar, then summed. In coins, a rally alone doesn't inflate OI."""
    parts = []
    for ex, df in oi_usd.items():
        if ex in closes:
            parts.append((df["c"] / closes[ex].reindex(df.index)).rename(ex))
    if not parts:
        return pd.Series(dtype=float)
    return pd.concat(parts, axis=1).sum(axis=1, min_count=1)


def as_percent(rate: pd.Series) -> pd.Series:
    """Coinalyze funding comes in percent (0.01 = 0.01%). If a series looks
    like a raw fraction instead (typical magnitude ~0.0001), convert it, so
    the chart is right either way."""
    typical = rate.abs().median()
    return rate * 100 if pd.notna(typical) and 0 < typical < 0.001 else rate


def oi_weighted_funding(funding: dict[str, pd.DataFrame], oi_usd: dict[str, pd.DataFrame]) -> pd.Series:
    """Funding averaged across exchanges, weighted by each one's USD open
    interest at that bar, in % per 8 hours."""
    rates, weights = [], []
    for ex, df in funding.items():
        if ex not in oi_usd:
            continue
        r = as_percent(df["c"])
        if ex.lower() in HOURLY_FUNDING:
            r = r * 8
        rates.append(r.rename(ex))
        weights.append(oi_usd[ex]["c"].reindex(r.index).ffill().rename(ex))
    if not rates:
        return pd.Series(dtype=float)
    R, W = pd.concat(rates, axis=1), pd.concat(weights, axis=1)
    W = W.where(R.notna())
    return (R * W).sum(axis=1) / W.sum(axis=1).replace(0, np.nan)


def liquidations(liq: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Total USD liquidated per bar: 'longs' and 'shorts' columns."""
    if not liq:
        return pd.DataFrame(columns=["longs", "shorts"])
    longs = pd.concat([df["l"] for df in liq.values()], axis=1).sum(axis=1)
    shorts = pd.concat([df["s"] for df in liq.values()], axis=1).sum(axis=1)
    return pd.DataFrame({"longs": longs, "shorts": shorts})


def coinbase_premium(coinbase_close: pd.Series, binance_close: pd.Series) -> pd.Series:
    """Coinbase USD price vs Binance USDT price, in %. Persistent negative =
    US spot buyers are the weaker side (selling or absent); positive = US
    demand leading. Small USDT/USD deviations shift it slightly."""
    b = binance_close.reindex(coinbase_close.index)
    return (coinbase_close / b - 1) * 100
