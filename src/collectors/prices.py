"""Daily price history, used to chart indicators against price."""
from __future__ import annotations

import pandas as pd

from .http import get_json

KRAKEN_PAIRS = {"BTC": "XBTUSD", "DOGE": "XDGUSD"}  # others are <ASSET>USD


def daily_close(asset: str) -> pd.Series:
    """Daily USD close. Yahoo Finance first (history back to 2014 for BTC),
    Kraken as a fallback (last 720 days only)."""
    # Yahoo's tickers for newer coins (HYPE, SUI, ENA) are ambiguous, so
    # only well-known tickers go to Yahoo; the rest come from Kraken.
    if asset in {"BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"}:
        try:
            return _yahoo(asset)
        except Exception:  # noqa: BLE001
            pass
    return _kraken(asset)


def _yahoo(asset: str) -> pd.Series:
    return yahoo_close(f"{asset}-USD")


def yahoo_close(ticker: str) -> pd.Series:
    """Daily close for any Yahoo Finance ticker (^GSPC, DX-Y.NYB, GC=F, ...)."""
    import yfinance as yf

    df = yf.download(ticker, period="max", interval="1d", progress=False, auto_adjust=False)
    if df.empty:
        raise ValueError(f"Yahoo returned no data for {ticker}")
    close = df["Close"]
    if isinstance(close, pd.DataFrame):  # newer yfinance returns one column per ticker
        close = close.iloc[:, 0]
    close.index = pd.to_datetime(close.index, utc=True)
    return close.astype(float).dropna()


def _kraken(asset: str) -> pd.Series:
    pair = KRAKEN_PAIRS.get(asset, f"{asset}USD")
    data = get_json("https://api.kraken.com/0/public/OHLC", params={"pair": pair, "interval": 1440})
    if data.get("error"):
        raise ValueError(f"Kraken: {data['error']}")
    key = next(k for k in data["result"] if k != "last")
    rows = data["result"][key]  # [time, open, high, low, close, vwap, volume, count]
    return pd.Series(
        [float(r[4]) for r in rows],
        index=pd.to_datetime([r[0] for r in rows], unit="s", utc=True),
    )
