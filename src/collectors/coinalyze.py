"""Coinalyze: aggregated derivatives and spot flow data across exchanges.

This is the same data behind the "Aggregated CVD / OI / Funding" indicators
on TradingView. One free API key covers every exchange Coinalyze tracks.

Two things to know about the API:
- Rate limit: 40 calls a minute, and every symbol in a request counts as one
  call. `Client` paces requests and waits when Coinalyze says to (HTTP 429).
- Intraday history is short: only the last ~1,500-2,000 bars per series are
  kept and older ones are deleted daily. The pipeline stores every fetch in
  SQLite, so local history keeps growing beyond what Coinalyze retains.

Field meanings (ohlcv-history): v = volume, bv = taker BUY volume, so taker
sell volume = v - bv. liquidation-history: l = longs liquidated, s = shorts.
"""
from __future__ import annotations

import json
import time
from collections import deque

import pandas as pd
import requests

from ..storage import DB_PATH
from .http import USER_AGENT

BASE = "https://api.coinalyze.net/v1"
CACHE = DB_PATH.parent / "coinalyze_markets.json"  # kept next to the database
CALLS_PER_MINUTE = 38  # a little under the limit of 40


class Client:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("No Coinalyze API key in config.yaml (api_keys: coinalyze)")
        self.s = requests.Session()
        self.s.headers.update({"api_key": api_key, "User-Agent": USER_AGENT})
        self.calls: deque[float] = deque()

    def _pace(self, cost: int) -> None:
        """Wait until `cost` more calls fit inside a rolling 60-second window."""
        while True:
            now = time.time()
            while self.calls and now - self.calls[0] > 60:
                self.calls.popleft()
            if len(self.calls) + cost <= CALLS_PER_MINUTE:
                self.calls.extend([now] * cost)
                return
            time.sleep(60 - (now - self.calls[0]) + 0.5)

    def get(self, path: str, params: dict | None = None, cost: int = 1):
        for _ in range(3):
            self._pace(cost)
            r = self.s.get(f"{BASE}/{path}", params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", 10)) + 1)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"Coinalyze kept rate-limiting {path}")

    # ------------------------------------------------------------------ #
    # Market lists
    # ------------------------------------------------------------------ #

    def markets(self, max_age_days: float = 7) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
        """(futures markets, spot markets, exchange code -> name), cached on
        disk for a week since listings rarely change."""
        if CACHE.exists() and time.time() - CACHE.stat().st_mtime < max_age_days * 86400:
            data = json.loads(CACHE.read_text())
        else:
            data = {"future": self.get("future-markets"), "spot": self.get("spot-markets"),
                    "exchanges": self.get("exchanges")}
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(data))
        names = {e["code"]: e["name"] for e in data["exchanges"]}
        fut, spot = pd.DataFrame(data["future"]), pd.DataFrame(data["spot"])
        for df in (fut, spot):
            df["exchange_name"] = df["exchange"].map(names).fillna(df["exchange"])
        return fut, spot, names

    # ------------------------------------------------------------------ #
    # History
    # ------------------------------------------------------------------ #

    def history(self, endpoint: str, symbols: list[str], interval: str, start: int, end: int,
                **extra) -> dict[str, pd.DataFrame]:
        """{symbol: DataFrame indexed by UTC time} for one history endpoint."""
        out: dict[str, pd.DataFrame] = {}
        for i in range(0, len(symbols), 20):
            chunk = symbols[i:i + 20]
            params = {"symbols": ",".join(chunk), "interval": interval, "from": start, "to": end, **extra}
            for item in self.get(endpoint, params, cost=len(chunk)):
                df = pd.DataFrame(item.get("history") or [])
                if df.empty:
                    continue
                df.index = pd.to_datetime(df.pop("t"), unit="s", utc=True)
                out[item["symbol"]] = df.astype(float)
        return out


QUOTE_PREFERENCE = ["USDT", "USD", "USDC", "FDUSD"]


def pick_markets(df: pd.DataFrame, asset: str, exchanges: list[str], perp: bool) -> dict[str, str]:
    """One market per exchange for this asset: {exchange name: symbol}.

    Perps: stablecoin-margined perpetuals only (the standard USDT contracts),
    matching the 'STABLECOIN-margined' aggregates on TradingView.
    """
    if df.empty:
        return {}
    m = df[df["base_asset"].str.upper() == asset.upper()]
    if perp:
        m = m[m["is_perpetual"].astype(bool) & (m["margined"].astype(str).str.upper() != "COIN")]
    picked = {}
    for ex in exchanges:
        rows = m[m["exchange_name"].str.lower() == ex.lower()]
        if rows.empty:
            continue
        # Coinbase's and Bitfinex's main books are in real dollars (BTC-USD);
        # the "Coinbase premium" compares Coinbase USD with Binance USDT.
        prefs = ["USD", "USDC", "USDT"] if ex.lower() in ("coinbase", "bitfinex") else QUOTE_PREFERENCE
        rank = rows["quote_asset"].str.upper().map(lambda q: prefs.index(q) if q in prefs else 99)
        best = rows.assign(rank=rank).sort_values("rank").iloc[0]
        if best["rank"] < 99:
            picked[ex] = best["symbol"]
    return picked
