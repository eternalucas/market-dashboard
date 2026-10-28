"""Background backtest jobs with progress, cached by (trader, venue,
window, config hash, data version) in tr_backtest_runs.

A run never overwrites actual data: it only writes its own row. The same
inputs give the same run_id, so a finished run is reused instead of
recomputed; changing an assumption or syncing new fills makes a new run.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time

import numpy as np
import pandas as pd

from . import backtest as bt
from . import costs as cst
from . import diagnosis as dg
from . import equity as eq
from . import market as mk
from . import replay as rp
from . import rules as rl
from . import store
from .reconstruct import reconstruct

WINDOWS = {"all": None, "90d": 90, "30d": 30}
RESULT_VERSION = 3                 # bump when the engine or the stored result changes (part of the cache key)
_THREADS: dict[str, threading.Thread] = {}
_LOCK = threading.Lock()


def config_hash(a: cst.Assumptions) -> str:
    return hashlib.sha1(json.dumps(a.as_dict(), sort_keys=True).encode()).hexdigest()[:12]


def run_id(venue: str, acct: str, window: str, a: cst.Assumptions, data_version: str) -> str:
    key = f"{venue}|{acct}|{window}|{config_hash(a)}|{data_version}|v{RESULT_VERSION}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def get(con, rid: str) -> dict | None:
    r = con.execute("SELECT status, progress, message, started, finished, result_json FROM tr_backtest_runs "
                    "WHERE run_id=?", (rid,)).fetchone()
    if not r:
        return None
    return {"status": r[0], "progress": r[1], "message": r[2], "started": r[3], "finished": r[4],
            "result": json.loads(r[5]) if r[5] else None}


def _set(con, rid, **kw):
    cols = ", ".join(f"{k}=?" for k in kw)
    con.execute(f"UPDATE tr_backtest_runs SET {cols} WHERE run_id=?", (*kw.values(), rid))
    con.commit()


def start(venue: str, acct: str, window: str, a: cst.Assumptions, fetch: bool = True) -> str:
    """Start (or reuse) a run; returns its run_id immediately."""
    con = store.connect()
    try:
        rid = run_id(venue, acct, window, a, store.data_version(con, venue, acct))
        cur = get(con, rid)
        with _LOCK:
            alive = rid in _THREADS and _THREADS[rid].is_alive()
            if cur and (cur["status"] == "done" or alive):
                return rid
            con.execute("INSERT OR REPLACE INTO tr_backtest_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (rid, venue, acct, window, config_hash(a), store.data_version(con, venue, acct), "running",
                         0.0, "Starting", pd.Timestamp.now(tz="UTC").isoformat(), None, None))
            con.commit()
            th = threading.Thread(target=_run, args=(rid, venue, acct, window, a, fetch), daemon=True,
                                  name=f"backtest-{rid[:6]}")
            _THREADS[rid] = th
            th.start()
        return rid
    finally:
        con.close()


def _run(rid, venue, acct, window, a, fetch):
    con = store.connect()
    last = [0.0]

    def progress(p, msg):
        if time.time() - last[0] > 1 or p >= 1:
            last[0] = time.time()
            _set(con, rid, progress=float(p), message=msg)
    try:
        res = compute(con, venue, acct, window, a, progress, fetch)
        blob = json.dumps(bt.clean(res))
        _set(con, rid, status="done", progress=1.0, message="Done", finished=pd.Timestamp.now(tz="UTC").isoformat(),
             result_json=blob)
        try:                                   # a copy as a file (data/ is never committed)
            out = store.ROOT / "data" / "backtests"
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{venue}_{acct}_{window}_{rid}.json").write_text(blob)
        except OSError:
            pass
    except Exception as e:  # noqa: BLE001 - shown on the page
        _set(con, rid, status="error", message=f"{type(e).__name__}: {e}"[:500],
             finished=pd.Timestamp.now(tz="UTC").isoformat())
    finally:
        con.close()


def compute(con, venue: str, acct: str, window: str, a: cst.Assumptions, progress=None, fetch: bool = True,
            now: pd.Timestamp | None = None, ad=None) -> dict:
    now = now or pd.Timestamp.now(tz="UTC")
    say = progress or (lambda p, m: None)
    fills = store.load(con, "tr_fills", venue, acct)
    funding = store.load(con, "tr_funding", venue, acct)
    trips, rep = reconstruct(fills, funding, legs=True)
    if trips.empty:
        return {"status": "too_few_trips", "trips": 0, "min_trips": bt.MIN_TRIPS}
    days = WINDOWS.get(window)
    if days:
        trips = trips[trips["open_ts"] >= now - pd.Timedelta(days=days)]
    closed = dg.closed_trips(trips)
    if len(closed) < bt.MIN_TRIPS:
        return {"status": "too_few_trips", "trips": int(len(closed)), "min_trips": bt.MIN_TRIPS}
    coins, skipped = mk.chosen_coins(closed)
    if fetch:
        from .. import venues
        ad = ad or venues.get(venue)
        say(0.01, "Loading prices")
        mk.ensure_candles(con, ad, mk.plan(closed, now, coins), venue, budget_s=420,
                          progress=lambda p, m: say(0.01 + 0.03 * p, m), now=now)
        mk.ensure_market_funding(con, ad, closed, coins, venue, budget_s=150, now=now)
    prices = mk.Prices.load(con, coins, venue, account_funding=funding)
    snaps = store.load(con, "tr_account_snapshots", venue, acct)
    avs = [g.set_index("ts")["account_value"] for _, g in snaps.groupby("source")] if not snaps.empty else []
    span = pd.date_range(closed["open_ts"].min().floor("D"), now.floor("D"), freq="D", tz="UTC")
    equity = eq.daily_equity(avs, span) if avs else pd.Series(dtype=float)
    say(0.045, "Measuring trips")
    enr = dg.enrich(closed.drop(columns="legs"), prices, equity, now)
    T = rp.prepare(closed, funding, prices, equity, enr)
    rates = cst.resolve(a, fills)
    e = equity[equity > 0] if len(equity) else equity
    equity_ref = float(e.median()) if len(e) else float(np.median([t.max_notional for t in T]))
    res = bt.run(T, rates, a, equity_ref, progress=say)
    res["equity_ref_source"] = "median account value" if len(e) else "median position size (no account history)"
    res["window"] = window
    res["skipped_coins"] = skipped
    res["data_version"] = store.data_version(con, venue, acct)
    res["config_hash"] = config_hash(a)
    res["history"] = {"first_trip": closed["open_ts"].min().isoformat(), "last_trip": closed["open_ts"].max().isoformat(),
                      "excluded_fills": rep["excluded_fills"]}
    if res.get("status") == "done":
        say(0.91, "Diagnosis summary")
        try:                                   # Right / Wrong for the plan, from the same trips
            d = dg.run(closed.drop(columns="legs"), fills, funding, prices, None, equity, now)
            res["right"] = d["strengths"]
            res["wrong"] = d["errors"].head(6).to_dict("records") if len(d["errors"]) else []
        except Exception as ex:  # noqa: BLE001
            res["right"], res["wrong"] = [], []
            res["diag_error"] = f"{type(ex).__name__}: {ex}"
        say(0.93, "Rule extraction (experimental)")
        try:
            res["rules"] = rl.extract(enr, prices, coins, pd.Timestamp(res["test_start"]), rates)
        except Exception as ex:  # noqa: BLE001 - experimental: never fails the run
            res["rules"] = {"status": "error", "message": f"{type(ex).__name__}: {ex}"}
    say(1.0, "Done")
    return res


def latest(con, venue: str, acct: str) -> dict | None:
    r = con.execute("SELECT run_id FROM tr_backtest_runs WHERE venue=? AND account_id=? AND status='done' "
                    "ORDER BY finished DESC LIMIT 1", (venue, acct)).fetchone()
    return get(con, r[0]) | {"run_id": r[0]} if r else None
