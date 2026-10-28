"""Normalized trade-review data, kept in its own SQLite file (data/trades.db)
so trader histories don't bloat the market database. Every row records its
venue and account_id. All tables are created with IF NOT EXISTS: adding
them changes nothing that exists, and deleting the file resets the feature.

Rows are written with INSERT OR REPLACE on their natural keys, so every
refresh is incremental and safe to repeat.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("TRADES_DB", ROOT / "data" / "trades.db"))
ENGINE_VERSION = 2          # bump when round-trip reconstruction changes (part of the backtest cache key)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tr_accounts (
    venue TEXT NOT NULL, account_id TEXT NOT NULL,
    owner_address TEXT, subaccount_name TEXT, label TEXT,
    first_seen TEXT, kind TEXT,                 -- trader | market_maker | vault | unknown
    kind_reason TEXT,
    PRIMARY KEY (venue, account_id)
);
CREATE TABLE IF NOT EXISTS tr_fills (
    venue TEXT NOT NULL, account_id TEXT NOT NULL, fill_id TEXT NOT NULL,
    ts TEXT NOT NULL, coin TEXT NOT NULL, side TEXT NOT NULL,      -- side: B buy / A sell
    px REAL, sz REAL, start_position REAL, closed_pnl REAL,
    fee REAL, fee_token TEXT, builder_fee REAL, is_taker INTEGER, order_id TEXT,
    liq_role TEXT, liq_mark_px REAL,                                -- liquidated | counterparty | NULL
    PRIMARY KEY (venue, account_id, fill_id)
);
CREATE INDEX IF NOT EXISTS tr_fills_acct_ts ON tr_fills (venue, account_id, ts);
CREATE TABLE IF NOT EXISTS tr_funding (
    venue TEXT NOT NULL, account_id TEXT NOT NULL, ts TEXT NOT NULL, coin TEXT NOT NULL,
    usdc REAL, position_size REAL, rate REAL,
    PRIMARY KEY (venue, account_id, ts, coin)
);
CREATE TABLE IF NOT EXISTS tr_liquidations (
    venue TEXT NOT NULL, account_id TEXT NOT NULL, ts TEXT NOT NULL, coin TEXT NOT NULL,
    size REAL, px REAL, mark_px REAL, method TEXT, fill_id TEXT,
    PRIMARY KEY (venue, account_id, ts, coin, fill_id)
);
CREATE TABLE IF NOT EXISTS tr_ledger (
    venue TEXT NOT NULL, account_id TEXT NOT NULL, ts TEXT NOT NULL, ref TEXT NOT NULL,
    kind TEXT, usdc REAL,
    PRIMARY KEY (venue, account_id, ts, ref)
);
CREATE TABLE IF NOT EXISTS tr_account_snapshots (
    venue TEXT NOT NULL, account_id TEXT NOT NULL, ts TEXT NOT NULL,
    account_value REAL, source TEXT,                                -- portfolio | clearinghouse
    PRIMARY KEY (venue, account_id, ts, source)
);
CREATE TABLE IF NOT EXISTS tr_round_trips (
    venue TEXT NOT NULL, account_id TEXT NOT NULL, trip_id TEXT NOT NULL,
    coin TEXT, side TEXT, open_ts TEXT, close_ts TEXT,
    entry_qty REAL, avg_entry REAL, avg_exit REAL, max_qty REAL, max_notional REAL,
    realized_pnl REAL, fees REAL, funding REAL, net REAL,
    fills INTEGER, taker_fills INTEGER, adds INTEGER, added_while_losing INTEGER,
    liquidated INTEGER, is_open INTEGER, engine_version INTEGER,
    PRIMARY KEY (venue, account_id, trip_id)
);
CREATE TABLE IF NOT EXISTS tr_candles (
    venue TEXT NOT NULL, coin TEXT NOT NULL, interval TEXT NOT NULL, ts TEXT NOT NULL,
    o REAL, h REAL, l REAL, c REAL, v REAL,
    PRIMARY KEY (venue, coin, interval, ts)
);
CREATE TABLE IF NOT EXISTS tr_market_funding (
    venue TEXT NOT NULL, coin TEXT NOT NULL, ts TEXT NOT NULL, rate REAL,
    PRIMARY KEY (venue, coin, ts)
);
CREATE TABLE IF NOT EXISTS tr_sync_state (
    venue TEXT NOT NULL, account_id TEXT NOT NULL, dataset TEXT NOT NULL,
    cursor_ms INTEGER, last_ok TEXT, notes TEXT,
    PRIMARY KEY (venue, account_id, dataset)
);
CREATE TABLE IF NOT EXISTS tr_metrics (            -- one row per account per window, recomputed on refresh
    venue TEXT NOT NULL, account_id TEXT NOT NULL, window TEXT NOT NULL,
    as_of TEXT, eligible INTEGER, exclusion TEXT, flags TEXT,
    pnl REAL, roe REAL, sharpe REAL, sortino REAL, max_dd_usd REAL, max_dd_pct REAL,
    profit_factor REAL, pct_weeks_profitable REAL, trips INTEGER, active_days INTEGER,
    metrics_json TEXT,
    PRIMARY KEY (venue, account_id, window)
);
CREATE TABLE IF NOT EXISTS tr_leaderboard_snapshots (
    venue TEXT NOT NULL, window TEXT NOT NULL, as_of TEXT NOT NULL, account_id TEXT NOT NULL,
    rank INTEGER, pnl REAL, metrics_json TEXT,
    PRIMARY KEY (venue, window, as_of, account_id)
);
CREATE TABLE IF NOT EXISTS tr_candidates (         -- who the venue's own leaderboard says to look at
    venue TEXT NOT NULL, account_id TEXT NOT NULL, as_of TEXT NOT NULL,
    source_windows TEXT, venue_pnl_json TEXT, account_value REAL,
    PRIMARY KEY (venue, account_id)
);
CREATE TABLE IF NOT EXISTS tr_seen (              -- accounts seen in a venue's public trades (Lighter discovery)
    venue TEXT NOT NULL, account_id TEXT NOT NULL, notional REAL, trades INTEGER, first_seen TEXT, last_seen TEXT,
    PRIMARY KEY (venue, account_id)
);
CREATE TABLE IF NOT EXISTS tr_backtest_runs (
    run_id TEXT PRIMARY KEY, venue TEXT, account_id TEXT, window TEXT, config_hash TEXT, data_version TEXT,
    status TEXT, progress REAL, message TEXT, started TEXT, finished TEXT, result_json TEXT
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def _iso(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True).map(lambda t: t.isoformat())


def upsert(con: sqlite3.Connection, table: str, df: pd.DataFrame, ts_cols: tuple[str, ...] = ("ts",)) -> int:
    if df is None or df.empty:
        return 0
    df = df.copy()
    for c in ts_cols:
        if c in df:
            df[c] = _iso(df[c])
    df = df.astype(object).where(df.notna(), None)
    cols = list(df.columns)
    con.executemany(f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    df.itertuples(index=False, name=None))
    con.commit()
    return len(df)


def load(con: sqlite3.Connection, table: str, venue: str, account_id: str | None = None,
         ts_cols: tuple[str, ...] = ("ts",), order: str = "ts") -> pd.DataFrame:
    sql, params = f"SELECT * FROM {table} WHERE venue = ?", [venue]
    if account_id is not None:
        sql += " AND account_id = ?"
        params.append(account_id)
    if order:
        sql += f" ORDER BY {order}"
    df = pd.read_sql_query(sql, con, params=params)
    for c in ts_cols:
        if c in df:
            df[c] = pd.to_datetime(df[c], utc=True, format="ISO8601")
    return df


def candles(con, venue: str, coin: str, interval: str) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT ts, o, h, l, c, v FROM tr_candles WHERE venue=? AND coin=? AND interval=? ORDER BY ts",
                           con, params=(venue, coin, interval))
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    return df.set_index("ts")


def get_sync(con, venue: str, account_id: str, dataset: str) -> dict:
    r = con.execute("SELECT cursor_ms, last_ok, notes FROM tr_sync_state WHERE venue=? AND account_id=? AND dataset=?",
                    (venue, account_id, dataset)).fetchone()
    return {"cursor_ms": r[0], "last_ok": r[1], "notes": json.loads(r[2]) if r and r[2] else {}} if r else \
        {"cursor_ms": None, "last_ok": None, "notes": {}}


def set_sync(con, venue: str, account_id: str, dataset: str, cursor_ms: int | None, notes: dict | None = None) -> None:
    con.execute("INSERT OR REPLACE INTO tr_sync_state VALUES (?, ?, ?, ?, ?, ?)",
                (venue, account_id, dataset, cursor_ms, pd.Timestamp.now(tz="UTC").isoformat(),
                 json.dumps(notes or {}, default=str)))
    con.commit()


def data_version(con, venue: str, account_id: str) -> str:
    """Changes whenever the account's stored data changes (part of the backtest cache key)."""
    n, last = con.execute("SELECT COUNT(*), MAX(ts) FROM tr_fills WHERE venue=? AND account_id=?",
                          (venue, account_id)).fetchone()
    f = con.execute("SELECT COUNT(*), MAX(ts) FROM tr_funding WHERE venue=? AND account_id=?",
                    (venue, account_id)).fetchone()
    return f"e{ENGINE_VERSION}-f{n}-{last}-u{f[0]}-{f[1]}"
