"""mempool.space: live mempool state (recorded every 10 minutes) plus
historical mining data (hash rate, difficulty, pools) and Lightning stats.
Public API, no key: https://mempool.space/docs/api/rest
"""
from __future__ import annotations

import pandas as pd

from .http import get_json

BASE = "https://mempool.space/api"
# Other servers running the same mempool.space software (identical API), tried in order.
MIRRORS = [BASE, "https://mempool.emzy.de/api"]

# Fee-rate bands (sat/vB) used to split the mempool, low to high.
FEE_BANDS = [0, 1, 2, 3, 5, 10, 20, 50, 100, 1e9]


def band_label(lo: float, hi: float) -> str:
    return f"{lo:g}+" if hi >= 1e9 else f"{lo:g}-{hi:g}"


FALLBACK = "https://blockstream.info/api"
# Blockstream's fee estimates (sat/vB to confirm within N blocks) mapped onto
# mempool.space's recommended-fee names.
FEE_TARGETS = {"fastestFee": "1", "halfHourFee": "3", "hourFee": "6", "economyFee": "144", "minimumFee": "1008"}


def _mempool_and_fees() -> tuple[dict, dict]:
    """(mempool summary, recommended fees) from the first mempool.space-software
    server that answers, else from Blockstream's Esplora API (same /mempool format)."""
    for base in MIRRORS:
        try:
            return get_json(f"{base}/mempool"), get_json(f"{base}/v1/fees/recommended")
        except Exception:  # noqa: BLE001 - try the next server
            continue
    m = get_json(f"{FALLBACK}/mempool")       # Blockstream: same /mempool, its own fee estimates
    est = get_json(f"{FALLBACK}/fee-estimates")
    return m, {k: est[n] for k, n in FEE_TARGETS.items() if n in est}


def _mirrored(path: str):
    """GET path from the first mempool.space-software server that answers."""
    last = None
    for base in MIRRORS:
        try:
            return get_json(f"{base}{path}")
        except Exception as e:  # noqa: BLE001
            last = e
    raise last


def mempool_state() -> dict:
    """count, vsize (vB), total_fee (sats), recommended fees, and vsize and fees
    per fee-rate band (from the fee histogram of [fee rate, vsize] pairs)."""
    m, rec = _mempool_and_fees()
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
    d = _mirrored(f"/v1/mining/hashrate/{period}")
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
