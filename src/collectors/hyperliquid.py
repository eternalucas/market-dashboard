"""Hyperliquid public data: the leaderboard, any account's fills, positions
and account-value history. Everything on Hyperliquid is on-chain, so no key
is needed and any trader's history can be reviewed.
"""
from __future__ import annotations

import random
import time

import pandas as pd

from .http import get_json, post_json

INFO = "https://api.hyperliquid.xyz/info"
LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"


def info(payload: dict):
    return post_json(INFO, payload)


# --------------------------------------------------------------------------- #
# Leaderboard
# --------------------------------------------------------------------------- #

_cache: dict = {}


def leaderboard() -> pd.DataFrame:
    """One row per account: address, account value, and PnL/ROI/volume for
    day, week, month and all time. The file is large, so it's kept for 10
    minutes and shared by the trade review and the cohort analysis."""
    if "lb" in _cache and time.time() - _cache["lb"][0] < 600:
        return _cache["lb"][1].copy()
    rows = []
    for r in get_json(LEADERBOARD)["leaderboardRows"]:
        row = {"address": r["ethAddress"], "account_value": float(r["accountValue"]),
               "name": r.get("displayName")}
        for window, perf in r["windowPerformances"]:
            for k in ("pnl", "roi", "vlm"):
                row[f"{window}_{k}"] = float(perf[k])
        rows.append(row)
    df = pd.DataFrame(rows)
    _cache["lb"] = (time.time(), df)
    return df.copy()


def eligible_traders(lb: pd.DataFrame, min_acct: float, max_acct: float, min_month_vlm: float) -> pd.DataFrame:
    """The pool a trader is drawn from: account between min and max, profitable
    all-time AND this month, and actively trading this month."""
    return lb[(lb["account_value"].between(min_acct, max_acct))
              & (lb["allTime_pnl"] > 0) & (lb["month_pnl"] > 0)
              & (lb["month_vlm"] >= min_month_vlm)].reset_index(drop=True)


def draw_order(pool: pd.DataFrame, seed: int) -> list[str]:
    """The pool's addresses in a random order fixed by the seed: the first
    candidate is the same as pick_trader's, the rest are the fallbacks."""
    addresses = sorted(pool["address"])
    random.Random(seed).shuffle(addresses)
    return addresses


def pick_trader(pool: pd.DataFrame, seed: int) -> dict:
    """Random draw from the pool, reproducible from the seed, so the same
    trader comes back on every run until the seed is changed."""
    if pool.empty:
        raise ValueError("No trader matches the selection rule")
    addresses = sorted(pool["address"])  # sort so the draw doesn't depend on API order
    chosen = random.Random(seed).choice(addresses)
    return pool[pool["address"] == chosen].iloc[0].to_dict()


# --------------------------------------------------------------------------- #
# One account
# --------------------------------------------------------------------------- #

def fills(address: str, start_ms: int = 0) -> pd.DataFrame:
    """All fills since start_ms. The API returns up to 2,000 per call and only
    keeps an account's 10,000 most recent fills, so this pages forward in time."""
    out, cursor = [], start_ms
    while True:
        batch = info({"type": "userFillsByTime", "user": address, "startTime": cursor, "aggregateByTime": True})
        if not batch:
            break
        out.extend(batch)
        cursor = max(f["time"] for f in batch) + 1
        if len(batch) < 2000:
            break
        time.sleep(0.3)
    if not out:
        return pd.DataFrame()
    df = pd.DataFrame(out)
    for c in ("px", "sz", "startPosition", "closedPnl", "fee"):
        df[c] = df[c].astype(float)
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    return df.drop_duplicates("tid").sort_values(["time", "tid"]).reset_index(drop=True)


def account_state(address: str) -> dict:
    """Account value and open positions right now."""
    s = info({"type": "clearinghouseState", "user": address})
    positions = []
    for p in s.get("assetPositions", []):
        pos = p["position"]
        positions.append({"coin": pos["coin"], "size": float(pos["szi"]),
                          "entry_px": float(pos.get("entryPx") or 0),
                          "value_usd": float(pos["positionValue"]),
                          "unrealized_pnl": float(pos["unrealizedPnl"]),
                          "leverage": (pos.get("leverage") or {}).get("value")})
    return {"account_value": float(s["marginSummary"]["accountValue"]), "positions": pd.DataFrame(positions)}


def account_value_history(address: str) -> pd.Series:
    """All-time account value history from the portfolio endpoint."""
    data = dict(info({"type": "portfolio", "user": address}))
    hist = data.get("allTime", {}).get("accountValueHistory", [])
    return pd.Series({pd.to_datetime(t, unit="ms", utc=True): float(v) for t, v in hist}).sort_index()
