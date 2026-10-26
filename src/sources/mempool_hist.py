"""mempool.space history: daily hash rate, difficulty adjustments and
Lightning network totals. (The live mempool is recorded by src/recorder.py.)

Raw series: mp:hashrate (H/s), mp:difficulty (at each adjustment, forward-
filled to days by the metrics layer), mp:ln:<field>.
"""
from __future__ import annotations

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
    return rows
