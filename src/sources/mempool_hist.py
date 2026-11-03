"""mempool.space history: daily hash rate and difficulty adjustments
(Lightning was retired on 3 Nov 2026: no free source kept it current). (The live mempool is recorded by src/recorder.py.)

Raw series: mp:hashrate (H/s), mp:difficulty (at each adjustment, forward-
filled to days by the metrics layer).
"""
from __future__ import annotations

from .. import storage
from ..collectors import mempool_space
from .base import to_daily


def update(con) -> int:
    hr, diff = mempool_space.hashrate_history("all")
    rows = storage.save_series(con, "mp:hashrate", to_daily(hr, "mean"))
    rows += storage.save_series(con, "mp:difficulty", diff)
    return rows

