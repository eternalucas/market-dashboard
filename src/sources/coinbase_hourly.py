"""Coinbase BTC-USD hourly candles since 2015 (public API, 300 candles per
request), used for the regional (US / EU / Asia) price-change metrics and
intraday views. Raw series: cb1h:BTC-USD:<open|high|low|close|volume>,
stamped at the start of each UTC hour."""
from __future__ import annotations

import time

import pandas as pd

from .. import storage
from ..collectors.http import get_json

URL = "https://api.exchange.coinbase.com/products/{p}/candles"
FIRST = pd.Timestamp("2015-07-20", tz="UTC")
CHUNK = pd.Timedelta(hours=300)


def update(con, product: str = "BTC-USD", max_calls: int = 400) -> int:
    last = storage.load_series(con, f"cb1h:{product}:close")
    cursor = FIRST if last.empty else last.index[-1] - pd.Timedelta(hours=2)
    now = pd.Timestamp.now(tz="UTC")
    rows, calls = 0, 0
    while cursor < now and calls < max_calls:
        end = min(cursor + CHUNK, now)
        data = get_json(URL.format(p=product), {"granularity": 3600, "start": cursor.isoformat(), "end": end.isoformat()})
        calls += 1
        if data:
            df = pd.DataFrame(data, columns=["t", "low", "high", "open", "close", "volume"])
            df.index = pd.to_datetime(df.pop("t"), unit="s", utc=True)
            for c in ("open", "high", "low", "close", "volume"):
                rows += storage.save_series(con, f"cb1h:{product}:{c}", df[c].astype(float))
        cursor = end
        time.sleep(0.15)
    return rows
