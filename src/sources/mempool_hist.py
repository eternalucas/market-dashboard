"""mempool.space history: daily hash rate, difficulty adjustments and
Lightning network totals. (The live mempool is recorded by src/recorder.py.)

Raw series: mp:hashrate (H/s), mp:difficulty (at each adjustment, forward-
filled to days by the metrics layer), mp:ln:<field>.
"""
from __future__ import annotations

import pandas as pd

from .. import storage
from ..collectors import mempool_space
from .base import to_daily


def update(con) -> int:
    hr, diff = mempool_space.hashrate_history("all")
    rows = storage.save_series(con, "mp:hashrate", to_daily(hr, "mean"))
    rows += storage.save_series(con, "mp:difficulty", diff)
    ln = mempool_space.lightning_history("all")
    for col in ln.columns:
        rows += storage.save_series(con, f"mp:ln:{col}", to_daily(ln[col]))
    # mempool.space can keep answering with an old Lightning history: say so in
    # fetch_log instead of letting the source's ok row imply the data is current.
    last = pd.Timestamp(ln.index.max()) if len(ln) else None
    if last is not None and last.tz is None:
        last = last.tz_localize("UTC")
    if last is None or pd.Timestamp.now(tz="UTC") - last > LN_STALE_AFTER:
        storage.log_fetch(con, "mempool_hist:lightning", False, 0,
                          f"Lightning history stale: last point {last:%Y-%m-%d}" if last is not None else "Lightning history empty")
    return rows


LN_STALE_AFTER = pd.Timedelta(days=3)
