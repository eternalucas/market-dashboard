"""Perpetual futures, spot order books and options data from public exchange APIs.

Every perp exchange is reduced to the same small snapshot, so the dashboard
can compare them side by side:

    {"exchange", "asset", "mark", "index", "funding_8h", "oi_usd"}

funding_8h is the funding rate per 8 hours as a fraction (0.0001 = 0.01%).
Hyperliquid pays funding hourly, so its rate is multiplied by 8 to match.

No API keys are needed: all endpoints used here are public market data.
"""
from __future__ import annotations

import pandas as pd

from .http import get_json, post_json

# --------------------------------------------------------------------------- #
# Perp snapshots
# --------------------------------------------------------------------------- #


def bybit_snapshot(asset: str) -> dict:
    d = get_json("https://api.bybit.com/v5/market/tickers",
                 {"category": "linear", "symbol": f"{asset}USDT"})["result"]["list"][0]
    return _snap("bybit", asset, d["markPrice"], d["indexPrice"], d["fundingRate"], d["openInterestValue"])


def okx_snapshot(asset: str) -> dict:
    inst = f"{asset}-USDT-SWAP"
    fr = get_json("https://www.okx.com/api/v5/public/funding-rate", {"instId": inst})["data"][0]
    mk = get_json("https://www.okx.com/api/v5/public/mark-price", {"instType": "SWAP", "instId": inst})["data"][0]
    ix = get_json("https://www.okx.com/api/v5/market/index-tickers", {"instId": f"{asset}-USDT"})["data"][0]
    oi = get_json("https://www.okx.com/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst})["data"][0]
    return _snap("okx", asset, mk["markPx"], ix["idxPx"], fr["fundingRate"], oi["oiUsd"])


def binance_snapshot(asset: str) -> dict:
    sym = f"{asset}USDT"
    p = get_json("https://fapi.binance.com/fapi/v1/premiumIndex", {"symbol": sym})
    oi = get_json("https://fapi.binance.com/fapi/v1/openInterest", {"symbol": sym})
    mark = float(p["markPrice"])
    return _snap("binance", asset, mark, p["indexPrice"], p["lastFundingRate"], float(oi["openInterest"]) * mark)


def hyperliquid_snapshot(asset: str) -> dict:
    meta, ctxs = post_json("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"})
    names = [u["name"] for u in meta["universe"]]
    c = ctxs[names.index(asset)]
    mark = float(c["markPx"])
    # Hyperliquid funding is hourly -> x8 for an 8h-equivalent rate.
    return _snap("hyperliquid", asset, mark, c["oraclePx"], float(c["funding"]) * 8, float(c["openInterest"]) * mark)


SNAPSHOTS = {
    "bybit": bybit_snapshot,
    "okx": okx_snapshot,
    "binance": binance_snapshot,
    "hyperliquid": hyperliquid_snapshot,
}


def _snap(exchange, asset, mark, index, funding_8h, oi_usd) -> dict:
    return {
        "exchange": exchange,
        "asset": asset,
        "mark": float(mark),
        "index": float(index),
        "funding_8h": float(funding_8h),
        "oi_usd": float(oi_usd),
    }


# --------------------------------------------------------------------------- #
# Funding-rate history (each returns a Series of 8h-equivalent rates)
# --------------------------------------------------------------------------- #


def bybit_funding_history(asset: str) -> pd.Series:
    rows = get_json("https://api.bybit.com/v5/market/funding/history",
                    {"category": "linear", "symbol": f"{asset}USDT", "limit": 200})["result"]["list"]
    return _series(rows, "fundingRateTimestamp", "fundingRate")


def okx_funding_history(asset: str) -> pd.Series:
    rows = get_json("https://www.okx.com/api/v5/public/funding-rate-history",
                    {"instId": f"{asset}-USDT-SWAP", "limit": 100})["data"]
    return _series(rows, "fundingTime", "fundingRate")


def binance_funding_history(asset: str) -> pd.Series:
    rows = get_json("https://fapi.binance.com/fapi/v1/fundingRate", {"symbol": f"{asset}USDT", "limit": 1000})
    return _series(rows, "fundingTime", "fundingRate")


def hyperliquid_funding_history(asset: str, days: int = 20) -> pd.Series:
    start = int((pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)).timestamp() * 1000)
    rows = post_json("https://api.hyperliquid.xyz/info", {"type": "fundingHistory", "coin": asset, "startTime": start})
    return _series(rows, "time", "fundingRate") * 8


FUNDING_HISTORY = {
    "bybit": bybit_funding_history,
    "okx": okx_funding_history,
    "binance": binance_funding_history,
    "hyperliquid": hyperliquid_funding_history,
}

# --------------------------------------------------------------------------- #
# Open-interest history (daily, in coins)
# --------------------------------------------------------------------------- #


def bybit_oi_history(asset: str) -> pd.Series:
    rows = get_json("https://api.bybit.com/v5/market/open-interest",
                    {"category": "linear", "symbol": f"{asset}USDT", "intervalTime": "1d", "limit": 200})["result"]["list"]
    return _series(rows, "timestamp", "openInterest")


def binance_oi_history(asset: str) -> pd.Series:
    rows = get_json("https://fapi.binance.com/futures/data/openInterestHist",
                    {"symbol": f"{asset}USDT", "period": "1d", "limit": 30})
    return _series(rows, "timestamp", "sumOpenInterest")


def okx_oi_history(asset: str) -> pd.Series:
    # OKX aggregates OI across its contracts: rows are [ts, oi_usd, volume_usd].
    rows = get_json("https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume",
                    {"ccy": asset, "period": "1D"})["data"]
    s = pd.Series({pd.to_datetime(int(r[0]), unit="ms", utc=True): float(r[1]) for r in rows})
    return s.sort_index()  # NB: USD, not coins; stored under a different series name


OI_HISTORY = {
    "bybit": bybit_oi_history,
    "binance": binance_oi_history,
}


def _series(rows: list[dict], t_key: str, v_key: str) -> pd.Series:
    s = pd.Series({pd.to_datetime(int(r[t_key]), unit="ms", utc=True): float(r[v_key]) for r in rows})
    return s.sort_index()


# --------------------------------------------------------------------------- #
# Spot order books -> (bids, asks) as DataFrames with price and size columns
# --------------------------------------------------------------------------- #


def coinbase_book(asset: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = get_json(f"https://api.exchange.coinbase.com/products/{asset}-USD/book", {"level": 2})
    return _book(d["bids"]), _book(d["asks"])


def kraken_book(asset: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    pair = {"BTC": "XBTUSD", "DOGE": "XDGUSD"}.get(asset, f"{asset}USD")
    d = get_json("https://api.kraken.com/0/public/Depth", {"pair": pair, "count": 500})
    if d.get("error"):
        raise ValueError(f"Kraken: {d['error']}")
    book = next(iter(d["result"].values()))
    return _book(book["bids"]), _book(book["asks"])


def bybit_spot_book(asset: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = get_json("https://api.bybit.com/v5/market/orderbook",
                 {"category": "spot", "symbol": f"{asset}USDT", "limit": 200})["result"]
    return _book(d["b"]), _book(d["a"])


SPOT_BOOKS = {"coinbase": coinbase_book, "kraken": kraken_book, "bybit": bybit_spot_book}


def _book(levels: list) -> pd.DataFrame:
    return pd.DataFrame(
        [(float(l[0]), float(l[1])) for l in levels], columns=["price", "size"]
    )


# --------------------------------------------------------------------------- #
# Deribit options (for skew)
# --------------------------------------------------------------------------- #


def deribit_option_summaries(asset: str) -> pd.DataFrame:
    """One row per listed option: expiry, strike, call/put, mark IV, forward price.

    Deribit lists BTC and ETH options in coin-margined form; SOL options are
    USDC-settled and thin, so only BTC and ETH are supported here.
    """
    rows = get_json("https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
                    {"currency": asset, "kind": "option"})["result"]
    out = []
    for r in rows:
        # Instrument names look like BTC-27DEC26-100000-C
        _, exp, strike, cp = r["instrument_name"].split("-")
        if r.get("mark_iv") is None or r.get("underlying_price") is None:
            continue
        out.append({
            "expiry": pd.to_datetime(exp, format="%d%b%y", utc=True) + pd.Timedelta(hours=8),
            "strike": float(strike),
            "type": cp,
            "iv": float(r["mark_iv"]) / 100,
            "forward": float(r["underlying_price"]),
            "oi": float(r.get("open_interest") or 0),
        })
    return pd.DataFrame(out)
