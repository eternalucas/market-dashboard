"""Validation for the backtest: time split, paired block bootstrap,
deflated Sharpe ratio and the confidence label.

- Split: round trips ordered by entry time; the first 70% train, the last
  30% test. Every parameter and selection threshold is chosen on train.
- Paired stationary block bootstrap (Politis & Romano 1994): daily P&L
  of the variant and of the actual trades over the test days, resampled
  together in blocks of geometric length (mean 5 days), 2,000 times; 90%
  intervals for the change in total P&L and in Sharpe.
- Deflated Sharpe ratio (Bailey & Lopez de Prado 2014): the probability
  that the variant's Sharpe is above zero once N tried variants (and the
  returns' skew and kurtosis) are accounted for.
- Confidence: High = the P&L interval excludes 0, deflated Sharpe >= 0.95
  and 50+ test trips; Medium = the interval excludes 0; Low otherwise.
"""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

N01 = NormalDist()
EULER = 0.5772156649015329


def split_index(n: int, train: float = 0.7) -> int:
    """Trips [0, k) train, [k, n) test (trips sorted by entry time)."""
    return int(math.floor(n * train))


def daily(values: np.ndarray, exit_ts: pd.Series, days: pd.DatetimeIndex) -> pd.Series:
    """Trip results summed by the UTC day they closed, on a fixed day index."""
    s = pd.Series(values, index=pd.DatetimeIndex(exit_ts).floor("D")).groupby(level=0).sum()
    return s.reindex(days, fill_value=0.0)


def stationary_indices(n: int, n_boot: int = 2000, mean_block: float = 5.0, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    idx = np.empty((n_boot, n), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, n_boot)
    new = rng.random((n_boot, n)) < 1.0 / mean_block
    jump = rng.integers(0, n, (n_boot, n))
    for t in range(1, n):
        idx[:, t] = np.where(new[:, t], jump[:, t], (idx[:, t - 1] + 1) % n)
    return idx


def sharpe(r: np.ndarray, axis=-1) -> np.ndarray:
    sd = r.std(axis=axis, ddof=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(sd > 0, r.mean(axis=axis) / sd, np.nan)


def paired_bootstrap(variant: np.ndarray, actual: np.ndarray, equity: np.ndarray, idx: np.ndarray,
                     level: float = 0.90) -> dict:
    """variant/actual: daily P&L on the same days; equity: account value
    used to turn P&L into returns. Returns the point estimates and intervals."""
    d = variant - actual
    lo, hi = (1 - level) / 2 * 100, (1 + level) / 2 * 100
    boot_tot = d[idx].sum(axis=1)
    rv, ra = variant / equity, actual / equity
    sh_v, sh_a = sharpe(rv[idx]), sharpe(ra[idx])
    dsh = (sh_v - sh_a) * math.sqrt(365)
    dsh = dsh[np.isfinite(dsh)]
    return {"delta": float(d.sum()), "delta_ci": (float(np.percentile(boot_tot, lo)), float(np.percentile(boot_tot, hi))),
            "sharpe_actual": float(sharpe(ra) * math.sqrt(365)) if len(ra) > 2 else np.nan,
            "sharpe_variant": float(sharpe(rv) * math.sqrt(365)) if len(rv) > 2 else np.nan,
            "sharpe_delta_ci": (float(np.percentile(dsh, lo)), float(np.percentile(dsh, hi))) if len(dsh) else (np.nan, np.nan),
            "p_better": float((boot_tot > 0).mean())}


def _moments(r: np.ndarray) -> tuple[float, float]:
    m, s = r.mean(), r.std()
    if s == 0 or len(r) < 3:
        return 0.0, 3.0
    z = (r - m) / s
    return float((z ** 3).mean()), float((z ** 4).mean())


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """SR0: the Sharpe the best of N unskilled variants would show by chance."""
    if n_trials <= 1 or var_sr <= 0:
        return 0.0
    return math.sqrt(var_sr) * ((1 - EULER) * N01.inv_cdf(1 - 1 / n_trials) +
                                EULER * N01.inv_cdf(1 - 1 / (n_trials * math.e)))


def deflated_sharpe(r: np.ndarray, n_trials: int, var_sr: float) -> float:
    """Probability that the true (per-period) Sharpe exceeds SR0."""
    r = np.asarray(r, float)
    T = len(r)
    if T < 5 or r.std(ddof=1) == 0:
        return np.nan
    sr = r.mean() / r.std(ddof=1)
    skew, kurt = _moments(r)
    sr0 = expected_max_sharpe(n_trials, var_sr)
    den = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if den <= 0:
        return np.nan
    return float(N01.cdf((sr - sr0) * math.sqrt(T - 1) / math.sqrt(den)))


def label(delta_ci: tuple, dsr: float, test_trips: int) -> str:
    excl = delta_ci[0] > 0 or delta_ci[1] < 0
    if excl and dsr == dsr and dsr >= 0.95 and test_trips >= 50:
        return "High"
    if excl:
        return "Medium"
    return "Low"
