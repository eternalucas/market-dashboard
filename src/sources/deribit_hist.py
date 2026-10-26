"""Deribit history that IS public: DVOL (30-day implied vol index) daily OHLC
since 2021. Raw series: dvol:BTC:<open|high|low|close> (vol points)."""
from __future__ import annotations

from .. import storage
from ..collectors import deribit


def update(con) -> int:
    df = deribit.dvol_history("BTC", 86400)
    return sum(storage.save_series(con, f"dvol:BTC:{c}", df[c].astype(float)) for c in df.columns)
