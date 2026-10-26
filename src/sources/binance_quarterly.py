"""Binance USDT-margined quarterly futures: daily history of the current- and
next-quarter continuous contracts plus the BTCUSDT index, back to 2020.
Used for the 3-month annualized rolling basis with full history (dated
futures from Deribit / OKX are only recorded from the day we start).

Raw series: bq:BTC:current_quarter, bq:BTC:next_quarter (daily close),
bq:BTC:index (daily close of the index price).
Quarterly contracts settle on the last Friday of Mar / Jun / Sep / Dec, 08:00 UTC.
"""
from __future__ import annotations

import time

import pandas as pd

from .. import storage
from ..collectors.http import get_json

FAPI = "https://fapi.binance.com/fapi/v1"
FIRST = pd.Timestamp("2020-08-01", tz="UTC")


def _klines(path: str, params: dict, start: pd.Timestamp) -> pd.Series:
    out, cursor = {}, int(start.timestamp() * 1000)
    while True:
        rows = get_json(f"{FAPI}/{path}", {**params, "interval": "1d", "startTime": cursor, "limit": 1500})
        if not rows:
            break
        for r in rows:
            out[pd.to_datetime(r[0], unit="ms", utc=True)] = float(r[4])
        if len(rows) < 1500:
            break
        cursor = rows[-1][0] + 1
        time.sleep(0.2)
    return pd.Series(out, dtype=float).sort_index()


def update(con, asset: str = "BTC") -> int:
    last = storage.load_series(con, f"bq:{asset}:index")
    start = FIRST if last.empty else last.index[-1] - pd.Timedelta(days=3)
    rows = storage.save_series(con, f"bq:{asset}:index", _klines("indexPriceKlines", {"pair": f"{asset}USDT"}, start))
    for ct in ("CURRENT_QUARTER", "NEXT_QUARTER"):
        s = _klines("continuousKlines", {"pair": f"{asset}USDT", "contractType": ct}, start)
        rows += storage.save_series(con, f"bq:{asset}:{ct.lower()}", s)
    return rows


def quarter_expiries(start: str = "2020-01-01", end: str = "2030-12-31") -> pd.DatetimeIndex:
    """Last Friday of Mar/Jun/Sep/Dec at 08:00 UTC."""
    out = []
    for m in pd.date_range(start, end, freq="QE-DEC"):
        d = m
        while d.weekday() != 4:
            d -= pd.Timedelta(days=1)
        out.append(pd.Timestamp(d.date(), tz="UTC") + pd.Timedelta(hours=8))
    return pd.DatetimeIndex(out)
