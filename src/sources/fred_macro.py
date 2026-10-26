"""FRED macro series for the Macro dashboard (key: api_keys.fred).

Raw series: fred:<SERIES_ID> at the series' own frequency (levels).
Each entry: id -> (Glassnode chart it backs, note). Entries marked
'approx' use the closest series FRED carries; the Data health tab flags any
whose latest observation is older than its expected cadence.
"""
from __future__ import annotations

import pandas as pd

from .. import storage
from ..collectors import fred

SERIES = {
    "WALCL": ("US Central Bank Balance Sheet", "weekly, $ millions"),
    "ECBASSETSW": ("Euro Area Central Bank Balance Sheet", "weekly, EUR millions"),
    "DFEDTARU": ("US Interest Rate", "daily, upper bound"),
    "ECBDFR": ("Euro Area Interest Rate", "daily, deposit facility rate"),
    "M2SL": ("US Money Supply M2", "monthly, $ billions"),
    "MYAGM2EZM196N": ("Euro Area Money Supply M2", "monthly; approx: check freshness"),
    "CPILFESL": ("US Core Inflation Rate", "monthly index -> YoY in metrics"),
    "00XEFDEZ19M086NEST": ("Euro Area Core Inflation Rate", "monthly HICP ex energy/food/alcohol/tobacco -> YoY"),
    "MICH": ("US Inflation Expectations", "monthly, UMich 1-year"),
    "T5YIE": ("US Inflation Expectations (market)", "daily, 5y breakeven"),
    "GFDEBTN": ("US Government Debt", "quarterly, $ millions"),
    "GFDEGDQ188S": ("US Government Debt to GDP", "quarterly, %"),
    "FYONGDA188S": ("US Government Spending to GDP", "annual, federal outlays % of GDP"),
    "GDP": ("US GDP", "quarterly, $ billions SAAR"),
    "CLVMNACSCAB1GQEA19": ("Euro Area GDP", "quarterly, chained 2010 EUR millions"),
    "USALOLITONOSTSAM": ("US Leading Economic Index", "monthly; approx: OECD composite leading indicator"),
    "UMCSENT": ("US Consumer Confidence", "monthly, UMich sentiment"),
    "CSCICP03EZM665S": ("Euro Area Consumer Confidence", "monthly, OECD"),
    "DGS2": ("US 2Y Yield", "daily, %"),
    "DGS10": ("US 10Y Yield", "daily, %"),
    "T10Y2Y": ("US 10Y-2Y Spread", "daily, pp"),
    "IRLTLT01DEM156N": ("Germany 10Y Yield", "monthly, %; approx (FRED has no daily Bund series)"),
}
# Not on FRED (stay partial): Germany 2Y yield, Germany 10Y-2Y spread, euro-area debt and spending ratios.


def update(con, api_key: str) -> int:
    rows, failed = 0, []
    for sid in SERIES:
        try:
            rows += storage.save_series(con, f"fred:{sid}", fred.series(sid, api_key, "lin", start="1990-01-01"))
        except Exception as e:  # noqa: BLE001 - one bad id must not stop the rest
            failed.append(f"{sid}: {type(e).__name__}")
    if failed:
        storage.log_fetch(con, "fred_macro:failed", False, 0, "; ".join(failed))
    return rows
