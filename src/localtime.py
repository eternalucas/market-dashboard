"""Display times in Montréal time. Stored timestamps stay UTC; only what is shown changes."""
from __future__ import annotations

import pandas as pd

LOCAL_TZ = "America/Toronto"   # EST, or EDT in summer


def local(ts) -> pd.Timestamp:
    """A timestamp (naive values are UTC) converted to local time."""
    t = pd.Timestamp(ts)
    return (t.tz_localize("UTC") if t.tzinfo is None else t).tz_convert(LOCAL_TZ)


def fmt(ts, f: str = "%d %b %H:%M") -> str:
    """'03 Nov 16:19 EST'."""
    return f"{local(ts):{f} %Z}"


def fmt_col(s: pd.Series, f: str = "%d %b %H:%M") -> pd.Series:
    """The same for a datetime column; missing values stay NaN."""
    s = pd.to_datetime(s, utc=True)
    return s.dt.tz_convert(LOCAL_TZ).dt.strftime(f + " %Z")
