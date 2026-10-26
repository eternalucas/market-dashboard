"""IBIT (iShares Bitcoin Trust) option chains from Yahoo Finance via yfinance.

Yahoo has no history of option chains, so these are recorded from the day the
recorder starts. Each US trading day after the close, the whole chain (every
expiry) is stored in option_snap with venue='ibit'. Implied volatility is
recomputed by the metrics layer from the bid/ask mid; Yahoo's own IV column
is kept as `iv` for reference.
"""
from __future__ import annotations

import time

import pandas as pd


def chain(ticker: str = "IBIT", max_expiries: int = 40) -> pd.DataFrame:
    import yfinance as yf
    t = yf.Ticker(ticker)
    spot = float(t.fast_info["last_price"])
    out = []
    for exp in list(t.options)[:max_expiries]:
        oc = t.option_chain(exp)
        # US equity options expire at the 16:00 New York close
        expiry = (pd.Timestamp(exp).tz_localize("America/New_York") + pd.Timedelta(hours=16)).tz_convert("UTC")
        for kind, df in (("C", oc.calls), ("P", oc.puts)):
            for r in df.itertuples():
                out.append({"expiry": expiry.isoformat(), "strike": float(r.strike), "type": kind,
                            "iv": float(r.impliedVolatility) if pd.notna(r.impliedVolatility) else None,
                            "bid_iv": None, "ask_iv": None,
                            "oi": float(r.openInterest) if pd.notna(r.openInterest) else 0.0,
                            "volume": float(r.volume) if pd.notna(r.volume) else 0.0,
                            "mark_price": (float(r.bid) + float(r.ask)) / 2 if r.ask else float(r.lastPrice),
                            "bid": float(r.bid), "ask": float(r.ask), "underlying": spot})
        time.sleep(0.3)
    return pd.DataFrame(out)
