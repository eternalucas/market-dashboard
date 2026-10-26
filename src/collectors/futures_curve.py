"""Dated futures prices from Binance and OKX (Deribit lives in deribit.py),
for the term structure and the annualized rolling basis."""
from __future__ import annotations

import pandas as pd

from . import deribit
from .http import get_json


def _exp_from_code(code: str) -> pd.Timestamp:
    """'261225' -> 2026-12-25 08:00 UTC (Binance and OKX quarterlies settle at 08:00 UTC)."""
    return pd.to_datetime(code, format="%y%m%d", utc=True) + pd.Timedelta(hours=8)


def binance_curve(asset: str = "BTC") -> pd.DataFrame:
    rows = get_json("https://fapi.binance.com/fapi/v1/premiumIndex")
    out = [{"venue": "binance", "instrument": r["symbol"], "expiry": _exp_from_code(r["symbol"].split("_")[1]),
            "price": float(r["markPrice"]), "index": float(r["indexPrice"]), "oi_usd": None}
           for r in rows if r["symbol"].startswith(f"{asset}USDT_")]
    return pd.DataFrame(out)


def okx_curve(asset: str = "BTC") -> pd.DataFrame:
    rows = get_json("https://www.okx.com/api/v5/market/tickers", {"instType": "FUTURES", "instFamily": f"{asset}-USD"})["data"]
    idx = float(get_json("https://www.okx.com/api/v5/market/index-tickers", {"instId": f"{asset}-USD"})["data"][0]["idxPx"])
    out = [{"venue": "okx", "instrument": r["instId"], "expiry": _exp_from_code(r["instId"].split("-")[-1]),
            "price": float(r["last"]), "index": idx, "oi_usd": None} for r in rows]
    return pd.DataFrame(out)


VENUES = {"deribit": deribit.futures, "binance": binance_curve, "okx": okx_curve}
