"""Fetch, store and assemble the cross-exchange flow data (Coinalyze).

update_flow()  pulls new bars from Coinalyze and stores them in SQLite.
load_flow()    reads stored bars back and builds everything the Flows tab
               draws: price, buy/sell volume per exchange, liquidations,
               open interest, CVD, funding and the Coinbase premium.

Stored series are named  cz:<interval>:<symbol>:<field>,
e.g. cz:5min:BTCUSDT_PERP.A:bv

Units: every market used here reports volume and OI in the base coin
(Coinalyze's 'oi_lq_vol_denominated_in' = BASE_ASSET), so volumes from
different exchanges can be added together. OI is requested in USD and
divided by each exchange's own price to get coins.
"""
from __future__ import annotations

import time

import pandas as pd

from . import storage
from .collectors import coinalyze
from .indicators import flow

# Bar sizes offered in the dashboard -> (Coinalyze interval, seconds per bar,
# pandas rule to resample to, or None). Weekly bars are built from daily ones.
BAR_SIZES = {
    "1 min": ("1min", 60, None),
    "5 min": ("5min", 300, None),
    "15 min": ("15min", 900, None),
    "30 min": ("30min", 1800, None),
    "1 hour": ("1hour", 3600, None),
    "4 hours": ("4hour", 14400, None),
    "12 hours": ("12hour", 43200, None),
    "1 day": ("daily", 86400, None),
    "1 week": ("daily", 86400, "W-MON"),
}
INTERVAL_SECONDS = {v[0]: v[1] for v in BAR_SIZES.values()}
MAX_BARS = 1500  # roughly what Coinalyze keeps per intraday series (daily is kept forever)

# (endpoint, extra params, fields to keep, applies to)
ENDPOINTS = [
    ("ohlcv-history", {}, ["o", "h", "l", "c", "v", "bv"], "all"),
    ("open-interest-history", {"convert_to_usd": "true"}, ["c"], "perp"),
    ("funding-rate-history", {}, ["c"], "perp"),
    ("liquidation-history", {"convert_to_usd": "true"}, ["l", "s"], "perp"),
]
SHORT = {"ohlcv-history": "px", "open-interest-history": "oi", "funding-rate-history": "fr",
         "liquidation-history": "liq"}

# How each field is combined when daily bars are rolled up into weekly ones
RESAMPLE_HOW = {"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum", "bv": "sum",
                "l_liq": "sum", "s": "sum"}


def _markets(cfg: dict, client: coinalyze.Client, asset: str) -> tuple[dict, dict]:
    cz = cfg["coinalyze"]
    fut, spot, _ = client.markets()
    return (coinalyze.pick_markets(spot, asset, cz["spot_exchanges"], perp=False),
            coinalyze.pick_markets(fut, asset, cz["perp_exchanges"], perp=True))


def client_for(cfg: dict) -> coinalyze.Client:
    return coinalyze.Client((cfg.get("api_keys") or {}).get("coinalyze", ""))


def update_flow(con, cfg: dict, asset: str, interval: str = "1hour") -> dict:
    """Fetch bars since the last stored one (or as far back as Coinalyze
    keeps) for every chosen spot and perp market of this asset.
    `interval` is a Coinalyze interval name (e.g. '5min', 'daily')."""
    client = client_for(cfg)
    spot_m, perp_m = _markets(cfg, client, asset)
    if not spot_m and not perp_m:
        raise ValueError(f"No Coinalyze markets found for {asset} on the configured exchanges")
    step = INTERVAL_SECONDS[interval]
    end = int(time.time())
    rows = 0
    for endpoint, extra, fields, scope in ENDPOINTS:
        symbols = list(perp_m.values()) + ([] if scope == "perp" else list(spot_m.values()))
        if not symbols:
            continue
        # Start from the last bar already stored (re-fetch it: it may have been
        # incomplete), but never before what Coinalyze still keeps.
        last = storage.load_series(con, f"cz:{interval}:{symbols[0]}:{SHORT[endpoint]}_{fields[0]}")
        start = end - MAX_BARS * step if last.empty else int(last.index[-1].timestamp()) - step
        start = max(start, end - MAX_BARS * step)
        for symbol, df in client.history(endpoint, symbols, interval, start, end, **extra).items():
            for f in fields:
                if f in df:
                    rows += storage.save_series(con, f"cz:{interval}:{symbol}:{SHORT[endpoint]}_{f}", df[f])
    return {"spot": spot_m, "perp": perp_m, "rows": rows}


def _frame(con, interval: str, symbol: str, prefix: str, fields: list[str], since) -> pd.DataFrame:
    cols = {}
    for f in fields:
        s = storage.load_series(con, f"cz:{interval}:{symbol}:{prefix}_{f}")
        if s.empty:
            continue
        cols[f] = s[s.index >= since] if since is not None else s
    return pd.DataFrame(cols).dropna(how="all") if cols else pd.DataFrame()


def _resample(df: pd.DataFrame, rule: str | None, how: dict) -> pd.DataFrame:
    if rule is None or df.empty:
        return df
    agg = {c: how.get(c, "last") for c in df.columns}
    return df.resample(rule, label="left", closed="left").agg(agg).dropna(how="all")


def exchange_volume(px: pd.DataFrame) -> pd.DataFrame:
    """Taker buy and sell volume per bar, in coins and USD."""
    out = pd.DataFrame(index=px.index)
    out["buy"] = px["bv"]
    out["sell"] = px["v"] - px["bv"]
    out["buy_usd"] = out["buy"] * px["c"]
    out["sell_usd"] = out["sell"] * px["c"]
    return out


def load_flow(con, cfg: dict, asset: str, bar_size: str, since: pd.Timestamp | None) -> dict:
    """Everything the flow charts need, from the local database only.
    `bar_size` is a key of BAR_SIZES (e.g. '5 min', '1 week')."""
    interval, _, rule = BAR_SIZES[bar_size]
    client = client_for(cfg)
    spot_m, perp_m = _markets(cfg, client, asset)
    ohlcv = ["o", "h", "l", "c", "v", "bv"]
    px = {ex: _frame(con, interval, s, "px", ohlcv, since) for ex, s in spot_m.items()}
    pp = {ex: _frame(con, interval, s, "px", ohlcv, since) for ex, s in perp_m.items()}
    oi = {ex: _frame(con, interval, s, "oi", ["c"], since) for ex, s in perp_m.items()}
    fr = {ex: _frame(con, interval, s, "fr", ["c"], since) for ex, s in perp_m.items()}
    lq = {ex: _frame(con, interval, s, "liq", ["l", "s"], since) for ex, s in perp_m.items()}

    px = {k: _resample(v, rule, RESAMPLE_HOW) for k, v in px.items() if not v.empty}
    pp = {k: _resample(v, rule, RESAMPLE_HOW) for k, v in pp.items() if not v.empty}
    oi = {k: _resample(v, rule, {"c": "last"}) for k, v in oi.items() if not v.empty}
    fr = {k: _resample(v, rule, {"c": "mean"}) for k, v in fr.items() if not v.empty}
    lq = {k: _resample(v, rule, {"l": "sum", "s": "sum"}) for k, v in lq.items() if not v.empty}

    perp_closes = {ex: df["c"] for ex, df in pp.items() if "c" in df}
    ref = next((pp[e] for e in ("Binance", "Bybit", "OKX") if e in pp), next(iter(pp.values()), pd.DataFrame()))
    premium = pd.Series(dtype=float)
    if "Coinbase" in px and "Binance" in px:
        premium = flow.coinbase_premium(px["Coinbase"]["c"], px["Binance"]["c"])
    has_vol = lambda d: {"v", "bv", "c"} <= set(d)  # noqa: E731
    return {
        "markets": {"spot": spot_m, "perp": perp_m},
        "price": ref,                                    # OHLC of the main perp
        "spot_ohlc": px,                                 # OHLC per spot exchange
        "spot_volume": {ex: exchange_volume(df) for ex, df in px.items() if has_vol(df)},
        "perp_volume": {ex: exchange_volume(df) for ex, df in pp.items() if has_vol(df)},
        "cvd_spot": flow.aggregate_cvd(px),
        "cvd_perp": flow.aggregate_cvd(pp),
        "cvd_by_spot_exchange": {ex: flow.cvd(df) for ex, df in px.items() if {"v", "bv"} <= set(df)},
        "oi_coins": flow.oi_in_coins(oi, perp_closes),
        "oi_by_exchange": {ex: (df["c"] / perp_closes[ex].reindex(df.index)) for ex, df in oi.items() if ex in perp_closes},
        "funding": flow.oi_weighted_funding(fr, oi),
        "liquidations": flow.liquidations(lq),
        "coinbase_premium": premium,
    }
