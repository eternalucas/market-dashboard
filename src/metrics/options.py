"""Options metrics for Deribit BTC (and IBIT).

Two kinds of input:
  * chain snapshots (option_snap, hourly for Deribit, daily for IBIT): IV
    surface, ATM IV, delta skews, GEX, max pain, ITM probabilities, OI.
    `snapshot_metrics` turns one snapshot into a flat dict; the recorder stores
    those as hourly series optm:<venue>:<name> right after each snapshot, and
    `rebuild` recomputes them for every stored snapshot (after a definition change).
  * trades (option_trades, every Deribit trade): premiums paid / received,
    buy / sell volume, Bull Bear Index, net premium by strike, combos.

Pricing model: Black-76 on each expiry's forward (Deribit quotes the forward
as underlying_price, so no rate input is needed). IBIT uses spot with a flat
4% rate (APPROX). Time to expiry in years of 365 days.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import storage
from .core import Ctx, norm_cdf, norm_pdf

TENORS = {"1w": 7, "1m": 30, "3m": 90, "6m": 180}
DELTAS = [5, 10, 15, 20, 25, 50]
CONTRACT_MULT = {"deribit": 1.0, "ibit": 100.0}   # underlying units per contract
IBIT_RATE = 0.04

# --------------------------------------------------------------------------- #
# Black-76
# --------------------------------------------------------------------------- #


def _d1d2(F, K, sig, t):
    F, K, sig, t = (np.asarray(x, dtype=float) for x in (F, K, sig, t))
    v = sig * np.sqrt(t)
    d1 = (np.log(F / K) + 0.5 * v * v) / v
    return d1, d1 - v


def delta(F, K, sig, t, kind):
    d1, _ = _d1d2(F, K, sig, t)
    call = norm_cdf(d1)
    return np.where(np.asarray(kind) == "C", call, call - 1)


def gamma(F, K, sig, t):
    """dDelta/dF per 1 unit of underlying [1/USD]."""
    d1, _ = _d1d2(F, K, sig, t)
    return norm_pdf(d1) / (np.asarray(F, dtype=float) * np.asarray(sig, dtype=float) * np.sqrt(np.asarray(t, dtype=float)))


def itm_prob(F, K, sig, t, kind):
    """Risk-neutral probability of finishing in the money: N(d2) for calls, N(-d2) for puts."""
    _, d2 = _d1d2(F, K, sig, t)
    return np.where(np.asarray(kind) == "C", norm_cdf(d2), norm_cdf(-d2))


# --------------------------------------------------------------------------- #
# One snapshot
# --------------------------------------------------------------------------- #


def prepare(snap: pd.DataFrame, venue: str) -> pd.DataFrame:
    """Add t (years), forward F, log-moneyness, delta, gamma; drop expired /
    unusable rows. One forward per expiry (median of the rows' underlying)."""
    df = snap.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    df["expiry"] = pd.to_datetime(df["expiry"], utc=True, format="ISO8601")
    df["t"] = (df["expiry"] - df["ts"]).dt.total_seconds() / (365 * 86400)
    df = df[(df["t"] > 1 / 365 / 24) & (df["iv"] > 0.01) & (df["iv"] < 5)].copy()
    if venue == "ibit":
        df["F"] = df["underlying"] * np.exp(IBIT_RATE * df["t"])
    else:
        df["F"] = df.groupby("expiry")["underlying"].transform("median")
    df["k"] = np.log(df["strike"] / df["F"])
    df["delta"] = delta(df["F"], df["strike"], df["iv"], df["t"], df["type"])
    df["gamma"] = gamma(df["F"], df["strike"], df["iv"], df["t"])
    return df


def _otm(e: pd.DataFrame) -> pd.DataFrame:
    """Out-of-the-money options of one expiry: puts below the forward, calls at/above."""
    return e[((e["type"] == "P") & (e["k"] < 0)) | ((e["type"] == "C") & (e["k"] >= 0))].sort_values("k")


def atm_iv_expiry(e: pd.DataFrame) -> float:
    """ATM IV of one expiry: OTM smile interpolated linearly in log-moneyness at k = 0."""
    o = _otm(e).drop_duplicates("k")
    if len(o) < 2 or o["k"].min() > 0 or o["k"].max() < 0:
        return float("nan")
    return float(np.interp(0.0, o["k"], o["iv"]))


def iv_at_delta(e: pd.DataFrame, d: int, kind: str) -> float:
    """IV at |delta| = d% on one side (calls: delta d/100; puts: delta -d/100),
    interpolated across that side's strikes; 50-delta = ATM."""
    if d == 50:
        return atm_iv_expiry(e)
    side = e[(e["type"] == kind)]
    side = side[side["k"] >= 0] if kind == "C" else side[side["k"] <= 0]
    x = side["delta"].abs().to_numpy()
    order = np.argsort(x)
    x, y = x[order], side["iv"].to_numpy()[order]
    if len(x) < 2 or not (x.min() <= d / 100 <= x.max()):
        return float("nan")
    return float(np.interp(d / 100, x, y))


def constant_maturity(per_exp: pd.DataFrame, col: str, days: float) -> float:
    """Interpolate `col` (an IV) to a constant maturity: linear in total
    variance w = iv^2 x t between the two expiries bracketing the target;
    flat extrapolation in IV outside the listed range."""
    d = per_exp[["t", col]].dropna().sort_values("t")
    if d.empty:
        return float("nan")
    T = days / 365
    if T <= d["t"].iloc[0]:
        return float(d[col].iloc[0])
    if T >= d["t"].iloc[-1]:
        return float(d[col].iloc[-1])
    w = d[col] ** 2 * d["t"]
    return float(np.sqrt(np.interp(T, d["t"], w) / T))


def max_pain(e: pd.DataFrame) -> float:
    """Max pain strike: the candidate settlement price (each listed strike)
    minimising total intrinsic value paid to holders,
    sum_calls OI x max(S - K, 0) + sum_puts OI x max(K - S, 0)."""
    if e.empty:
        return float("nan")
    ks = np.sort(e["strike"].unique())
    calls, puts = e[e["type"] == "C"], e[e["type"] == "P"]
    pay = [(calls["oi"] * np.maximum(s - calls["strike"], 0)).sum() + (puts["oi"] * np.maximum(puts["strike"] - s, 0)).sum()
           for s in ks]
    return float(ks[int(np.argmin(pay))])


def gex_by_strike(df: pd.DataFrame, venue: str) -> pd.DataFrame:
    """Dealer gamma exposure per strike [USD per 1% move].

    GEX = sum over contracts of  sign x OI x gamma x F^2 x 0.01 x multiplier.
    CONVENTION (as specified for this build): dealers are SHORT calls and LONG
    puts, so calls contribute negative and puts positive gamma. Positive total
    = dealers long gamma (dampening), negative = short gamma (amplifying).
    APPROX vs Glassnode, which infers dealer side from per-trade taker tags;
    see gex_trade_tagged for that variant."""
    m = CONTRACT_MULT[venue]
    sign = np.where(df["type"] == "C", -1.0, 1.0)
    g = sign * df["oi"] * df["gamma"] * df["F"] ** 2 * 0.01 * m
    return g.groupby(df["strike"]).sum().rename("gex_usd").to_frame()


def skew_index_expiry(e: pd.DataFrame) -> tuple[float, float]:
    """Upside / downside implied vol of one expiry by variance-swap style
    integration of OTM option prices (prices in units of the forward):
      up_var   = 2/t x integral_{K>F} C(K)/K^2 dK,  down_var = 2/t x integral_{K<F} P(K)/K^2 dK
    using Black-76 prices (USD) from each strike's mark IV; upvol = sqrt(2 x up_var),
    downvol = sqrt(2 x down_var), so both equal ATM IV on a flat smile.
    Returns (upvol, downvol).
    APPROX of the Glassnode Skew Index components (method in their 'Measuring
    Market Asymmetry' article; exact strike weighting not published)."""
    o = _otm(e).drop_duplicates("strike")
    if len(o) < 4:
        return float("nan"), float("nan")
    F, t = float(o["F"].iloc[0]), float(o["t"].iloc[0])
    atm = atm_iv_expiry(e)
    # strike grid with an exact at-the-forward point, so neither half misses
    # the segment next to the forward (where option prices are largest)
    K = np.r_[o["strike"].to_numpy(), F]
    iv = np.r_[o["iv"].to_numpy(), atm if np.isfinite(atm) else o["iv"].iloc[(o["k"].abs()).argmin()]]
    order = np.argsort(K)
    K, iv = K[order], iv[order]
    d1, d2 = _d1d2(F, K, iv, t)
    call = F * norm_cdf(d1) - K * norm_cdf(d2)
    put = K * norm_cdf(-d2) - F * norm_cdf(-d1)
    up, dn = K >= F, K <= F
    up_var = 2 / t * np.trapezoid(call[up] / K[up] ** 2, K[up]) if up.sum() > 1 else np.nan
    dn_var = 2 / t * np.trapezoid(put[dn] / K[dn] ** 2, K[dn]) if dn.sum() > 1 else np.nan
    # Each half carries about half the total variance on a flat smile, so
    # sqrt(2 x half) puts upvol / downvol on the same scale as ATM IV.
    return float(np.sqrt(max(up_var, 0) * 2)), float(np.sqrt(max(dn_var, 0) * 2))


def snapshot_metrics(snap: pd.DataFrame, venue: str = "deribit") -> dict:
    """Every per-snapshot metric as a flat {name: value} dict:
      atm_iv:<tenor>                 constant-maturity ATM IV (fraction)
      call_iv_d<d>:<tenor>, put_iv_d<d>:<tenor>   IV at delta d (5..50)
      skew25_norm:<tenor>            (put25 IV - call25 IV) / ATM IV  (Glassnode sign: + = puts richer)
      cp_skew_d<d>:<tenor>           call IV - put IV at delta d
      upvol:<tenor>, downvol:<tenor>, skew_index:<tenor> (= up - down), skew_ratio:<tenor> (= up / down)
      max_pain:<tenor>               max pain of the listed expiry closest to the tenor; max_pain:all over all OI
      gex_total                      dealer GEX, USD per 1% move (see gex_by_strike convention)
      oi_contracts, oi_usd, oi_call, oi_put, pc_oi    open interest totals and put/call OI ratio
      volume_24h_contracts           sum of each contract's 24h volume"""
    df = prepare(snap, venue)
    if df.empty:
        return {}
    out: dict[str, float] = {}
    per = []
    for exp, e in df.groupby("expiry"):
        row = {"expiry": exp, "t": float(e["t"].iloc[0]), "atm": atm_iv_expiry(e), "max_pain": max_pain(e)}
        for d in DELTAS:
            row[f"c{d}"], row[f"p{d}"] = iv_at_delta(e, d, "C"), iv_at_delta(e, d, "P")
        row["up"], row["down"] = skew_index_expiry(e)
        per.append(row)
    per = pd.DataFrame(per)
    for name, days in TENORS.items():
        atm = constant_maturity(per, "atm", days)
        out[f"atm_iv:{name}"] = atm
        for d in DELTAS:
            ci, pi = constant_maturity(per, f"c{d}", days), constant_maturity(per, f"p{d}", days)
            out[f"call_iv_d{d}:{name}"], out[f"put_iv_d{d}:{name}"] = ci, pi
            out[f"cp_skew_d{d}:{name}"] = ci - pi
        out[f"skew25_norm:{name}"] = (out[f"put_iv_d25:{name}"] - out[f"call_iv_d25:{name}"]) / atm if atm else np.nan
        up, dn = constant_maturity(per, "up", days), constant_maturity(per, "down", days)
        out[f"upvol:{name}"], out[f"downvol:{name}"] = up, dn
        out[f"skew_index:{name}"], out[f"skew_ratio:{name}"] = up - dn, up / dn if dn else np.nan
        near = per.iloc[(per["t"] * 365 - days).abs().argsort()[:1]]
        out[f"max_pain:{name}"] = float(near["max_pain"].iloc[0]) if not near.empty else np.nan
    out["max_pain:all"] = max_pain(df)
    out["gex_total"] = float(gex_by_strike(df, venue)["gex_usd"].sum())
    m = CONTRACT_MULT[venue]
    out["oi_contracts"] = float(df["oi"].sum())
    out["oi_usd"] = float((df["oi"] * m * df["F"]).sum())
    out["oi_call"] = float(df.loc[df["type"] == "C", "oi"].sum())
    out["oi_put"] = float(df.loc[df["type"] == "P", "oi"].sum())
    out["pc_oi"] = out["oi_put"] / out["oi_call"] if out["oi_call"] else np.nan
    out["volume_24h_contracts"] = float(df["volume"].sum())
    return {k: v for k, v in out.items() if v is not None and np.isfinite(v)}


def store_snapshot_metrics(con, venue: str, ts: str) -> int:
    """Compute snapshot_metrics for the stored snapshot (venue, ts) and save
    each value as series optm:<venue>:<name> at ts."""
    snap = pd.read_sql_query("SELECT * FROM option_snap WHERE venue = ? AND ts = ?", con, params=(venue, ts))
    m = snapshot_metrics(snap, venue)
    t = pd.Timestamp(ts)
    return sum(storage.save_point(con, f"optm:{venue}:{k}", v, t) for k, v in m.items())


def catch_up(con, venue: str = "deribit") -> int:
    """Compute metrics for any stored snapshot that doesn't have them yet
    (e.g. snapshots recorded before this code existed)."""
    done = {r[0] for r in con.execute("SELECT ts FROM observations WHERE series = ?", (f"optm:{venue}:oi_contracts",))}
    todo = [r[0] for r in con.execute("SELECT DISTINCT ts FROM option_snap WHERE venue = ?", (venue,))]
    todo = [t for t in todo if pd.Timestamp(t).isoformat() not in done]
    return sum(store_snapshot_metrics(con, venue, ts) for ts in todo)


def rebuild(con, venue: str = "deribit") -> int:
    """Recompute stored per-snapshot metrics for every snapshot of `venue`."""
    ts_list = [r[0] for r in con.execute("SELECT DISTINCT ts FROM option_snap WHERE venue = ? ORDER BY ts", (venue,))]
    return sum(store_snapshot_metrics(con, venue, ts) for ts in ts_list)


# --------------------------------------------------------------------------- #
# Reading the stored per-snapshot metrics
# --------------------------------------------------------------------------- #


def series(c: Ctx, name: str, venue: str = "deribit", daily: bool = True) -> pd.Series:
    """One stored per-snapshot metric; daily=True takes the last snapshot of each UTC day."""
    s = c.s(f"optm:{venue}:{name}")
    return s.resample("1D").last().dropna() if daily and not s.empty else s


def atm_iv(c: Ctx, venue: str = "deribit") -> pd.DataFrame:
    """Options ATM Implied Volatility (1w / 1m / 3m / 6m) [fraction], constant maturity."""
    return pd.DataFrame({k: series(c, f"atm_iv:{k}", venue) for k in TENORS})


def iv_rank_percentile(c: Ctx, windows=(30, 100, 365)) -> pd.DataFrame:
    """IV Rank and IV Percentile (1 Month) over rolling 30 / 100 / 365 days:
      rank       = (IV - window low) / (window high - window low)
      percentile = share of days in the window with IV below today's
    Input: daily 1m ATM IV; until a year of it is recorded, DVOL (Deribit's
    30-day IV index, history since 2021) fills the earlier days.
    APPROX: DVOL is a VIX-style index, typically within ~1-3 vol points of 1m ATM IV."""
    iv = series(c, "atm_iv:1m")
    dvol = c.s("dvol:BTC:close") / 100
    s = iv.combine_first(dvol).sort_index()
    out = {}
    for w in windows:
        r = s.rolling(w, min_periods=w)
        out[f"rank_{w}d"] = (s - r.min()) / (r.max() - r.min())
        out[f"percentile_{w}d"] = s.rolling(w, min_periods=w).apply(lambda x: (x[:-1] < x[-1]).mean(), raw=True)
    return pd.DataFrame(out)


def latest_snapshot(c: Ctx, venue: str = "deribit") -> pd.DataFrame:
    ts = c.con.execute("SELECT MAX(ts) FROM option_snap WHERE venue = ?", (venue,)).fetchone()[0]
    if not ts:
        return pd.DataFrame()
    return prepare(pd.read_sql_query("SELECT * FROM option_snap WHERE venue = ? AND ts = ?", c.con, params=(venue, ts)), venue)


def term_structure(c: Ctx, venue: str = "deribit") -> pd.DataFrame:
    """Options Volatility Term Structure: ATM IV of every listed expiry (latest snapshot)."""
    df = latest_snapshot(c, venue)
    return pd.DataFrame([{"expiry": e, "days": float(g["t"].iloc[0] * 365), "atm_iv": atm_iv_expiry(g)}
                         for e, g in df.groupby("expiry")]).dropna()


def smile(c: Ctx, expiry=None, venue: str = "deribit") -> pd.DataFrame:
    """Volatility Smile: mark IV by strike for one expiry (OTM side), latest snapshot."""
    df = latest_snapshot(c, venue)
    if df.empty:
        return df
    if expiry is None:   # default: the expiry nearest 30 days
        expiry = df.iloc[(df["t"] * 365 - 30).abs().argsort()[:1]]["expiry"].iloc[0]
    return _otm(df[df["expiry"] == expiry])[["strike", "k", "iv", "type", "delta"]]


def oi_by_expiry(c: Ctx, venue: str = "deribit") -> pd.DataFrame:
    df = latest_snapshot(c, venue)
    return df.pivot_table(index="expiry", columns="type", values="oi", aggfunc="sum").fillna(0)


def oi_by_strike(c: Ctx, venue: str = "deribit") -> pd.DataFrame:
    df = latest_snapshot(c, venue)
    return df.pivot_table(index="strike", columns="type", values="oi", aggfunc="sum").fillna(0)


def itm_probabilities(c: Ctx, expiry=None, venue: str = "deribit") -> pd.DataFrame:
    """Options ITM Prob (Call / Put) by strike for one expiry: N(d2) / N(-d2)."""
    df = latest_snapshot(c, venue)
    if df.empty:
        return df
    if expiry is None:
        expiry = df.iloc[(df["t"] * 365 - 30).abs().argsort()[:1]]["expiry"].iloc[0]
    e = df[df["expiry"] == expiry].copy()
    e["itm_prob"] = itm_prob(e["F"], e["strike"], e["iv"], e["t"], e["type"])
    return e.pivot_table(index="strike", columns="type", values="itm_prob")


def gex_strikes(c: Ctx, venue: str = "deribit") -> pd.DataFrame:
    """GEX by strike [USD per 1% move], latest snapshot (dealer short calls / long puts)."""
    return gex_by_strike(latest_snapshot(c, venue), venue)


def gex_strike_heatmap(c: Ctx, days: int = 30, venue: str = "deribit", bins: int = 100) -> pd.DataFrame:
    """GEX Strike Heatmap: daily (last snapshot of each day) GEX in 100
    log-spaced strike bins (Glassnode: 'strikes are bucketed into 100
    logarithmically-spaced bins'). Rows = day, columns = bin centre strike."""
    since = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)).isoformat()
    snaps = pd.read_sql_query("SELECT * FROM option_snap WHERE venue = ? AND ts >= ?", c.con, params=(venue, since))
    if snaps.empty:
        return pd.DataFrame()
    snaps["day"] = pd.to_datetime(snaps["ts"], utc=True, format="ISO8601").dt.floor("D")
    last = snaps.groupby("day")["ts"].transform("max") == snaps["ts"]
    snaps = snaps[last]
    edges = np.logspace(np.log10(snaps["strike"].min()), np.log10(snaps["strike"].max()), bins + 1)
    centres = np.sqrt(edges[:-1] * edges[1:])
    rows = {}
    for day, s in snaps.groupby("day"):
        g = gex_by_strike(prepare(s, venue), venue)
        b = np.clip(np.digitize(g.index.to_numpy(), edges) - 1, 0, bins - 1)
        rows[day] = pd.Series(g["gex_usd"].to_numpy(), index=b).groupby(level=0).sum().reindex(range(bins), fill_value=0).to_numpy()
    return pd.DataFrame(rows, index=centres).T


# --------------------------------------------------------------------------- #
# Trades: premiums, volume, Bull Bear Index
# --------------------------------------------------------------------------- #

MATURITY_BUCKETS = [(0, 30, "short"), (30, 90, "mid"), (90, 1e9, "long")]


def trades(c: Ctx, since: pd.Timestamp | None = None) -> pd.DataFrame:
    sql, params = "SELECT * FROM option_trades", ()
    if since is not None:
        sql, params = sql + " WHERE ts >= ?", (since.isoformat(),)
    df = pd.read_sql_query(sql, c.con, params=params)
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    df["expiry"] = pd.to_datetime(df["expiry"], utc=True, format="ISO8601")
    df["premium_usd"] = df["price"] * df["amount"] * df["index_price"]
    df["notional_usd"] = df["amount"] * df["index_price"]
    df["days"] = (df["expiry"] - df["ts"]).dt.total_seconds() / 86400
    df["bucket"] = pd.cut(df["days"], [b[0] for b in MATURITY_BUCKETS] + [1e9], labels=[b[2] for b in MATURITY_BUCKETS],
                          right=False)
    df["day"] = df["ts"].dt.floor("D")
    return df


def premiums(c: Ctx) -> pd.DataFrame:
    """Options Premiums [USD/day] (Deribit): the four legs from taker trades
      call_paid / put_paid          premium spent by takers buying calls / puts
      call_received / put_received  premium received by takers selling calls / puts
    plus net = paid - received per type.
    APPROX: Glassnode sums Deribit, OKX and Binance; this is Deribit only (~85%+ of BTC options)."""
    t = trades(c)
    if t.empty:
        return t
    t["leg"] = np.where(t["type"] == "C", "call", "put") + "_" + np.where(t["direction"] == "buy", "paid", "received")
    out = t.pivot_table(index="day", columns="leg", values="premium_usd", aggfunc="sum").fillna(0)
    for k in ("call", "put"):
        out[f"{k}_net"] = out.get(f"{k}_paid", 0) - out.get(f"{k}_received", 0)
    return out


def bull_bear_index(c: Ctx) -> pd.DataFrame:
    """Options Bull Bear Index [-1, 1] = (call premium paid by buyers - put
    premium paid by buyers) / their sum, daily, overall and by maturity
    bucket (short 0-30 d, mid 30-90 d, long > 90 d) - Glassnode definition."""
    t = trades(c)
    if t.empty:
        return t
    b = t[t["direction"] == "buy"]

    def bbi(x: pd.DataFrame) -> pd.Series:
        pv = x.pivot_table(index="day", columns="type", values="premium_usd", aggfunc="sum").reindex(columns=["C", "P"]).fillna(0)
        tot = (pv["C"] + pv["P"]).replace(0, np.nan)
        return (pv["C"] - pv["P"]) / tot
    out = {"all": bbi(b)}
    for _, _, name in MATURITY_BUCKETS:
        sub = b[b["bucket"] == name]
        if not sub.empty:
            out[name] = bbi(sub)
    return pd.DataFrame(out)


def volume_breakdown(c: Ctx) -> pd.DataFrame:
    """Options volume [contracts/day and USD notional/day]: total, taker buy /
    sell, calls / puts, and the volume put/call ratio."""
    t = trades(c)
    if t.empty:
        return t
    g = t.groupby("day")
    out = pd.DataFrame({
        "contracts": g["amount"].sum(), "notional_usd": g["notional_usd"].sum(),
        "buy": t[t["direction"] == "buy"].groupby("day")["amount"].sum(),
        "sell": t[t["direction"] == "sell"].groupby("day")["amount"].sum(),
        "calls": t[t["type"] == "C"].groupby("day")["amount"].sum(),
        "puts": t[t["type"] == "P"].groupby("day")["amount"].sum(),
    }).fillna(0)
    out["pc_volume"] = out["puts"] / out["calls"].replace(0, np.nan)
    return out


def net_premium_by_strike(c: Ctx, hours: int = 24) -> pd.DataFrame:
    """Net Premium by Strike [USD] over the past 24 h: premium paid minus
    received (taker perspective), per strike, calls and puts."""
    t = trades(c, pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=hours))
    if t.empty:
        return t
    t["signed"] = np.where(t["direction"] == "buy", 1, -1) * t["premium_usd"]
    return t.pivot_table(index="strike", columns="type", values="signed", aggfunc="sum").fillna(0)


def net_premium_strike_heatmap(c: Ctx, step: float = 5000) -> pd.DataFrame:
    """Options Net Premium Strike Heatmap: daily net premium (paid - received)
    in strike buckets of `step` USD. Rows = day, columns = bucket floor."""
    t = trades(c)
    if t.empty:
        return t
    t["signed"] = np.where(t["direction"] == "buy", 1, -1) * t["premium_usd"]
    t["bucket"] = (t["strike"] // step) * step
    return t.pivot_table(index="day", columns="bucket", values="signed", aggfunc="sum").fillna(0)


def premium_weighted_median_strike(c: Ctx) -> pd.DataFrame:
    """Premium Weighted Median Strike [USD] per day, by type and moneyness
    (ITM / ATM within 5% of index / OTM): the strike at which cumulative
    premium (sorted by strike) crosses half of that day's total."""
    t = trades(c)
    if t.empty:
        return t
    m = t["strike"] / t["index_price"] - 1
    t["moneyness"] = np.where(m.abs() <= 0.05, "ATM",
                              np.where(((t["type"] == "C") & (m < 0)) | ((t["type"] == "P") & (m > 0)), "ITM", "OTM"))

    def wmed(x):
        x = x.sort_values("strike")
        cum = x["premium_usd"].cumsum()
        return float(x.loc[cum >= cum.iloc[-1] / 2, "strike"].iloc[0]) if len(x) else np.nan
    return t.groupby(["day", "type", "moneyness"]).apply(wmed, include_groups=False).unstack(["type", "moneyness"])


def combo_premiums(c: Ctx) -> pd.DataFrame:
    """Options Combo Premiums [USD/day]: premium paid by buyers and received
    by sellers on legs of multi-leg strategy trades (Deribit combo / block
    trades), and net = buyers - sellers. APPROX: aggregated over strategies;
    Glassnode splits by strategy type."""
    t = trades(c)
    if t.empty:
        return t
    t = t[t["combo"] == 1]
    out = pd.DataFrame({"buyers": t[t["direction"] == "buy"].groupby("day")["premium_usd"].sum(),
                        "sellers": t[t["direction"] == "sell"].groupby("day")["premium_usd"].sum()}).fillna(0)
    out["net"] = out["buyers"] - out["sellers"]
    return out


def gex_trade_tagged(c: Ctx) -> pd.DataFrame:
    """Dealer GEX with dealer positions inferred from taker trades (Glassnode's
    method): dealer position per contract = -(taker buys - taker sells) since
    recording began, x gamma x F^2 x 0.01, by strike, latest snapshot.
    APPROX until a year of trades is stored: positions opened before the
    first recorded trade are missing."""
    t = trades(c)
    snap = latest_snapshot(c)
    if t.empty or snap.empty:
        return pd.DataFrame()
    t["signed"] = np.where(t["direction"] == "buy", 1, -1) * t["amount"]
    pos = -t.groupby(["expiry", "strike", "type"])["signed"].sum()
    s = snap.set_index(["expiry", "strike", "type"])
    j = s.join(pos.rename("dealer_pos"), how="inner")
    j["gex_usd"] = j["dealer_pos"] * j["gamma"] * j["F"] ** 2 * 0.01
    return j.groupby(level="strike")["gex_usd"].sum().to_frame()
