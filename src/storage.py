"""SQLite storage.

Everything is kept in one "long" table: one row per (series, timestamp, value).
A series name looks like "wiki:Bitcoin" or "funding_8h:bybit:BTC". This keeps
storage simple: any collector can save any time series without a new table.

A second table, fetch_log, records every fetch attempt (source, success or
failure, row count, error). The dashboard's Data health tab reads it.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# MARKET_DB lets tests point at a throwaway database instead of the real one.
DB_PATH = Path(os.environ.get("MARKET_DB", Path(__file__).resolve().parent.parent / "data" / "market.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    series TEXT NOT NULL,
    ts     TEXT NOT NULL,      -- ISO 8601, UTC
    value  REAL,
    PRIMARY KEY (series, ts)
);
CREATE TABLE IF NOT EXISTS book_levels (   -- order-book snapshots for the heatmap
    asset   TEXT NOT NULL,
    ts      TEXT NOT NULL,
    price   REAL NOT NULL,     -- bucket floor
    bid_usd REAL,
    ask_usd REAL,
    PRIMARY KEY (asset, ts, price)
);
CREATE TABLE IF NOT EXISTS events (       -- macro calendar for the event study
    date  TEXT NOT NULL,
    event TEXT NOT NULL,
    PRIMARY KEY (date, event)
);
CREATE TABLE IF NOT EXISTS option_snap (  -- raw option chain snapshots (Deribit hourly, IBIT daily)
    venue      TEXT NOT NULL,     -- 'deribit' or 'ibit'
    ts         TEXT NOT NULL,     -- snapshot time, UTC ISO
    expiry     TEXT NOT NULL,     -- UTC ISO
    strike     REAL NOT NULL,
    type       TEXT NOT NULL,     -- 'C' or 'P'
    iv         REAL,              -- mark implied volatility, decimal (0.45 = 45%)
    bid_iv     REAL,
    ask_iv     REAL,
    oi         REAL,              -- open interest, in contracts (BTC for Deribit, shares/100 for IBIT)
    volume     REAL,              -- 24h volume, contracts
    mark_price REAL,              -- option price in quote units (BTC for Deribit, USD for IBIT)
    bid        REAL,
    ask        REAL,
    underlying REAL,              -- forward / underlying price, USD
    PRIMARY KEY (venue, ts, expiry, strike, type)
);
CREATE TABLE IF NOT EXISTS option_trades ( -- every Deribit BTC option trade
    trade_id   TEXT PRIMARY KEY,
    ts         TEXT NOT NULL,
    instrument TEXT NOT NULL,
    expiry     TEXT NOT NULL,
    strike     REAL NOT NULL,
    type       TEXT NOT NULL,
    direction  TEXT NOT NULL,     -- taker side: 'buy' or 'sell'
    amount     REAL NOT NULL,     -- contracts (1 = 1 BTC)
    price      REAL NOT NULL,     -- premium per contract, BTC
    index_price REAL NOT NULL,    -- USD
    iv         REAL,              -- decimal
    combo      INTEGER DEFAULT 0  -- 1 when the trade was part of a combo / block strategy
);
CREATE INDEX IF NOT EXISTS option_trades_ts ON option_trades (ts);
CREATE TABLE IF NOT EXISTS book_metrics (  -- one row per venue per minute (see indicators/orderbook.py)
    ts TEXT NOT NULL, market TEXT NOT NULL, venue TEXT NOT NULL, asset TEXT NOT NULL,
    mid REAL, spread_bps REAL,
    bid_25 REAL, ask_25 REAL, bid_100 REAL, ask_100 REAL, bid_500 REAL, ask_500 REAL,
    bid_1000 REAL, ask_1000 REAL, bid_2000 REAL, ask_2000 REAL,
    impact_buy_100k REAL, impact_sell_100k REAL, impact_buy_1m REAL, impact_sell_1m REAL,
    impact_buy_10m REAL, impact_sell_10m REAL,
    PRIMARY KEY (market, venue, asset, ts)
);
CREATE TABLE IF NOT EXISTS heatmap (       -- resting notional per fixed price bucket, venues summed
    market TEXT NOT NULL, asset TEXT NOT NULL, ts TEXT NOT NULL, price REAL NOT NULL, usd REAL,
    PRIMARY KEY (market, asset, ts, price)
);
CREATE TABLE IF NOT EXISTS futures_curve ( -- dated futures snapshots for term structure / basis
    ts TEXT NOT NULL, venue TEXT NOT NULL, instrument TEXT NOT NULL, expiry TEXT,
    price REAL, idx REAL, oi_usd REAL,
    PRIMARY KEY (venue, instrument, ts)
);
CREATE TABLE IF NOT EXISTS liquidations (  -- actual forced closes from the exchanges' public feeds
    venue TEXT NOT NULL, asset TEXT NOT NULL, ts TEXT NOT NULL,
    side  TEXT NOT NULL,      -- position that was liquidated: 'long' or 'short'
    price REAL NOT NULL, qty REAL NOT NULL, usd REAL,
    PRIMARY KEY (venue, asset, ts, side, price, qty)
);
CREATE INDEX IF NOT EXISTS liquidations_ts ON liquidations (asset, ts);
CREATE TABLE IF NOT EXISTS hl_liq_levels (  -- exact Hyperliquid liquidation levels, one snapshot per scan
    coin TEXT NOT NULL, ts TEXT NOT NULL, side TEXT NOT NULL, price REAL NOT NULL, usd REAL,
    PRIMARY KEY (coin, ts, side, price)
);
CREATE TABLE IF NOT EXISTS fetch_log (
    ts      TEXT NOT NULL,
    source  TEXT NOT NULL,
    ok      INTEGER NOT NULL,
    rows    INTEGER,
    message TEXT
);
"""


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # timeout + WAL: the update writes from two threads at once (Coinalyze runs
    # alongside the other sources) while the dashboard keeps reading.
    con = sqlite3.connect(db_path, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def save_series(con: sqlite3.Connection, series: str, data: pd.Series) -> int:
    """Upsert a pandas Series indexed by timestamp. Returns rows written.

    Re-running a fetch overwrites the same timestamps instead of duplicating
    them, so updates are safe to repeat.
    """
    data = data.dropna()
    if data.empty:
        return 0
    idx = pd.to_datetime(data.index, utc=True)
    rows = [(series, t.isoformat(), float(v)) for t, v in zip(idx, data.values)]
    con.executemany(
        "INSERT OR REPLACE INTO observations (series, ts, value) VALUES (?, ?, ?)", rows
    )
    con.commit()
    return len(rows)


def save_point(con: sqlite3.Connection, series: str, value: float, ts: datetime | None = None) -> int:
    ts = ts or now_hour()
    return save_series(con, series, pd.Series([value], index=[ts]))


def load_series(con: sqlite3.Connection, series: str) -> pd.Series:
    df = pd.read_sql_query(
        "SELECT ts, value FROM observations WHERE series = ? ORDER BY ts", con, params=(series,)
    )
    if df.empty:
        return pd.Series(dtype=float, name=series, index=pd.DatetimeIndex([], tz="UTC"))
    s = pd.Series(df["value"].values, index=pd.to_datetime(df["ts"], utc=True, format="ISO8601"), name=series)
    return s


def load_prefix(con: sqlite3.Connection, prefix: str) -> dict[str, pd.Series]:
    """All series whose name starts with prefix, e.g. 'wiki:' -> {name: Series}."""
    return {n: load_series(con, n) for n in series_names(con, prefix)}


def series_names(con: sqlite3.Connection, prefix: str) -> list[str]:
    """Names starting with prefix. A range on the primary key instead of
    LIKE, because SQLite can't use the index for LIKE (a full scan of a few
    million rows per call)."""
    rows = con.execute("SELECT DISTINCT series FROM observations WHERE series >= ? AND series < ?",
                       (prefix, prefix + "\U0010ffff"))
    return sorted(r[0] for r in rows)


def log_fetch(con: sqlite3.Connection, source: str, ok: bool, rows: int = 0, message: str = "") -> None:
    con.execute(
        "INSERT INTO fetch_log (ts, source, ok, rows, message) VALUES (?, ?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), source, int(ok), rows, message[:500]),
    )
    con.commit()


def fetch_log(con: sqlite3.Connection, limit: int = 500) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT ts, source, ok, rows, message FROM fetch_log ORDER BY ts DESC LIMIT ?",
        con, params=(limit,),
    )


def now_hour() -> datetime:
    """Current UTC time rounded down to the hour, so snapshots taken within the
    same hour replace each other instead of piling up."""
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)


def save_book_levels(con: sqlite3.Connection, asset: str, levels: pd.DataFrame, ts: datetime) -> int:
    """levels: DataFrame with price, bid_usd, ask_usd (one row per price bucket)."""
    rows = [(asset, ts.isoformat(), float(r.price), float(r.bid_usd), float(r.ask_usd))
            for r in levels.itertuples()]
    con.executemany("INSERT OR REPLACE INTO book_levels VALUES (?, ?, ?, ?, ?)", rows)
    con.commit()
    return len(rows)


def load_book_levels(con: sqlite3.Connection, asset: str, since: datetime) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT ts, price, bid_usd, ask_usd FROM book_levels WHERE asset = ? AND ts >= ? ORDER BY ts",
        con, params=(asset, since.isoformat()))
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    return df


def replace_event_type(con: sqlite3.Connection, event: str, events: pd.DataFrame) -> int:
    """Swap every stored date of one event type for a new set."""
    con.execute("DELETE FROM events WHERE event = ?", (event,))
    con.commit()
    return save_events(con, events)


def save_events(con: sqlite3.Connection, events: pd.DataFrame) -> int:
    rows = [(d.strftime("%Y-%m-%d"), e) for d, e in zip(pd.to_datetime(events["date"]), events["event"])]
    con.executemany("INSERT OR REPLACE INTO events (date, event) VALUES (?, ?)", rows)
    con.commit()
    return len(rows)


def load_events(con: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT date, event FROM events ORDER BY date", con)
    df["date"] = pd.to_datetime(df["date"])
    return df


def save_frame(con: sqlite3.Connection, table: str, df: pd.DataFrame) -> int:
    """Upsert a DataFrame into `table` (columns must match the table's).
    INSERT OR REPLACE on the primary key keeps repeated snapshots idempotent."""
    if df.empty:
        return 0
    cols = list(df.columns)
    vals = df.astype(object).where(df.notna(), None).values.tolist()
    con.executemany(f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
    con.commit()
    return len(vals)


def load_frame(con: sqlite3.Connection, sql: str, params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, con, params=params)


def prune(con: sqlite3.Connection, table: str, where: str, params: tuple = ()) -> int:
    n = con.execute(f"DELETE FROM {table} WHERE {where}", params).rowcount
    con.commit()
    return n


def last_success(con: sqlite3.Connection, source: str):
    """Time of the most recent successful fetch of one source, or None."""
    row = con.execute("SELECT MAX(ts) FROM fetch_log WHERE source = ? AND ok = 1", (source,)).fetchone()
    return pd.Timestamp(row[0]) if row and row[0] else None
