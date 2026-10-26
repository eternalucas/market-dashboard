"""BGeometrics free API (bitcoin-data.com): on-chain valuation and cohort
metrics that otherwise need a node.

Free tier limits (portal.bgeometrics.com/bguser/free-features.html, read
2026-10-21): 15 requests a day, 8 an hour, last ~4 years of history, and the
most recent 7 days are withheld (data is 7 days delayed). No key.

So this adapter is a QUEUE, not a loop:
  * each call fetches one endpoint (all its fields, full available range on
    the first call, from 14 days before the last stored day afterwards);
  * at most DAILY_BUDGET calls per rolling 24 h and HOURLY_BUDGET per hour,
    counted from fetch_log, so the dashboard and the recorder share one budget;
  * endpoints never fetched go first (by PRIORITY order), then the stalest
    ones last fetched more than REFRESH_DAYS ago.
60 endpoints at 14 a day -> the full backfill takes about 5 days; after
that every endpoint refreshes weekly, which loses nothing because the free
data is 7 days behind anyway.

Raw series stored as  bg:<endpoint>:<field>  (e.g. bg:sth-mvrv:sthMvrv).
Fields are taken generically: every key except d / unixTs / delayed / message
whose values parse as numbers.
"""
from __future__ import annotations

import pandas as pd

from .. import storage
from ..collectors.http import get_json

BASE = "https://bitcoin-data.com/v1"
DAILY_BUDGET = 14     # one below the cap, so a manual check never trips it
HOURLY_BUDGET = 7
REFRESH_DAYS = 6
SKIP = {"d", "unixTs", "delayed", "message"}

# Endpoint order = fetch priority on the first backfill (most-used charts first).
PRIORITY = [
    "utxo-count", "sth-mvrv", "lth-mvrv", "sth-realized-price", "lth-realized-price", "sopr", "sth-sopr", "lth-sopr",
    "asopr", "nupl-sth", "nupl-lth", "hodl-waves-supply", "realized-cap-hodl-waves", "puell-multiple",
    "supply-profit", "supply-loss", "realized-profit", "realized-loss", "nrpl-usd", "etf-flow-btc",
    "etf-btc-total", "bitcoin-dominance", "realized_profit_lth", "realized_loss_lth", "realized_profit_sth",
    "realized_loss_sth", "cdd", "supply-adjusted-cdd", "cdd-90dma", "vdd-multiple", "asol", "aviv",
    "true-market-mean", "delta-cap", "investor-price", "thermo-cap", "utxos-in-profit-pct",
    "coins-addr-1-BTC", "coins-addr-10-1-BTC", "coins-addr-100-10-BTC", "coins-addr-1K-100-BTC",
    "coins-addr-10K-1K-BTC", "coins-addr-10K-BTC", "balance-addr-1-BTC", "balance-addr-10-1-BTC",
    "balance-addr-100-10-BTC", "balance-addr-1K-100-BTC", "balance-addr-10K-1K-BTC", "balance-addr-10K-BTC",
    "nrpl-btc", "profit-loss", "vdd", "terminal-price", "investor-cap", "thermo-price", "realized-price-delta",
    "utxos-in-profit-count", "utxos-in-loss-count", "utxos-in-loss-pct", "hashprice", "nvts",
]


def parse(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["d"], utc=True)
    cols = [c for c in df.columns if c not in SKIP]
    out = df[cols].apply(pd.to_numeric, errors="coerce")
    return out.loc[:, out.notna().any()].sort_index()


def _calls_since(con, hours: float) -> int:
    since = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=hours)).isoformat()
    return con.execute("SELECT COUNT(*) FROM fetch_log WHERE source LIKE 'bgeo:%' AND ts >= ?", (since,)).fetchone()[0]


def budget_left(con) -> int:
    return max(0, min(DAILY_BUDGET - _calls_since(con, 24), HOURLY_BUDGET - _calls_since(con, 1)))


def plan(con, n: int) -> list[str]:
    """The next n endpoints to call: never-fetched first, then stalest."""
    last = {}
    for ep in PRIORITY:
        ts = storage.last_success(con, f"bgeo:{ep}")
        last[ep] = None if ts is None else pd.Timestamp(ts).tz_convert("UTC") if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts).tz_localize("UTC")
    never = [ep for ep in PRIORITY if last[ep] is None]
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=REFRESH_DAYS)
    stale = sorted((ep for ep in PRIORITY if last[ep] is not None and last[ep] < cutoff), key=lambda e: last[e])
    return (never + stale)[:n]


def fetch_endpoint(con, ep: str) -> int:
    first_field = con.execute("SELECT series FROM observations WHERE series LIKE ? LIMIT 1", (f"bg:{ep}:%",)).fetchone()
    params = {}
    if first_field:
        s = storage.load_series(con, first_field[0])
        if not s.empty:
            params["startday"] = (s.index[-1] - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
    rows = get_json(f"{BASE}/{ep}", params or None)
    df = parse(rows if isinstance(rows, list) else [rows])
    return sum(storage.save_series(con, f"bg:{ep}:{c}", df[c]) for c in df.columns)


def update(con, max_calls: int | None = None) -> int:
    """Spend what's left of the budget (or max_calls) on the queue."""
    n = budget_left(con) if max_calls is None else min(max_calls, budget_left(con))
    total = 0
    for ep in plan(con, n):
        try:
            rows = fetch_endpoint(con, ep)
            storage.log_fetch(con, f"bgeo:{ep}", True, rows)
            total += rows
        except Exception as e:  # noqa: BLE001 - a failed call still counts against the budget
            storage.log_fetch(con, f"bgeo:{ep}", False, 0, f"{type(e).__name__}: {e}")
    return total


def queue_status(con) -> dict:
    done = sum(storage.last_success(con, f"bgeo:{ep}") is not None for ep in PRIORITY)
    return {"endpoints": len(PRIORITY), "backfilled": done, "calls_last_24h": _calls_since(con, 24),
            "budget_left_now": budget_left(con)}
