"""Daily perpetual-futures data (BTC and ETH, every exchange Coinalyze
covers, both stablecoin- and coin-margined) and BTC spot volume / buy volume
per exchange, for the Glassnode-style futures and spot-volume metrics.
Coinalyze keeps daily bars indefinitely.

Per symbol, raw series (UTC days):
  czd:<symbol>:oi_usd     open interest at the close, USD
  czd:<symbol>:vol_usd    traded volume, USD
  czd:<symbol>:buy_usd    taker-buy volume, USD
  czd:<symbol>:fr         funding rate (per the venue's interval, % as Coinalyze reports it)
  czd:<symbol>:liq_long_usd / liq_short_usd   liquidations, USD
Contract metadata (exchange, margin type) is written to data/coinalyze_btc_perps.json.

Units: markets whose oi_lq_vol_denominated_in is BASE_ASSET report volume in
BTC (converted with that market's own close); QUOTE_ASSET markets (coin-
margined inverse contracts) report USD already.
"""
from __future__ import annotations

import json

import pandas as pd

from .. import flows, storage
from ..collectors import coinalyze

META = storage.DB_PATH.parent / "coinalyze_btc_perps.json"
EXCLUDE = {"Polymarket"}
START = "2019-01-01"


USD_QUOTES = {"USD", "USDT", "USDC", "FDUSD", "USDE", "USD1"}


def markets(cfg) -> list[dict]:
    """BTC and ETH perpetuals on every covered exchange (both margin types),
    plus BTC spot markets quoted in USD or stablecoins that carry buy/sell data."""
    fut, spot, _ = flows.client_for(cfg).markets()
    out = []
    for asset in ("BTC", "ETH"):
        m = fut[(fut["base_asset"].str.upper() == asset) & fut["is_perpetual"].astype(bool)
                & ~fut["exchange_name"].isin(EXCLUDE)]
        out += [{"symbol": r.symbol, "asset": asset, "kind": "perp", "exchange": r.exchange_name,
                 "margined": r.margined, "denom": r.oi_lq_vol_denominated_in} for r in m.itertuples()]
    sp = spot[(spot["base_asset"].str.upper() == "BTC") & spot["quote_asset"].str.upper().isin(USD_QUOTES)]
    if "has_buy_sell_data" in sp:
        sp = sp[sp["has_buy_sell_data"].astype(bool)]
    out += [{"symbol": r.symbol, "asset": "BTC", "kind": "spot", "exchange": r.exchange_name,
             "margined": None, "denom": "BASE_ASSET"} for r in sp.itertuples()]
    return out


def update(con, cfg) -> int:
    client = flows.client_for(cfg)
    mk = markets(cfg)
    META.write_text(json.dumps(mk, indent=1))
    perps = [m["symbol"] for m in mk if m["kind"] == "perp"]
    allsyms = [m["symbol"] for m in mk]
    denom = {m["symbol"]: m["denom"] for m in mk}
    last = storage.load_series(con, f"czd:{perps[0]}:oi_usd")
    start = pd.Timestamp(START, tz="UTC") if last.empty else last.index[-1] - pd.Timedelta(days=3)
    s, e = int(start.timestamp()), int(pd.Timestamp.now(tz="UTC").timestamp())
    rows = 0
    for sym, df in client.history("ohlcv-history", allsyms, "daily", s, e).items():
        mult = df["c"] if denom.get(sym) == "BASE_ASSET" else 1.0
        rows += storage.save_series(con, f"czd:{sym}:close", df["c"])
        rows += storage.save_series(con, f"czd:{sym}:vol_usd", df["v"] * mult)
        if "bv" in df:
            rows += storage.save_series(con, f"czd:{sym}:buy_usd", df["bv"] * mult)
    for sym, df in client.history("open-interest-history", perps, "daily", s, e, convert_to_usd="true").items():
        rows += storage.save_series(con, f"czd:{sym}:oi_usd", df["c"])
    for sym, df in client.history("funding-rate-history", perps, "daily", s, e).items():
        rows += storage.save_series(con, f"czd:{sym}:fr", df["c"])
    for sym, df in client.history("liquidation-history", perps, "daily", s, e, convert_to_usd="true").items():
        rows += storage.save_series(con, f"czd:{sym}:liq_long_usd", df["l"])
        rows += storage.save_series(con, f"czd:{sym}:liq_short_usd", df["s"])
    return rows


def meta() -> list[dict]:
    return json.loads(META.read_text()) if META.exists() else []
