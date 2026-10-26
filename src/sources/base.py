"""Helpers shared by the source adapters: daily normalisation and checks."""
from __future__ import annotations

import pandas as pd

from .. import storage


def to_daily(s: pd.Series, how: str = "last") -> pd.Series:
    """Normalise to one value per UTC day, stamped at 00:00 UTC (Glassnode's
    and Coin Metrics' convention: the bar for day D is labelled D 00:00)."""
    s = s.dropna()
    if s.empty:
        return s
    s.index = pd.to_datetime(s.index, utc=True)
    return getattr(s.resample("1D"), how)().dropna()


def save_daily(con, name: str, s: pd.Series, how: str = "last") -> int:
    return storage.save_series(con, name, to_daily(s, how))


def check_series(s: pd.Series, freq: str = "1D", lo: float | None = None, hi: float | None = None,
                 max_gap: int = 3, flat_run: int = 10) -> list[str]:
    """Data checks used by tests and by the Phase 5 review: duplicate
    timestamps, gaps, flat-lined runs, values outside a plausible range.
    Returns a list of problems (empty = clean)."""
    problems = []
    if s.empty:
        return ["empty"]
    if s.index.has_duplicates:
        problems.append(f"{s.index.duplicated().sum()} duplicate timestamps")
    step = pd.Timedelta(freq)
    gaps = s.index.to_series().diff().dropna()
    big = gaps[gaps > step * max_gap]
    if len(big):
        problems.append(f"{len(big)} gaps > {max_gap}x{freq} (largest {big.max()} ending {big.idxmax():%Y-%m-%d})")
    runs = (s.diff() != 0).cumsum()
    longest = runs.value_counts().max()
    if longest >= flat_run:
        problems.append(f"flat run of {longest} identical values")
    if lo is not None and (s < lo).any():
        problems.append(f"{(s < lo).sum()} values below {lo}")
    if hi is not None and (s > hi).any():
        problems.append(f"{(s > hi).sum()} values above {hi}")
    return problems
