"""Charts added on 27 Oct 2026 so every Studio dashboard has an even number
of panels (no empty slot at the bottom right). Each uses data the dashboard
already collects; formulas are in the docstrings and src/explanations.py."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import futures as fu
from . import onchain as oc
from . import options as op
from . import spot as sp
from .core import Ctx


# ---------------------------------------------------------------- Spot
def days_since_ath(c: Ctx) -> pd.Series:
    """Days Since All-Time High [days]: days since the daily close last set a record (0 on a record day)."""
    p = c.price().dropna()
    is_ath = p >= p.cummax()
    when = pd.Series(p.index.where(is_ath), index=p.index).ffill()
    return pd.Series((p.index - pd.DatetimeIndex(when)).total_seconds() / 86400, index=p.index).round()


def rsi(c: Ctx, n: int = 14) -> pd.Series:
    """Relative Strength Index (14D) [0-100], Wilder's smoothing of daily close changes:
    RSI = 100 - 100 / (1 + average gain / average loss)."""
    d = c.price().diff()
    gain = d.clip(lower=0).ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    return (100 - 100 / (1 + gain / loss.replace(0, np.nan))).dropna()


# ---------------------------------------------------------------- Futures
def liquidations_30d(c: Ctx) -> pd.DataFrame:
    """Futures Liquidations, 30-Day Rolling Total [USD]: long and short liquidations summed over the last 30 days."""
    lq = fu.liquidations(c)
    return lq[["long", "short"]].rolling(30, min_periods=20).sum().dropna(how="all")


def dated_futures_oi(c: Ctx) -> pd.DataFrame:
    """Dated Futures Open Interest [USD] per venue: open interest of every dated (expiring)
    BTC future in the recorded futures curve, last snapshot of each day."""
    df = c.frame("SELECT ts, venue, instrument, oi_usd FROM futures_curve WHERE expiry IS NOT NULL")
    if df.empty:
        return pd.DataFrame()
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    snap = df.groupby([df["ts"].dt.floor("D"), "venue"])["ts"].transform("max")
    last = df[df["ts"] == snap]
    out = last.groupby([last["ts"].dt.floor("D"), "venue"])["oi_usd"].sum().unstack()
    out.columns = [str(x).title() if str(x) != "okx" else "OKX" for x in out.columns]
    return out


def eth_btc_ratio(c: Ctx) -> pd.Series:
    """ETH/BTC Price Ratio = ETH price / BTC price (Coin Metrics daily closes)."""
    return (c.s("cm:eth:PriceUSD") / c.price()).dropna()


# ---------------------------------------------------------------- Options
def iv_vs_realized(c: Ctx) -> pd.DataFrame:
    """Implied vs Realized Volatility (1 Month) [annualized fraction]: 1-month ATM implied
    volatility (Deribit; DVOL before our own record starts), 30-day realized volatility, and
    the spread between them (the volatility risk premium)."""
    iv = op.series(c, "atm_iv:1m").combine_first(c.s("dvol:BTC:close") / 100).sort_index()
    iv.index = iv.index.normalize()
    iv = iv[~iv.index.duplicated(keep="last")]
    rv = sp.realized_volatility(c)["1m"]
    df = pd.DataFrame({"implied_1m": iv, "realized_30d": rv}).dropna()
    df["premium"] = df["implied_1m"] - df["realized_30d"]
    return df


def skew_term_structure(c: Ctx, venue: str = "deribit") -> pd.DataFrame:
    """25 Delta Skew Term Structure [fraction]: (25-delta put IV - 25-delta call IV) / ATM IV
    for every listed expiry, latest snapshot. Positive = puts dearer than calls."""
    df = op.latest_snapshot(c, venue)
    if df.empty:
        return pd.DataFrame()
    rows = []
    for e, g in df.groupby("expiry"):
        atm, p25, c25 = op.atm_iv_expiry(g), op.iv_at_delta(g, 25, "P"), op.iv_at_delta(g, 25, "C")
        if atm and np.isfinite(atm) and np.isfinite(p25) and np.isfinite(c25):
            rows.append({"days": float(g["t"].iloc[0] * 365), "skew25": (p25 - c25) / atm})
    return pd.DataFrame(rows).set_index("days").sort_index() if rows else pd.DataFrame()


def ibit_volume(c: Ctx) -> pd.Series:
    """IBIT Options Volume [contracts/day]: total daily volume across all IBIT option contracts."""
    return op.series(c, "volume_24h_contracts", "ibit")


# ---------------------------------------------------------------- ETFs
def etf_cumulative_flows(c: Ctx) -> pd.Series:
    """US Spot ETF Cumulative Net Flows [USD]: running total of daily net flows since launch."""
    from . import markets as mk
    f = mk.etf_flows(c)
    return f["Total"].cumsum() if "Total" in f else pd.Series(dtype=float)


# ---------------------------------------------------------------- Macro
def real_policy_rate(c: Ctx) -> pd.DataFrame:
    """US Real Policy Rate [%] = Fed funds target (upper bound) - US core CPI inflation (YoY),
    monthly; next to the nominal rate."""
    from . import markets as mk
    m = mk.macro(c)
    if "us_rate" not in m or "us_core_cpi_yoy" not in m:
        return pd.DataFrame()
    rate = m["us_rate"].dropna().resample("MS").last()
    cpi = m["us_core_cpi_yoy"].dropna().resample("MS").last()
    df = pd.DataFrame({"Fed funds (nominal)": rate, "Core CPI YoY": cpi}).ffill(limit=2).dropna()
    df["Real rate"] = df["Fed funds (nominal)"] - df["Core CPI YoY"]
    return df[["Real rate", "Fed funds (nominal)"]]


def btc_correlations(c: Ctx) -> pd.DataFrame:
    """BTC 90-Day Correlation [-1..1] of daily returns with the S&P 500, gold and the US dollar index."""
    from . import markets as mk
    out = {}
    for t, name in (("^GSPC", "S&P 500"), ("GC=F", "Gold"), ("DX-Y.NYB", "US dollar (DXY)")):
        s = c.s(f"yf:{t}:close")
        if not s.empty:
            out[name] = mk.correlation(c, s, 90)
    return pd.DataFrame(out).dropna(how="all")


# ---------------------------------------------------------------- On-chain
def exchange_netflow_usd(c: Ctx) -> pd.Series:
    """Exchange Netflow (USD, 7D Average) [USD/day] = (inflow - outflow) in USD, 7-day average."""
    i, o = c.s("cm:FlowInExUSD"), c.s("cm:FlowOutExUSD")
    return (i - o).rolling(7, min_periods=5).mean().dropna()


def por_change_30d(c: Ctx) -> pd.DataFrame:
    """Proof of Reserves, 30-Day Change [BTC] per exchange: change in BTC held in each exchange's
    published wallets over 30 days (the five largest exchanges, the rest summed)."""
    df = oc.proof_of_reserves(c).drop(columns="total", errors="ignore")
    if df.empty:
        return df
    ch = df.diff(30)
    top = df.iloc[-1].sort_values(ascending=False).index[:5]
    out = ch[top].copy()
    rest = [x for x in ch.columns if x not in top]
    if rest:
        out["Others"] = ch[rest].sum(axis=1, min_count=1)
    return out.dropna(how="all")


def whale_supply_share(c: Ctx) -> pd.DataFrame:
    """Supply Held by Whales [% of supply]: BTC held by addresses with 1k-10k BTC and > 10k BTC,
    as a share of circulating supply (BGeometrics balance bands)."""
    b = oc.address_bands(c)
    sup = c.supply()
    out = {}
    for band, name in (("supply:1k-10k", "1k-10k BTC addresses"), ("supply:> 10k", "> 10k BTC addresses")):
        if band in b:
            out[name] = (b[band] / sup.reindex(b.index)) * 100
    return pd.DataFrame(out).dropna(how="all")


def unrealized_pl_usd(c: Ctx) -> pd.Series:
    """Net Unrealized Profit/Loss in USD = market cap - realized cap: the paper gain (or loss) of all holders."""
    return (c.mcap() - c.realized_cap()).dropna()


# ---------------------------------------------------------------- Indicators
def risk_signals(c: Ctx) -> pd.Series:
    """Bull Market Risk: Overheating Signals [count 0-6] = how many of six classic top signals are on:
    MVRV > 3, NUPL > 0.6, percent supply in profit > 95%, Mayer Multiple > 2.4, perpetual funding
    (7-day average) > 0.03% per 8h, Fear & Greed > 80. Each signal counts once data exists for it."""
    mv = c.s("cm:CapMVRVCur")
    sig = {
        "mvrv": mv > 3,
        "nupl": (1 - 1 / mv) > 0.6,
        "profit": oc.profit_loss(c)["pct_supply_profit"] > 95,
        "mayer": sp.mayer_multiple(c) > 2.4,
        "funding": fu.funding_rate(c).rolling(7, min_periods=5).mean() > 0.03,
        "fear_greed": oc.fear_greed(c) > 80,
    }
    df = pd.DataFrame({k: v.astype(float).where(v.notna()) for k, v in sig.items() if len(v)})
    df.index = df.index.normalize()
    df = df.groupby(level=0).last()
    return df.sum(axis=1, min_count=2).dropna()


def alt_btc_ratio(c: Ctx) -> pd.Series:
    """Altcoin / BTC Market Cap Ratio = altcoin market cap / BTC market cap."""
    return (sp.altcoin_market_cap(c) / c.mcap()).dropna()
