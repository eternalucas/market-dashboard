"""ETFs, treasury companies (DATs), macro and TradFi."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .core import Ctx

# --------------------------------------------------------------------------- #
# US spot bitcoin ETFs
# --------------------------------------------------------------------------- #


def etf_flows(c: Ctx) -> pd.DataFrame:
    """US Spot ETF Net Flows [USD/day] per fund and total.
    Primary: Farside daily table (US$ m x 1e6). Fallback when Farside is
    unreachable: BGeometrics total flow in BTC x price (total only, 7-day delayed)."""
    fs = {k.split(":", 1)[1]: v * 1e6 for k, v in c.prefix("farside:").items()}
    if fs:
        df = pd.DataFrame(fs)
        if "Total" not in df:
            df["Total"] = df.sum(axis=1)
        # Farside shows "-" (stored as 0) for funds that haven't reported yet:
        # trailing all-zero days are not-yet-reported, not zero flow.
        nz = df.ne(0).any(axis=1)
        last = nz[nz].index.max() if nz.any() else None
        return df[df.index <= last] if last is not None else df
    flow_btc = next(iter(c.prefix("bg:etf-flow-btc:").values()), pd.Series(dtype=float))
    return pd.DataFrame({"Total": flow_btc * c.price()}).dropna()


def etf_netflows_btc(c: Ctx) -> pd.Series:
    """US Spot ETF Net Flows [BTC/day] = day-over-day change in total US spot
    ETF BTC holdings (BGeometrics, 7-day delayed). This is Glassnode's method
    (flows from issuer-reported holdings); converting Farside's USD flows at
    the daily close does not reproduce it (checked 2026-10-21: off 17% over
    the week of 2024-03-11). Falls back to Farside USD / close when holdings
    aren't available yet."""
    bal = next(iter(c.prefix("bg:etf-btc-total:").values()), pd.Series(dtype=float))
    if len(bal) > 1:
        return bal.diff().dropna()
    f = etf_flows(c)
    return (f["Total"] / c.price().reindex(f.index)).dropna() if "Total" in f else pd.Series(dtype=float)


def etf_balances(c: Ctx) -> pd.DataFrame:
    """US Spot ETF Balances [BTC] (BGeometrics total holdings, 7-day delayed);
    plus cumulative net flow [USD] from the flow series."""
    bal = next(iter(c.prefix("bg:etf-btc-total:").values()), pd.Series(dtype=float))
    f = etf_flows(c)
    out = pd.DataFrame({"balance_btc": bal})
    if not f.empty:
        out = out.join(pd.DataFrame({"cumulative_flow_usd": f["Total"].cumsum()}), how="outer")
    return out


def etf_cost_basis(c: Ctx) -> pd.Series:
    """US Spot ETF Cost Basis [USD per BTC] = cumulative net USD flows /
    cumulative net BTC bought, BTC bought each day = flow / that day's price.
    APPROX: Glassnode values each day's flow at the fund's own transaction
    price; the daily close is used here."""
    f = etf_flows(c)
    if f.empty:
        return pd.Series(dtype=float)
    usd = f["Total"]
    btc = usd / c.price().reindex(usd.index)
    return (usd.cumsum() / btc.cumsum()).where(btc.cumsum() > 0)


def etf_market(c: Ctx) -> pd.DataFrame:
    """US Spot ETF Price [USD] and Trading Volume [USD/day] per fund, total volume."""
    from ..sources.yahoo import ETFS
    vol = {t: c.s(f"yf:{t}:volume") * c.s(f"yf:{t}:close") for t in ETFS}
    df = pd.DataFrame({f"vol:{t}": v for t, v in vol.items() if not v.empty})
    df["vol:total"] = df.sum(axis=1)
    for t in ETFS:
        p = c.s(f"yf:{t}:close")
        if not p.empty:
            df[f"price:{t}"] = p
    return df


def purpose_etf(c: Ctx) -> pd.DataFrame:
    """Purpose Bitcoin ETF (BTCC-B.TO): price [CAD] and dollar volume.
    APPROX: Glassnode's flows / holdings come from Purpose's daily NAV
    report; only the market data is free."""
    p, v = c.s("yf:BTCC-B.TO:close"), c.s("yf:BTCC-B.TO:volume")
    return pd.DataFrame({"price_cad": p, "volume_cad": p * v})


# --------------------------------------------------------------------------- #
# Treasury companies
# --------------------------------------------------------------------------- #


def treasury_companies(c: Ctx) -> pd.DataFrame:
    """Treasury company share prices [local currency] and dollar volume, and
    Strategy's preferred shares (STRK / STRF / STRD / STRC).
    APPROX: holdings history (balances, flows, P&L, mNAV) has no free API;
    see config 'treasury_holdings' for manually entered current holdings."""
    from ..sources.yahoo import STRATEGY_PREFERREDS, TREASURY_COS
    out = {}
    for t in list(TREASURY_COS) + list(STRATEGY_PREFERREDS):
        p, v = c.s(f"yf:{t}:close"), c.s(f"yf:{t}:volume")
        if not p.empty:
            out[f"price:{t}"], out[f"volume:{t}"] = p, p * v
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- #
# Macro and TradFi
# --------------------------------------------------------------------------- #


def yoy(s: pd.Series, periods_per_year: int = 12) -> pd.Series:
    return (s / s.shift(periods_per_year) - 1) * 100


def macro(c: Ctx) -> pd.DataFrame:
    """Macro series (FRED), at their own frequency:
      fed_balance_sheet [$bn]  WALCL / 1,000        ecb_balance_sheet [EUR bn] ECBASSETSW / 1,000
      us_rate [%] DFEDTARU     ez_rate [%] ECBDFR   us_m2 [$bn] M2SL       ez_m2 MYAGM2EZM196N
      us_core_cpi_yoy [%]  YoY of CPILFESL           ez_core_hicp_yoy [%] YoY of 00XEFDEZ19M086NEST
      us_infl_exp_1y [%] MICH    us_breakeven_5y [%] T5YIE
      us_debt [$bn] GFDEBTN/1,000   us_debt_gdp [%] GFDEGDQ188S   us_spend_gdp [%] FYONGDA188S
      us_gdp [$bn] GDP    ez_gdp CLVMNACSCAB1GQEA19    us_lei OECD CLI   us_sentiment UMCSENT
      ez_confidence CSCICP03EZM665S"""
    f = lambda sid: c.s(f"fred:{sid}")  # noqa: E731
    cols = {
        "fed_balance_sheet": f("WALCL") / 1000, "ecb_balance_sheet": f("ECBASSETSW") / 1000,
        "us_rate": f("DFEDTARU"), "ez_rate": f("ECBDFR"), "us_m2": f("M2SL"), "ez_m2": f("MYAGM2EZM196N"),
        "us_core_cpi_yoy": yoy(f("CPILFESL")), "ez_core_hicp_yoy": yoy(f("00XEFDEZ19M086NEST")),
        "us_infl_exp_1y": f("MICH"), "us_breakeven_5y": f("T5YIE"), "us_debt": f("GFDEBTN") / 1000,
        "us_debt_gdp": f("GFDEGDQ188S"), "us_spend_gdp": f("FYONGDA188S"), "us_gdp": f("GDP"),
        "ez_gdp": f("CLVMNACSCAB1GQEA19"), "us_lei": f("USALOLITONOSTSAM"), "us_sentiment": f("UMCSENT"),
        "ez_confidence": f("CSCICP03EZM665S"),
    }
    return pd.DataFrame({k: v for k, v in cols.items() if not v.empty})


def tradfi(c: Ctx) -> pd.DataFrame:
    """TradFi markets (Yahoo daily closes; yields from FRED):
    S&P 500, Nasdaq 100, Euro Stoxx 50, VIX, VSTOXX, gold, silver, WTI, DXY,
    EUR/USD; US 2Y / 10Y yields and 10Y-2Y spread [%], Germany 10Y [%] (monthly)."""
    from ..sources.yahoo import MACRO
    out = {name: c.s(f"yf:{t}:close") for t, name in MACRO.items()}
    out.update({"US 2Y Yield": c.s("fred:DGS2"), "US 10Y Yield (FRED)": c.s("fred:DGS10"),
                "US 10Y-2Y Spread": c.s("fred:T10Y2Y"), "Germany 10Y Yield": c.s("fred:IRLTLT01DEM156N")})
    return pd.DataFrame({k: v for k, v in out.items() if not v.empty})


def correlation(c: Ctx, other: pd.Series, window: int = 90) -> pd.Series:
    """Rolling correlation of daily log returns of BTC and another series."""
    a = np.log(c.price()).diff()
    b = np.log(other).diff()
    b.index = b.index.normalize()
    a, b = a.align(b, join="inner")
    return a.rolling(window, min_periods=window // 2).corr(b)
