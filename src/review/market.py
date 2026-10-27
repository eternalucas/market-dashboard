"""Market data for the trader diagnosis and backtests: candles at the finest
resolution the venue still keeps, and the market's hourly funding rates.

Hyperliquid keeps only the most recent 5,000 candles per interval, so the
resolution available for a trade depends on its age:
    1m  ~3.5 days     15m ~52 days     1h ~208 days     4h ~833 days     1d all
Each trade is measured on the finest interval that covers it, and the
interval used is reported with every result (data-quality panel).
Candles and funding are stored in data/trades.db, so each range is fetched
once; the venue's rate limiter is shared with the rest of the dashboard.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import store

INTERVALS = ["1m", "15m", "1h", "4h", "1d"]
INTERVAL_TD = {"1m": pd.Timedelta(minutes=1), "15m": pd.Timedelta(minutes=15), "1h": pd.Timedelta(hours=1),
               "4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1)}
KEEP = 5000                         # candles per interval the venue keeps
CONTEXT_LOOKBACK = pd.Timedelta(days=30)


def retention_start(interval: str, now: pd.Timestamp) -> pd.Timestamp:
    return now - INTERVAL_TD[interval] * (KEEP - 1)


def _stored_range(con, venue: str, coin: str, interval: str) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    lo, hi = con.execute("SELECT MIN(ts), MAX(ts) FROM tr_candles WHERE venue=? AND coin=? AND interval=?",
                         (venue, coin, interval)).fetchone()
    return (pd.Timestamp(lo) if lo else None, pd.Timestamp(hi) if hi else None)


def chosen_coins(trips: pd.DataFrame, max_coins: int = 24, cover: float = 0.98) -> tuple[list[str], list[str]]:
    """The most traded coins, enough to cover `cover` of the trips (at most
    max_coins); the rest are left unmeasured and listed as such."""
    if trips is None or trips.empty:
        return [], []
    counts = trips["coin"].value_counts()
    share = counts.cumsum() / counts.sum()
    k = int(min(max_coins, (share < cover).sum() + 1))
    return list(counts.index[:k]), list(counts.index[k:])


def plan(trips: pd.DataFrame, now: pd.Timestamp, coins: list[str] | None = None
         ) -> list[tuple[str, str, pd.Timestamp, pd.Timestamp]]:
    """(coin, interval, start, end) ranges needed: each trip's finest
    available interval, plus 1h (else 4h) bars from 30 days before the
    first trip for the entry context (trend, volatility)."""
    out = []
    if trips is None or trips.empty:
        return out
    t = trips.assign(end=trips["close_ts"].fillna(now))
    if coins is not None:
        t = t[t["coin"].isin(coins)]
    for coin, g in t.groupby("coin"):
        remaining = g
        for iv in INTERVALS:
            if remaining.empty:
                break
            keep_from = retention_start(iv, now)
            fits = remaining[remaining["open_ts"] >= keep_from] if iv != "1d" else remaining
            if not fits.empty:
                out.append((coin, iv, fits["open_ts"].min().floor(INTERVAL_TD[iv]), fits["end"].max()))
                remaining = remaining.drop(fits.index)
        # context bars
        for iv in ("1h", "4h"):
            keep_from = retention_start(iv, now)
            if g["open_ts"].max() >= keep_from:
                out.append((coin, iv, max(g["open_ts"].min() - CONTEXT_LOOKBACK, keep_from).floor(INTERVAL_TD[iv]),
                            g["end"].max()))
                break
    # merge duplicate (coin, interval) ranges
    merged: dict[tuple[str, str], list] = {}
    for coin, iv, s, e in out:
        k = (coin, iv)
        merged[k] = [min(s, merged[k][0]), max(e, merged[k][1])] if k in merged else [s, e]
    return [(c, iv, s, e) for (c, iv), (s, e) in merged.items()]


def _gaps(con, venue, coin, iv, start, end, now) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    lo, hi = _stored_range(con, venue, coin, iv)
    step = INTERVAL_TD[iv]
    start = max(start, retention_start(iv, now)) if iv != "1d" else start
    gaps = []
    floor = store.get_sync(con, venue, coin, f"candles_{iv}_floor")["cursor_ms"]
    if lo is None:
        gaps.append((start, end))
    else:
        asked = floor is not None and floor <= start.timestamp() * 1000
        if start < lo - step and lo > retention_start(iv, now) + step and not asked:
            gaps.append((start, lo))
        if end > hi + 2 * step:          # the current bar is always still forming
            gaps.append((hi, end))
    return gaps


def missing(con, needs, venue: str = "hyperliquid", now: pd.Timestamp | None = None) -> list:
    """The needs that still have something to fetch (no network)."""
    now = now or pd.Timestamp.now(tz="UTC")
    return [n for n in needs if _gaps(con, venue, *n, now)]


def ensure_candles(con, ad, needs, venue: str = "hyperliquid", budget_s: float = 240, progress=None,
                   now: pd.Timestamp | None = None) -> dict:
    """Fetch what isn't stored yet. Returns {"fetched": n, "errors": {...},
    "skipped": [...]} (skipped = not reached within the time budget)."""
    now = now or pd.Timestamp.now(tz="UTC")
    rep = {"fetched": 0, "errors": {}, "skipped": []}
    t0 = time.time()
    for i, (coin, iv, start, end) in enumerate(needs):
        if progress:
            progress(i / max(len(needs), 1), f"Prices: {coin} {iv}")
        step = INTERVAL_TD[iv]
        for s, e in _gaps(con, venue, coin, iv, start, end, now):
            if time.time() - t0 > budget_s:
                rep["skipped"].append(f"{coin} {iv}")
                continue
            try:
                c = ad.candles(coin, iv, int(s.timestamp() * 1000), int(e.timestamp() * 1000))
                if not c.empty:
                    store.upsert(con, "tr_candles", c)
                rep["fetched"] += 1
                if c.empty or c["ts"].min() > s + step:   # the venue has nothing earlier: don't ask again
                    prev = store.get_sync(con, venue, coin, f"candles_{iv}_floor")["cursor_ms"]
                    ms = int(s.timestamp() * 1000)
                    store.set_sync(con, venue, coin, f"candles_{iv}_floor", min(ms, prev) if prev else ms)
            except Exception as ex:  # noqa: BLE001 - recorded and shown in Data quality
                rep["errors"][f"{coin} {iv}"] = f"{type(ex).__name__}: {ex}"[:200]
    if progress:
        progress(1.0, "Prices loaded")
    return rep


FUNDING_LOOKBACK = pd.Timedelta(days=60)


def ensure_market_funding(con, ad, trips: pd.DataFrame, coins: list[str], venue: str = "hyperliquid",
                          budget_s: float = 120, now: pd.Timestamp | None = None) -> dict:
    """The market's hourly funding over the last 60 days (each month costs
    ~2 calls per coin). Older entries use the rate on the account's own
    funding payments where it held that coin at the time."""
    rep = {"coins": [], "errors": {}, "not_fetched": []}
    if trips is None or trips.empty:
        return rep
    now = now or pd.Timestamp.now(tz="UTC")
    t0 = time.time()
    for coin in coins:
        if time.time() - t0 > budget_s:
            rep["not_fetched"].append(coin)
            continue
        first = max(trips.loc[trips["coin"] == coin, "open_ts"].min() - pd.Timedelta(hours=2), now - FUNDING_LOOKBACK)
        hi = con.execute("SELECT MAX(ts), MIN(ts) FROM tr_market_funding WHERE venue=? AND coin=?",
                         (venue, coin)).fetchone()
        try:
            if hi[0] is None or pd.Timestamp(hi[1]) > first + pd.Timedelta(hours=2):
                start = first
            elif now - pd.Timestamp(hi[0]) > pd.Timedelta(hours=1):
                start = pd.Timestamp(hi[0]) + pd.Timedelta(seconds=1)
            else:
                rep["coins"].append(coin)
                continue
            f = ad.market_funding(coin, int(start.timestamp() * 1000))
            if not f.empty:
                store.upsert(con, "tr_market_funding", f)
            rep["coins"].append(coin)
        except Exception as ex:  # noqa: BLE001
            rep["errors"][coin] = f"{type(ex).__name__}: {ex}"[:200]
    return rep


# --------------------------------------------------------------------------- #
# Read side
# --------------------------------------------------------------------------- #

@dataclass
class Prices:
    """Stored candles and funding for one account's coins, held in memory."""
    bars: dict[tuple[str, str], pd.DataFrame] = field(default_factory=dict)
    funding: dict[str, pd.Series] = field(default_factory=dict)

    @classmethod
    def load(cls, con, coins, venue: str = "hyperliquid", account_funding: pd.DataFrame | None = None) -> "Prices":
        p = cls()
        for coin in coins:
            for iv in INTERVALS:
                c = store.candles(con, venue, coin, iv)
                if not c.empty:
                    p.bars[(coin, iv)] = c
            f = pd.read_sql_query("SELECT ts, rate FROM tr_market_funding WHERE venue=? AND coin=? ORDER BY ts",
                                  con, params=(venue, coin))
            s = pd.Series(f["rate"].to_numpy(), index=pd.to_datetime(f["ts"], utc=True, format="ISO8601")) \
                if not f.empty else pd.Series(dtype=float)
            if account_funding is not None and not account_funding.empty and "rate" in account_funding:
                a = account_funding[account_funding["coin"] == coin]
                if not a.empty:   # the account's own payments carry the same hourly rate
                    s2 = pd.Series(a["rate"].to_numpy(), index=a["ts"]).dropna()
                    s = s.combine_first(s2) if not s.empty else s2
            if not s.empty:
                p.funding[coin] = s[~s.index.duplicated()].sort_index()
        return p

    def finest(self, coin: str, start: pd.Timestamp, end: pd.Timestamp) -> tuple[str | None, pd.DataFrame]:
        """Bars of the finest interval covering [start, end]: every bar that
        overlaps the period. Intervals coarser than 15 minutes are only used
        when the trip spans at least 3 of their bars (a 2-hour trade measured
        on a daily candle would show the whole day's range). (None, empty)
        when nothing suitable covers it."""
        for iv in INTERVALS:
            b = self.bars.get((coin, iv))
            if b is None or b.empty:
                continue
            step = INTERVAL_TD[iv]
            if iv not in ("1m", "15m") and end - start < 3 * step:
                return None, pd.DataFrame()
            if b.index[0] <= start.floor(step) and b.index[-1] + step >= end:
                sel = b[(b.index > start - step) & (b.index <= end)]
                if not sel.empty:
                    return iv, sel
        return None, pd.DataFrame()

    def context(self, coin: str) -> tuple[str | None, pd.DataFrame]:
        for iv in ("1h", "4h", "1d"):
            b = self.bars.get((coin, iv))
            if b is not None and len(b) > 30:
                return iv, b
        return None, pd.DataFrame()

    def funding_at(self, coin: str, ts: pd.Timestamp) -> float:
        """Last hourly rate settled at or before ts (no look-ahead)."""
        s = self.funding.get(coin)
        if s is None or s.empty:
            return np.nan
        k = s.index.searchsorted(ts, side="right") - 1
        if k < 0 or ts - s.index[k] > pd.Timedelta(hours=2):
            return np.nan
        return float(s.iloc[k])
