"""mempool.space: live mempool state (recorded every 10 minutes) plus
historical mining data (hash rate, difficulty, pools) and Lightning stats.
Public API, no key: https://mempool.space/docs/api/rest
"""
from __future__ import annotations

import pandas as pd

from .http import get_json

BASE = "https://mempool.space/api"

# Fee-rate bands (sat/vB) used to split the mempool, low to high.
FEE_BANDS = [0, 1, 2, 3, 5, 10, 20, 50, 100, 1e9]


def band_label(lo: float, hi: float) -> str:
    return f"{lo:g}+" if hi >= 1e9 else f"{lo:g}-{hi:g}"


def mempool_state() -> dict:
    """count, vsize (vB), total_fee (sats), recommended fees, and vsize and fees
    per fee-rate band (from the fee histogram of [fee rate, vsize] pairs)."""
    m = get_json(f"{BASE}/mempool")
    rec = get_json(f"{BASE}/v1/fees/recommended")
    out = {"count": float(m["count"]), "vsize": float(m["vsize"]), "total_fee": float(m["total_fee"])}
    out.update({f"rec_{k}": float(v) for k, v in rec.items()})
    hist = pd.DataFrame(m.get("fee_histogram") or [], columns=["rate", "vsize"])
    for lo, hi in zip(FEE_BANDS[:-1], FEE_BANDS[1:]):
        h = hist[(hist["rate"] >= lo) & (hist["rate"] < hi)]
        lab = band_label(lo, hi)
        out[f"vsize_band:{lab}"] = float(h["vsize"].sum())
        out[f"fees_band:{lab}"] = float((h["rate"] * h["vsize"]).sum())   # sats
    if out["total_fee"] and out["vsize"]:
        out["avg_fee_rate"] = out["total_fee"] / out["vsize"]
    if not hist.empty:  # vsize-weighted median fee rate
        h = hist.sort_values("rate")
        cum = h["vsize"].cumsum()
        out["median_fee_rate"] = float(h.loc[cum >= cum.iloc[-1] / 2, "rate"].iloc[0])
    return out


def hashrate_history(period: str = "all") -> tuple[pd.Series, pd.Series]:
    """(daily average hash rate in H/s, difficulty at each adjustment)."""
    d = get_json(f"{BASE}/v1/mining/hashrate/{period}")
    hr = pd.Series({pd.to_datetime(x["timestamp"], unit="s", utc=True): float(x["avgHashrate"]) for x in d["hashrates"]})
    diff = pd.Series({pd.to_datetime(x["time"], unit="s", utc=True): float(x["difficulty"]) for x in d["difficulty"]})
    return hr.sort_index(), diff.sort_index()


def lightning_history(period: str = "all") -> pd.DataFrame:
    """Daily Lightning network totals since 2018: channel_count,
    total_capacity (sats) and node counts by type. node_count = tor +
    clearnet + clearnet_tor + unannounced (mempool.space's own total)."""
    df = pd.DataFrame(get_json(f"{BASE}/v1/lightning/statistics/{period}"))
    df.index = pd.to_datetime(df.pop("added"), unit="s", utc=True)
    df["node_count"] = df[["tor_nodes", "clearnet_nodes", "clearnet_tor_nodes", "unannounced_nodes"]].sum(axis=1)
    return df.sort_index().astype(float)


def lightning_latest() -> dict:
    """Today's Lightning stats, including the averages and fee medians that
    the history endpoint leaves out (recorded daily from the day we start)."""
    return {k: float(v) for k, v in get_json(f"{BASE}/v1/lightning/statistics/latest")["latest"].items()
            if isinstance(v, (int, float))}


def pools(period: str = "1w") -> pd.DataFrame:
    d = get_json(f"{BASE}/v1/mining/pools/{period}")
    return pd.DataFrame(d["pools"])
