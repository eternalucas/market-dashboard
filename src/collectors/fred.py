"""FRED (St. Louis Fed): release dates for US macro events, and the data
behind them. Free key from fredaccount.stlouisfed.org."""
from __future__ import annotations

import pandas as pd

from .http import get_json

BASE = "https://api.stlouisfed.org/fred"

# FRED release IDs (checked against FRED's release list).
RELEASES = {
    "CPI": 10,
    "PPI": 46,
    "Payrolls (NFP)": 50,
    "Jobless claims": 180,
}  # FOMC dates come from the Fed's own calendar in events.py


def release_dates(release_id: int, api_key: str, start: str = "2018-01-01") -> pd.DatetimeIndex:
    """Every date the release came out, including scheduled future dates."""
    data = get_json(f"{BASE}/release/dates", {
        "release_id": release_id, "api_key": api_key, "file_type": "json",
        "include_release_dates_with_no_data": "true", "realtime_start": start,
        "realtime_end": "9999-12-31", "sort_order": "asc", "limit": 10000,
    })
    return pd.DatetimeIndex(sorted({d["date"] for d in data["release_dates"]}))


def series(series_id: str, api_key: str, units: str = "lin", start: str = "2015-01-01") -> pd.Series:
    """Observations of one FRED series. units: lin (level), pc1 (% vs a year
    ago), chg (change vs previous period)."""
    data = get_json(f"{BASE}/series/observations", {
        "series_id": series_id, "api_key": api_key, "file_type": "json",
        "units": units, "observation_start": start,
    })
    s = pd.Series({pd.Timestamp(o["date"]): o["value"] for o in data["observations"]})
    return pd.to_numeric(s, errors="coerce").dropna()
