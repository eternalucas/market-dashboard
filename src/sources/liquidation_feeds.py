"""Inputs for the liquidation heatmap beyond the open-interest model.

1. Hyperliquid exact levels. Hyperliquid is on-chain: clearinghouseState gives
   every account's positions with their exact liquidation price (cross margin
   included). scan_hyperliquid() walks the accounts on the Hyperliquid
   leaderboard, largest first, keeps each account's latest positions in
   hl_positions, and after every scan stores a snapshot of the levels per
   coin in hl_liq_levels, plus the share of Hyperliquid open interest the
   scanned positions cover (series hl_liq_coverage:<coin>).

2. Actual liquidations, recorded live from the public feeds (table
   liquidations): Bybit's allLiquidation stream (every liquidation), Binance's
   !forceOrder stream (the largest liquidation per contract each second) and
   OKX's liquidation-orders endpoint (polled every minute). They are drawn on
   the chart and used to tune the model's leverage mix (calibrate()).

Read-only public data; nothing here signs or trades.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import storage
from ..collectors.http import get_json

DATA = Path(__file__).resolve().parents[2] / "data"
HL_DB = DATA / "hl_positions.db"


def _hl_db():
    import sqlite3
    c = sqlite3.connect(HL_DB, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS hl_positions (account TEXT NOT NULL, coin TEXT NOT NULL, szi REAL,
                 entry REAL, liq_px REAL, value REAL, ts TEXT NOT NULL, PRIMARY KEY (account, coin))""")
    return c
SCAN_STATE = DATA / "hl_liq_scan.json"
CALIBRATION = DATA / "liq_calibration.json"


# --------------------------------------------------------------------------- #
# 1. Hyperliquid exact levels
# --------------------------------------------------------------------------- #

# Own weight budget: the Top 100 review keeps 800/min, Hyperliquid allows 1,200.
HL_SCAN_PER_MIN = 300          # clearinghouseState costs 2 -> 150 accounts a minute
HL_MIN_ACCOUNT_VALUE = 1_000
HL_TOP_EVERY_SCAN = 1_000      # the largest accounts are re-read on every scan


def _bucket(price: float, step: float) -> float:
    return float(np.floor(price / step) * step)


def scan_hyperliquid(con, coins: list[str], budget_s: float = 780, say=print) -> dict:
    from ..indicators.liquidations import price_step
    from ..venues.base import RateLimiter
    from ..venues.hyperliquid import Hyperliquid as HyperliquidAdapter
    hl = HyperliquidAdapter()
    limiter = RateLimiter(HL_SCAN_PER_MIN)
    state = json.loads(SCAN_STATE.read_text()) if SCAN_STATE.exists() else {}
    if not state.get("accounts") or time.time() - state.get("accounts_at", 0) > 86400:
        lb = hl.leaderboard()
        lb = lb[lb["account_value"] >= HL_MIN_ACCOUNT_VALUE].sort_values("account_value", ascending=False)
        state.update(accounts=lb["account_id"].tolist(), accounts_at=time.time(), cursor=state.get("cursor", 0))
    accts = state["accounts"]
    top, rest = accts[:HL_TOP_EVERY_SCAN], accts[HL_TOP_EVERY_SCAN:]
    cur = state.get("cursor", 0) % max(len(rest), 1)
    order = top + rest[cur:] + rest[:cur]
    t0, done = time.time(), 0
    hdb = _hl_db()
    import requests
    session = requests.Session()
    for a in order:
        if time.time() - t0 > budget_s:
            break
        limiter.spend(2)
        try:
            r = session.post("https://api.hyperliquid.xyz/info", json={"type": "clearinghouseState", "user": a},
                             timeout=20)
            if r.status_code == 429:
                time.sleep(10)
                continue
            r.raise_for_status()
            s = r.json()
        except Exception:  # noqa: BLE001 - skip this account, keep scanning
            continue
        now = pd.Timestamp.now(tz="UTC").isoformat()
        rows = []
        for ap in s.get("assetPositions", []):
            p = ap["position"]
            liq = p.get("liquidationPx")
            rows.append((a, p["coin"], float(p["szi"]), float(p.get("entryPx") or 0), float(liq) if liq else None,
                         float(p.get("positionValue") or 0), now))
        hdb.execute("DELETE FROM hl_positions WHERE account = ?", (a,))
        hdb.executemany("INSERT OR REPLACE INTO hl_positions VALUES (?,?,?,?,?,?,?)", rows)
        hdb.commit()
        done += 1
    scanned_rest = max(0, done - len(top))
    state["cursor"] = (cur + scanned_rest) % max(len(rest), 1)
    SCAN_STATE.write_text(json.dumps(state))
    # drop positions not refreshed for a day (account left the leaderboard or emptied)
    hdb.execute("DELETE FROM hl_positions WHERE ts < ?", ((pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=1)).isoformat(),))
    hdb.commit()
    res = snapshot_hl_levels(con, coins, price_step, hdb)
    hdb.close()
    return {"scanned": done, "accounts": len(accts), **res}


def snapshot_hl_levels(con, coins: list[str], price_step, hdb) -> dict:
    """Store the current exact levels per coin and the open-interest coverage."""
    meta = json.loads(json.dumps(get_hl_meta()))
    ts = pd.Timestamp.now(tz="UTC").floor("min").isoformat()
    cover = {}
    for coin in coins:
        pos = pd.read_sql_query("SELECT szi, liq_px FROM hl_positions WHERE coin = ?", hdb, params=(coin,))
        m = meta.get(coin)
        if pos.empty or not m:
            continue
        mark, oi = m
        cover[coin] = float(pos["szi"].abs().sum() / (2 * oi)) if oi else np.nan
        step = price_step(mark, 0.05)
        p = pos.dropna(subset=["liq_px"])
        p = p[(p["liq_px"] > mark * 0.5) & (p["liq_px"] < mark * 1.5)]
        p = p.assign(side=np.where(p["szi"] > 0, "long", "short"), usd=p["szi"].abs() * p["liq_px"],
                     price=(np.floor(p["liq_px"] / step) * step))
        g = p.groupby(["side", "price"], as_index=False)["usd"].sum()
        con.executemany("INSERT OR REPLACE INTO hl_liq_levels VALUES (?,?,?,?,?)",
                        [(coin, ts, r.side, float(r.price), float(r.usd)) for r in g.itertuples()])
        con.commit()
        storage.save_point(con, f"hl_liq_coverage:{coin}", cover[coin], pd.Timestamp(ts).to_pydatetime())
    return {"coverage": cover}


def get_hl_meta() -> dict:
    """coin -> (mark price, open interest in coins, one side)."""
    import requests
    meta, ctxs = requests.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"}, timeout=20).json()
    return {u["name"]: (float(c["markPx"]), float(c["openInterest"])) for u, c in zip(meta["universe"], ctxs)
            if c.get("markPx")}


def hl_levels(con, coin: str, since: pd.Timestamp) -> pd.DataFrame:
    return pd.read_sql_query("SELECT ts, side, price, usd FROM hl_liq_levels WHERE coin = ? AND ts >= ? ORDER BY ts",
                             con, params=(coin, since.isoformat())).assign(
        ts=lambda d: pd.to_datetime(d["ts"], utc=True, format="ISO8601"))


# --------------------------------------------------------------------------- #
# 2. Actual liquidations from the public feeds
# --------------------------------------------------------------------------- #

class LiquidationFeeds:
    """Bybit and Binance websockets plus an OKX poll, in background threads.
    Rows are queued and written in batches by one writer thread."""

    def __init__(self, assets: list[str]):
        self.assets = assets
        self.q: queue.Queue = queue.Queue()
        self.stop = threading.Event()
        self.counts = {"bybit": 0, "binance": 0, "okx": 0}
        self.errors: dict[str, str] = {}

    def start(self) -> None:
        for target in (self._writer, self._bybit, self._binance, self._okx):
            threading.Thread(target=target, daemon=True, name=f"liq_{target.__name__.strip('_')}").start()

    # ---- writer
    def _writer(self) -> None:
        con = storage.connect()
        while not self.stop.is_set():
            time.sleep(10)
            rows = []
            while not self.q.empty():
                rows.append(self.q.get_nowait())
            if rows:
                try:
                    con.executemany("INSERT OR IGNORE INTO liquidations VALUES (?,?,?,?,?,?,?)", rows)
                    con.commit()
                except Exception:  # noqa: BLE001 - database busy: put the rows back, retry in 10 s
                    for r in rows:
                        self.q.put(r)

    def _put(self, venue, asset, ts_ms, side, price, qty) -> None:
        if asset not in self.assets or price <= 0 or qty <= 0:
            return
        ts = pd.Timestamp(int(ts_ms), unit="ms", tz="UTC").isoformat()
        self.q.put((venue, asset, ts, side, float(price), float(qty), float(price) * float(qty)))
        self.counts[venue] += 1

    # ---- feeds
    def _ws_loop(self, url: str, on_open, on_message, heartbeat: str | None = None) -> None:
        import certifi
        import websocket
        venue = "bybit" if "bybit" in url else "binance"

        def on_error(ws, err):
            self.errors[venue] = f"{type(err).__name__}: {str(err)[:120]}"

        def on_message_safe(ws, msg):
            try:
                on_message(ws, msg)
            except Exception as e:  # noqa: BLE001 - one odd message must not stop the feed
                self.errors[venue] = f"message: {type(e).__name__}: {str(e)[:120]}"
        while not self.stop.is_set():
            # Python from python.org on a Mac has no system CA store for raw SSL sockets:
            # use certifi's bundle, the same one requests uses for every other source.
            ws = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message_safe, on_error=on_error)
            if heartbeat:
                def beat(w=ws):
                    while not self.stop.is_set() and w.keep_running:
                        time.sleep(20)
                        try:
                            w.send(heartbeat)
                        except Exception:  # noqa: BLE001
                            return
                threading.Thread(target=beat, daemon=True).start()
            try:
                ws.run_forever(ping_interval=0 if heartbeat else 60, sslopt={"ca_certs": certifi.where()})
            except Exception as e:  # noqa: BLE001
                self.errors[venue] = f"{type(e).__name__}: {str(e)[:120]}"
            time.sleep(5)            # reconnect

    def _bybit(self) -> None:
        def on_open(ws):
            ws.send(json.dumps({"op": "subscribe", "args": [f"allLiquidation.{a}USDT" for a in self.assets]}))

        def on_message(ws, msg):
            d = json.loads(msg)
            for r in d.get("data") or []:
                # S = position side: Buy means a long position was liquidated (Bybit docs)
                self._put("bybit", r["s"].removesuffix("USDT"), r["T"], "long" if r["S"] == "Buy" else "short",
                          float(r["p"]), float(r["v"]))
        self._ws_loop("wss://stream.bybit.com/v5/public/linear", on_open, on_message, heartbeat='{"op":"ping"}')

    def _binance(self) -> None:
        def on_message(ws, msg):
            o = json.loads(msg).get("o") or {}
            if not o.get("s", "").endswith("USDT"):
                return
            # S = the liquidation order's side: SELL closes a long
            self._put("binance", o["s"].removesuffix("USDT"), o["T"], "long" if o["S"] == "SELL" else "short",
                      float(o.get("ap") or o["p"]), float(o.get("z") or o["q"]))
        self._ws_loop("wss://fstream.binance.com/ws/!forceOrder@arr", None, on_message)

    def _okx(self) -> None:
        ct_val: dict[str, float] = {}
        while not self.stop.is_set():
            for a in self.assets:
                try:
                    if a not in ct_val:
                        inst = get_json("https://www.okx.com/api/v5/public/instruments",
                                        {"instType": "SWAP", "instId": f"{a}-USDT-SWAP"})["data"]
                        ct_val[a] = float(inst[0]["ctVal"]) if inst else 0.0
                    if not ct_val[a]:
                        continue
                    d = get_json("https://www.okx.com/api/v5/public/liquidation-orders",
                                 {"instType": "SWAP", "uly": f"{a}-USDT", "state": "filled"})["data"]
                    for blk in d:
                        for r in blk.get("details", []):
                            self._put("okx", a, r["ts"], r["posSide"] if r["posSide"] in ("long", "short")
                                      else ("long" if r["side"] == "sell" else "short"),
                                      float(r["bkPx"]), float(r["sz"]) * ct_val[a])
                except Exception:  # noqa: BLE001 - try again next minute
                    pass
                time.sleep(0.5)
            time.sleep(60)


def actual_liquidations(con, asset: str, since: pd.Timestamp) -> pd.DataFrame:
    return pd.read_sql_query("SELECT venue, ts, side, price, usd FROM liquidations WHERE asset = ? AND ts >= ?",
                             con, params=(asset, since.isoformat())).assign(
        ts=lambda d: pd.to_datetime(d["ts"], utc=True, format="ISO8601"))


# --------------------------------------------------------------------------- #
# 3. Calibration of the leverage mix against actual liquidations
# --------------------------------------------------------------------------- #

TIERS = (5, 10, 25, 50, 100)
MIN_DAYS, MIN_EVENTS = 3, 300


def load_weights(asset: str) -> tuple[dict | None, dict]:
    """Fitted leverage weights for an asset (falls back to BTC's), and the fit info."""
    if not CALIBRATION.exists():
        return None, {}
    cal = json.loads(CALIBRATION.read_text())
    info = cal.get(asset) or cal.get("BTC") or {}
    w = info.get("weights")
    return ({int(k): v for k, v in w.items()} if w else None), info


def calibrate(con, asset: str, inputs, step_pct: float = 0.25, grid_step: float = 0.05) -> dict | None:
    """Pick the leverage mix whose modelled liquidations best match where and
    when liquidations actually happened (cosine similarity over hour x 0.25%
    price bucket x side). The model is linear in the weights, so each tier is
    run once and any mix is a weighted sum. inputs(asset) -> (candles, venues)
    for 5-minute bars over the calibration period."""
    from itertools import product

    from ..indicators import liquidations as lq
    first = con.execute("SELECT MIN(ts) FROM liquidations WHERE asset = ?", (asset,)).fetchone()[0]
    if not first:
        return None
    since = max(pd.Timestamp(first), pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=14))
    actual = actual_liquidations(con, asset, since)
    if len(actual) < MIN_EVENTS or pd.Timestamp.now(tz="UTC") - since < pd.Timedelta(days=MIN_DAYS):
        return None
    candles, venues = inputs(asset)
    candles = candles[candles.index >= since - pd.Timedelta(days=2)]
    mid = float(candles["c"].iloc[-1])
    bucket = mid * step_pct / 100

    def to_matrix(df: pd.DataFrame) -> pd.Series:
        if df.empty:
            return pd.Series(dtype=float)
        df = df.assign(ts=pd.to_datetime(df["ts"], utc=True))
        df = df[df["ts"] >= since]
        key = [df["ts"].dt.floor("h"), (df["price"] / bucket).round().astype(int), df["side"]]
        return df.groupby(key)["usd"].sum()

    act = to_matrix(actual)
    per_tier = {}
    for lev in TIERS:
        parts = []
        for name, v in venues.items():
            bars = candles[["h", "l", "c", "buy", "sell"]].copy()
            bars["oi"] = v["oi"].reindex(bars.index)
            if {"buy", "sell"} <= set(v.columns):
                bars[["buy", "sell"]] = v[["buy", "sell"]].reindex(bars.index).fillna(bars[["buy", "sell"]])
            if bars["oi"].notna().sum() < 10:
                continue
            out = lq.liquidation_levels(bars, step=lq.price_step(mid, 0.05), leverage={lev: 1.0}, track_removed=True)
            parts.append(out["removed"])
        per_tier[lev] = to_matrix(pd.concat(parts)) if parts else pd.Series(dtype=float)
    idx = act.index
    for s in per_tier.values():
        idx = idx.union(s.index)
    A = act.reindex(idx, fill_value=0).to_numpy()
    M = np.column_stack([per_tier[lev].reindex(idx, fill_value=0).to_numpy() for lev in TIERS])

    def score(w):
        p = M @ w
        den = np.linalg.norm(p) * np.linalg.norm(A)
        return float(p @ A / den) if den else 0.0

    steps = int(round(1 / grid_step))
    best, best_w = -1.0, None
    for combo in product(range(steps + 1), repeat=len(TIERS) - 1):
        if sum(combo) > steps:
            continue
        w = np.array([*combo, steps - sum(combo)], float) / steps
        sc = score(w)
        if sc > best:
            best, best_w = sc, w
    default = np.array([lq.LEVERAGE.get(t, 0.0) for t in TIERS])
    info = {"weights": {str(t): round(float(x), 3) for t, x in zip(TIERS, best_w) if x > 0},
            "score": round(best, 4), "default_score": round(score(default), 4),
            "events": int(len(actual)), "since": since.isoformat(),
            "fitted_at": pd.Timestamp.now(tz="UTC").isoformat()}
    cal = json.loads(CALIBRATION.read_text()) if CALIBRATION.exists() else {}
    cal[asset] = info
    CALIBRATION.write_text(json.dumps(cal, indent=1))
    return info
