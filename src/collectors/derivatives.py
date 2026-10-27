"""Perpetual futures, spot order books and options data from public exchange APIs.

Every perp exchange is reduced to the same small snapshot, so the dashboard
can compare them side by side:

    {"exchange", "asset", "mark", "index", "funding_8h", "oi_usd"}

funding_8h is the funding rate per 8 hours as a fraction (0.0001 = 0.01%).
Exchanges quote the rate per payment, and the payment interval differs:
Hyperliquid pays hourly, and Binance, Bybit and OKX pay every 4 hours (or 1-2)
on some contracts (e.g. ENA, HYPE on Binance). Each rate is scaled to 8 hours
using that contract's own interval, so the columns compare like with like.
The raw rate and its interval are kept as funding_rate and interval_h.

No API keys are needed: all endpoints used here are public market data.
"""
from __future__ import annotations

import time

import pandas as pd

from .http import get_json, post_json

# --------------------------------------------------------------------------- #
# Perp snapshots
# --------------------------------------------------------------------------- #


def bybit_snapshot(asset: str) -> dict:
    d = get_json("https://api.bybit.com/v5/market/tickers",
                 {"category": "linear", "symbol": f"{asset}USDT"})["result"]["list"][0]
    # Bybit's openInterest counts both sides (longs + shorts); every other venue reports one side.
    # singleOpenInterestValue is the one-sided figure (Bybit API docs, checked 27 Oct 2026).
    oi = d.get("singleOpenInterestValue") or float(d["openInterestValue"]) / 2
    return _snap("bybit", asset, d["markPrice"], d["indexPrice"], d["fundingRate"],
                 float(d.get("fundingIntervalHour") or 8), oi)


def okx_snapshot(asset: str) -> dict:
    inst = f"{asset}-USDT-SWAP"
    fr = get_json("https://www.okx.com/api/v5/public/funding-rate", {"instId": inst})["data"][0]
    mk = get_json("https://www.okx.com/api/v5/public/mark-price", {"instType": "SWAP", "instId": inst})["data"][0]
    ix = get_json("https://www.okx.com/api/v5/market/index-tickers", {"instId": f"{asset}-USDT"})["data"][0]
    oi = get_json("https://www.okx.com/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst})["data"][0]
    interval = (int(fr["nextFundingTime"]) - int(fr["fundingTime"])) / 3.6e6 or 8
    return _snap("okx", asset, mk["markPx"], ix["idxPx"], fr["fundingRate"], interval, oi["oiUsd"])


def binance_snapshot(asset: str) -> dict:
    sym = f"{asset}USDT"
    p = get_json("https://fapi.binance.com/fapi/v1/premiumIndex", {"symbol": sym})
    oi = get_json("https://fapi.binance.com/fapi/v1/openInterest", {"symbol": sym})
    mark = float(p["markPrice"])
    return _snap("binance", asset, mark, p["indexPrice"], p["lastFundingRate"], _binance_interval(sym),
                 float(oi["openInterest"]) * mark)


_BN_INFO: dict = {"ts": 0.0, "hours": {}}


def _binance_interval(symbol: str) -> float:
    """Binance lists only the contracts whose funding interval isn't 8h."""
    if time.time() - _BN_INFO["ts"] > 3600:
        rows = get_json("https://fapi.binance.com/fapi/v1/fundingInfo")
        _BN_INFO.update(ts=time.time(), hours={r["symbol"]: float(r["fundingIntervalHours"]) for r in rows})
    return _BN_INFO["hours"].get(symbol, 8.0)


def hyperliquid_snapshot(asset: str) -> dict:
    meta, ctxs = post_json("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"})
    names = [u["name"] for u in meta["universe"]]
    c = ctxs[names.index(asset)]
    mark = float(c["markPx"])
    # Hyperliquid funding is paid hourly
    return _snap("hyperliquid", asset, mark, c["oraclePx"], c["funding"], 1.0, float(c["openInterest"]) * mark)


SNAPSHOTS = {
    "bybit": bybit_snapshot,
    "okx": okx_snapshot,
    "binance": binance_snapshot,
    "hyperliquid": hyperliquid_snapshot,
}


def _snap(exchange, asset, mark, index, rate, interval_h, oi_usd) -> dict:
    return {
        "exchange": exchange,
        "asset": asset,
        "mark": float(mark),
        "index": float(index),
        "funding_rate": float(rate),            # per payment, as the exchange quotes it
        "interval_h": float(interval_h),
        "funding_8h": float(rate) * 8 / float(interval_h),
        "oi_usd": float(oi_usd),
    }


def to_8h(s: pd.Series) -> pd.Series:
    """Funding history (rate per payment) -> 8h-equivalent rate. The interval
    is read from the spacing of the payments (rolling median, so one missing
    payment doesn't distort it), which also handles contracts whose interval
    changed over time."""
    s = s.sort_index()
    if len(s) < 2:
        return s
    gap = s.index.to_series().diff().dt.total_seconds().div(3600).bfill()
    hours = gap.rolling(5, center=True, min_periods=1).median().clip(lower=1, upper=8)
    return s * 8 / hours


# --------------------------------------------------------------------------- #
# Funding-rate history (each returns a Series of 8h-equivalent rates)
# --------------------------------------------------------------------------- #


def bybit_funding_history(asset: str) -> pd.Series:
    rows = get_json("https://api.bybit.com/v5/market/funding/history",
                    {"category": "linear", "symbol": f"{asset}USDT", "limit": 200})["result"]["list"]
    return to_8h(_series(rows, "fundingRateTimestamp", "fundingRate"))


def okx_funding_history(asset: str) -> pd.Series:
    rows = get_json("https://www.okx.com/api/v5/public/funding-rate-history",
                    {"instId": f"{asset}-USDT-SWAP", "limit": 100})["data"]
    return to_8h(_series(rows, "fundingTime", "fundingRate"))


def binance_funding_history(asset: str) -> pd.Series:
    rows = get_json("https://fapi.binance.com/fapi/v1/fundingRate", {"symbol": f"{asset}USDT", "limit": 1000})
    return to_8h(_series(rows, "fundingTime", "fundingRate"))


def hyperliquid_funding_history(asset: str, days: int = 20) -> pd.Series:
    start = int((pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)).timestamp() * 1000)
    rows = post_json("https://api.hyperliquid.xyz/info", {"type": "fundingHistory", "coin": asset, "startTime": start})
    return to_8h(_series(rows, "time", "fundingRate"))


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
    # one side (see bybit_snapshot)
    return pd.Series({pd.to_datetime(int(r["timestamp"]), unit="ms", utc=True):
                      float(r.get("singleOpenInterest") or float(r["openInterest"]) / 2) for r in rows}).sort_index()


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


# --------------------------------------------------------------------------- #
# Liquidation-heatmap inputs (Binance USDT-M perps): candles, open interest
# and taker buy/sell volume per bar
# --------------------------------------------------------------------------- #

BN_PERIOD_MS = {"5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000}


def _bn_paged(path: str, symbol: str, period: str, n: int) -> list[dict]:
    """Binance futures/data endpoints return at most 500 rows (and only the
    last 30 days): page backwards with endTime until n rows."""
    rows, end = [], None
    while len(rows) < n:
        params = {"symbol": symbol, "period": period, "limit": min(500, n - len(rows))}
        if end:
            params["endTime"] = end
        try:
            page = get_json(f"https://fapi.binance.com/futures/data/{path}", params)
        except Exception:  # noqa: BLE001 - older than Binance's 30-day limit: keep what we have
            if rows:
                break
            raise
        if not page:
            break
        rows = page + rows
        end = int(page[0]["timestamp"]) - 1
        if len(page) < params["limit"]:
            break
    return rows


def binance_liq_inputs(asset: str, period: str, n: int) -> pd.DataFrame:
    """n bars of `period`: h, l, c (perp candles), oi (coins), buy, sell
    (taker volume in coins), indexed by bar open time (UTC)."""
    sym = f"{asset}USDT"
    k = get_json("https://fapi.binance.com/fapi/v1/klines", {"symbol": sym, "interval": period, "limit": min(n, 1500)})
    bars = pd.DataFrame([(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in k],
                        columns=["t", "o", "h", "l", "c"])
    bars.index = pd.to_datetime(bars.pop("t"), unit="ms", utc=True)
    oi = _bn_paged("openInterestHist", sym, period, n)
    tk = _bn_paged("takerlongshortRatio", sym, period, n)
    # openInterestHist is stamped at the end of each period: align it to the bar it closes
    step = pd.Timedelta(milliseconds=BN_PERIOD_MS[period])
    ois = pd.Series({pd.to_datetime(int(r["timestamp"]), unit="ms", utc=True) - step: float(r["sumOpenInterest"])
                     for r in oi})
    tks = pd.DataFrame({pd.to_datetime(int(r["timestamp"]), unit="ms", utc=True):
                        {"buy": float(r["buyVol"]), "sell": float(r["sellVol"])} for r in tk}).T
    bars["oi"] = ois.reindex(bars.index)
    bars = bars.join(tks)
    return bars[bars["oi"].notna()]


BYBIT_INTERVAL = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h"}
OKX_PERIOD = {"5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H"}


def bybit_liq_inputs(asset: str, period: str, n: int) -> pd.DataFrame:
    """Bybit USDT perp open interest per bar (one side, coins), indexed by the
    bar it closes. Bybit publishes no taker buy/sell history, so the model
    uses Binance's split for Bybit."""
    rows, end = [], None
    while len(rows) < n:
        params = {"category": "linear", "symbol": f"{asset}USDT", "intervalTime": BYBIT_INTERVAL[period], "limit": 200}
        if end:
            params["endTime"] = end
        page = get_json("https://api.bybit.com/v5/market/open-interest", params)["result"]["list"]
        if not page:
            break
        rows += page
        end = int(page[-1]["timestamp"]) - 1
        if len(page) < 200:
            break
    step = pd.Timedelta(milliseconds=BN_PERIOD_MS[period])
    oi = pd.Series({pd.to_datetime(int(r["timestamp"]), unit="ms", utc=True) - step:
                    float(r.get("singleOpenInterest") or float(r["openInterest"]) / 2) for r in rows})
    return pd.DataFrame({"oi": oi.sort_index()})


def okx_liq_inputs(asset: str, period: str, n: int) -> pd.DataFrame:
    """OKX USDT swap open interest (coins) and taker buy/sell volume per bar."""
    inst = f"{asset}-USDT-SWAP"

    def paged(path: str) -> list[list]:
        rows, end = [], None
        while len(rows) < n:
            params = {"instId": inst, "period": OKX_PERIOD[period], "limit": 100}
            if end:
                params["end"] = end
            page = get_json(f"https://www.okx.com/api/v5/rubik/stat/{path}", params).get("data") or []
            if not page:
                break
            rows += page
            end = page[-1][0]
            if len(page) < 100:
                break
            time.sleep(0.45)              # 5 requests per 2 seconds
        return rows

    step = pd.Timedelta(milliseconds=BN_PERIOD_MS[period])
    oi = pd.Series({pd.to_datetime(int(r[0]), unit="ms", utc=True) - step: float(r[2])
                    for r in paged("contracts/open-interest-history")})
    tk = pd.DataFrame({pd.to_datetime(int(r[0]), unit="ms", utc=True): {"sell": float(r[1]), "buy": float(r[2])}
                       for r in paged("taker-volume-contract")}).T
    out = pd.DataFrame({"oi": oi.sort_index()})
    return out.join(tk) if not tk.empty else out


def liq_model_inputs(asset: str, period: str, n: int) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict]:
    """Inputs for the liquidation model over n bars: Binance perp candles with
    Binance taker buy/sell volume, and open interest per venue (Binance,
    Bybit, OKX). A venue that fails is left out and reported in errors."""
    bn = binance_liq_inputs(asset, period, n)
    candles = bn[["o", "h", "l", "c", "buy", "sell"]]
    venues, errors = {"Binance": bn[["oi", "buy", "sell"]]}, {}
    for name, fn in (("Bybit", bybit_liq_inputs), ("OKX", okx_liq_inputs)):
        try:
            v = fn(asset, period, n)
            if v["oi"].notna().sum() >= 3:
                venues[name] = v
        except Exception as e:  # noqa: BLE001
            errors[name] = f"{type(e).__name__}: {str(e)[:120]}"
    return candles, venues, errors
