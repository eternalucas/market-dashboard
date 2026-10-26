"""Yahoo Finance via yfinance: ETFs, treasury companies, macro / TradFi
markets, CME bitcoin futures and the altcoins used by the Altcoin Season
Index. Daily close and volume, full history.

Raw series: yf:<TICKER>:close, yf:<TICKER>:volume (UTC day of the session).
"""
from __future__ import annotations

import pandas as pd

from .. import storage

ETFS = {  # US spot bitcoin ETFs
    "IBIT": "BlackRock", "FBTC": "Fidelity", "GBTC": "Grayscale", "BTC": "Grayscale Mini", "ARKB": "ARK 21Shares",
    "BITB": "Bitwise", "HODL": "VanEck", "BRRR": "Valkyrie / CoinShares", "EZBC": "Franklin", "BTCO": "Invesco Galaxy",
    "BTCW": "WisdomTree", "MSBT": "Morgan Stanley",
}
CANADA_ETFS = {"BTCC-B.TO": "Purpose Bitcoin ETF"}
TREASURY_COS = {  # listed bitcoin treasury companies
    "MSTR": "Strategy", "MARA": "MARA Holdings", "XXI": "Twenty One", "3350.T": "Metaplanet",
    "RIOT": "Riot Platforms", "CLSK": "CleanSpark", "HUT": "Hut 8", "COIN": "Coinbase", "TSLA": "Tesla",
    "SMLR": "Semler Scientific", "GLXY": "Galaxy Digital", "NAKA": "KindlyMD / Nakamoto",
}
STRATEGY_PREFERREDS = {"STRK": "Strike", "STRF": "Strife", "STRD": "Stride", "STRC": "Stretch"}
MACRO = {
    "^GSPC": "US Stock Market Index (S&P 500)", "^NDX": "US Tech 100 Index", "^STOXX50E": "EU Stock Market Index",
    "^VIX": "US Fear Index (VIX)", "^V2TX": "EU Fear Index (VSTOXX)", "GC=F": "Gold", "SI=F": "Silver",
    "CL=F": "WTI Crude Oil", "DX-Y.NYB": "U.S. Dollar Index", "EURUSD=X": "EUR/USD", "^TNX": "US 10Y Yield",
    "BTC=F": "CME Bitcoin futures (front month)",
}
# Top non-stablecoin altcoins with long Yahoo histories (Altcoin Season Index).
ALTS = ["ETH", "XRP", "BNB", "SOL", "DOGE", "ADA", "TRX", "LINK", "AVAX", "XLM", "BCH", "LTC", "HBAR", "DOT",
        "SHIB", "XMR", "ETC", "ICP", "FIL", "ATOM", "ALGO", "VET", "AAVE", "NEAR", "INJ", "RNDR", "STX", "MKR",
        "QNT", "EGLD", "XTZ", "THETA", "SAND", "MANA", "AXS", "EOS", "NEO", "ZEC", "DASH", "KAS", "SUI20947",
        "APT21794", "UNI7083", "TON11419", "HYPE32196", "ENA", "PEPE24478", "TAO22974", "ONDO", "ARB11841"]

GROUPS = {"etf": ETFS, "etf_ca": CANADA_ETFS, "treasury": TREASURY_COS, "pref": STRATEGY_PREFERREDS, "macro": MACRO,
          "alt": {f"{a}-USD": a.rstrip("0123456789") for a in ALTS}}


def download(tickers: list[str], start: str | None = None) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    df = yf.download(tickers, start=start, period=None if start else "max", interval="1d",
                     progress=False, auto_adjust=False, group_by="ticker", threads=True)
    out = {}
    for t in tickers:
        try:
            d = df[t] if isinstance(df.columns, pd.MultiIndex) else df
        except KeyError:
            continue
        d = d[["Close", "Volume"]].dropna(how="all")
        if not d.empty:
            d.index = pd.to_datetime(d.index).tz_localize("UTC") if d.index.tz is None else d.index.tz_convert("UTC")
            out[t] = d
    return out


def update(con, groups: list[str] | None = None) -> int:
    rows = 0
    for g in groups or list(GROUPS):
        tickers = list(GROUPS[g])
        last = [storage.load_series(con, f"yf:{t}:close") for t in tickers]
        known = [s.index[-1] for s in last if not s.empty]
        start = None if len(known) < len(tickers) else (min(known) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        for t, d in download(tickers, start).items():
            rows += storage.save_series(con, f"yf:{t}:close", d["Close"].astype(float))
            rows += storage.save_series(con, f"yf:{t}:volume", d["Volume"].astype(float))
    return rows
