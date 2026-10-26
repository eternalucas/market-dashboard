"""Futures metrics: open interest, volume, funding, liquidations, ratios,
leverage, position openings / closures, basis and term structure, CME."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .core import Ctx, zscore

# Venues whose perpetual funding is paid hourly (Coinalyze reports the
# per-interval rate); converted to an 8-hour rate (x8) before averaging.
HOURLY_FUNDING = {"Hyperliquid", "dYdX", "Kraken", "Coinbase", "Lighter", "Aster"}


def _perps(asset: str = "BTC") -> list[dict]:
    from ..sources.coinalyze_daily import META
    return [m for m in json.loads(META.read_text()) if m["kind"] == "perp" and m["asset"] == asset] if META.exists() else []


def _per_symbol(c: Ctx, field: str, asset: str = "BTC") -> pd.DataFrame:
    return pd.DataFrame({m["symbol"]: c.s(f"czd:{m['symbol']}:{field}") for m in _perps(asset)}).sort_index()


def _by(c: Ctx, field: str, key: str, asset: str = "BTC") -> pd.DataFrame:
    df = _per_symbol(c, field, asset)
    groups = {m["symbol"]: m[key] for m in _perps(asset)}
    return df.T.groupby(groups).sum(min_count=1).T


# --------------------------------------------------------------------------- #
# Open interest
# --------------------------------------------------------------------------- #


def open_interest(c: Ctx, asset: str = "BTC") -> pd.Series:
    """Futures Open Interest Perpetual [USD]: sum of perpetual open interest at
    each day's close across all covered exchanges (25 BTC perps, both margin
    types). APPROX: dated futures OI is not included (Glassnode's 'Futures Open
    Interest' adds them; its 'Perpetual' variant matches this)."""
    return _per_symbol(c, "oi_usd", asset).sum(axis=1, min_count=1)


def open_interest_by_exchange(c: Ctx, asset: str = "BTC") -> pd.DataFrame:
    """Futures Open Interest (Stacked) [USD] per exchange."""
    return _by(c, "oi_usd", "exchange", asset)


def open_interest_by_margin(c: Ctx) -> pd.DataFrame:
    """Cash- vs Crypto-Margined Futures Open Interest [USD] and Percent
    Crypto-Margined [%]. STABLE = cash (USD / stablecoin) margined, COIN = margined in BTC."""
    df = _by(c, "oi_usd", "margined")
    out = pd.DataFrame({"cash": df.get("STABLE"), "crypto": df.get("COIN")})
    out["crypto_pct"] = out["crypto"] / (out["cash"] + out["crypto"]) * 100
    return out


def leverage_ratio(c: Ctx) -> pd.Series:
    """Futures Estimated Leverage Ratio [ratio] = futures open interest (in BTC)
    / BTC held on exchanges. Inputs: perp OI / price, Coin Metrics SplyExNtv.
    APPROX: Glassnode divides per exchange by that exchange's balance; this is
    the aggregate version."""
    oi_btc = open_interest(c) / c.price()
    return (oi_btc / c.s("cm:SplyExNtv")).dropna()


def btc_eth_oi_dominance(c: Ctx) -> pd.Series:
    """BTC/ETH Futures Open Interest Dominance [%] = BTC OI / (BTC OI + ETH OI) x 100."""
    b, e = open_interest(c, "BTC"), open_interest(c, "ETH")
    return (b / (b + e) * 100).dropna()


# --------------------------------------------------------------------------- #
# Volume
# --------------------------------------------------------------------------- #


def volume(c: Ctx, asset: str = "BTC") -> pd.DataFrame:
    """Futures (perpetual) Volume [USD/day]: total, taker-buy, taker-sell and
    Volume Delta = buy - sell, summed over covered exchanges."""
    tot = _per_symbol(c, "vol_usd", asset).sum(axis=1, min_count=1)
    buy = _per_symbol(c, "buy_usd", asset).sum(axis=1, min_count=1)
    sell = tot - buy
    return pd.DataFrame({"total": tot, "buy": buy, "sell": sell, "delta": buy - sell})


def volume_by_exchange(c: Ctx) -> pd.DataFrame:
    """Futures Volume 24h (Stacked) [USD/day] per exchange."""
    return _by(c, "vol_usd", "exchange")


def btc_eth_volume_dominance(c: Ctx) -> pd.Series:
    """BTC vs ETH Perpetual Futures Volume Dominance (7 EMA) [%]."""
    b, e = volume(c, "BTC")["total"], volume(c, "ETH")["total"]
    return (b / (b + e) * 100).ewm(span=7).mean().dropna()


# --------------------------------------------------------------------------- #
# Funding
# --------------------------------------------------------------------------- #


def _funding_8h(c: Ctx, asset: str = "BTC") -> pd.DataFrame:
    fr = _per_symbol(c, "fr", asset)
    for m in _perps(asset):
        if m["exchange"] in HOURLY_FUNDING and m["symbol"] in fr:
            fr[m["symbol"]] = fr[m["symbol"]] * 8
    return fr


def funding_rate(c: Ctx, asset: str = "BTC") -> pd.Series:
    """Futures Perpetual Funding Rate (All) [% per 8h]: open-interest-weighted
    mean of each perpetual's funding rate (Glassnode: 'the open-interest-
    weighted average of each exchange's funding rate'). Hourly-funded venues
    are scaled x8 to an 8-hour rate first."""
    fr, oi = _funding_8h(c, asset), _per_symbol(c, "oi_usd", asset)
    fr, oi = fr.align(oi, join="inner")
    w = oi.where(fr.notna())
    return ((fr * w).sum(axis=1) / w.sum(axis=1)).dropna()


def funding_by_exchange(c: Ctx, top: int = 8) -> pd.DataFrame:
    """Funding per exchange [% per 8h] (OI-weighted across that exchange's
    contracts), for the `top` exchanges by open interest over the last 30 days."""
    fr, oi = _funding_8h(c), _per_symbol(c, "oi_usd")
    ex = {m["symbol"]: m["exchange"] for m in _perps()}
    num = (fr * oi).T.groupby(ex).sum(min_count=1).T
    den = oi.where(fr.notna()).T.groupby(ex).sum(min_count=1).T
    out = num / den
    size = den.iloc[-30:].mean().sort_values(ascending=False)
    return out[[x for x in size.index[:top] if x in out]]


def btc_eth_funding_spread(c: Ctx) -> pd.Series:
    """BTC vs ETH Funding Rate Spread [pp per 8h] = BTC funding - ETH funding."""
    return (funding_rate(c, "BTC") - funding_rate(c, "ETH")).dropna()


# --------------------------------------------------------------------------- #
# Liquidations
# --------------------------------------------------------------------------- #


def liquidations(c: Ctx) -> pd.DataFrame:
    """Futures Liquidations [USD/day]: long, short and total across covered
    exchanges; long dominance = long / total x 100 [%]; mean = total / number
    of exchanges reporting that day (Glassnode's '(Mean)' variant)."""
    lo = _by(c, "liq_long_usd", "exchange")
    sh = _by(c, "liq_short_usd", "exchange")
    long_, short_ = lo.sum(axis=1, min_count=1), sh.sum(axis=1, min_count=1)
    tot = long_ + short_
    n = lo.notna().sum(axis=1).replace(0, np.nan)
    return pd.DataFrame({"long": long_, "short": short_, "total": tot,
                         "long_mean": long_ / n, "short_mean": short_ / n, "total_mean": tot / n,
                         "long_dominance": long_ / tot.replace(0, np.nan) * 100})


# --------------------------------------------------------------------------- #
# Ratios
# --------------------------------------------------------------------------- #


def ratios(c: Ctx) -> pd.DataFrame:
    """Futures ratios (Glassnode definitions), daily:
      oi_mcap      = open interest / market cap
      oi_volume    = open interest / futures volume
      vol_mcap     = futures volume / market cap
      spot_futures = spot volume / futures volume
      liq_mcap     = liquidations / market cap
      liq_oi       = liquidations / open interest"""
    from .spot import spot_volume
    oi, fv, mc = open_interest(c), volume(c)["total"], c.mcap()
    liq = liquidations(c)["total"]
    return pd.DataFrame({"oi_mcap": oi / mc, "oi_volume": oi / fv, "vol_mcap": fv / mc,
                         "spot_futures": spot_volume(c) / fv, "liq_mcap": liq / mc, "liq_oi": liq / oi})


# --------------------------------------------------------------------------- #
# Position openings / closures
# --------------------------------------------------------------------------- #


def position_openings_closures(c: Ctx, ma: int = 7, z_window: int = 90) -> pd.DataFrame:
    """Leverage Position Openings / Closures (and Scaled).

    Glassnode: 'trend alignment between 7-day moving averages of price and open
    interest percentage changes', classified by direction (research article
    'Leverage position openings and closures'):
      trend_p = 7-day mean of daily % change in price
      trend_o = 7-day mean of daily % change in open interest
      signal  = trend_p x trend_o           (product = directional alignment)
      long opening  : trend_p > 0, trend_o > 0   value = signal
      short opening : trend_p < 0, trend_o > 0   value = |signal|
      long closure  : trend_p < 0, trend_o < 0   value = signal
      short closure : trend_p > 0, trend_o < 0   value = |signal|
    Scaled = each column's 90-day rolling z-score (Glassnode: 'normalized using
    90-day z-scores'). APPROX: the article gives the logic, not the exact
    window treatment of zero days; zero is used where a type doesn't apply."""
    tp = c.price().pct_change().rolling(ma).mean()
    to = open_interest(c).pct_change().rolling(ma).mean()
    tp, to = tp.align(to, join="inner")
    sig = tp * to
    out = pd.DataFrame({
        "long_opening": sig.where((tp > 0) & (to > 0), 0.0),
        "short_opening": sig.abs().where((tp < 0) & (to > 0), 0.0),
        "long_closure": sig.where((tp < 0) & (to < 0), 0.0),
        "short_closure": sig.abs().where((tp > 0) & (to < 0), 0.0),
    }).dropna()
    for col in list(out.columns):
        out[f"{col}_scaled"] = zscore(out[col], z_window)
    return out


# --------------------------------------------------------------------------- #
# Basis and term structure
# --------------------------------------------------------------------------- #


def annualized_basis(f: float | pd.Series, s: float | pd.Series, days: float | pd.Series):
    """Annualized basis [fraction/year] = (F / S - 1) x 365 / days to expiry."""
    return (f / s - 1) * 365 / days


def rolling_basis_3m(c: Ctx) -> pd.Series:
    """Futures Annualized Rolling Basis (3M) [fraction/year].

    Binance USDT quarterlies (daily closes since 2020): the current- and
    next-quarter contracts' annualized basis against the index, linearly
    interpolated in days-to-expiry to a constant 90 days (the two contracts
    bracket 90 days for most of each quarter; when both are on the same side,
    the nearer-to-90 contract is used). Expiries: last Friday of each quarter, 08:00 UTC."""
    from ..sources.binance_quarterly import quarter_expiries
    idx_ = c.s("bq:BTC:index")
    cq, nq = c.s("bq:BTC:current_quarter"), c.s("bq:BTC:next_quarter")
    df = pd.concat({"s": idx_, "cq": cq, "nq": nq}, axis=1).dropna()
    if df.empty:
        return pd.Series(dtype=float)
    exps = quarter_expiries()
    out = {}
    for t, r in df.iterrows():
        close_t = t + pd.Timedelta(days=1)             # a daily bar stamped t closes at t + 1 day
        nxt = exps[exps > close_t]
        if len(nxt) < 2:
            continue
        d1 = (nxt[0] - close_t).total_seconds() / 86400
        d2 = (nxt[1] - close_t).total_seconds() / 86400
        if d1 < 1:
            continue
        b1, b2 = annualized_basis(r["cq"], r["s"], d1), annualized_basis(r["nq"], r["s"], d2)
        if d1 <= 90 <= d2:
            w = (90 - d1) / (d2 - d1)
            out[t] = b1 + w * (b2 - b1)
        else:
            out[t] = b1 if abs(d1 - 90) < abs(d2 - 90) else b2
    return pd.Series(out, dtype=float)


def term_structure(c: Ctx, at: pd.Timestamp | None = None) -> pd.DataFrame:
    """Futures Term Structure: annualized basis and price for every dated
    future at the latest recorded snapshot (or the snapshot at/just before
    `at`), per exchange. Basis uses each exchange's own index."""
    df = c.frame("SELECT * FROM futures_curve WHERE expiry IS NOT NULL")
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    df["expiry"] = pd.to_datetime(df["expiry"], utc=True, format="ISO8601")
    snap = df["ts"].max() if at is None else df.loc[df["ts"] <= at, "ts"].max()
    d = df[df["ts"] == snap].copy()
    d["days"] = (d["expiry"] - d["ts"]).dt.total_seconds() / 86400
    d = d[d["days"] > 0.5]
    d["basis_ann"] = annualized_basis(d["price"], d["idx"], d["days"])
    return d.sort_values(["venue", "expiry"])


def funding_vs_basis(c: Ctx) -> pd.DataFrame:
    """Annualized Perpetual Funding vs 3M Rolling Basis [fraction/year]:
    funding (% per 8h) x 3 x 365 / 100, next to the 3M basis."""
    f = funding_rate(c) * 3 * 365 / 100
    return pd.DataFrame({"funding_ann": f, "basis_3m": rolling_basis_3m(c)})


# --------------------------------------------------------------------------- #
# CME
# --------------------------------------------------------------------------- #

CME_BTC_PER_CONTRACT = 5.0


def cme(c: Ctx) -> pd.DataFrame:
    """CME Bitcoin futures: open interest [BTC and USD] from the weekly CFTC
    report (contracts x 5 BTC, x price on the report date); daily volume
    [USD] from Yahoo BTC=F (contracts x 5 BTC x close). Options OI [contracts]
    = combined futures+options report - futures-only (delta-adjusted).
    APPROX: weekly OI, and BTC=F volume is the front month only."""
    oi_ct = c.s("cftc:btc:open_interest_all")
    p = c.price()
    oi_btc = oi_ct * CME_BTC_PER_CONTRACT
    out = pd.DataFrame({"oi_btc": oi_btc, "oi_usd": oi_btc * p.reindex(oi_btc.index)})
    vol = c.s("yf:BTC=F:volume") * CME_BTC_PER_CONTRACT * c.s("yf:BTC=F:close")
    comb = c.s("cftc:btc_combined:open_interest_all")
    out = out.join(pd.DataFrame({"volume_usd": vol}), how="outer")
    if not comb.empty:
        out = out.join(pd.DataFrame({"options_oi_contracts": comb - oi_ct}), how="outer")
    return out.sort_index()


def cme_positioning(c: Ctx) -> pd.DataFrame:
    """CFTC TFF net positioning [contracts]: asset managers, leveraged funds,
    dealers (long - short), weekly."""
    g = lambda f: c.s(f"cftc:btc:{f}")  # noqa: E731
    return pd.DataFrame({"asset_managers": g("asset_mgr_positions_long") - g("asset_mgr_positions_short"),
                         "leveraged_funds": g("lev_money_positions_long") - g("lev_money_positions_short"),
                         "dealers": g("dealer_positions_long_all") - g("dealer_positions_short_all")})


def total_crypto_oi(c: Ctx) -> pd.Series:
    """Total Open Interest [USD] = BTC + ETH perpetual open interest.
    APPROX: Glassnode's index covers all assets; BTC + ETH are ~70-80% of it."""
    return open_interest(c, "BTC").add(open_interest(c, "ETH"), fill_value=0)
