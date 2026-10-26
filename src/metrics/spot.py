"""Spot market metrics: price, performance, models, volatility, volume,
technicals and market-cap aggregates."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .core import GENESIS, HALVINGS, Ctx, sma

# --------------------------------------------------------------------------- #
# Price
# --------------------------------------------------------------------------- #


def price(c: Ctx) -> pd.Series:
    """Price [USD]. Daily close of BTC in USD.
    Input: Coin Metrics reference rate (cm:PriceUSD), a volume-weighted
    multi-exchange composite, like Glassnode's market.PriceUsdClose."""
    return c.price()


def ohlc(c: Ctx, rule: str = "1D") -> pd.DataFrame:
    """Price OHLC [USD], built from Coinbase BTC-USD hourly candles.
    APPROX: Glassnode's OHLC is a multi-exchange VWAP composite; Coinbase's
    single-venue OHLC is typically within 0.1% of it."""
    o, h, l, cl = (c.s(f"cb1h:BTC-USD:{x}") for x in ("open", "high", "low", "close"))
    df = pd.concat({"open": o, "high": h, "low": l, "close": cl}, axis=1).dropna()
    return df.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def drawdown_from_ath(c: Ctx) -> pd.Series:
    """Price Drawdown from ATH [fraction, <= 0] = daily close / running all-time
    high - 1, where the ATH includes intraday highs (Coinbase hourly highs) as
    well as daily closes. Glassnode's ATH is intraday: on 2025-10-06 it reports
    -1.2% at a record close because the intraday high ($126.2k) sat above it."""
    p = c.price()
    hi = c.s("cb1h:BTC-USD:high")
    ath = p.cummax()
    if not hi.empty:
        day_hi = hi.resample("1D").max().reindex(p.index).fillna(0).cummax()
        ath = pd.concat([ath, day_hi], axis=1).max(axis=1)
    return p / ath - 1


# Working hours (local 08:00-20:00) per region, as Glassnode defines them.
REGIONS = {"Asia": "Asia/Shanghai", "EU": "Europe/Berlin", "US": "America/New_York"}


def regional_price(c: Ctx) -> pd.DataFrame:
    """Regional price series [USD] for US / EU / Asia.

    Glassnode (market.Amer30DPriceChange etc.): 'price movements are assigned to
    regions based on working hours in the US, Europe, and Asia. Regional prices
    are then derived by taking the cumulative sum of the price changes' during
    each region's 08:00-20:00 local working hours (DST handled by the time zone).
    Input: Coinbase hourly closes. Each hourly change is assigned to every
    region whose working hours contain it; APPROX: where two regions overlap
    the change is split equally between them (Glassnode doesn't publish its
    overlap rule)."""
    close = c.s("cb1h:BTC-USD:close")
    dp = close.diff().dropna()
    starts = dp.index - pd.Timedelta(hours=1)        # change during the hour that starts here
    active = {}
    for reg, tz in REGIONS.items():
        loc = starts.tz_convert(tz)
        active[reg] = pd.Series(((loc.hour >= 8) & (loc.hour < 20)).astype(float), index=dp.index)
    act = pd.DataFrame(active)
    share = act.div(act.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)
    return share.mul(dp, axis=0).cumsum()


def regional_mom_change(c: Ctx) -> pd.DataFrame:
    """US / EU / Asia Month-over-Month Price Change [USD]: 30-day change of
    each regional price series, sampled daily (00:00 UTC)."""
    rp = regional_price(c).resample("1D").last()
    return rp - rp.shift(30)


# --------------------------------------------------------------------------- #
# Performance
# --------------------------------------------------------------------------- #


def cycle_aths(c: Ctx) -> list[pd.Timestamp]:
    """Cycle ATHs (Glassnode): an all-time high followed by a drawdown of at
    least 75% before the next all-time high, plus the most recent ATH."""
    p = c.price()
    out, peak_t, peak = [], None, -np.inf
    trough_since_peak = np.inf
    for t, v in p.items():
        if v > peak:
            if peak_t is not None and trough_since_peak <= peak * 0.25:
                out.append(peak_t)
            peak_t, peak, trough_since_peak = t, v, v
        else:
            trough_since_peak = min(trough_since_peak, v)
    out.append(peak_t)
    return out


def performance_since(c: Ctx, anchors: list[pd.Timestamp], days: int = 1500) -> pd.DataFrame:
    """Price as a multiple of the price on each anchor date, by days elapsed
    (columns = anchor year, index = day 0..days)."""
    p = c.price()
    cols = {}
    for a in anchors:
        seg = p[p.index >= a].iloc[: days + 1]
        if len(seg) < 2:
            continue
        cols[f"{a:%Y-%m-%d}"] = pd.Series(seg.values / seg.iloc[0], index=range(len(seg)))
    return pd.DataFrame(cols)


def performance_since_cycle_ath(c: Ctx) -> pd.DataFrame:
    """Performance Since Cycle ATH [multiple of the ATH] by days since each cycle ATH."""
    return performance_since(c, cycle_aths(c))


def performance_since_cycle_low(c: Ctx) -> pd.DataFrame:
    """Performance Since Cycle Low [multiple]: anchors are the lowest close
    between consecutive cycle ATHs (and after the last one)."""
    p, aths = c.price(), cycle_aths(c)
    lows = [p[(p.index >= a) & (p.index < b)].idxmin() for a, b in zip(aths[:-1], aths[1:])]
    return performance_since(c, lows)


def performance_since_halving(c: Ctx) -> pd.DataFrame:
    """Performance Since Halving [multiple], anchored at each halving date."""
    return performance_since(c, list(HALVINGS))


def rolling_returns(c: Ctx, windows=(7, 30, 90, 180, 365)) -> pd.DataFrame:
    """Rolling Performance [fraction]: price / price n days earlier - 1."""
    p = c.price()
    return pd.DataFrame({f"{w}d": p / p.shift(w) - 1 for w in windows})


def period_returns(c: Ctx) -> pd.DataFrame:
    """Period-over-Period Returns [fraction]: calendar month, quarter and year
    returns (close of period / close of previous period - 1)."""
    p = c.price()
    out = {}
    for name, rule in (("month", "ME"), ("quarter", "QE"), ("year", "YE")):
        r = p.resample(rule).last()
        out[name] = r / r.shift(1) - 1
    return pd.concat(out, axis=1)


def cagr_4y(c: Ctx) -> pd.Series:
    """4y CAGR [fraction/year] = (price / price 4 years (1,461 days) earlier)^(1/4) - 1."""
    p = c.price()
    return (p / p.shift(1461)) ** 0.25 - 1


# --------------------------------------------------------------------------- #
# Price models
# --------------------------------------------------------------------------- #


def power_law(c: Ctx) -> pd.Series:
    """Power-Law Model [USD] = exp(5.71 * ln(days) - 38.16), days since the
    genesis block (2009-01-03). Coefficients exactly as Glassnode publishes
    (fit to 2024-03-21). Extended 2 years forward."""
    idx = pd.date_range(c.price().index[0], c.price().index[-1] + pd.Timedelta(days=730), freq="D")
    days = (idx - GENESIS).days.astype(float)
    return pd.Series(np.exp(5.71 * np.log(days) - 38.16), index=idx)


def mayer_multiple(c: Ctx) -> pd.Series:
    """Mayer Multiple [ratio] = price / 200-day SMA of price."""
    p = c.price()
    return p / sma(p, 200)


def pi_cycle(c: Ctx) -> pd.DataFrame:
    """Pi Cycle Top Indicator [USD]: 111-day SMA and 2 x 350-day SMA of price.
    A top signal fires when the 111DMA crosses above 2x350DMA (column 'signal')."""
    p = c.price()
    a, b = sma(p, 111), 2 * sma(p, 350)
    cross = (a > b) & (a.shift(1) <= b.shift(1))
    return pd.DataFrame({"sma111": a, "sma350x2": b, "signal": cross.astype(float)})


def investor_tool(c: Ctx) -> pd.DataFrame:
    """Investor Tool [USD]: 2-year (730-day) SMA and 5 x the 2-year SMA."""
    m = sma(c.price(), 730)
    return pd.DataFrame({"sma730": m, "sma730x5": 5 * m})


def sma_200w_heatmap(c: Ctx) -> pd.DataFrame:
    """200-week MA heatmap: 200-week (1,400-day) SMA [USD], and the colour
    value = 4-week (28-day) % change of that SMA [fraction]."""
    m = sma(c.price(), 1400)
    return pd.DataFrame({"sma200w": m, "sma200w_change_4w": m / m.shift(28) - 1})


def price_temperature(c: Ctx) -> pd.Series:
    """Price Temperature (BPT) [sigma] = (price - 4-year SMA) / 4-year std of price.
    APPROX: Glassnode's description isn't public in detail; this is the
    standard Bitcoin Price Temperature definition (1,461-day window)."""
    p = c.price()
    r = p.rolling(1461, min_periods=365)
    return (p - r.mean()) / r.std()


# --------------------------------------------------------------------------- #
# Volatility
# --------------------------------------------------------------------------- #

VOL_WINDOWS = {"1w": 7, "2w": 14, "1m": 30, "3m": 90, "6m": 180, "1y": 365}


def realized_volatility(c: Ctx) -> pd.DataFrame:
    """Realized Volatility [annualized fraction] = std of daily log returns over
    the window x sqrt(365), for 1w / 2w / 1m / 3m / 6m / 1y (Glassnode definition)."""
    lr = np.log(c.price()).diff()
    return pd.DataFrame({k: lr.rolling(n, min_periods=n).std() * np.sqrt(365) for k, n in VOL_WINDOWS.items()})


# --------------------------------------------------------------------------- #
# Spot volume (Coinalyze daily, every BTC/USD-family spot market with buy/sell data)
# --------------------------------------------------------------------------- #


def _meta(kind: str, asset: str = "BTC") -> list[dict]:
    from ..sources.coinalyze_daily import META
    if not META.exists():
        return []
    return [m for m in json.loads(META.read_text()) if m["kind"] == kind and m["asset"] == asset]


def spot_volume_by_exchange(c: Ctx) -> pd.DataFrame:
    """Spot Volume 24h by exchange [USD/day]: daily volume of BTC against USD,
    USDT, USDC and other USD stablecoins, summed per exchange (stacked view).
    Coverage: every spot market Coinalyze tracks with buy/sell data."""
    cols = {}
    for m in _meta("spot"):
        s = c.s(f"czd:{m['symbol']}:vol_usd")
        if not s.empty:
            ex = m["exchange"]
            cols[ex] = cols[ex].add(s, fill_value=0) if ex in cols else s
    return pd.DataFrame(cols).fillna(0)


def spot_volume(c: Ctx) -> pd.Series:
    """Spot Volume 24h [USD/day], all covered exchanges."""
    return spot_volume_by_exchange(c).sum(axis=1)


def spot_buy_sell(c: Ctx) -> pd.DataFrame:
    """Spot Buying / Selling Volume [USD/day] (aggressor side) and Volume
    Delta = buy - sell, summed over covered exchanges."""
    buy, tot = 0, 0
    for m in _meta("spot"):
        b, v = c.s(f"czd:{m['symbol']}:buy_usd"), c.s(f"czd:{m['symbol']}:vol_usd")
        if not b.empty:
            buy = b.add(buy, fill_value=0) if isinstance(buy, pd.Series) else b
            tot = v.add(tot, fill_value=0) if isinstance(tot, pd.Series) else v
    if not isinstance(buy, pd.Series):
        return pd.DataFrame()
    sell = tot - buy
    return pd.DataFrame({"buy": buy, "sell": sell, "delta": buy - sell})


def spot_cvd(c: Ctx) -> pd.Series:
    """Spot Cumulative Volume Delta [USD] = running sum of daily (buy - sell) volume."""
    d = spot_buy_sell(c)
    return d["delta"].cumsum() if not d.empty else pd.Series(dtype=float)


def spot_volume_ratios(c: Ctx) -> pd.DataFrame:
    """Spot Volume / Market Cap [ratio]; Spot Relative Volume (30D) = volume /
    trailing 30-day mean volume; Spot Volume Trend (7D) = 7-day mean volume /
    the 7-day mean 7 days earlier - 1 (Glassnode definitions)."""
    v = spot_volume(c)
    m7 = v.rolling(7).mean()
    return pd.DataFrame({"vol_mcap": v / c.mcap(), "relative_30d": v / v.rolling(30).mean(),
                         "trend_7d": m7 / m7.shift(7) - 1})


def spot_technicals(c: Ctx, vwap_window: int = 30, mfi_window: int = 14) -> pd.DataFrame:
    """Spot technicals from daily OHLC (Coinbase) and aggregate spot volume (coins):
      VWAP  = sum(typical price x volume) / sum(volume) over a rolling 30-day window,
              typical price = (high + low + close) / 3
      OBV   = cumulative volume, + on up closes, - on down closes
      VPT   = cumulative volume x (close / previous close - 1)
      ADL   = cumulative ((close - low) - (high - close)) / (high - low) x volume
      MFI   = 100 - 100 / (1 + positive money flow / negative money flow), 14 days
    APPROX: Glassnode computes these on its multi-exchange price; OHLC here is Coinbase's."""
    o = ohlc(c)
    vol = (spot_volume(c) / c.price()).reindex(o.index)
    tp = (o["high"] + o["low"] + o["close"]) / 3
    vwap = (tp * vol).rolling(vwap_window).sum() / vol.rolling(vwap_window).sum()
    sign = np.sign(o["close"].diff()).fillna(0)
    obv = (sign * vol).cumsum()
    vpt = (vol * o["close"].pct_change()).cumsum()
    rng = (o["high"] - o["low"]).replace(0, np.nan)
    adl = (((o["close"] - o["low"]) - (o["high"] - o["close"])) / rng * vol).fillna(0).cumsum()
    mf = tp * vol
    up = mf.where(tp > tp.shift(1), 0).rolling(mfi_window).sum()
    dn = mf.where(tp < tp.shift(1), 0).rolling(mfi_window).sum()
    mfi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    return pd.DataFrame({"vwap": vwap, "obv": obv, "vpt": vpt, "adl": adl, "mfi": mfi})


# --------------------------------------------------------------------------- #
# Market cap aggregates
# --------------------------------------------------------------------------- #


def market_cap(c: Ctx) -> pd.Series:
    """Market Cap [USD] = supply x price (Coin Metrics CapMrktCurUSD)."""
    return c.mcap()


def btc_dominance(c: Ctx) -> pd.Series:
    """BTC Dominance [%] = BTC market cap / total crypto market cap.
    Inputs: BGeometrics daily dominance history (4 years, 7-day delayed),
    extended with the CoinGecko global snapshot recorded daily."""
    bg = c.s("bg:bitcoin-dominance:bitcoinDominance")
    if bg.empty:
        bg = next(iter(c.prefix("bg:bitcoin-dominance:").values()), pd.Series(dtype=float))
    cg = c.s("cg:dominance:btc")
    return cg.combine_first(bg).sort_index()


def total_market_cap(c: Ctx) -> pd.Series:
    """Total crypto market cap [USD] = BTC market cap / (BTC dominance / 100)."""
    dom = btc_dominance(c)
    return (c.mcap() / (dom / 100)).dropna()


def altcoin_market_cap(c: Ctx) -> pd.Series:
    """Altcoin Market Cap [USD] = total market cap - BTC market cap
    (Glassnode: all assets except BTC; stablecoins included)."""
    return (total_market_cap(c) - c.mcap()).dropna()


def stablecoin_market_cap(c: Ctx) -> pd.Series:
    """Stablecoin Market Cap [USD]: DefiLlama total circulating USD-pegged stablecoins."""
    return c.s("dl:stable:total")


def btc_eth_dominance(c: Ctx) -> pd.Series:
    """BTC/ETH Market Cap Dominance [%] = BTC mcap / (BTC mcap + ETH mcap) x 100."""
    e = c.s("cm:eth:CapMrktCurUSD")
    return (c.mcap() / (c.mcap() + e) * 100).dropna()
