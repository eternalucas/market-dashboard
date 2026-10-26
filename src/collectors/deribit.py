"""Deribit public API: BTC option chain snapshots, option trades, dated
futures and the DVOL volatility index. No key needed."""
from __future__ import annotations

import time

import pandas as pd

from .http import get_json

BASE = "https://www.deribit.com/api/v2/public"
# Trades older than about a day are served by Deribit's history host.
HISTORY = "https://history.deribit.com/api/v2/public"


def parse_instrument(name: str) -> tuple[pd.Timestamp, float, str]:
    """BTC-27NOV26-73000-P -> (expiry 08:00 UTC, strike, 'P')."""
    _, exp, strike, cp = name.split("-")
    return pd.to_datetime(exp, format="%d%b%y", utc=True) + pd.Timedelta(hours=8), float(strike), cp


def option_chain(currency: str = "BTC") -> pd.DataFrame:
    """Every listed option right now: one row per contract, in option_snap's
    columns (without venue / ts, which the recorder adds)."""
    rows = get_json(f"{BASE}/get_book_summary_by_currency", {"currency": currency, "kind": "option"})["result"]
    out = []
    for r in rows:
        if r.get("mark_iv") is None or r.get("underlying_price") is None:
            continue
        exp, k, cp = parse_instrument(r["instrument_name"])
        out.append({"expiry": exp.isoformat(), "strike": k, "type": cp,
                    "iv": r["mark_iv"] / 100, "bid_iv": None, "ask_iv": None,
                    "oi": r.get("open_interest") or 0.0, "volume": r.get("volume") or 0.0,
                    "mark_price": r.get("mark_price"), "bid": r.get("bid_price"), "ask": r.get("ask_price"),
                    "underlying": r["underlying_price"]})
    return pd.DataFrame(out)


def option_trades(start_ms: int, end_ms: int, currency: str = "BTC", history: bool = False) -> pd.DataFrame:
    """All option trades between two times (ms), paging forward 1,000 at a
    time. history=True uses history.deribit.com (needed beyond ~24 h)."""
    out, cursor = [], start_ms
    host = HISTORY if history else BASE
    while True:
        d = get_json(f"{host}/get_last_trades_by_currency_and_time",
                     {"currency": currency, "kind": "option", "start_timestamp": cursor,
                      "end_timestamp": end_ms, "count": 1000, "sorting": "asc"})["result"]
        trades = d["trades"]
        for t in trades:
            exp, k, cp = parse_instrument(t["instrument_name"])
            out.append({"trade_id": str(t["trade_id"]),
                        "ts": pd.to_datetime(t["timestamp"], unit="ms", utc=True).isoformat(),
                        "instrument": t["instrument_name"], "expiry": exp.isoformat(), "strike": k, "type": cp,
                        "direction": t["direction"], "amount": float(t["amount"]), "price": float(t["price"]),
                        "index_price": float(t["index_price"]), "iv": (t.get("iv") or 0) / 100 or None,
                        "combo": int(bool(t.get("combo_id") or t.get("block_trade_id")))})
        if not d.get("has_more") or not trades:
            break
        cursor = trades[-1]["timestamp"] + 1 if trades[-1]["timestamp"] > cursor else cursor + 1
        time.sleep(0.2)
    return pd.DataFrame(out).drop_duplicates("trade_id") if out else pd.DataFrame()


def futures(currency: str = "BTC") -> pd.DataFrame:
    """Dated futures (and the perpetual): instrument, expiry, mark price, index."""
    rows = get_json(f"{BASE}/get_book_summary_by_currency", {"currency": currency, "kind": "future"})["result"]
    out = []
    for r in rows:
        name = r["instrument_name"]
        exp = None if name.endswith("PERPETUAL") else \
            pd.to_datetime(name.split("-")[1], format="%d%b%y", utc=True) + pd.Timedelta(hours=8)
        out.append({"venue": "deribit", "instrument": name, "expiry": exp,
                    "price": float(r["mark_price"]), "index": float(r["estimated_delivery_price"]),
                    "oi_usd": float(r.get("open_interest") or 0)})
    return pd.DataFrame(out)


def dvol_history(currency: str = "BTC", resolution: int = 86400) -> pd.DataFrame:
    """DVOL (Deribit's 30-day implied volatility index) OHLC, full history.
    The API returns the newest 1,000 bars plus a continuation timestamp to
    page backwards."""
    end, frames = int(time.time() * 1000), []
    for _ in range(20):
        d = get_json(f"{BASE}/get_volatility_index_data",
                     {"currency": currency, "resolution": resolution,
                      "start_timestamp": 1_609_459_200_000, "end_timestamp": end})["result"]
        if not d["data"]:
            break
        frames.append(pd.DataFrame(d["data"], columns=["t", "open", "high", "low", "close"]))
        if not d.get("continuation"):
            break
        end = d["continuation"]
        time.sleep(0.2)
    df = pd.concat(frames).drop_duplicates("t")
    df.index = pd.to_datetime(df.pop("t"), unit="ms", utc=True)
    return df.sort_index()
