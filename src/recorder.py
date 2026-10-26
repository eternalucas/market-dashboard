"""Recorders: data that has no public history and only exists if we capture it.

Run it on its own (double-click "Start Recorder.command", or
`python -m src.recorder`) and leave it open, or let the dashboard run it in the
background while the dashboard is open. A lock file makes sure only one copy
records at a time, so both can be started safely.

Jobs and cadence (UTC):
  books          every 1 min   spot (Coinbase, Binance) and perp (Binance, Bybit, OKX,
                               Hyperliquid) BTC books -> book_metrics; every 10 min also
                               the fixed-grid heatmap per market
  futures_curve  every 15 min  dated futures on Deribit / Binance / OKX -> futures_curve
  option_trades  every 5 min   every Deribit BTC option trade since the last one -> option_trades
  option_trades_backfill  every hour  2 more days of older trades from history.deribit.com, to 1 year
  option_chain   every hour    full Deribit BTC option chain -> option_snap (venue deribit)
  mempool        every 10 min  mempool size, fees, fee-band split -> observations mempool:*
  ibit_chain     once a day    after the US close (20:30 UTC, weekdays) -> option_snap (venue ibit)
  lightning      once a day    Lightning averages and fee medians -> observations ln:*
  sources        every hour    daily data sources that are due (src/sources/registry.py),
                               plus the BGeometrics call queue
  housekeeping   once a day    drop minute-level Flows heatmap rows older than 7 days

Every write is an upsert on the table's primary key, so a repeated or
overlapping run never duplicates rows. Failures go to fetch_log; successes
are logged at most once an hour per job so the log stays readable.
"""
from __future__ import annotations

import fcntl
import threading
import time
import traceback
from datetime import datetime, timezone

import pandas as pd

from . import storage
from .collectors import books, deribit, futures_curve, ibit, mempool_space
from .indicators import orderbook as ob

ASSET = "BTC"
HEATMAP_STEP = {"BTC": 50.0}       # USD per bucket on the fixed price grid
HEATMAP_RANGE_PCT = 5.0
LOCK_PATH = storage.DB_PATH.parent / "recorder.lock"


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


class Recorder:
    def __init__(self):
        self.last_run: dict[str, float] = {}
        self.last_ok_log: dict[str, float] = {}
        self.con = None

    # ------------------------------------------------------------------ #
    def ok(self, source: str, rows: int) -> None:
        if time.time() - self.last_ok_log.get(source, 0) > 3600:
            storage.log_fetch(self.con, source, True, rows)
            self.last_ok_log[source] = time.time()

    def fail(self, source: str, e: Exception) -> None:
        storage.log_fetch(self.con, source, False, 0, f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------------ #
    def job_books(self) -> None:
        ts = _now().floor("min")
        rows, grids = [], {"spot": [], "perp": []}
        for market, venues in (("spot", books.SPOT), ("perp", books.PERP)):
            for venue, fetch in venues.items():
                src = f"rec:book:{market}:{venue}"
                try:
                    bids, asks = fetch(ASSET)
                    m = ob.snapshot_metrics(bids, asks)
                    rows.append({"ts": ts.isoformat(), "market": market, "venue": venue, "asset": ASSET, **m})
                    if ts.minute % 10 == 0:
                        grids[market].append(ob.heatmap_buckets(bids, asks, HEATMAP_STEP[ASSET], HEATMAP_RANGE_PCT))
                    self.ok(src, len(bids) + len(asks))
                except Exception as e:  # noqa: BLE001
                    self.fail(src, e)
        storage.save_frame(self.con, "book_metrics", pd.DataFrame(rows))
        for market, parts in grids.items():
            if parts:
                g = pd.concat(parts).groupby("price", as_index=False)["usd"].sum()
                g.insert(0, "ts", ts.isoformat())
                g.insert(0, "asset", ASSET)
                g.insert(0, "market", market)
                storage.save_frame(self.con, "heatmap", g)

    def job_futures_curve(self) -> None:
        ts = _now().floor("15min").isoformat()
        for venue, fetch in futures_curve.VENUES.items():
            src = f"rec:futures_curve:{venue}"
            try:
                df = fetch(ASSET).rename(columns={"index": "idx"})
                df["expiry"] = df["expiry"].map(lambda e: e.isoformat() if pd.notna(e) else None)
                df.insert(0, "ts", ts)
                self.ok(src, storage.save_frame(self.con, "futures_curve",
                                                df[["ts", "venue", "instrument", "expiry", "price", "idx", "oi_usd"]]))
            except Exception as e:  # noqa: BLE001
                self.fail(src, e)

    def job_option_trades(self) -> None:
        src = "rec:option_trades:deribit"
        try:
            row = self.con.execute("SELECT MAX(ts) FROM option_trades").fetchone()
            start = pd.Timestamp(row[0]) if row and row[0] else _now() - pd.Timedelta(hours=23)
            df = deribit.option_trades(int(start.timestamp() * 1000), int(time.time() * 1000), ASSET)
            self.ok(src, storage.save_frame(self.con, "option_trades", df))
        except Exception as e:  # noqa: BLE001
            self.fail(src, e)

    TRADES_BACKFILL_DAYS = 365

    def job_option_trades_backfill(self) -> None:
        """Hourly: extend option-trade history backwards by 2 days from
        Deribit's history host, until a year is stored."""
        src = "rec:option_trades_backfill:deribit"
        row = self.con.execute("SELECT MIN(ts) FROM option_trades").fetchone()
        if not row or not row[0]:
            return
        oldest = pd.Timestamp(row[0])
        if oldest < _now() - pd.Timedelta(days=self.TRADES_BACKFILL_DAYS):
            return
        try:
            start = oldest - pd.Timedelta(days=2)
            df = deribit.option_trades(int(start.timestamp() * 1000), int(oldest.timestamp() * 1000), ASSET, history=True)
            if df.empty:
                raise ValueError(f"no trades returned before {oldest:%Y-%m-%d}")
            self.ok(src, storage.save_frame(self.con, "option_trades", df))
        except Exception as e:  # noqa: BLE001
            self.fail(src, e)

    def job_option_chain(self) -> None:
        src = "rec:option_chain:deribit"
        try:
            df = deribit.option_chain(ASSET)
            ts = _now().floor("h").isoformat()
            df.insert(0, "ts", ts)
            df.insert(0, "venue", "deribit")
            self.ok(src, storage.save_frame(self.con, "option_snap", df))
            from .metrics import options as om
            om.store_snapshot_metrics(self.con, "deribit", ts)   # surface, skew, GEX, max pain
        except Exception as e:  # noqa: BLE001
            self.fail(src, e)

    def job_mempool(self) -> None:
        src = "rec:mempool"
        try:
            ts = _now().floor("10min")
            n = sum(storage.save_point(self.con, f"mempool:{k}", v, ts) for k, v in mempool_space.mempool_state().items())
            self.ok(src, n)
        except Exception as e:  # noqa: BLE001
            self.fail(src, e)

    def job_ibit_chain(self) -> None:
        now = _now()
        if now.weekday() >= 5 or now.hour * 60 + now.minute < 20 * 60 + 30:
            return  # weekends, or before the US close has settled
        last = storage.last_success(self.con, "rec:option_chain:ibit")
        if last is not None and pd.Timestamp(last).tz_convert("UTC").date() == now.date():
            return
        src = "rec:option_chain:ibit"
        try:
            df = ibit.chain("IBIT")
            df.insert(0, "ts", now.normalize().isoformat())
            df.insert(0, "venue", "ibit")
            rows = storage.save_frame(self.con, "option_snap", df)
            storage.log_fetch(self.con, src, True, rows)   # always logged: it gates the next day
            from .metrics import options as om
            om.store_snapshot_metrics(self.con, "ibit", now.normalize().isoformat())
        except Exception as e:  # noqa: BLE001
            self.fail(src, e)

    def job_lightning(self) -> None:
        src = "rec:lightning"
        try:
            today = _now().normalize()
            n = sum(storage.save_point(self.con, f"ln:{k}", v, today) for k, v in mempool_space.lightning_latest().items())
            self.ok(src, n)
        except Exception as e:  # noqa: BLE001
            self.fail(src, e)

    def job_sources(self) -> None:
        """Hourly: refresh the daily sources that are due (each has its own
        freshness window) and spend the BGeometrics call budget."""
        # Own thread and connection: a first backfill can take many minutes
        # and must not hold up the minute order-book samples.
        if getattr(self, "_src_thread", None) and self._src_thread.is_alive():
            return

        def run():
            from .config import load_config
            from .sources import registry
            c = storage.connect()
            try:
                registry.update_all(c, load_config(), say=lambda *_: None)
            finally:
                c.close()
        self._src_thread = threading.Thread(target=run, daemon=True, name="sources")
        self._src_thread.start()

    def job_trade_leaderboard(self) -> None:
        """Hourly: sync the Top 100 candidates that are due (each account at
        most every 20 h), in its own thread and database (data/trades.db),
        within a 40-minute budget so a first build spreads over a few runs."""
        if getattr(self, "_tl_thread", None) and self._tl_thread.is_alive():
            return

        def run():
            from .config import load_config
            from .review import leaderboard as lbm
            from .review import store as rstore
            c = rstore.connect()
            log = storage.connect()
            try:
                el = lbm.Eligibility.from_cfg(load_config())
                res = lbm.refresh(c, "hyperliquid", budget_s=2400, say=lambda *_: None, el=el)
                storage.log_fetch(log, "rec:trade_leaderboard", True, res["synced"],
                                  f"pool {res['pool']}, synced {res['synced']}, skipped {res['skipped']}, {res['seconds']} s")
            except Exception as e:  # noqa: BLE001
                storage.log_fetch(log, "rec:trade_leaderboard", False, 0, f"{type(e).__name__}: {e}")
            finally:
                c.close()
                log.close()
        self._tl_thread = threading.Thread(target=run, daemon=True, name="trade_leaderboard")
        self._tl_thread.start()

    def job_option_metrics_catchup(self) -> None:
        from .metrics import options as om
        for venue in ("deribit", "ibit"):
            try:
                om.catch_up(self.con, venue)
            except Exception as e:  # noqa: BLE001
                self.fail(f"rec:option_metrics:{venue}", e)

    def job_housekeeping(self) -> None:
        cutoff = (_now() - pd.Timedelta(days=7)).isoformat()
        storage.prune(self.con, "book_levels", "ts < ?", (cutoff,))

    # ------------------------------------------------------------------ #
    JOBS = [  # (name, every N seconds)
        ("books", 60), ("futures_curve", 900), ("option_trades", 300), ("option_trades_backfill", 3600), ("option_chain", 3600),
        ("mempool", 600), ("ibit_chain", 900), ("lightning", 86400), ("sources", 3600), ("option_metrics_catchup", 3600), ("housekeeping", 86400),
        ("trade_leaderboard", 3600),
    ]

    def run_due(self) -> None:
        for name, every in self.JOBS:
            # align to the clock: a 10-minute job runs at :00, :10, ...
            slot = int(time.time() // every)
            if self.last_run.get(name) == slot:
                continue
            self.last_run[name] = slot
            try:
                getattr(self, f"job_{name}")()
            except Exception as e:  # noqa: BLE001 - a job must never kill the loop
                self.fail(f"rec:{name}", e)
                traceback.print_exc()

    def loop(self, stop: threading.Event | None = None) -> None:
        self.con = storage.connect()
        while not (stop and stop.is_set()):
            self.run_due()
            time.sleep(5)


def acquire_lock():
    """Return an open lock file if this process may record, else None."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    f = open(LOCK_PATH, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        f.write(f"{datetime.now(timezone.utc).isoformat()}\n")
        f.flush()
        return f
    except OSError:
        f.close()
        return None


def start_background() -> str:
    """Start the recorder in a daemon thread unless another process already
    records. Returns 'started', 'running elsewhere' or 'already running'."""
    if getattr(start_background, "_thread", None) and start_background._thread.is_alive():
        return "already running"
    lock = acquire_lock()
    if lock is None:
        return "running elsewhere"
    t = threading.Thread(target=Recorder().loop, daemon=True, name="recorder")
    t._lock = lock  # keep the lock open for the thread's lifetime
    t.start()
    start_background._thread = t
    return "started"


def status(con) -> pd.DataFrame:
    """Latest success / failure per recorder job, for the Data health tab."""
    return pd.read_sql_query(
        "SELECT source, MAX(CASE WHEN ok=1 THEN ts END) AS last_ok, "
        "MAX(CASE WHEN ok=0 THEN ts END) AS last_error, "
        "(SELECT message FROM fetch_log f2 WHERE f2.source=f.source AND ok=0 ORDER BY ts DESC LIMIT 1) AS error "
        "FROM fetch_log f WHERE source LIKE 'rec:%' GROUP BY source ORDER BY source", con)


if __name__ == "__main__":
    lock = acquire_lock()
    if lock is None:
        print("Another recorder is already running (the dashboard or another window). Nothing to do.")
    else:
        print("Recording. Leave this window open; close it to stop.")
        Recorder().loop()
