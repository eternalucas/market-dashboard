"""Coin Metrics community API (free, no key): daily BTC network, market and
exchange-flow data back to 2009-2010.

Docs: https://docs.coinmetrics.io/api/v4  (community tier: 10 requests per
6 seconds per IP). Values for the latest day carry a "-status": "flash" flag
and can be revised, so every update re-fetches the last 7 days.

Raw series stored as  cm:<MetricName>  (e.g. cm:CapMVRVCur).
"""
from __future__ import annotations

import time

import pandas as pd

from .. import storage
from ..collectors.http import get_json

BASE = "https://community-api.coinmetrics.io/v4"

# Every BTC metric on the community tier (catalog checked 2026-10-21).
METRICS = [
    "AdrActCnt", "AdrBalCnt", "BlkCnt", "CapMVRVCur", "CapMrktCurUSD", "FeeTotNtv",
    "FlowInExNtv", "FlowInExUSD", "FlowOutExNtv", "FlowOutExUSD", "HashRate",
    "IssTotNtv", "IssTotUSD", "PriceUSD", "SplyCur", "SplyExNtv", "SplyExUSD",
    "TxCnt", "TxTfrCnt",
]
EXTRA = ["volume_reported_spot_usd_1d"]   # fetched separately: one unavailable metric fails a whole request


def fetch(metrics: list[str], start: str = "2009-01-03", asset: str = "btc") -> pd.DataFrame:
    """Wide DataFrame (UTC day x metric), following next_page_url."""
    url, params = f"{BASE}/timeseries/asset-metrics", {
        "assets": asset, "metrics": ",".join(metrics), "frequency": "1d",
        "start_time": start, "page_size": 10000}
    rows = []
    while url:
        d = get_json(url, params)
        rows += d.get("data", [])
        url, params = d.get("next_page_url"), None
        if url:
            time.sleep(0.7)   # stay under 10 requests / 6 s
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df.pop("time"), utc=True)
    keep = [c for c in df.columns if c in metrics]
    return df[keep].apply(pd.to_numeric, errors="coerce").sort_index()


def update(con) -> int:
    last = storage.load_series(con, "cm:PriceUSD")
    start = "2009-01-03" if last.empty else (last.index[-1] - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    df = fetch(METRICS, start)
    rows = sum(storage.save_series(con, f"cm:{m}", df[m]) for m in df.columns)
    for m in EXTRA:
        try:
            ex = fetch([m], start)
            rows += sum(storage.save_series(con, f"cm:{x}", ex[x]) for x in ex.columns)
        except Exception:  # noqa: BLE001 - optional metric
            pass
    # ETH market cap and price, for BTC/ETH dominance and the BTC vs ETH charts
    last_eth = storage.load_series(con, "cm:eth:CapMrktCurUSD")
    start_eth = "2015-08-08" if last_eth.empty else (last_eth.index[-1] - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    eth = fetch(["CapMrktCurUSD", "PriceUSD", "CapMVRVCur"], start_eth, asset="eth")
    rows += sum(storage.save_series(con, f"cm:eth:{m}", eth[m]) for m in eth.columns)
    return rows
