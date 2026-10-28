"""Top 100 on-chain perp traders.

1. Candidates: the venue's own leaderboard says whom to look at (top N by
   PnL in each of its windows). Nothing from it is shown as a result.
2. Each candidate's fills, funding and account-value history are synced
   into data/trades.db (incremental).
3. Market makers, vaults and protocol accounts are detected from behaviour
   and excluded; the reason is stored.
4. Every metric is recomputed from our own data: daily mark-to-market P&L,
   time-weighted return on equity, Sharpe/Sortino, drawdown, profit factor,
   % of profitable weeks, plus everything the single-trader view shows.
5. Eligibility filters are applied when reading, so changing them in the UI
   needs no refetch.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .. import venues
from . import equity as eq
from . import store
from .reconstruct import reconstruct, to_legacy

DAY_MS = 86_400_000


@dataclass
class Eligibility:
    min_trips: int = 50
    min_active_days: int = 10
    max_fills_per_day: float = 2000       # median over active days
    max_maker_share: float = 0.80          # together with a sub-minute median hold
    min_median_hold_min: float = 1.0
    max_two_sided_share: float = 0.30      # fills with an opposite-side fill on the same coin within 1 s
    single_trade_flag: float = 0.50        # flag when one trade is > 50% of window PnL

    @classmethod
    def from_cfg(cls, cfg: dict | None) -> "Eligibility":
        tcfg = ((cfg or {}).get("trade_review") or {}).get("eligibility") or {}
        return cls(**{k: type(getattr(cls(), k))(v) for k, v in tcfg.items() if hasattr(cls(), k)})


# --------------------------------------------------------------------------- #
# Candidates
# --------------------------------------------------------------------------- #

def candidates(lb: pd.DataFrame, per_window: int = 1000) -> pd.DataFrame:
    """Union of the top `per_window` accounts by PnL in each of the venue's
    windows (week, month, allTime). The venue has no 90-day window; 90d is
    computed by us for everyone in the pool."""
    picks: dict[str, set] = {}
    for w in ("week", "month", "allTime"):
        col = f"{w}_pnl"
        if col not in lb:
            continue
        for a in lb.nlargest(per_window, col)["account_id"]:
            picks.setdefault(a, set()).add(w)
    pool = lb[lb["account_id"].isin(picks)].copy()
    pool["source_windows"] = pool["account_id"].map(lambda a: ",".join(sorted(picks[a])))
    # No trading volume in any window = the PnL didn't come from trading (vault
    # deposits, spot holdings): nothing to review, so not worth a fetch.
    vlm = [c for c in ("week_vlm", "month_vlm", "allTime_vlm") if c in pool]
    if vlm:
        pool = pool[pool[vlm].fillna(0).sum(axis=1) > 0]
    return pool


def refresh_candidates(con, venue: str = "hyperliquid", per_window: int = 1000) -> pd.DataFrame:
    ad = venues.get(venue)
    lb = ad.leaderboard()
    pool = candidates(lb, per_window)
    now = pd.Timestamp.now(tz="UTC").isoformat()
    rows = pd.DataFrame({
        "venue": venue, "account_id": pool["account_id"], "as_of": now, "source_windows": pool["source_windows"],
        "venue_pnl_json": pool[[c for c in pool.columns if c.endswith(("_pnl", "_vlm"))]].apply(
            lambda r: json.dumps(r.to_dict()), axis=1),
        "account_value": pool["account_value"]})
    # the pool is replaced, not appended: accounts that left the venue's lists drop off the board
    con.execute("DELETE FROM tr_candidates WHERE venue=?", (venue,))
    store.upsert(con, "tr_candidates", rows, ts_cols=())
    return pool


def current_pool(con, venue: str = "hyperliquid", max_age_h: float = 6, per_window: int = 1000) -> pd.DataFrame:
    """The stored pool if it's recent, otherwise a fresh one (the stats file is ~20 MB)."""
    last = con.execute("SELECT MAX(as_of) FROM tr_candidates WHERE venue=?", (venue,)).fetchone()[0]
    n = con.execute("SELECT COUNT(*) FROM tr_candidates WHERE venue=?", (venue,)).fetchone()[0]
    if last and n >= per_window and pd.Timestamp.now(tz="UTC") - pd.Timestamp(last) < pd.Timedelta(hours=max_age_h):
        df = pd.read_sql_query("SELECT * FROM tr_candidates WHERE venue=?", con, params=(venue,))
        pnl = df["venue_pnl_json"].map(json.loads).apply(pd.Series)
        return pd.concat([df, pnl], axis=1)
    return refresh_candidates(con, venue, per_window)


# --------------------------------------------------------------------------- #
# Sync one account
# --------------------------------------------------------------------------- #

def sync_account(con, account_id: str, venue: str = "hyperliquid", mm_fills_per_day: float = 2000) -> dict:
    """Incremental sync. A quick check first: an account with >= 2,000
    fills in the last 24 hours is a market maker / high-frequency account
    and is not paged further (it would cost 5 calls and fill the database)."""
    ad = venues.get(venue)
    acct = account_id.lower()
    info = {"account_id": acct}
    prior = con.execute("SELECT kind, kind_reason FROM tr_accounts WHERE venue=? AND account_id=?", (venue, acct)).fetchone()
    if prior is None or prior[0] in (None, "unknown"):
        recent = ad.recent_fill_rate(acct, 24)
        if recent >= mm_fills_per_day:
            _set_kind(con, venue, acct, "market_maker", f"{recent:,}+ fills in the last 24 hours")
            info["kind"] = "market_maker"
            return info
        if ad.is_vault(acct):
            _set_kind(con, venue, acct, "vault", "Hyperliquid vault (vaultDetails)")
            info["kind"] = "vault"
            return info
    elif sticky_kind(prior[0], prior[1]):
        info["kind"] = prior[0]
        return info

    # fills
    st = store.get_sync(con, venue, acct, "fills")
    start = (st["cursor_ms"] + 1) if st["cursor_ms"] else 0
    new = ad.fills(acct, start)
    store.upsert(con, "tr_fills", new)
    last = int(new["ts"].max().timestamp() * 1000) if not new.empty else st["cursor_ms"]
    n_total, first_ts = con.execute("SELECT COUNT(*), MIN(ts) FROM tr_fills WHERE venue=? AND account_id=?",
                                    (venue, acct)).fetchone()
    # funding
    fs = store.get_sync(con, venue, acct, "funding")
    f_start = (fs["cursor_ms"] + 1) if fs["cursor_ms"] else (
        int(pd.Timestamp(first_ts).timestamp() * 1000) if first_ts else 0)
    fund = ad.funding(acct, f_start)
    store.upsert(con, "tr_funding", fund)
    store.set_sync(con, venue, acct, "funding",
                   int(fund["ts"].max().timestamp() * 1000) if not fund.empty else fs["cursor_ms"])
    # account value history and all-time volume (for truncation)
    pf = ad.portfolio(acct)
    snaps = []
    for w in ("allTime", "month", "week"):
        s = pf.get(w, {}).get("av", pd.Series(dtype=float))
        if not s.empty:
            snaps.append(pd.DataFrame({"venue": venue, "account_id": acct, "ts": s.index, "account_value": s.values,
                                       "source": f"portfolio:{w}"}))
    if snaps:
        store.upsert(con, "tr_account_snapshots", pd.concat(snaps))
    venue_vlm = pf.get("perpAllTime", pf.get("allTime", {})).get("vlm", 0.0)
    our_vlm = con.execute("SELECT SUM(ABS(px*sz)) FROM tr_fills WHERE venue=? AND account_id=?",
                          (venue, acct)).fetchone()[0] or 0.0
    coverage = our_vlm / venue_vlm if venue_vlm else np.nan
    truncated = bool(n_total >= 0.95 * 10_000 or (coverage == coverage and coverage < 0.98))
    store.set_sync(con, venue, acct, "fills", last, {
        "fills": n_total, "first_fill": first_ts, "venue_all_time_volume": venue_vlm, "our_volume": our_vlm,
        "volume_coverage": coverage, "history_truncated": truncated})
    if prior is None:
        _set_kind(con, venue, acct, "unknown", "")
    info.update({"new_fills": len(new), "fills": n_total, "truncated": truncated, "coverage": coverage})
    return info


def _set_kind(con, venue, acct, kind, reason):
    con.execute("INSERT INTO tr_accounts (venue, account_id, owner_address, kind, kind_reason, first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(venue, account_id) DO UPDATE SET kind=excluded.kind, "
                "kind_reason=excluded.kind_reason", (venue, acct, acct, kind, reason, pd.Timestamp.now(tz="UTC").isoformat()))
    con.commit()


# --------------------------------------------------------------------------- #
# Prices for marking positions
# --------------------------------------------------------------------------- #

def ensure_daily_closes(con, coins: list[str], venue: str = "hyperliquid") -> dict[str, pd.Series]:
    """Daily closes per coin, refreshed at most every 6 hours (5,000 daily
    candles cover the venue's whole history)."""
    ad = venues.get(venue)
    out = {}
    now_ms = int(time.time() * 1000)
    for coin in coins:
        st = store.get_sync(con, venue, coin, "candles_1d")
        fresh = st["last_ok"] and pd.Timestamp.now(tz="UTC") - pd.Timestamp(st["last_ok"]) < pd.Timedelta(hours=6)
        if not fresh:
            try:
                c = ad.candles(coin, "1d", now_ms - 4999 * DAY_MS, now_ms)
                store.upsert(con, "tr_candles", c)
                store.set_sync(con, venue, coin, "candles_1d", now_ms, {"rows": len(c)})
            except Exception as e:  # noqa: BLE001 - recorded; that coin is then realized-only
                store.set_sync(con, venue, coin, "candles_1d", None, {"error": f"{type(e).__name__}: {e}"})
        c = store.candles(con, venue, coin, "1d")
        out[coin] = c["c"] if not c.empty else pd.Series(dtype=float)
    return out


# --------------------------------------------------------------------------- #
# Behaviour checks and metrics
# --------------------------------------------------------------------------- #

def behaviour(fills: pd.DataFrame, trips: pd.DataFrame) -> dict:
    if fills.empty:
        return {"fills_per_active_day": 0.0, "maker_share": np.nan, "two_sided_share": np.nan,
                "median_hold_min": np.nan}
    per_day = fills.groupby(fills["ts"].dt.floor("D")).size()
    f = fills.sort_values("ts")
    two = 0
    for _, g in f.groupby("coin"):
        t = pd.DatetimeIndex(g["ts"]).as_unit("ms").asi8          # milliseconds, whatever the stored resolution
        s = (g["side"] == "B").to_numpy()
        if len(t) < 2:
            continue
        # opposite-side fill on the same coin within 1 second (before or after)
        prev_opp = np.r_[False, (np.diff(t) <= 1000) & (s[1:] != s[:-1])]
        next_opp = np.r_[(np.diff(t) <= 1000) & (s[1:] != s[:-1]), False]
        two += int((prev_opp | next_opp).sum())
    closed = trips[~trips["is_open"]] if not trips.empty else trips
    return {"fills_per_active_day": float(per_day.median()),
            "maker_share": float(1 - fills["is_taker"].mean()),
            "two_sided_share": two / len(fills),
            "median_hold_min": float(closed["hold_hours"].median() * 60) if not closed.empty else np.nan}


def sticky_kind(kind: str | None, reason: str | None) -> bool:
    """Vaults, and market makers caught by the quick fill-rate check at sync
    (whose fills are never downloaded), stay excluded. A market-maker label
    from behaviour is recomputed every time."""
    return kind == "vault" or (kind == "market_maker" and "in the last 24 hours" in (reason or ""))


def classify(beh: dict, kind: str | None, el: Eligibility) -> tuple[str, str]:
    """('trader' | 'market_maker' | 'vault', reason)."""
    if kind in ("market_maker", "vault"):
        return kind, "detected during sync"
    reasons = []
    if beh["fills_per_active_day"] > el.max_fills_per_day:
        reasons.append(f"{beh['fills_per_active_day']:,.0f} fills per active day")
    if beh["maker_share"] == beh["maker_share"] and beh["maker_share"] > el.max_maker_share and \
            beh["median_hold_min"] == beh["median_hold_min"] and beh["median_hold_min"] < el.min_median_hold_min:
        reasons.append(f"{beh['maker_share']:.0%} maker fills with a {beh['median_hold_min']:.1f}-minute median hold")
    if beh["two_sided_share"] == beh["two_sided_share"] and beh["two_sided_share"] > el.max_two_sided_share:
        reasons.append(f"{beh['two_sided_share']:.0%} of fills quoted on both sides within 1 s")
    return ("market_maker", "; ".join(reasons)) if reasons else ("trader", "")


def compute_account(con, account_id: str, venue: str = "hyperliquid", el: Eligibility | None = None,
                    now: pd.Timestamp | None = None) -> dict:
    """Recompute everything for one account and store one tr_metrics row per window."""
    el = el or Eligibility()
    acct = account_id.lower()
    kind_row = con.execute("SELECT kind, kind_reason FROM tr_accounts WHERE venue=? AND account_id=?",
                           (venue, acct)).fetchone()
    kind, reason = (kind_row or (None, ""))
    now = (now or pd.Timestamp.now(tz="UTC"))
    if sticky_kind(kind, reason):
        _store_excluded(con, venue, acct, kind, reason, now)
        return {"kind": kind}
    kind = None                      # behaviour-based labels are re-evaluated on every recompute
    fills = store.load(con, "tr_fills", venue, acct)
    funding = store.load(con, "tr_funding", venue, acct)
    trips, rep = reconstruct(fills, funding)
    store.upsert(con, "tr_round_trips", _trip_rows(trips, venue, acct), ts_cols=("open_ts", "close_ts"))
    beh = behaviour(fills, trips)
    kind, reason = classify(beh, kind, el)
    _set_kind(con, venue, acct, kind, reason)
    perp = fills[~fills["coin"].str.startswith("@") & ~fills["coin"].str.contains("/", regex=False)] if not fills.empty else fills
    closes = ensure_daily_closes(con, sorted(perp["coin"].unique()), venue) if not perp.empty else {}
    daily = eq.daily_pnl(perp, funding, closes, now)
    snaps = store.load(con, "tr_account_snapshots", venue, acct)
    avs = [g.set_index("ts")["account_value"] for _, g in snaps.groupby("source")] if not snaps.empty else []
    equity = eq.daily_equity(avs, daily.index) if not daily.empty else pd.Series(dtype=float)
    legacy = to_legacy(trips) if not trips.empty else pd.DataFrame(columns=["open", "close_time", "net"])
    sync = store.get_sync(con, venue, acct, "fills")["notes"]
    all_closed = trips[~trips["is_open"] & ~trips["exit_unobserved"]] if not trips.empty else trips
    lifetime = {"trips_all": int(len(all_closed)),
                "active_days_all": int(fills["ts"].dt.floor("D").nunique()) if not fills.empty else 0}
    rows = []
    for w, days in eq.WINDOWS.items():
        m = eq.window_metrics(daily, equity, legacy, days, now)
        m.update(beh)
        m.update(lifetime)
        m["report"] = rep
        m["history_truncated"] = bool(sync.get("history_truncated"))
        m["volume_coverage"] = sync.get("volume_coverage")
        m["account_value"] = float(equity.dropna().iloc[-1]) if equity.notna().any() else np.nan
        excl = []
        if kind != "trader":
            excl.append(f"market maker: {reason}")
        if lifetime["trips_all"] < el.min_trips:
            excl.append(f"{lifetime['trips_all']} round trips (< {el.min_trips})")
        if lifetime["active_days_all"] < el.min_active_days:
            excl.append(f"{lifetime['active_days_all']} active days (< {el.min_active_days})")
        if m["active_days"] == 0:
            excl.append("no trading in this window")
        flags = []
        if m["top_trade_share"] == m["top_trade_share"] and m["top_trade_share"] > el.single_trade_flag:
            flags.append(f"one trade = {m['top_trade_share']:.0%} of PnL")
        if m["history_truncated"]:
            flags.append("history truncated")
        if m["window_partial"]:
            flags.append("window only partly covered")
        if m["missing_price_days"]:
            flags.append(f"{m['missing_price_days']} days without a price for some coin")
        if rep["excluded_fills"]:
            flags.append(f"{rep['excluded_fills']} fills of a position opened before the history")
        rows.append(_metrics_row(venue, acct, w, now, not excl, "; ".join(excl), flags, m))
    store.upsert(con, "tr_metrics", pd.DataFrame(rows), ts_cols=("as_of",))
    return {"kind": kind, "windows": {r["window"]: r["pnl"] for r in rows}}


def _metrics_row(venue, acct, window, now, eligible, exclusion, flags, m) -> dict:
    def num(x):
        return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else float(x)
    return {"venue": venue, "account_id": acct, "window": window, "as_of": now, "eligible": int(eligible),
            "exclusion": exclusion, "flags": "; ".join(flags), "pnl": num(m["pnl"]), "roe": num(m["roe"]),
            "sharpe": num(m["sharpe"]), "sortino": num(m["sortino"]), "max_dd_usd": num(m["max_dd_usd"]),
            "max_dd_pct": num(m["max_dd_pct"]), "profit_factor": num(m["profit_factor"]),
            "pct_weeks_profitable": num(m["pct_weeks_profitable"]), "trips": int(m["trips"]),
            "active_days": int(m["active_days"]), "metrics_json": json.dumps(m, default=_json)}


def _json(x):
    if isinstance(x, (pd.Timestamp,)):
        return x.isoformat()
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x) if np.isfinite(x) else None
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return str(x)


def _store_excluded(con, venue, acct, kind, reason, now):
    rows = [{"venue": venue, "account_id": acct, "window": w, "as_of": now, "eligible": 0,
             "exclusion": f"{kind.replace('_', ' ')}: {reason}", "flags": "", "pnl": None, "roe": None, "sharpe": None,
             "sortino": None, "max_dd_usd": None, "max_dd_pct": None, "profit_factor": None,
             "pct_weeks_profitable": None, "trips": 0, "active_days": 0, "metrics_json": "{}"} for w in eq.WINDOWS]
    store.upsert(con, "tr_metrics", pd.DataFrame(rows), ts_cols=("as_of",))


def _trip_rows(trips: pd.DataFrame, venue: str, acct: str) -> pd.DataFrame:
    if trips.empty:
        return trips
    cols = ["trip_id", "coin", "side", "open_ts", "close_ts", "entry_qty", "avg_entry", "avg_exit", "max_qty",
            "max_notional", "realized_pnl", "fees", "funding", "net", "fills", "taker_fills", "adds",
            "added_while_losing", "liquidated", "is_open"]
    out = trips[cols].copy()
    for c in ("added_while_losing", "liquidated", "is_open"):
        out[c] = out[c].astype(int)
    out.insert(0, "account_id", acct)
    out.insert(0, "venue", venue)
    out["engine_version"] = store.ENGINE_VERSION
    return out


# --------------------------------------------------------------------------- #
# Reading the board
# --------------------------------------------------------------------------- #

SORTS = {"Total PnL": ("pnl", False), "Return on equity": ("roe", False), "Sharpe": ("sharpe", False),
         "Sortino": ("sortino", False), "Max drawdown (smallest)": ("max_dd_pct", False),
         "Profit factor": ("profit_factor", False), "% profitable weeks": ("pct_weeks_profitable", False)}


def board(con, window: str, sort: str = "Total PnL", venue: str | None = None, n: int = 100,
          el: Eligibility | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(top n eligible rows, every candidate with its status). Eligibility
    thresholds are re-applied from metrics_json, so the UI can change them."""
    el = el or Eligibility()
    sql = ("SELECT m.* FROM tr_metrics m JOIN tr_candidates c ON c.venue = m.venue AND c.account_id = m.account_id "
           "WHERE m.window = ?")
    params: list = [window]
    if venue:
        sql += " AND m.venue = ?"
        params.append(venue)
    df = pd.read_sql_query(sql, con, params=params)
    if df.empty:
        return df, df
    m = df["metrics_json"].map(lambda s: json.loads(s) if s else {})
    df["trips_all"] = m.map(lambda d: d.get("trips_all", 0))
    df["active_days_all"] = m.map(lambda d: d.get("active_days_all", 0))
    df["top_trade_share"] = m.map(lambda d: d.get("top_trade_share"))
    reasons = []
    for r, d in zip(df.itertuples(), m):
        why = []
        if r.exclusion and (r.exclusion.startswith("market maker") or r.exclusion.startswith("vault")):
            why.append(r.exclusion.split(";")[0])
        else:
            if d.get("trips_all", 0) < el.min_trips:
                why.append(f"{d.get('trips_all', 0)} round trips (< {el.min_trips})")
            if d.get("active_days_all", 0) < el.min_active_days:
                why.append(f"{d.get('active_days_all', 0)} active days (< {el.min_active_days})")
            if r.active_days == 0:
                why.append("no trading in this window")
            beh_mm, beh_reason = classify(d, None, el) if d else ("trader", "")
            if beh_mm == "market_maker":
                why.append(f"market maker: {beh_reason}")
        reasons.append("; ".join(why))
    df["exclusion"] = reasons
    df["eligible"] = df["exclusion"] == ""
    col, asc = SORTS.get(sort, ("pnl", False))
    elig = df[df["eligible"]].copy()
    if col == "max_dd_pct":
        elig = elig.sort_values(col, ascending=False, na_position="last")   # closest to 0 first
    else:
        elig = elig.sort_values(col, ascending=asc, na_position="last")
    elig["rank"] = range(1, len(elig) + 1)
    return elig.head(n), df


def snapshot_boards(con, venue: str = "hyperliquid") -> int:
    """Daily record of the Top 100 per window (for the persistence test)."""
    as_of = pd.Timestamp.now(tz="UTC").normalize()
    rows = []
    for w in eq.WINDOWS:
        top, _ = board(con, w, venue=venue)
        for r in top.itertuples():
            rows.append({"venue": venue, "window": w, "as_of": as_of, "account_id": r.account_id,
                         "rank": r.rank, "pnl": r.pnl, "metrics_json": r.metrics_json})
    return store.upsert(con, "tr_leaderboard_snapshots", pd.DataFrame(rows), ts_cols=("as_of",))


def refresh(con, venue: str = "hyperliquid", per_window: int = 1000, budget_s: float = 2400,
            max_age_h: float = 20, say=print, el: Eligibility | None = None) -> dict:
    """One refresh pass: candidates, then accounts not synced for max_age_h,
    most promising first, until the time budget runs out. Safe to stop and
    resume: progress is stored per account."""
    t0 = time.time()
    fix_two_sided(con, venue, el, say)
    pool = current_pool(con, venue, per_window=per_window)
    # Active traders first: monthly volume relative to account size, so the board fills with
    # reviewable traders early instead of spending the first hours on buy-and-hold whales.
    turnover = (pool["month_vlm"] if "month_vlm" in pool else 0) / pool["account_value"].clip(lower=1_000)
    pool = pool.assign(_t=turnover).sort_values(["_t", "allTime_pnl"], ascending=False)
    done = skipped = 0
    for acct in pool["account_id"]:
        if time.time() - t0 > budget_s:
            break
        st = store.get_sync(con, venue, acct, "fills")
        kind = con.execute("SELECT kind, kind_reason FROM tr_accounts WHERE venue=? AND account_id=?",
                           (venue, acct)).fetchone()
        if kind and sticky_kind(*kind):
            skipped += 1
            continue
        if st["last_ok"] and pd.Timestamp.now(tz="UTC") - pd.Timestamp(st["last_ok"]) < pd.Timedelta(hours=max_age_h):
            skipped += 1
            continue
        try:
            sync_account(con, acct, venue)
            compute_account(con, acct, venue, el)
            done += 1
            say(f"synced {acct} ({done} this run)")
        except Exception as e:  # noqa: BLE001 - one account failing never stops the board
            store.set_sync(con, venue, acct, "error", None, {"error": f"{type(e).__name__}: {e}"})
    if done:
        snapshot_boards(con, venue)
    return {"pool": len(pool), "synced": done, "skipped": skipped, "seconds": round(time.time() - t0)}


def fix_two_sided(con, venue: str = "hyperliquid", el: Eligibility | None = None, say=print) -> int:
    """One-off repair (27 Oct 2026): the "quoted on both sides within 1 s"
    check read microsecond timestamps as nanoseconds, so its window was
    1,000 s and ordinary traders were labelled market makers. Every account
    labelled for that reason is recomputed from its stored fills (no network
    calls except daily closes older than 6 hours)."""
    if store.get_sync(con, venue, "_meta", "two_sided_fix")["last_ok"]:
        return 0
    rows = con.execute("SELECT account_id FROM tr_accounts WHERE venue=? AND kind='market_maker' "
                       "AND kind_reason LIKE '%both sides%'", (venue,)).fetchall()
    n = 0
    for (acct,) in rows:
        try:
            compute_account(con, acct, venue, el)
            n += 1
        except Exception as e:  # noqa: BLE001
            store.set_sync(con, venue, acct, "error", None, {"error": f"two-sided fix: {type(e).__name__}: {e}"})
    if n:
        snapshot_boards(con, venue)
    store.set_sync(con, venue, "_meta", "two_sided_fix", None, {"recomputed": n})
    say(f"two-sided fix: recomputed {n} accounts")
    return n


def status(con, venue: str = "hyperliquid") -> dict:
    pool = con.execute("SELECT COUNT(*), MAX(as_of) FROM tr_candidates WHERE venue=?", (venue,)).fetchone()
    kinds = dict(con.execute("SELECT kind, COUNT(*) FROM tr_accounts WHERE venue=? GROUP BY kind", (venue,)).fetchall())
    synced = con.execute("SELECT COUNT(DISTINCT account_id) FROM tr_metrics WHERE venue=?", (venue,)).fetchone()[0]
    return {"pool": pool[0], "pool_as_of": pool[1], "accounts_by_kind": kinds, "with_metrics": synced}


def eligibility_dict(el: Eligibility) -> dict:
    return asdict(el)
