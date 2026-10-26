"""CFTC Traders in Financial Futures report for CME Bitcoin futures (weekly,
positions as of Tuesday, published Friday). Socrata dataset gpe5-46if.
Contract code 133741 = CME Bitcoin (5 BTC per contract).

Raw series (stamped on the report date): cftc:btc:<field>, open interest in
contracts. The metrics layer converts to BTC (x5) and USD (x price).
"""
from __future__ import annotations

import pandas as pd

from .. import storage
from ..collectors.http import get_json

URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"          # futures only
URL_COMBINED = "https://publicreporting.cftc.gov/resource/yw9f-hn96.json"  # futures + options (delta-adjusted)
FIELDS = ["open_interest_all", "dealer_positions_long_all", "dealer_positions_short_all",
          "asset_mgr_positions_long", "asset_mgr_positions_short",
          "lev_money_positions_long", "lev_money_positions_short",
          "other_rept_positions_long", "other_rept_positions_short"]
CONTRACT_BTC = 5.0


def update(con, code: str = "133741") -> int:
    n = _update(con, URL, "cftc:btc", code)
    try:   # options OI = combined - futures-only (CME options are delta-adjusted in the combined report)
        n += _update(con, URL_COMBINED, "cftc:btc_combined", code)
    except Exception:  # noqa: BLE001 - the futures report is the essential one
        pass
    return n


def _update(con, url: str, prefix: str, code: str) -> int:
    rows = get_json(url, {"cftc_contract_market_code": code, "$limit": 5000,
                          "$order": "report_date_as_yyyy_mm_dd ASC"})
    df = pd.DataFrame(rows)
    if df.empty:
        return 0
    df.index = pd.to_datetime(df["report_date_as_yyyy_mm_dd"], utc=True)
    return sum(storage.save_series(con, f"{prefix}:{f}", pd.to_numeric(df[f], errors="coerce"))
               for f in FIELDS if f in df)
