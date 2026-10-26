"""alternative.me Crypto Fear & Greed Index (free, daily since Feb 2018).
Raw series: fng (0-100)."""
from __future__ import annotations

import pandas as pd

from .. import storage
from ..collectors.http import get_json


def update(con) -> int:
    d = get_json("https://api.alternative.me/fng/", {"limit": 0})["data"]
    s = pd.Series({pd.to_datetime(int(x["timestamp"]), unit="s", utc=True): float(x["value"]) for x in d})
    return storage.save_series(con, "fng", s.sort_index())
