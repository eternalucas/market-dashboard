"""Shared access to stored raw series, plus small numeric helpers."""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
import pandas as pd

from .. import storage

GENESIS = pd.Timestamp("2009-01-03", tz="UTC")
HALVINGS = pd.to_datetime(["2012-11-28", "2016-07-09", "2020-05-11", "2024-04-20"], utc=True)


class Ctx:
    """Read-only, cached view of the database for one computation pass."""

    def __init__(self, con):
        self.con = con
        self._cache: dict[str, pd.Series] = {}

    def s(self, name: str) -> pd.Series:
        if name not in self._cache:
            self._cache[name] = storage.load_series(self.con, name)
        return self._cache[name]

    def prefix(self, prefix: str) -> dict[str, pd.Series]:
        return {n: self.s(n) for n in storage.series_names(self.con, prefix)}

    def frame(self, sql: str, params: tuple = ()) -> pd.DataFrame:
        return pd.read_sql_query(sql, self.con, params=params)

    # Core daily series used everywhere -------------------------------------
    def price(self) -> pd.Series:
        """BTC daily close, USD (Coin Metrics reference rate, stamped 00:00 UTC of the day)."""
        return self.s("cm:PriceUSD")

    def mcap(self) -> pd.Series:
        return self.s("cm:CapMrktCurUSD")

    def supply(self) -> pd.Series:
        return self.s("cm:SplyCur")

    def realized_cap(self) -> pd.Series:
        """Realized cap = market cap / MVRV (Coin Metrics publishes MVRV but
        not realized cap on the free tier; this inversion is exact)."""
        return (self.mcap() / self.s("cm:CapMVRVCur")).dropna()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def zscore(s: pd.Series, n: int) -> pd.Series:
    r = s.rolling(n, min_periods=max(10, n // 3))
    return (s - r.mean()) / r.std()


def align(*series: pd.Series) -> list[pd.Series]:
    df = pd.concat(series, axis=1, join="inner")
    return [df.iloc[:, i] for i in range(df.shape[1])]


def norm_cdf(x):
    return 0.5 * (1 + np.vectorize(math.erf)(np.asarray(x, dtype=float) / math.sqrt(2)))


def norm_pdf(x):
    x = np.asarray(x, dtype=float)
    return np.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)
