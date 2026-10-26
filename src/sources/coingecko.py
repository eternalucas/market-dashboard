"""CoinGecko global market snapshot (free public API). CoinGecko's free tier
has no history of total market cap, so this is recorded daily from the day we
start; longer history of BTC dominance comes from BGeometrics.

Raw series: cg:total_mcap_usd, cg:total_volume_usd, cg:dominance:<coin> (%).
"""
from __future__ import annotations

import pandas as pd

from .. import storage
from ..collectors.http import get_json


def update(con) -> int:
    d = get_json("https://api.coingecko.com/api/v3/global")["data"]
    day = pd.Timestamp.now(tz="UTC").normalize()
    rows = storage.save_point(con, "cg:total_mcap_usd", float(d["total_market_cap"]["usd"]), day)
    rows += storage.save_point(con, "cg:total_volume_usd", float(d["total_volume"]["usd"]), day)
    for coin, pct in d["market_cap_percentage"].items():
        rows += storage.save_point(con, f"cg:dominance:{coin}", float(pct), day)
    return rows
