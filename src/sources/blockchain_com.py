"""blockchain.com charts API (free, no key): miner revenue, transfer volume,
UTXO count, transaction counts. https://www.blockchain.com/explorer/api/charts_api

The API thins out long ranges ("sampled") unless sampled=false, and
unsampled full histories can hold ~1M points (UTXO count is per block), so
history is fetched one year at a time and reduced to one value per UTC day.

Raw series stored as  bc:<chart-name>.
"""
from __future__ import annotations

import time

import pandas as pd

from .. import storage
from ..collectors.http import get_json
from .base import to_daily

BASE = "https://api.blockchain.info/charts"
# chart name -> how to reduce intraday points to a day
CHARTS = {
    "miners-revenue": "last",                 # USD per day (block rewards + fees)
    "transaction-fees-usd": "last",           # USD per day
    "estimated-transaction-volume-usd": "last",
    "output-volume": "last",                  # BTC per day, all outputs
    "utxo-count": "last",                     # number of unspent outputs
    "n-transactions": "last",
    "n-transactions-total": "last",
    "avg-block-size": "last",                 # MB
}
FIRST_YEAR = 2009


def fetch(chart: str, start: str, timespan: str = "1year") -> pd.Series:
    d = get_json(f"{BASE}/{chart}", {"timespan": timespan, "start": start, "format": "json",
                                       "sampled": "false", "cors": "true"})
    vals = d.get("values") or []
    return pd.Series({pd.to_datetime(v["x"], unit="s", utc=True): float(v["y"]) for v in vals}, dtype=float)


def update(con) -> int:
    rows, failed = 0, []
    for chart, how in CHARTS.items():
        try:
            rows += _update_chart(con, chart, how)
        except Exception as e:  # noqa: BLE001 - one bad chart must not stop the others
            failed.append(f"{chart}: {type(e).__name__}")
    if failed and not rows:
        raise RuntimeError("; ".join(failed))
    if failed:
        storage.log_fetch(con, "blockchain_com:partial", False, rows, "; ".join(failed))
    return rows


def _update_chart(con, chart: str, how: str) -> int:
    rows = 0
    this_year = pd.Timestamp.now(tz="UTC").year
    last = storage.load_series(con, f"bc:{chart}")
    years = range(FIRST_YEAR, this_year + 1) if last.empty else range(last.index[-1].year, this_year + 1)
    for y in years:
        start = f"{y}-01-01" if last.empty or y > last.index[-1].year else \
            (last.index[-1] - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
        s = fetch(chart, start)
        rows += storage.save_series(con, f"bc:{chart}", to_daily(s, how))
        time.sleep(0.5)
    return rows
