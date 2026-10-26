"""Calculations on derivatives and order-book data.

Kept separate from the collectors so each formula can be unit-tested with
hand-made numbers (see tests/test_positioning.py).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# Funding, basis, open interest
# --------------------------------------------------------------------------- #


def annualize_funding(rate_8h: float | pd.Series) -> float | pd.Series:
    """8-hour funding -> simple annual rate (3 payments a day x 365)."""
    return rate_8h * 3 * 365


def basis(mark: float, index: float) -> float:
    """Perp premium over spot index, as a fraction. Positive = perp above spot,
    i.e. longs are paying up for leverage."""
    return mark / index - 1


def summarize_perps(snaps: list[dict]) -> dict:
    """Combine per-exchange snapshots into one cross-exchange reading.

    Funding and basis are weighted by each exchange's open interest, so a big
    venue counts more than a small one.
    """
    df = pd.DataFrame(snaps)
    if df.empty:
        return {}
    df["basis"] = df["mark"] / df["index"] - 1
    w = df["oi_usd"] / df["oi_usd"].sum()
    f8 = float((df["funding_8h"] * w).sum())
    return {
        "total_oi_usd": float(df["oi_usd"].sum()),
        "oi_weighted_funding_8h": f8,
        "oi_weighted_funding_annual": annualize_funding(f8),
        "oi_weighted_basis": float((df["basis"] * w).sum()),
        "funding_spread_8h": float(df["funding_8h"].max() - df["funding_8h"].min()),
        "n_exchanges": int(len(df)),
    }


def zscore_latest(s: pd.Series, window: int = 90) -> float:
    """How unusual the latest value is versus the trailing window, in standard
    deviations. Used for alerts like 'funding is 2 sigma above normal'."""
    s = s.dropna()
    if len(s) < 10:
        return float("nan")
    tail = s.iloc[-window:]
    sd = tail.std()
    return float((tail.iloc[-1] - tail.mean()) / sd) if sd > 0 else 0.0


# --------------------------------------------------------------------------- #
# Order book depth and imbalance
# --------------------------------------------------------------------------- #


def book_depth(bids: pd.DataFrame, asks: pd.DataFrame, bands_pct: list[float]) -> tuple[dict, pd.DataFrame]:
    """USD resting within X% of the mid price on each side.

    imbalance = (bid_usd - ask_usd) / (bid_usd + ask_usd), from -1 (all asks:
    heavy offer, thin bid support) to +1 (all bids).

    coverage tells you how far from mid the returned book actually reaches:
    if an exchange only sends its top 500 levels and they span 0.8%, the 2%
    band is incomplete, and the table says so instead of silently
    understating depth.
    """
    bids = bids.sort_values("price", ascending=False)
    asks = asks.sort_values("price")
    best_bid, best_ask = bids["price"].iloc[0], asks["price"].iloc[0]
    mid = (best_bid + best_ask) / 2
    bid_reach = (mid - bids["price"].min()) / mid * 100
    ask_reach = (asks["price"].max() - mid) / mid * 100

    rows = []
    for b in bands_pct:
        lo, hi = mid * (1 - b / 100), mid * (1 + b / 100)
        bid_usd = float((bids.loc[bids["price"] >= lo, "price"] * bids.loc[bids["price"] >= lo, "size"]).sum())
        ask_usd = float((asks.loc[asks["price"] <= hi, "price"] * asks.loc[asks["price"] <= hi, "size"]).sum())
        total = bid_usd + ask_usd
        rows.append({
            "band_pct": b,
            "bid_usd": bid_usd,
            "ask_usd": ask_usd,
            "imbalance": (bid_usd - ask_usd) / total if total else float("nan"),
            "complete": bool(bid_reach >= b and ask_reach >= b),
        })
    meta = {
        "mid": mid,
        "spread_bps": (best_ask - best_bid) / mid * 1e4,
        "bid_reach_pct": bid_reach,
        "ask_reach_pct": ask_reach,
    }
    return meta, pd.DataFrame(rows)


def depth_curve(bids: pd.DataFrame, asks: pd.DataFrame, max_pct: float = 2.0) -> pd.DataFrame:
    """Cumulative USD depth by distance from mid, for the depth chart."""
    bids = bids.sort_values("price", ascending=False)
    asks = asks.sort_values("price")
    mid = (bids["price"].iloc[0] + asks["price"].iloc[0]) / 2
    b = bids.assign(side="bid", dist_pct=(bids["price"] / mid - 1) * 100,
                    cum_usd=(bids["price"] * bids["size"]).cumsum())
    a = asks.assign(side="ask", dist_pct=(asks["price"] / mid - 1) * 100,
                    cum_usd=(asks["price"] * asks["size"]).cumsum())
    out = pd.concat([b, a])
    return out[out["dist_pct"].abs() <= max_pct]


# --------------------------------------------------------------------------- #
# Options skew (25-delta risk reversal)
# --------------------------------------------------------------------------- #


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_delta(forward: float, strike: float, iv: float, t_years: float, kind: str) -> float:
    """Black-76 delta on the forward (Deribit quotes each expiry's forward, so
    no interest-rate input is needed)."""
    if iv <= 0 or t_years <= 0:
        return float("nan")
    d1 = (math.log(forward / strike) + 0.5 * iv * iv * t_years) / (iv * math.sqrt(t_years))
    call = _norm_cdf(d1)
    return call if kind == "C" else call - 1


def skew_25d(options: pd.DataFrame, now: pd.Timestamp, target_days: int = 30, min_days: int = 3) -> dict:
    """25-delta risk reversal for the listed expiry closest to target_days.

    risk_reversal = IV(25-delta call) - IV(25-delta put)
      negative -> puts cost more than calls: the market is paying for downside
                  protection (fear)
      positive -> calls cost more: the market is chasing upside (greed)

    IVs at exactly 25 delta are interpolated between neighbouring strikes.
    """
    if options.empty:
        return {}
    days = (options["expiry"] - now).dt.total_seconds() / 86400
    expiries = options.loc[days >= min_days, "expiry"].unique()
    if len(expiries) == 0:
        return {}
    exp = min(expiries, key=lambda e: abs((e - now).total_seconds() / 86400 - target_days))
    chain = options[options["expiry"] == exp].copy()
    t = (exp - now).total_seconds() / (365 * 86400)
    fwd = float(chain["forward"].median())
    chain["delta"] = [bs_delta(fwd, k, iv, t, c) for k, iv, c in zip(chain["strike"], chain["iv"], chain["type"])]
    chain = chain.dropna(subset=["delta"])

    calls = chain[(chain["type"] == "C") & (chain["strike"] >= fwd)].sort_values("delta")
    puts = chain[(chain["type"] == "P") & (chain["strike"] <= fwd)].assign(absd=lambda d: d["delta"].abs()).sort_values("absd")
    if len(calls) < 2 or len(puts) < 2:
        return {}
    call25 = float(np.interp(0.25, calls["delta"], calls["iv"]))
    put25 = float(np.interp(0.25, puts["absd"], puts["iv"]))
    atm_row = chain.iloc[(chain["strike"] - fwd).abs().argsort()[:2]]
    return {
        "expiry": pd.Timestamp(exp),
        "days_to_expiry": round((exp - now).total_seconds() / 86400, 1),
        "forward": fwd,
        "atm_iv": float(atm_row["iv"].mean()),
        "call25_iv": call25,
        "put25_iv": put25,
        "risk_reversal": call25 - put25,
    }


# --------------------------------------------------------------------------- #
# Options open interest: net delta and OI by strike
# --------------------------------------------------------------------------- #


def options_with_delta(options: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    """Add each option's delta (Black-76 on its own expiry's forward) and its
    delta-weighted open interest. Expired contracts are dropped."""
    df = options.copy()
    df["t"] = (df["expiry"] - now).dt.total_seconds() / (365 * 86400)
    df = df[df["t"] > 0]
    df["delta"] = [bs_delta(f, k, iv, t, c) for f, k, iv, t, c in
                   zip(df["forward"], df["strike"], df["iv"], df["t"], df["type"])]
    df["delta_oi"] = df["delta"] * df["oi"]
    return df.dropna(subset=["delta"])


def net_delta_by_expiry(df: pd.DataFrame) -> pd.DataFrame:
    """Per expiry: call and put open interest, put/call ratio, and net delta.

    Net delta = sum of (open interest x delta), in coins. It is the delta that
    option *holders* carry in aggregate (writers carry the opposite). Positive
    means holders are net positioned for upside; negative, for downside. Who
    is long versus short isn't public, so read it as positioning tilt, not as
    dealer hedging flow.
    """
    g = df.groupby("expiry")
    out = pd.DataFrame({
        "call_oi": g.apply(lambda x: x.loc[x["type"] == "C", "oi"].sum(), include_groups=False),
        "put_oi": g.apply(lambda x: x.loc[x["type"] == "P", "oi"].sum(), include_groups=False),
        "net_delta": g["delta_oi"].sum(),
    })
    out["put_call_ratio"] = out["put_oi"] / out["call_oi"].replace(0, np.nan)
    return out.sort_index()


def oi_by_strike(df: pd.DataFrame, expiry) -> pd.DataFrame:
    """Call and put open interest at each strike for one expiry."""
    one = df[df["expiry"] == expiry]
    return one.pivot_table(index="strike", columns="type", values="oi", aggfunc="sum").fillna(0)


# --------------------------------------------------------------------------- #
# Order-book heatmap buckets
# --------------------------------------------------------------------------- #


def nice_step(mid: float, target_pct: float = 0.05) -> float:
    """A round price step near target_pct of mid (e.g. $50 at BTC $100k)."""
    raw = mid * target_pct / 100
    mag = 10 ** math.floor(math.log10(raw))
    return min((m * mag for m in (1, 2, 5, 10)), key=lambda x: abs(x - raw))


def bucket_book(bids: pd.DataFrame, asks: pd.DataFrame, range_pct: float = 3.0) -> pd.DataFrame:
    """Resting USD per price bucket within +/- range_pct of mid. One snapshot
    of these, stored every minute, becomes one column of the heatmap."""
    mid = (bids["price"].max() + asks["price"].min()) / 2
    step = nice_step(mid)
    lo, hi = mid * (1 - range_pct / 100), mid * (1 + range_pct / 100)
    parts = []
    for side, df in (("bid_usd", bids), ("ask_usd", asks)):
        d = df[(df["price"] >= lo) & (df["price"] <= hi)]
        usd = (d["price"] * d["size"]).groupby((d["price"] // step) * step).sum()
        parts.append(usd.rename(side))
    out = pd.concat(parts, axis=1).fillna(0.0)
    out.index.name = "price"
    return out.reset_index()
