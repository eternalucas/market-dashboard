"""Batch mode: the backtest across the whole Top 100, and what it says
about top traders in general ("most common mistakes"), plus the
persistence test (did last month's top traders stay on top?).

Each trader's backtest is the same cached run as on their own page, so a
batch reuses anything already computed. Everything simulated is labelled.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time

import numpy as np
import pandas as pd

from . import backtest as bt
from . import costs as cst
from . import equity as eq
from . import jobs
from . import leaderboard as lbm
from . import store

_THREAD: dict[str, threading.Thread] = {}


def batch_id(venue: str, board_window: str, a: cst.Assumptions) -> str:
    day = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
    h = hashlib.sha1(f"{venue}|{board_window}|{jobs.config_hash(a)}|v{jobs.RESULT_VERSION}|{day}".encode()).hexdigest()[:10]
    return f"batch-{day}-{h}"


def start(venue: str = "hyperliquid", board_window: str = "30d", a: cst.Assumptions | None = None,
          el: lbm.Eligibility | None = None) -> str:
    a = a or cst.Assumptions()
    bid = batch_id(venue, board_window, a)
    con = store.connect()
    try:
        cur = jobs.get(con, bid)
        alive = bid in _THREAD and _THREAD[bid].is_alive()
        if cur and (cur["status"] == "done" or alive):
            return bid
        con.execute("INSERT OR REPLACE INTO tr_backtest_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (bid, venue, "*", board_window, jobs.config_hash(a), "batch", "running", 0.0, "Starting",
                     pd.Timestamp.now(tz="UTC").isoformat(), None, None))
        con.commit()
    finally:
        con.close()
    th = threading.Thread(target=_run, args=(bid, venue, board_window, a, el), daemon=True, name="backtest-batch")
    _THREAD[bid] = th
    th.start()
    return bid


def _run(bid, venue, board_window, a, el):
    con = store.connect()
    last = [0.0]

    def progress(p, msg):
        if time.time() - last[0] > 2 or p >= 1:
            last[0] = time.time()
            jobs._set(con, bid, progress=float(p), message=msg)
    try:
        res = run(con, venue, board_window, a, el, progress)
        blob = json.dumps(bt.clean(res))
        jobs._set(con, bid, status="done", progress=1.0, message="Done",
                  finished=pd.Timestamp.now(tz="UTC").isoformat(), result_json=blob)
        try:
            out = store.ROOT / "data" / "backtests"
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{bid}.json").write_text(blob)
        except OSError:
            pass
    except Exception as e:  # noqa: BLE001
        jobs._set(con, bid, status="error", message=f"{type(e).__name__}: {e}"[:500],
                  finished=pd.Timestamp.now(tz="UTC").isoformat())
    finally:
        con.close()


def run(con, venue: str, board_window: str, a: cst.Assumptions, el=None, progress=None, fetch: bool = True,
        accounts: list[str] | None = None) -> dict:
    say = progress or (lambda p, m: None)
    if accounts is None:
        top, _ = lbm.board(con, board_window, "Total PnL", venue=venue, n=100, el=el)
        accounts = top["account_id"].tolist()
    per = []
    for i, acct in enumerate(accounts):
        say(0.02 + 0.8 * i / max(len(accounts), 1), f"Backtesting trader {i + 1} of {len(accounts)}")
        rid = jobs.run_id(venue, acct, "all", a, store.data_version(con, venue, acct))
        cur = jobs.get(con, rid)
        if cur and cur["status"] == "done" and cur["result"]:
            r = cur["result"]
        else:
            try:
                r = bt.clean(jobs.compute(con, venue, acct, "all", a, None, fetch))
                con.execute("INSERT OR REPLACE INTO tr_backtest_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (rid, venue, acct, "all", jobs.config_hash(a), store.data_version(con, venue, acct), "done",
                             1.0, "Done (batch)", pd.Timestamp.now(tz="UTC").isoformat(),
                             pd.Timestamp.now(tz="UTC").isoformat(), json.dumps(r)))
                con.commit()
            except Exception as e:  # noqa: BLE001 - one trader failing never stops the batch
                r = {"status": "error", "message": f"{type(e).__name__}: {e}"}
        per.append((acct, r))
    say(0.85, "Persistence test")
    pers = persistence(con, venue, el)
    say(0.97, "Summarising")
    out = summarise(per)
    out["persistence"] = pers
    out["board_window"] = board_window
    out["assumptions"] = a.as_dict()
    out["as_of"] = pd.Timestamp.now(tz="UTC").isoformat()
    return out


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #

def pattern_kind(p: str) -> str:
    """'Losing market: XRP' -> 'Losing market'."""
    return re.split(r":\s", p, maxsplit=1)[0]


def summarise(per: list[tuple[str, dict]]) -> dict:
    done = [(a, r) for a, r in per if r.get("status") == "done"]
    out = {"traders": len(per), "backtested": len(done),
           "too_few_trips": sum(r.get("status") == "too_few_trips" for _, r in per),
           "errors": sum(r.get("status") == "error" for _, r in per),
           "golden_pass": sum(bool(r["golden"]["pass"]) for _, r in done),
           "golden_median_error": float(np.median([r["golden"]["error"] for _, r in done])) if done else None}
    n = len(done)
    # mistakes (actual trades)
    mk: dict[str, dict] = {}
    for _, r in done:
        seen = set()
        for w in r.get("wrong") or []:
            k = pattern_kind(w["pattern"])
            d = mk.setdefault(k, {"traders": 0, "impact": 0.0, "trades": 0, "example_rule": w.get("rule", "")})
            if k not in seen:
                d["traders"] += 1
                seen.add(k)
            d["impact"] += float(w.get("impact_usd") or 0)
            d["trades"] += int(w.get("trades_flagged") or 0)
    out["mistakes"] = sorted(({"pattern": k, **v, "share": v["traders"] / n if n else 0} for k, v in mk.items()),
                             key=lambda d: -d["traders"])
    # strengths (actual trades)
    sk: dict[str, int] = {}
    for _, r in done:
        for s in {re.split(r":\s", x["title"])[0] for x in r.get("right") or []}:
            sk[s] = sk.get(s, 0) + 1
    out["strengths"] = sorted(({"title": k, "traders": v, "share": v / n if n else 0} for k, v in sk.items()),
                              key=lambda d: -d["traders"])
    # rule families (simulated): adopted on train, and what happened on test
    fam: dict[str, dict] = {}
    for _, r in done:
        base = abs(r.get("actual_test") or 0) or None
        for f in r.get("findings") or []:
            d = fam.setdefault(f["family"], {"family": f["family"], "category": f.get("category"), "tested": 0,
                                             "adopted": 0, "helped": 0, "confident": 0, "gains_pct": [], "gains": []})
            d["tested"] += 1
            if (f.get("train_delta") or 0) > 0:
                d["adopted"] += 1
                td = f.get("test_delta") or 0
                if td > 0:
                    d["helped"] += 1
                    if f.get("label") in ("High", "Medium"):
                        d["confident"] += 1
                d["gains"].append(td)
                if base:
                    d["gains_pct"].append(td / base)
    fams = []
    for d in fam.values():
        g, gp = d.pop("gains"), d.pop("gains_pct")
        d["median_gain"] = float(np.median(g)) if g else None
        d["median_gain_pct_of_test_pnl"] = float(np.median(gp)) if gp else None
        d["helped_share_of_adopted"] = d["helped"] / d["adopted"] if d["adopted"] else None
        d["helped_share_of_traders"] = d["helped"] / n if n else None
        fams.append(d)
    out["rules"] = sorted(fams, key=lambda d: -(d["helped_share_of_traders"] or 0))
    out["rule_reproduces"] = sum(bool((r.get("rules") or {}).get("reproduces")) for _, r in done)
    out["per_trader"] = [{
        "account_id": a, "status": r.get("status"), "trips": r.get("trips"),
        "actual_test": r.get("actual_test"), "golden_error": (r.get("golden") or {}).get("error"),
        "best_rule": next((f["name"] for f in bt.summary(r.get("findings") or [], 1)), None),
        "best_gain": next((f["test_delta"] for f in bt.summary(r.get("findings") or [], 1)), None),
        "best_label": next((f["label"] for f in bt.summary(r.get("findings") or [], 1)), None),
        "mistakes": [w["pattern"] for w in (r.get("wrong") or [])][:3]} for a, r in per]
    return out


# --------------------------------------------------------------------------- #
# Persistence: did the previous window's top traders stay on top?
# --------------------------------------------------------------------------- #

def persistence(con, venue: str = "hyperliquid", el=None, window_days: int = 30, top_n: int = 100,
                now: pd.Timestamp | None = None) -> dict:
    """Rank every analysed, eligible account by its mark-to-market P&L over
    the 30 days before the last 30, take the top 100, and measure the same
    accounts over the last 30 days. Uses stored fills and stored daily
    closes only (no network). The pool is today's candidate list, which is
    itself biased towards accounts that did well recently; that bias is
    reported with the result."""
    now = (now or pd.Timestamp.now(tz="UTC")).normalize()
    mid, start = now - pd.Timedelta(days=window_days), now - pd.Timedelta(days=2 * window_days)
    _, allrows = lbm.board(con, "all", venue=venue, el=el)
    if allrows.empty:
        return {"status": "no_data"}
    pool = allrows[allrows["eligible"]]["account_id"].tolist()
    closes_cache: dict[str, pd.Series] = {}
    rows = []
    for acct in pool:
        f = store.load(con, "tr_fills", venue, acct)
        if f.empty or f["ts"].min() > start + pd.Timedelta(days=3):
            continue                                        # history doesn't cover the first window
        f = f[~f["coin"].str.startswith("@") & ~f["coin"].str.contains("/", regex=False)]
        fu = store.load(con, "tr_funding", venue, acct)
        for c in f["coin"].unique():
            if c not in closes_cache:
                s = store.candles(con, venue, c, "1d")
                closes_cache[c] = s["c"] if not s.empty else pd.Series(dtype=float)
        d = eq.daily_pnl(f, fu, {c: closes_cache[c] for c in f["coin"].unique()}, now)
        if d.empty:
            continue
        p1 = float(d.loc[(d.index >= start) & (d.index < mid), "mtm"].sum())
        p2 = float(d.loc[(d.index >= mid) & (d.index < now), "mtm"].sum())
        rows.append({"account_id": acct, "prev": p1, "next": p2})
    df = pd.DataFrame(rows)
    if len(df) < 20:
        return {"status": "too_few", "accounts": len(df)}
    df["prev_rank"] = df["prev"].rank(ascending=False, method="first")
    df["next_rank"] = df["next"].rank(ascending=False, method="first")
    top = df[df["prev_rank"] <= top_n]
    rest = df[df["prev_rank"] > top_n]
    return {"status": "done", "pool": int(len(df)), "top_n": int(len(top)),
            "first_window": [start.isoformat(), mid.isoformat()], "second_window": [mid.isoformat(), now.isoformat()],
            "top_profitable_next": float((top["next"] > 0).mean()),
            "rest_profitable_next": float((rest["next"] > 0).mean()) if len(rest) else None,
            "top_median_next": float(top["next"].median()),
            "rest_median_next": float(rest["next"].median()) if len(rest) else None,
            "top_stayed_top": float((top["next_rank"] <= top_n).mean()),
            "rank_corr": float(df["prev"].rank().corr(df["next"].rank())),
            "bias": "The pool is today's Hyperliquid candidate list (traders near the top of its leaderboard now), "
                    "so accounts that did badly in the second window are under-represented: the real fall-off is "
                    "likely larger than shown. Daily leaderboard snapshots started on 27 Oct 2026 and will replace "
                    "this once two windows have passed."}
