"""Fetch everything, clean it, and store it.

Run from the dashboard's "Update data" button, or on its own with:
    python -m src.pipeline

Design rule: one broken source never stops the others. Each fetch is wrapped
in `attempt()`, which records success or the error in the fetch log, and the
Data health tab shows that log. When an exchange blocks your region or Google
rate-limits Trends, you see exactly which feed failed and why.
"""
from __future__ import annotations

import time

from typing import Callable

import numpy as np
import pandas as pd

import json

from . import events as ev
from . import flows, storage
from .collectors import attention, derivatives, fred, hyperliquid, onchain, prices
from .config import ROOT, load_config
from .indicators import onchain as oc
from .indicators import positioning as pos

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def attempt(con, source: str, fn: Callable[[], int | None]) -> bool:
    """Run one fetch-and-store step; log the result instead of raising."""
    try:
        rows = fn() or 0
        storage.log_fetch(con, source, True, rows)
        return True
    except Exception as e:  # noqa: BLE001 - any failure is logged, not fatal
        storage.log_fetch(con, source, False, 0, f"{type(e).__name__}: {e}")
        return False


def is_fresh(con, source: str, max_age: pd.Timedelta) -> bool:
    """True when `source` last succeeded less than `max_age` ago."""
    last = storage.last_success(con, source)
    if last is None:
        return False
    last = last.tz_localize("UTC") if last.tzinfo is None else last.tz_convert("UTC")
    return pd.Timestamp.now(tz="UTC") - last < max_age


# How often each part of the update is worth re-fetching. Sources that only
# change once a day (or that only store one reading a day) are skipped when
# they already ran recently; "Full refresh" in the sidebar ignores this.
HOURLY = pd.Timedelta(minutes=55)
DAILY = pd.Timedelta(hours=20)


def _last_ts(con, series: str):
    s = storage.load_series(con, series)
    return None if s.empty else s.index[-1]


# Social Risk inputs live in src/sources/social.py and run via the source
# registry (hourly from the recorder, and at the end of run_all).


def update_prices(con, cfg):
    for asset in cfg["assets"]:
        attempt(con, f"price:{asset}",
                lambda asset=asset: storage.save_series(con, f"price:{asset}", prices.daily_close(asset)))


# --------------------------------------------------------------------------- #
# Positioning
# --------------------------------------------------------------------------- #


FUNDING_FIX_MARK = "meta:funding_interval_fix"


def fix_funding_intervals(con) -> bool:
    """One-off repair (27 Oct 2026): rates from contracts that pay every 4h were
    stored as if they were 8h rates. Funding history (stored per payment) is
    rescaled from the spacing of its payments; the few live snapshots of the
    4h contracts are doubled and their OI-weighted average recomputed.
    Runs once; a marker row records that it ran."""
    if not storage.load_series(con, FUNDING_FIX_MARK).empty:
        return False
    for name in storage.series_names(con, "funding_hist_8h:"):
        if name.split(":")[1] in ("binance", "bybit", "okx"):
            raw = storage.load_series(con, name)
            storage.save_series(con, name, derivatives.to_8h(raw))
    four_hour = {"binance": ["HYPE", "ENA"], "bybit": ["ENA"]}      # intervals checked live on 27 Oct 2026
    for ex, assets in four_hour.items():
        for a in assets:
            s = storage.load_series(con, f"funding_8h:{ex}:{a}")
            storage.save_series(con, f"funding_8h:{ex}:{a}", s * 2)
    for a in ("HYPE", "ENA"):
        fr = pd.DataFrame({ex: storage.load_series(con, f"funding_8h:{ex}:{a}") for ex in SNAPSHOT_EXCHANGES})
        oi = pd.DataFrame({ex: storage.load_series(con, f"oi_usd:{ex}:{a}") for ex in SNAPSHOT_EXCHANGES})
        fr, oi = fr.align(oi, join="inner")
        w = oi.where(fr.notna())
        storage.save_series(con, f"agg_funding_8h:{a}", ((fr * w).sum(axis=1) / w.sum(axis=1)).dropna())
    storage.save_point(con, FUNDING_FIX_MARK, 1.0)
    return True


BYBIT_OI_FIX_MARK = "meta:bybit_oi_one_side_fix"


def fix_bybit_oi(con, assets) -> bool:
    """One-off repair (27 Oct 2026): Bybit's openInterest counts both sides, so
    stored Bybit open interest was double the one-sided figure every other
    venue reports. Halves it and recomputes the cross-exchange totals and
    OI-weighted averages at each stored hour."""
    if not storage.load_series(con, BYBIT_OI_FIX_MARK).empty:
        return False
    for prefix in ("oi_usd:bybit:", "oi_coin_hist:bybit:"):
        for name in storage.series_names(con, prefix):
            storage.save_series(con, name, storage.load_series(con, name) / 2)
    for a in assets:
        oi = pd.DataFrame({ex: storage.load_series(con, f"oi_usd:{ex}:{a}") for ex in SNAPSHOT_EXCHANGES})
        if oi.empty:
            continue
        storage.save_series(con, f"agg_oi_usd:{a}", oi.sum(axis=1, min_count=1).dropna())
        w = oi.div(oi.sum(axis=1), axis=0)
        for metric, agg in (("funding_8h", "agg_funding_8h"), ("basis", "agg_basis")):
            v = pd.DataFrame({ex: storage.load_series(con, f"{metric}:{ex}:{a}") for ex in SNAPSHOT_EXCHANGES})
            v, ww = v.align(w, join="inner")
            ww = ww.where(v.notna())
            storage.save_series(con, f"{agg}:{a}", ((v * ww).sum(axis=1) / ww.sum(axis=1)).dropna())
    storage.save_point(con, BYBIT_OI_FIX_MARK, 1.0)
    return True


SNAPSHOT_EXCHANGES = ("bybit", "okx", "binance", "hyperliquid")
DEPTH_BANDS = (0.1, 0.25, 0.5, 1.0, 2.0, 5.0)   # % from mid; config bands are added to these


def positioning_snapshot(con, cfg, asset: str) -> dict:
    """Fetch live perps, spot book and options skew for one asset, store the
    readings, and return them for display."""
    pcfg = cfg["positioning"]
    now = storage.now_hour()
    out = {"asset": asset, "snaps": [], "errors": {}, "book": None, "depth": None, "skew": {}}

    for ex in pcfg["perp_exchanges"]:
        try:
            snap = derivatives.SNAPSHOTS[ex](asset)
            out["snaps"].append(snap)
            storage.save_point(con, f"funding_8h:{ex}:{asset}", snap["funding_8h"], now)
            storage.save_point(con, f"oi_usd:{ex}:{asset}", snap["oi_usd"], now)
            storage.save_point(con, f"basis:{ex}:{asset}", pos.basis(snap["mark"], snap["index"]), now)
            storage.log_fetch(con, f"perp:{ex}:{asset}", True, 1)
        except Exception as e:  # noqa: BLE001
            out["errors"][ex] = f"{type(e).__name__}: {e}"
            storage.log_fetch(con, f"perp:{ex}:{asset}", False, 0, out["errors"][ex])

    summary = pos.summarize_perps(out["snaps"])
    out["summary"] = summary
    if summary:
        storage.save_point(con, f"agg_funding_8h:{asset}", summary["oi_weighted_funding_8h"], now)
        storage.save_point(con, f"agg_oi_usd:{asset}", summary["total_oi_usd"], now)
        storage.save_point(con, f"agg_basis:{asset}", summary["oi_weighted_basis"], now)

    for venue in pcfg["spot_books"]:
        try:
            bids, asks = derivatives.SPOT_BOOKS[venue](asset)
            meta, depth = pos.book_depth(bids, asks, sorted(set(DEPTH_BANDS) | set(pcfg["depth_bands_pct"])))
            out.update(book=(bids, asks), depth=depth, book_meta={**meta, "venue": venue})
            for _, r in depth.iterrows():
                b = r["band_pct"]
                storage.save_point(con, f"depth_bid_usd_{b}:{asset}", r["bid_usd"], now)
                storage.save_point(con, f"depth_ask_usd_{b}:{asset}", r["ask_usd"], now)
                storage.save_point(con, f"imbalance_{b}:{asset}", r["imbalance"], now)
            storage.save_book_levels(con, asset, pos.bucket_book(bids, asks), _now_minute())
            storage.log_fetch(con, f"book:{venue}:{asset}", True, len(bids) + len(asks))
            break  # first venue that works is enough
        except Exception as e:  # noqa: BLE001
            out["errors"][f"book:{venue}"] = f"{type(e).__name__}: {e}"
            storage.log_fetch(con, f"book:{venue}:{asset}", False, 0, out["errors"][f"book:{venue}"])

    if asset in ("BTC", "ETH"):
        try:
            opts = derivatives.deribit_option_summaries(asset)
            skew = pos.skew_25d(opts, pd.Timestamp.now(tz="UTC"), pcfg["skew_target_days"])
            out["skew"] = skew
            out["options"] = opts
            if not opts.empty:
                greeks = pos.options_with_delta(opts, pd.Timestamp.now(tz="UTC"))
                storage.save_point(con, f"options_net_delta:{asset}", float(greeks["delta_oi"].sum()), now)
            if skew:
                storage.save_point(con, f"skew_rr25:{asset}", skew["risk_reversal"], now)
                storage.save_point(con, f"atm_iv:{asset}", skew["atm_iv"], now)
            storage.log_fetch(con, f"options:deribit:{asset}", True, len(opts))
        except Exception as e:  # noqa: BLE001
            out["errors"]["deribit"] = f"{type(e).__name__}: {e}"
            storage.log_fetch(con, f"options:deribit:{asset}", False, 0, out["errors"]["deribit"])
    return out


def record_book(con, cfg, asset: str) -> bool:
    """One order-book snapshot for the heatmap (called every minute while
    recording is switched on in the dashboard)."""
    for venue in cfg["positioning"]["spot_books"]:
        try:
            bids, asks = derivatives.SPOT_BOOKS[venue](asset)
            storage.save_book_levels(con, asset, pos.bucket_book(bids, asks), _now_minute())
            return True
        except Exception:  # noqa: BLE001 - try the next venue
            continue
    return False


def _now_minute():
    return pd.Timestamp.now(tz="UTC").floor("min").to_pydatetime()


def update_flows(con, cfg):
    """Hourly cross-exchange flow bars for every asset (Coinalyze). Shorter
    intervals are fetched when you open them in the dashboard."""
    if not (cfg.get("api_keys") or {}).get("coinalyze"):
        storage.log_fetch(con, "coinalyze", False, 0, "skipped: no API key in config.yaml")
        return
    for asset in cfg["assets"]:
        if is_fresh(con, f"coinalyze:1hour:{asset}", HOURLY):
            continue  # hourly bars already up to date
        attempt(con, f"coinalyze:1hour:{asset}", lambda asset=asset: flows.update_flow(con, cfg, asset, "1hour")["rows"])


def update_positioning_history(con, cfg):
    """Backfill funding and open-interest history from exchange endpoints, so
    charts have weeks of data on the first run rather than one point."""
    for asset in cfg["assets"]:
        for ex in cfg["positioning"]["perp_exchanges"]:
            fh = derivatives.FUNDING_HISTORY.get(ex)
            if fh:
                attempt(con, f"funding_hist:{ex}:{asset}",
                        lambda fh=fh, ex=ex, asset=asset: storage.save_series(
                            con, f"funding_hist_8h:{ex}:{asset}", fh(asset)))
            oh = derivatives.OI_HISTORY.get(ex)
            if oh:
                attempt(con, f"oi_hist:{ex}:{asset}",
                        lambda oh=oh, ex=ex, asset=asset: storage.save_series(
                            con, f"oi_coin_hist:{ex}:{asset}", oh(asset)))


# --------------------------------------------------------------------------- #
# Macro event study
# --------------------------------------------------------------------------- #

FRED_DATA = {  # name: (series id, units)
    "cpi_yoy": ("CPIAUCSL", "pc1"),
    "ppi_yoy": ("PPIFIS", "pc1"),
    "payrolls_chg": ("PAYEMS", "chg"),
    "claims": ("ICSA", "lin"),
    "fed_upper": ("DFEDTARU", "lin"),
}


def update_events(con, cfg):
    key = (cfg.get("api_keys") or {}).get("fred")
    if key:
        for name, rid in fred.RELEASES.items():
            attempt(con, f"fred_release:{name}", lambda name=name, rid=rid: storage.replace_event_type(
                con, name, pd.DataFrame({"date": fred.release_dates(rid, key), "event": name})))
        for name, (sid, units) in FRED_DATA.items():
            attempt(con, f"fred:{sid}", lambda name=name, sid=sid, units=units: storage.save_series(
                con, f"fred:{name}", fred.series(sid, key, units)))
    else:
        storage.log_fetch(con, "fred", False, 0, "skipped: no FRED key in config.yaml (US events need it)")
    cal = ev.central_bank_calendar()
    attempt(con, "central_bank_calendar", lambda: sum(
        storage.replace_event_type(con, name, grp) for name, grp in cal.groupby("event")))
    for name, ticker in cfg["events"]["assets"].items():
        attempt(con, f"event_price:{name}", lambda name=name, ticker=ticker: storage.save_series(
            con, f"evpx:{name}", prices.yahoo_close(ticker)))


# --------------------------------------------------------------------------- #
# Trade review (a Hyperliquid trader picked by rule)
# --------------------------------------------------------------------------- #

HL_DIR = ROOT / "data" / "hyperliquid"


def select_trader(cfg) -> dict:
    """The trader to review: the pinned address, or a reproducible random
    draw from leaderboard accounts that match the selection rule."""
    tcfg = cfg["trade_review"]
    HL_DIR.mkdir(parents=True, exist_ok=True)
    if tcfg.get("address"):
        sel = {"address": tcfg["address"], "how": "pinned in config.yaml"}
    else:
        lb = hyperliquid.leaderboard()
        pool = hyperliquid.eligible_traders(lb, tcfg["min_account_usd"], tcfg["max_account_usd"],
                                            tcfg["min_trades_month_volume_usd"])
        if pool.empty:
            raise ValueError("No leaderboard account matches the selection rule")
        # Walk the seeded random order and keep the first account that trades
        # like a person (bots are skipped: they hold for seconds).
        from .indicators import trades as tr
        chosen, skipped = None, 0
        for addr in hyperliquid.draw_order(pool, tcfg["seed"])[:20]:
            recent = hyperliquid.fills(addr, int((pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=60)).timestamp() * 1000))
            trips = tr.round_trips(recent) if not recent.empty else pd.DataFrame()
            style = tr.trading_style(trips[~trips["open"]] if not trips.empty else trips)
            if tr.looks_human(style):
                chosen = (addr, style)
                break
            skipped += 1
            time.sleep(0.3)
        if chosen is None:
            raise ValueError("None of the first 20 drawn accounts trades like a person; change the seed")
        pick = pool[pool["address"] == chosen[0]].iloc[0].to_dict()
        st = chosen[1]
        sel = {**pick, "how": f"random draw (seed {tcfg['seed']}) from {len(pool):,} of {len(lb):,} leaderboard "
                              f"accounts: ${tcfg['min_account_usd']:,}-${tcfg['max_account_usd']:,} account, "
                              f"profitable all-time and this month, "
                              f">${tcfg['min_trades_month_volume_usd']:,} volume this month. "
                              f"{skipped} earlier draw(s) skipped as automated (median hold under 15 min "
                              f"or over 25 trades a day); this trader's median hold is "
                              f"{st['median_hold_min']:.0f} min, {st['trades_per_day']:.1f} trades a day",
               "pool_size": len(pool), "leaderboard_size": len(lb), "skipped_as_bots": skipped}
    sel = {k: (None if isinstance(v, float) and v != v else v) for k, v in sel.items()}  # NaN -> null
    (HL_DIR / "selection.json").write_text(json.dumps(sel, default=str, indent=2))
    return sel


def update_trade_review(con, cfg):
    def step():
        sel = select_trader(cfg)
        addr = sel["address"]
        path = HL_DIR / f"{addr}_fills.csv"
        from .views import read_fills
        old = read_fills(path) if path.exists() else pd.DataFrame()
        start = int(old["time"].max().timestamp() * 1000) + 1 if not old.empty else 0
        new = hyperliquid.fills(addr, start)
        allf = pd.concat([old, new]).drop_duplicates("tid") if not new.empty else old
        allf.to_csv(path, index=False)
        state = hyperliquid.account_state(addr)
        state["positions"].to_csv(HL_DIR / f"{addr}_positions.csv", index=False)
        storage.save_point(con, f"hl_account_value:{addr}", state["account_value"])
        try:
            storage.save_series(con, f"hl_value_hist:{addr}", hyperliquid.account_value_history(addr))
        except Exception:  # noqa: BLE001 - history is a nice-to-have
            pass
        return len(new)
    attempt(con, "trade_review:hyperliquid", step)


# --------------------------------------------------------------------------- #
# On-chain watchlist and Hyperliquid cohorts
# --------------------------------------------------------------------------- #

ONCHAIN_DIR = ROOT / "data" / "onchain"


def _append_csv(path, df: pd.DataFrame, key: list[str]) -> None:
    """Add rows to a CSV, keeping one row per key (re-fetches don't duplicate)."""
    if df.empty:
        return
    if path.exists():
        df = pd.concat([pd.read_csv(path), df], ignore_index=True)
    dedupe = df[key].fillna("").astype(str).agg("|".join, axis=1)
    df[~dedupe.duplicated(keep="last")].to_csv(path, index=False)


def _fetch_wallet(w) -> dict:
    """Network part of one wallet update (runs in a worker thread)."""
    try:
        if w.chain == "btc":
            bal = onchain.btc_balance(w.address)
            txs = onchain.btc_transfers(w.address)
            if not txs.empty:
                txs["address"] = w.address
                txs["counterparties"] = txs["counterparties"].map(lambda c: " ".join(c))
        else:
            bal = onchain.eth_balance(w.address)
            txs = onchain.eth_token_transfers(w.address)
        return {"w": w, "bal": bal, "txs": txs}
    except Exception as e:  # noqa: BLE001 - logged by the caller
        return {"w": w, "error": f"{type(e).__name__}: {e}"}


BTC_SYNC_STATE = "btc_sync.json"


def sync_btc_wallets(con, max_pages: int = 8) -> dict:
    """Hourly (recorder) and on 'Update all data': every watched Bitcoin
    wallet's balance, plus all its new confirmed transactions since the last
    sync (paged until the last stored one). Per wallet, `complete_since` is
    the time from which the stored history has no holes; flows are only
    trusted after it (see indicators/onchain.wallet_coverage)."""
    import json
    ONCHAIN_DIR.mkdir(parents=True, exist_ok=True)
    wl = onchain.load_watchlist()
    wl = wl[wl["valid"] & (wl["chain"] == "btc")]
    path = ONCHAIN_DIR / "btc_txs.csv"
    stored = pd.read_csv(path, usecols=["txid", "address", "time"]) if path.exists() else pd.DataFrame(columns=["txid", "address", "time"])
    known = stored.groupby("address")["txid"].apply(set).to_dict()
    state_path = ONCHAIN_DIR / BTC_SYNC_STATE
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    now = storage.now_hour()
    new_rows, n_ok = [], 0
    for w in wl.itertuples():
        try:
            bal = onchain.btc_balance(w.address)
            rows, complete = onchain.btc_sync(w.address, known.get(w.address, set()), max_pages)
        except Exception as e:  # noqa: BLE001 - one explorer hiccup never stops the rest
            storage.log_fetch(con, f"onchain:btc:{w.label}", False, 0, f"{type(e).__name__}: {e}")
            continue
        storage.save_point(con, f"onchain_bal:btc:{w.address}", bal, ts=now)
        st = state.get(w.address, {})
        prior = stored.loc[stored["address"] == w.address, "time"]
        oldest_new = rows["time"].min().isoformat() if not rows.empty else now.isoformat()
        if not complete:
            st["complete_since"] = oldest_new                  # hole before this batch
        elif "complete_since" not in st:
            # first sync under this method: rows stored earlier came from snapshots of the
            # newest 25 transactions and can have holes, so trust only from the newest of them
            st["complete_since"] = (pd.to_datetime(prior, utc=True, format="ISO8601").max().isoformat()
                                    if len(prior) else "1970-01-01T00:00:00+00:00")
        if not rows.empty:
            rows["address"] = w.address
            rows["counterparties"] = rows["counterparties"].map(lambda c: " ".join(c))
            new_rows.append(rows)
        st["last_sync"], st["balance"] = now.isoformat(), bal
        state[w.address] = st
        storage.log_fetch(con, f"onchain:btc:{w.label}", True, len(rows))
        n_ok += 1
    if new_rows:
        _append_csv(path, pd.concat(new_rows, ignore_index=True), ["txid", "address"])
    state_path.write_text(json.dumps(state, indent=1, default=str))
    storage.log_fetch(con, "onchain:btc_sync", True, n_ok)
    return {"wallets": n_ok, "new_txs": int(sum(len(r) for r in new_rows))}


def update_onchain(con, cfg):
    """Balances and recent transfers for every watched wallet. Wallets are
    fetched a few at a time per explorer (the BTC and ETH explorers are
    separate sites, so they run side by side); everything is written to the
    database from this thread."""
    from concurrent.futures import ThreadPoolExecutor
    ONCHAIN_DIR.mkdir(parents=True, exist_ok=True)
    wl = onchain.load_watchlist()
    bad = wl[~wl["valid"]]
    if len(bad):
        storage.log_fetch(con, "watchlist", False, 0, f"{len(bad)} addresses fail the checksum: "
                          + ", ".join(bad["address"].head(5)))
    today = attention.today_utc()
    sync_btc_wallets(con)                            # Bitcoin: balances + every new transaction
    todo = [w for w in wl[wl["valid"]].itertuples() if w.chain == "eth"]
    btc_txs, eth_txs = [], []
    with ThreadPoolExecutor(max_workers=3) as btc_pool, ThreadPoolExecutor(max_workers=3) as eth_pool:
        futures = [(btc_pool if w.chain == "btc" else eth_pool).submit(_fetch_wallet, w) for w in todo]
        for f in futures:
            r = f.result()
            w = r["w"]
            source = f"onchain:{w.chain}:{w.label}"
            if "error" in r:
                storage.log_fetch(con, source, False, 0, r["error"])
                continue
            storage.save_point(con, f"onchain_bal:{w.chain}:{w.address}", r["bal"], ts=today)
            if not r["txs"].empty:
                (btc_txs if w.chain == "btc" else eth_txs).append(r["txs"])
            storage.log_fetch(con, source, True, len(r["txs"]))
    if btc_txs:
        _append_csv(ONCHAIN_DIR / "btc_txs.csv", pd.concat(btc_txs, ignore_index=True), ["txid", "address"])
    if eth_txs:
        _append_csv(ONCHAIN_DIR / "eth_transfers.csv", pd.concat(eth_txs, ignore_index=True),
                    ["tx", "from", "to", "token", "amount"])
    storage.log_fetch(con, "onchain", True, len(todo))


def update_hl_cohorts(con, cfg):
    """Sample accounts from each Hyperliquid size cohort and record how they
    are positioned, coin by coin."""
    def step():
        ONCHAIN_DIR.mkdir(parents=True, exist_ok=True)
        lb = hyperliquid.leaderboard()
        lb = lb[lb["month_vlm"] > 0].copy()           # active this month
        lb["cohort"] = oc.hl_size_cohort(lb["account_value"])
        lb.loc[lb["account_value"] < oc.HL_EMPTIED_BELOW, "cohort"] = np.nan   # emptied: size unknown
        lb["turnover"] = oc.hl_turnover_cohort(lb["month_vlm"], lb["account_value"])
        lb["turnover_x"] = lb["month_vlm"] / lb["account_value"].replace(0, np.nan)
        lb[["address", "account_value", "month_vlm", "month_pnl", "allTime_pnl", "cohort", "turnover",
            "turnover_x"]].to_csv(ONCHAIN_DIR / "hl_leaderboard.csv", index=False)
        n = cfg["onchain"]["hl_cohort_sample"]
        # Positioning sample: the largest accounts in each cohort, leaving out
        # bot-like turnover (> 100x the account a month: market makers, HFT),
        # whose inventory says nothing about how traders lean.
        pool = lb[lb["cohort"].notna() & (lb["turnover_x"] <= oc.HL_BOT_TURNOVER)]
        sample = pool.sort_values("account_value", ascending=False).groupby("cohort", observed=True).head(n)
        rows = []
        for a in sample.itertuples():
            try:
                pos = hyperliquid.account_state(a.address)["positions"]
            except Exception:  # noqa: BLE001 - skip an account that fails
                continue
            for p in pos.itertuples():
                rows.append({"cohort": a.cohort, "address": a.address, "coin": p.coin,
                             "size": p.size, "value_usd": p.value_usd, "rule": "largest_non_bot"})
            time.sleep(0.2)
        positions = pd.DataFrame(rows)
        positions.to_csv(ONCHAIN_DIR / "hl_cohort_positions.csv", index=False)
        if not positions.empty:
            summary = oc.cohort_positioning(positions)
            now = storage.now_hour()
            for r in summary.itertuples():
                storage.save_point(con, f"hl_cohort_net:{r.cohort}:{r.coin}", r.net_usd, now)
        return len(positions)
    attempt(con, "hyperliquid_cohorts", step)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def run_all(cfg: dict | None = None, include_trends: bool = True,
            progress: Callable[[str], None] | None = None, full: bool = False) -> None:
    """Update every source. Parts that already ran recently are skipped
    unless `full` is True (see HOURLY / DAILY above)."""
    cfg = cfg or load_config()
    con = storage.connect()
    say = progress or print
    # (label, function, fetch-log source that marks it done, how long it stays fresh)
    steps = [
        ("Prices", update_prices, f"price:{cfg['assets'][0]}", DAILY),
        # Social Risk inputs (Wikipedia, Trends, App Store, YouTube, Hacker News,
        # GDELT) now update through the source registry below (sources/social.py).
        ("Funding and OI history", update_positioning_history,
         f"funding_hist:{cfg['positioning']['perp_exchanges'][0]}:{cfg['assets'][0]}", HOURLY),
        ("Macro event calendar and prices", update_events, "central_bank_calendar", DAILY),
        ("Trade review (Hyperliquid)", update_trade_review, "trade_review:hyperliquid", DAILY),
        ("Watched wallets (on-chain)", update_onchain, "onchain", DAILY),
        ("Hyperliquid cohorts", update_hl_cohorts, "hyperliquid_cohorts", HOURLY),
    ]
    # Coinalyze is slow only because of its 40-calls-a-minute limit, so it runs
    # in the background (own database connection) while everything else updates.
    import threading
    def _flows():
        c = storage.connect()
        try:
            update_flows(c, cfg)
        finally:
            c.close()
    say("Updating cross-exchange flows (Coinalyze) in the background...")
    flows_thread = threading.Thread(target=_flows, daemon=True)
    flows_thread.start()
    skipped = []
    for label, fn, source, max_age in steps:
        if not full and source and is_fresh(con, source, max_age):
            skipped.append(label)
            continue
        say(f"Updating {label}...")
        fn(con, cfg)
    if skipped:
        say("Already up to date, skipped: " + ", ".join(skipped))
    if flows_thread.is_alive():
        say("Waiting for Coinalyze flows to finish (rate-limited)...")
    flows_thread.join()
    from .sources import registry
    say("Updating Glassnode-style data sources...")
    registry.update_all(con, {**cfg, "_include_trends": include_trends}, force=full, say=say)
    for asset in cfg["assets"]:
        say(f"Updating live positioning for {asset}...")
        positioning_snapshot(con, cfg, asset)
    say("Done.")
    con.close()


if __name__ == "__main__":
    run_all()
