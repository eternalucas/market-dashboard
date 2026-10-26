"""Which sources to update, how often, and the one entry point that runs
them. Called by the recorder every hour (so data stays fresh without
clicking) and by the dashboard's "Update all data" button.

Each source runs only when its last success is older than its freshness
window; BGeometrics spends whatever is left of its call budget.
"""
from __future__ import annotations

import pandas as pd

from .. import storage
from . import (alternative_me, bgeometrics, binance_quarterly, blockchain_com, cftc, coinalyze_daily, coinbase_hourly, coingecko,
               coinmetrics, defillama,
               deribit_hist, farside, fred_macro, mempool_hist, social, yahoo)

H = pd.Timedelta(hours=1)
SOURCES = [  # (name, function(con, cfg), freshness)
    ("coinmetrics", lambda con, cfg: coinmetrics.update(con), 6 * H),
    ("blockchain_com", lambda con, cfg: blockchain_com.update(con), 12 * H),
    ("mempool_hist", lambda con, cfg: mempool_hist.update(con), 12 * H),
    ("defillama_stablecoins", lambda con, cfg: defillama.update_stablecoins(con), 12 * H),
    ("defillama_cex", lambda con, cfg: defillama.update_cex_btc(con), 20 * H),
    ("fear_greed", lambda con, cfg: alternative_me.update(con), 12 * H),
    ("coingecko_global", lambda con, cfg: coingecko.update(con), 12 * H),
    ("cftc", lambda con, cfg: cftc.update(con), 24 * H),
    ("dvol", lambda con, cfg: deribit_hist.update(con), 12 * H),
    ("yahoo", lambda con, cfg: yahoo.update(con), 12 * H),
    ("farside", lambda con, cfg: farside.update(con), 12 * H),
    ("binance_quarterly", lambda con, cfg: binance_quarterly.update(con), 12 * H),
    ("coinbase_hourly", lambda con, cfg: coinbase_hourly.update(con), 1 * H),
    ("coinalyze_daily", lambda con, cfg: coinalyze_daily.update(con, cfg), 12 * H),
    # Each Social Risk input checks its own schedule, so this runs every hour
    # and usually returns at once (Hacker News backfills a slice per run).
    ("social", lambda con, cfg: social.update(con, cfg), 1 * H),
    ("fred_macro", lambda con, cfg: fred_macro.update(con, (cfg.get("api_keys") or {}).get("fred") or ""), 20 * H),
]


def _fresh(con, name: str, window: pd.Timedelta) -> bool:
    last = storage.last_success(con, f"src:{name}")
    if last is None:
        return False
    last = last.tz_localize("UTC") if last.tzinfo is None else last.tz_convert("UTC")
    return pd.Timestamp.now(tz="UTC") - last < window


def update_all(con, cfg: dict, force: bool = False, say=print) -> dict:
    done = {}
    for name, fn, window in SOURCES:
        if not force and _fresh(con, name, window):
            continue
        if name == "fred_macro" and not (cfg.get("api_keys") or {}).get("fred"):
            continue
        say(f"Updating {name}...")
        try:
            rows = fn(con, cfg) or 0
            storage.log_fetch(con, f"src:{name}", True, rows)
            done[name] = rows
        except Exception as e:  # noqa: BLE001 - one source failing never stops the others
            storage.log_fetch(con, f"src:{name}", False, 0, f"{type(e).__name__}: {e}")
            done[name] = f"failed: {type(e).__name__}"
    rows = bgeometrics.update(con)
    if rows:
        done["bgeometrics"] = rows
    return done
