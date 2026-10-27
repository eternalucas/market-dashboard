"""Trader diagnosis: what the trader does, how they execute, how they handle
risk and size, where the edge comes from, and what they do right.

Everything here describes the trader's actual trades (no simulation).
Inputs are the round trips from reconstruct.py, the account's fills and
funding, daily mark-to-market P&L and equity (equity.py), and the stored
market data (market.Prices). Each function returns plain dicts/DataFrames
so the UI and the tests read the same numbers.

Conventions
- side_sign: +1 long, -1 short.
- Excursions (MAE/MFE) are % moves of price from the trip's average entry,
  signed so that MFE >= 0 is in the trade's favour and MAE <= 0 against it,
  measured on every bar that overlaps the trip at the finest stored
  interval. Coarser bars overstate excursions a little (the first bar can
  include price action just before the entry); the interval is reported.
- Entry context uses only bars that closed before the entry (no look-ahead).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .market import INTERVAL_TD, Prices

HOLD_BINS = [-0.01, 0.25, 1, 4, 24, 72, np.inf]
HOLD_LABELS = ["<15 min", "15 min-1h", "1-4h", "4-24h", "1-3d", ">3d"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _sign(side: pd.Series) -> np.ndarray:
    return np.where(side == "Long", 1.0, -1.0)


def closed_trips(trips: pd.DataFrame) -> pd.DataFrame:
    if trips is None or trips.empty:
        return pd.DataFrame(columns=list(trips.columns) if trips is not None else [])
    c = trips[~trips["is_open"].astype(bool)]
    if "exit_unobserved" in c:
        c = c[~c["exit_unobserved"].astype(bool)]
    return c.copy()


def _rank_corr(a: pd.Series, b: pd.Series) -> float:
    ok = a.notna() & b.notna()
    if ok.sum() < 8 or a[ok].nunique() < 2 or b[ok].nunique() < 2:
        return np.nan
    return float(a[ok].rank().corr(b[ok].rank()))


def summary_stats(net: pd.Series) -> dict:
    """win rate, payoff, expectancy, profit factor for a set of trip results."""
    if len(net) == 0:
        return {"trades": 0, "win_rate": np.nan, "payoff": np.nan, "expectancy": np.nan, "net": 0.0,
                "profit_factor": np.nan}
    w, l_ = net[net > 0], net[net <= 0]
    return {"trades": int(len(net)), "win_rate": float((net > 0).mean()),
            "payoff": float(w.mean() / abs(l_.mean())) if len(w) and len(l_) and l_.mean() else np.nan,
            "expectancy": float(net.mean()), "net": float(net.sum()),
            "profit_factor": float(w.sum() / abs(l_.sum())) if l_.sum() else np.inf}


# --------------------------------------------------------------------------- #
# Per-trip enrichment: excursions and entry context
# --------------------------------------------------------------------------- #

def enrich(trips: pd.DataFrame, prices: Prices, equity: pd.Series | None = None,
           now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Adds to each trip: mfe_pct, mae_pct, excursion_interval, prior_ret
    (return over the 24h before entry), move_vs_vol (that return in units of
    the 24h volatility), context ('With the move' / 'Against the move' /
    'Flat'), vol_regime ('Low' / 'Normal' / 'High'), chased (entered after a
    >2 sigma 4h move in the trade's direction), funding_at_entry (hourly
    rate; >0 = longs pay), funding_cost_at_entry (annualised, >0 = this side
    pays), leverage (max notional / account value at the start of the day)."""
    now = now or pd.Timestamp.now(tz="UTC")
    t = trips.copy()
    n = len(t)
    cols = {k: np.full(n, np.nan) for k in ("mfe_pct", "mae_pct", "mae_first_pct", "prior_ret", "move_vs_vol", "vol_ratio",
                                            "ret_4h_sigma", "funding_at_entry", "leverage")}
    interval = np.array([None] * n, dtype=object)
    ctx_iv = np.array([None] * n, dtype=object)
    sign = _sign(t["side"]) if n else np.array([])
    ends = t["close_ts"].fillna(now) if n else t.get("close_ts")
    ctx_cache: dict[str, tuple] = {}
    for i, (idx, r) in enumerate(t.iterrows()):
        iv, bars = prices.finest(r["coin"], r["open_ts"], ends.loc[idx])
        if iv is not None and r["avg_entry"] > 0:
            hi, lo = bars["h"].max() / r["avg_entry"] - 1, bars["l"].min() / r["avg_entry"] - 1
            fav, adv = (hi, lo) if sign[i] > 0 else (-lo, -hi)
            cols["mfe_pct"][i], cols["mae_pct"][i] = max(fav, 0.0), min(adv, 0.0)
            first = r.get("first_px", r["avg_entry"])
            first = first if first == first and first > 0 else r["avg_entry"]
            lo_f = bars["l"].min() / first - 1 if sign[i] > 0 else -(bars["h"].max() / first - 1)
            cols["mae_first_pct"][i] = min(lo_f, 0.0)
            interval[i] = iv
        # entry context from bars that closed before the entry
        if r["coin"] not in ctx_cache:
            civ, cb = prices.context(r["coin"])
            if civ is not None:
                step = INTERVAL_TD[civ]
                lr = np.log(cb["c"]).diff()
                per_day = int(pd.Timedelta(days=1) / step)
                vol24 = lr.rolling(max(per_day, 2)).std()
                vol_med = vol24.rolling(max(per_day * 30, 10), min_periods=max(per_day * 5, 5)).median()
                ctx_cache[r["coin"]] = (civ, step, cb, lr, per_day, vol24, vol_med)
            else:
                ctx_cache[r["coin"]] = None
        cc = ctx_cache[r["coin"]]
        if cc is not None:
            civ, step, cb, lr, per_day, vol24, vol_med = cc
            k = cb.index.searchsorted(r["open_ts"] - step, side="right") - 1   # last bar closed by the entry
            if k >= per_day:
                c0, c1 = cb["c"].iloc[k - per_day], cb["c"].iloc[k]
                cols["prior_ret"][i] = c1 / c0 - 1
                v = vol24.iloc[k]
                if v == v and v > 0:
                    cols["move_vs_vol"][i] = np.log(c1 / c0) / (v * np.sqrt(per_day)) * sign[i]
                    if vol_med.iloc[k] == vol_med.iloc[k] and vol_med.iloc[k] > 0:
                        cols["vol_ratio"][i] = v / vol_med.iloc[k]
                    n4 = max(int(pd.Timedelta(hours=4) / step), 1)
                    if k >= n4:
                        cols["ret_4h_sigma"][i] = np.log(cb["c"].iloc[k] / cb["c"].iloc[k - n4]) / (v * np.sqrt(n4)) * sign[i]
                ctx_iv[i] = civ
        cols["funding_at_entry"][i] = prices.funding_at(r["coin"], r["open_ts"])
        if equity is not None and not equity.empty:
            day = r["open_ts"].floor("D") - pd.Timedelta(days=1)
            e = equity.get(day, np.nan)
            if e != e:
                e = equity.get(r["open_ts"].floor("D"), np.nan)
            if e == e and e > 0:
                cols["leverage"][i] = r["max_notional"] / e
    for k, v in cols.items():
        t[k] = v
    t["excursion_interval"] = interval
    t["context_interval"] = ctx_iv
    t["context"] = np.select([t["move_vs_vol"] > 0.5, t["move_vs_vol"] < -0.5, t["move_vs_vol"].notna()],
                             ["With the move", "Against the move", "Flat"], default=None)
    t["vol_regime"] = np.select([t["vol_ratio"] < 0.75, t["vol_ratio"] > 1.33, t["vol_ratio"].notna()],
                                ["Low", "High", "Normal"], default=None)
    t["chased"] = np.where(t["ret_4h_sigma"].isna(), np.nan, (t["ret_4h_sigma"] > 2).astype(float))
    t["funding_cost_at_entry"] = t["funding_at_entry"] * sign * 24 * 365
    t["funding_side"] = np.select([t["funding_cost_at_entry"] > 0.10, t["funding_cost_at_entry"] < -0.10,
                                   t["funding_cost_at_entry"].notna()],
                                  ["Pays > 10%/yr", "Receives > 10%/yr", "Near zero"], default=None)
    t["ret_pct"] = (t["avg_exit"] / t["avg_entry"] - 1) * sign if n else []
    t["hold_bucket"] = pd.cut(t["hold_hours"], HOLD_BINS, labels=HOLD_LABELS)
    return t


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #

def fingerprint(t: pd.DataFrame) -> dict:
    """Strategy fingerprint on closed trips (enriched)."""
    c = closed_trips(t)
    out: dict = {"trips": len(c)}
    if c.empty:
        return out
    notional = c.groupby("coin")["max_notional"].sum().sort_values(ascending=False)
    share = notional / notional.sum()
    out["asset_share"] = share
    out["top_asset"], out["top_asset_share"] = share.index[0], float(share.iloc[0])
    out["hhi"] = float((share ** 2).sum())
    out["effective_assets"] = 1 / out["hhi"]
    longs = c["side"] == "Long"
    out["long_share_trips"] = float(longs.mean())
    out["long_share_notional"] = float(c.loc[longs, "max_notional"].sum() / c["max_notional"].sum())
    out["hold_dist"] = c["hold_bucket"].value_counts().reindex(HOLD_LABELS, fill_value=0)
    out["median_hold_h"] = float(c["hold_hours"].median())
    out["hour_dist"] = c["open_ts"].dt.hour.value_counts().reindex(range(24), fill_value=0)
    out["weekday_dist"] = c["open_ts"].dt.day_name().value_counts().reindex(WEEKDAYS, fill_value=0)
    lev = c["leverage"].dropna()
    out["leverage_median"] = float(lev.median()) if len(lev) else np.nan
    out["leverage_p90"] = float(lev.quantile(0.9)) if len(lev) else np.nan
    out["leverage_known"] = int(len(lev))
    out["scale_in_share"] = float((c["adds"] > 0).mean())
    partial = (c["fills"] - 1 - c["adds"] - 1).clip(lower=0)   # reducing fills before the final exit
    out["scale_out_share"] = float((partial > 0).mean())
    ctx = c["context"].dropna()
    out["context_share"] = ctx.value_counts(normalize=True) if len(ctx) else pd.Series(dtype=float)
    out["context_known"] = int(len(ctx))
    out["style"] = ("Trend / momentum" if len(ctx) and (ctx == "With the move").mean() > 0.45 and
                    (ctx == "With the move").mean() > (ctx == "Against the move").mean() + 0.1 else
                    "Mean reversion / fading" if len(ctx) and (ctx == "Against the move").mean() > 0.45 and
                    (ctx == "Against the move").mean() > (ctx == "With the move").mean() + 0.1 else
                    "Mixed" if len(ctx) else "Unknown")
    vr = c["vol_regime"].dropna()
    out["vol_share"] = vr.value_counts(normalize=True) if len(vr) else pd.Series(dtype=float)
    fs = c["funding_cost_at_entry"].dropna()
    out["funding_cost_median"] = float(fs.median()) if len(fs) else np.nan
    out["funding_known"] = int(len(fs))
    return out


def describe(fp: dict) -> str:
    """One sentence for the top of the page."""
    if not fp.get("trips"):
        return "No closed round trips to describe."
    hold = fp["median_hold_h"]
    hold_s = f"{hold * 60:.0f} min" if hold < 1 else f"{hold:.1f}h" if hold < 48 else f"{hold / 24:.1f} days"
    bias = fp["long_share_notional"]
    side = "mostly long" if bias > 0.65 else "mostly short" if bias < 0.35 else "both long and short"
    conc = (f"concentrated in {fp['top_asset']} ({fp['top_asset_share']:.0%} of size)" if fp["top_asset_share"] > 0.5
            else f"spread over ~{fp['effective_assets']:.0f} markets (largest {fp['top_asset']}, "
                 f"{fp['top_asset_share']:.0%})")
    lev = f", typically {fp['leverage_median']:.2g}x account value" if fp.get("leverage_median") == fp.get("leverage_median") \
        and fp.get("leverage_median") is not None else ""
    style = {"Trend / momentum": "enters with the prior day's move", "Mean reversion / fading":
             "enters against the prior day's move", "Mixed": "no clear trend or fade bias",
             "Unknown": "entry context unknown"}[fp["style"]]
    return f"{fp['trips']:,} closed trips, {side}, {conc}; median hold {hold_s}{lev}; {style}."


def execution(t: pd.DataFrame, fills: pd.DataFrame, funding: pd.DataFrame, prices: Prices | None = None) -> dict:
    out: dict = {}
    if fills is None or fills.empty:
        return out
    f = fills.copy()
    f["notional"] = (f["px"] * f["sz"]).abs()
    taker = f["is_taker"].astype(bool)
    out["fills"] = len(f)
    out["taker_share_fills"] = float(taker.mean())
    out["taker_share_notional"] = float(f.loc[taker, "notional"].sum() / f["notional"].sum()) if f["notional"].sum() else np.nan
    out["volume"] = float(f["notional"].sum())
    out["fees"] = float(f["fee"].sum())
    out["fee_bps"] = out["fees"] / out["volume"] * 1e4 if out["volume"] else np.nan
    out["fee_bps_taker"] = float(f.loc[taker, "fee"].sum() / f.loc[taker, "notional"].sum() * 1e4) if taker.any() and f.loc[taker, "notional"].sum() else np.nan
    out["fee_bps_maker"] = float(f.loc[~taker, "fee"].sum() / f.loc[~taker, "notional"].sum() * 1e4) if (~taker).any() and f.loc[~taker, "notional"].sum() else np.nan
    if funding is not None and not funding.empty:
        out["funding_received"] = float(funding.loc[funding["usdc"] > 0, "usdc"].sum())
        out["funding_paid"] = float(funding.loc[funding["usdc"] < 0, "usdc"].sum())
        out["funding_net"] = float(funding["usdc"].sum())
        out["funding_by_coin"] = funding.groupby("coin")["usdc"].sum().sort_values()
    else:
        out["funding_received"] = out["funding_paid"] = out["funding_net"] = 0.0
        out["funding_by_coin"] = pd.Series(dtype=float)
    c = closed_trips(t)
    gross = c.loc[c["realized_pnl"] > 0, "realized_pnl"].sum() if not c.empty else 0
    out["fees_share_of_gross"] = float(c["fees"].sum() / gross) if gross > 0 else np.nan
    # slippage proxy: taker fill price vs the close of the 1-minute bar before it
    out["slippage_bps_median"], out["slippage_n"] = np.nan, 0
    if prices is not None:
        rows = []
        for coin, g in f[taker].groupby("coin"):
            b = prices.bars.get((coin, "1m"))
            if b is None or b.empty:
                continue
            g = g[g["ts"] >= b.index[0] + pd.Timedelta(minutes=1)]
            if g.empty:
                continue
            k = b.index.searchsorted(g["ts"] - pd.Timedelta(minutes=1), side="right") - 1
            ok = k >= 0
            ref = b["c"].to_numpy()[k[ok]]
            sgn = np.where(g["side"].to_numpy()[ok] == "B", 1.0, -1.0)
            rows.append(pd.Series((g["px"].to_numpy()[ok] / ref - 1) * sgn * 1e4))
        if rows:
            s = pd.concat(rows)
            out["slippage_bps_median"], out["slippage_n"] = float(s.median()), int(len(s))
            out["slippage_bps_mean"] = float(s.clip(s.quantile(0.01), s.quantile(0.99)).mean())
    # chasing
    if not c.empty and c["chased"].notna().any():
        known = c[c["chased"].notna()]
        ch = known["chased"].astype(bool)
        out["chased_share"] = float(ch.mean())
        out["chased_n"] = int(ch.sum())
        out["chased_expectancy"] = float(known.loc[ch, "net"].mean()) if ch.any() else np.nan
        out["not_chased_expectancy"] = float(known.loc[~ch, "net"].mean()) if (~ch).any() else np.nan
    return out


def risk(t: pd.DataFrame, daily: pd.DataFrame | None = None, equity: pd.Series | None = None,
         now: pd.Timestamp | None = None) -> dict:
    now = now or pd.Timestamp.now(tz="UTC")
    c = closed_trips(t)
    out: dict = {"liquidations": int(t["liquidated"].astype(bool).sum()) if not t.empty else 0}
    if c.empty:
        return out
    x = c.dropna(subset=["mae_pct"])
    out["excursions_known"] = int(len(x))
    out["excursion_intervals"] = x["excursion_interval"].value_counts()
    w, l_ = x[x["net"] > 0], x[x["net"] <= 0]
    out["mae_winners_median"] = float(w["mae_pct"].median()) if len(w) else np.nan
    out["mae_losers_median"] = float(l_["mae_pct"].median()) if len(l_) else np.nan
    out["mfe_winners_median"] = float(w["mfe_pct"].median()) if len(w) else np.nan
    out["mfe_losers_median"] = float(l_["mfe_pct"].median()) if len(l_) else np.nan
    # Winners almost never sit deeper than this against the first entry; losers held past it.
    # Measured from the first fill, so averaging down can't hide how deep a loser went.
    if len(w) >= 5 and len(l_) >= 3:
        cut = float(w["mae_first_pct"].quantile(0.10))     # 90% of winners stayed above this
        past = l_[l_["mae_first_pct"] < cut]
        out["winner_mae_p10"] = cut
        out["losers_past_winner_cut"] = int(len(past))
        out["losers_past_winner_cut_share"] = float(len(past) / len(l_))
        out["losers_past_winner_cut_pnl"] = float(past["net"].sum())
    # capture: share of the best move that winners kept
    wk = w[(w["mfe_pct"] > 0) & w["ret_pct"].notna()]
    out["winner_capture_median"] = float((wk["ret_pct"] / wk["mfe_pct"]).clip(-1, 1).median()) if len(wk) else np.nan
    # losers that were in profit first
    lk = l_[l_["mfe_pct"].notna()]
    out["losers_were_green"] = float((lk["mfe_pct"] > 0.01).mean()) if len(lk) else np.nan
    out["mae_mfe"] = x[["coin", "side", "net", "mae_pct", "mfe_pct", "excursion_interval", "hold_hours"]]
    # concurrency (approximate: each trip at its max size for its whole life)
    tt = t.assign(end=t["close_ts"].fillna(now))
    ev = pd.concat([pd.DataFrame({"ts": tt["open_ts"], "n": 1, "g": tt["max_notional"], "dir": _sign(tt["side"])}),
                    pd.DataFrame({"ts": tt["end"], "n": -1, "g": -tt["max_notional"], "dir": -_sign(tt["side"])})])
    ev = ev.sort_values(["ts", "n"]).reset_index(drop=True)
    ev["open_n"] = ev["n"].cumsum()
    ev["gross"] = ev["g"].cumsum()
    ev["net_dir"] = ev["dir"].cumsum()
    dur = ev["ts"].shift(-1) - ev["ts"]
    secs = dur.dt.total_seconds().fillna(0)
    live = secs[ev["open_n"] > 0].sum()
    out["max_concurrent"] = int(ev["open_n"].max())
    out["max_gross_notional"] = float(ev["gross"].max())
    out["time_multi_share"] = float(secs[ev["open_n"] >= 2].sum() / live) if live else np.nan
    out["time_same_dir_share"] = float(secs[(ev["open_n"] >= 2) & (ev["net_dir"].abs() == ev["open_n"])].sum() / live) if live else np.nan
    if equity is not None and not equity.empty:
        e = equity.reindex(pd.DatetimeIndex(ev["ts"].dt.floor("D"))).to_numpy()
        lev = ev["gross"].to_numpy() / np.where(e > 0, e, np.nan)
        out["max_gross_leverage"] = float(np.nanmax(lev)) if np.isfinite(lev).any() else np.nan
    out["exposure"] = ev[["ts", "open_n", "gross"]]
    # drawdown profile from daily mark-to-market P&L
    if daily is not None and not daily.empty:
        cum = daily["mtm"].cumsum()
        peak = cum.cummax().clip(lower=0)
        dd = cum - peak
        out["dd_usd"] = float(dd.min())
        out["dd_current"] = float(dd.iloc[-1])
        under = dd < -1e-9
        run, best = 0, 0
        for u in under:
            run = run + 1 if u else 0
            best = max(best, run)
        out["longest_underwater_days"] = int(best)
        trough = dd.idxmin()
        rec = dd[trough:][dd[trough:] >= -1e-9]
        out["dd_trough"] = trough
        out["dd_recovery_days"] = int((rec.index[0] - trough).days) if len(rec) else None
        if equity is not None and not equity.empty:      # drawdown as a share of the account value at the peak
            e = equity.reindex(dd.index)
            peak_eq = e.where((cum >= cum.cummax()) & (e > 0)).ffill()
            pct = (dd / peak_eq).replace([np.inf, -np.inf], np.nan)
            out["dd_pct_of_equity"] = float(pct.min()) if pct.notna().any() else np.nan
        out["drawdown"] = dd
    return out


def sizing(t: pd.DataFrame) -> dict:
    c = closed_trips(t).sort_values("open_ts").reset_index(drop=True)
    out: dict = {}
    if len(c) < 5:
        return out
    out["size_vs_return_corr"] = _rank_corr(c["max_notional"], c["return_on_notional"])
    out["size_vs_pnl_corr"] = _rank_corr(c["max_notional"], c["net"])
    q = pd.qcut(c["max_notional"].rank(method="first"), 5, labels=["Smallest", "Q2", "Q3", "Q4", "Largest"])
    out["by_size"] = pd.DataFrame({k: summary_stats(g["net"]) for k, g in c.groupby(q, observed=True)}).T
    out["by_size"]["median_size"] = c.groupby(q, observed=True)["max_notional"].median()
    # size after a win vs after a loss: the previous trip closed before this one opened
    closes = c.sort_values("close_ts")
    ct = closes["close_ts"].to_numpy()
    k = np.searchsorted(ct, c["open_ts"].to_numpy(), side="right") - 1
    prev = np.where(k >= 0, closes["net"].to_numpy()[np.clip(k, 0, None)], np.nan)
    c["prev_result"] = np.select([prev > 0, prev <= 0], ["After a win", "After a loss"], default=None)
    rel = c["max_notional"] / c["max_notional"].rolling(20, min_periods=5).median().shift(1)
    c["size_rel"] = rel
    g = c.dropna(subset=["prev_result"]).groupby("prev_result")
    out["after"] = pd.DataFrame({"trades": g.size(), "median_size": g["max_notional"].median(),
                                 "median_size_vs_recent": g["size_rel"].median(), "expectancy": g["net"].mean(),
                                 "win_rate": g["net"].apply(lambda s: (s > 0).mean())})
    a = out["after"]
    if {"After a win", "After a loss"} <= set(a.index):
        out["tilt_ratio"] = float(a.loc["After a loss", "median_size_vs_recent"] /
                                  a.loc["After a win", "median_size_vs_recent"])
    # size vs volatility: do they size down when the market is wilder?
    if c["vol_ratio"].notna().sum() >= 8:
        out["size_vs_vol_corr"] = _rank_corr(c["size_rel"], c["vol_ratio"])
    out["sizes"] = c[["open_ts", "coin", "max_notional", "net", "prev_result", "vol_ratio"]]
    return out


def edge(t: pd.DataFrame) -> dict:
    c = closed_trips(t)
    out: dict = {"overall": summary_stats(c["net"]) if not c.empty else summary_stats(pd.Series(dtype=float))}
    if c.empty:
        return out
    total = c["net"].sum()
    top5 = c["net"].nlargest(5).sum()
    out["top5_share"] = float(top5 / total) if total > 0 else np.nan
    out["top5"] = c.nlargest(5, "net")[["coin", "side", "open_ts", "hold_hours", "max_notional", "net"]]
    out["without_top5"] = float(total - top5)
    tables = {}
    for col, title in (("coin", "Market"), ("side", "Side"), ("hold_bucket", "Holding time"),
                       ("context", "Entry context"), ("vol_regime", "Volatility at entry"),
                       ("funding_side", "Funding at entry")):
        if col not in c or c[col].isna().all():
            continue
        rows = {k: summary_stats(g["net"]) for k, g in c.groupby(col, observed=True)}
        df = pd.DataFrame(rows).T
        df["share_of_pnl"] = df["net"] / total if total else np.nan
        tables[title] = df.sort_values("net", ascending=False) if col == "coin" else df
    out["tables"] = tables
    return out


# --------------------------------------------------------------------------- #
# What they do right
# --------------------------------------------------------------------------- #

def strengths(fp: dict, ex: dict, rk: dict, sz: dict, ed: dict, min_trips: int = 20,
              errors: pd.DataFrame | None = None) -> list[dict]:
    """Each item: title, evidence. Only claims the data supports with
    enough trips; ordered by how strong the evidence is."""
    o = ed.get("overall", {})
    n = o.get("trades", 0)
    out = []

    def add(score, title, evidence):
        out.append({"score": score, "title": title, "evidence": evidence})

    if n < 5:
        return []
    if o.get("expectancy", 0) > 0 and o.get("profit_factor", 0) > 1.2:
        add(3, "Positive edge overall",
            f"Expectancy ${o['expectancy']:,.0f} per trip over {n:,} trips, profit factor {o['profit_factor']:.2f}.")
    if o.get("payoff", 0) == o.get("payoff", 0) and o.get("payoff", 0) > 1.5:
        add(2.5, "Winners are much bigger than losers",
            f"Average win is {o['payoff']:.1f}x the average loss, so a {o['win_rate']:.0%} win rate is enough.")
    if o.get("win_rate", 0) > 0.6 and o.get("payoff", 0) == o.get("payoff", 0) and o.get("payoff", 0) > 0.8:
        add(2.2, "High hit rate without giving it back on losers",
            f"{o['win_rate']:.0%} of trips win and the average loss is no larger than {1 / max(o['payoff'], 1e-9):.1f}x the average win.")
    if rk.get("losers_past_winner_cut_share") is not None and rk["losers_past_winner_cut_share"] < 0.25 \
            and rk.get("excursions_known", 0) >= min_trips:
        add(2.4, "Cuts losers before they get deep",
            f"Only {rk['losers_past_winner_cut_share']:.0%} of losers went further against the first entry than 90% "
            f"of winners ever did ({rk['winner_mae_p10']:.1%}).")
    if rk.get("winner_capture_median") == rk.get("winner_capture_median") and rk.get("winner_capture_median", 0) > 0.6:
        add(2, "Keeps most of the move on winners",
            f"Median winner exits with {rk['winner_capture_median']:.0%} of its best move (maximum favourable excursion).")
    if rk.get("liquidations", 0) == 0 and n >= min_trips:
        add(1.5, "Never liquidated", f"No liquidation in {n:,} closed trips.")
    if ex.get("taker_share_notional") == ex.get("taker_share_notional") and ex.get("taker_share_notional", 1) < 0.4:
        add(2, "Patient execution",
            f"{1 - ex['taker_share_notional']:.0%} of volume is filled with resting (maker) orders; fees average "
            f"{ex['fee_bps']:.1f} bps of volume.")
    if ex.get("funding_net", 0) > 0 and ex.get("funding_received", 0) > 0:
        add(1.6, "Gets paid funding", f"Net funding received: ${ex['funding_net']:,.0f}.")
    if sz.get("tilt_ratio") == sz.get("tilt_ratio") and sz.get("tilt_ratio") is not None and sz["tilt_ratio"] <= 1.05:
        add(1.8, "No revenge sizing",
            f"Size after a loss is {sz['tilt_ratio']:.2f}x the size after a win (relative to their recent sizes).")
    if sz.get("size_vs_vol_corr") == sz.get("size_vs_vol_corr") and sz.get("size_vs_vol_corr") is not None \
            and sz["size_vs_vol_corr"] < -0.15:
        add(1.7, "Sizes down when the market is volatile",
            f"Rank correlation between size and volatility at entry: {sz['size_vs_vol_corr']:+.2f}.")
    if sz.get("size_vs_return_corr") == sz.get("size_vs_return_corr") and sz.get("size_vs_return_corr") is not None \
            and sz["size_vs_return_corr"] > 0.15:
        add(1.9, "Bigger bets on better trades",
            f"Larger positions earn a higher return on size (rank correlation {sz['size_vs_return_corr']:+.2f}).")
    t5 = ed.get("top5_share")
    if t5 == t5 and t5 is not None and 0 < t5 < 0.5 and n >= 50:
        add(2.1, "Edge is broad, not a few lucky trades",
            f"The best 5 trips are {t5:.0%} of net P&L; without them the account still made ${ed['without_top5']:,.0f}.")
    tables = ed.get("tables", {})
    for title in ("Side", "Entry context", "Holding time", "Market"):
        df = tables.get(title)
        if df is None:
            continue
        good = df[(df["trades"] >= max(min_trips, 15)) & (df["expectancy"] > 0) & (df["profit_factor"] > 1.5)]
        if not good.empty and title != "Market":
            k = good["net"].idxmax()
            r = good.loc[k]
            what = {"Side": f"Strong on the {str(k).lower()} side",
                    "Entry context": {"With the move": "Strong when entering with the prior day's move",
                                      "Against the move": "Strong when fading the prior day's move",
                                      "Flat": "Strong when entering after a quiet day"}.get(k, f"Strong: {k}"),
                    "Holding time": f"Strong on {k} holds"}[title]
            add(1.4, what,
                f"{int(r['trades'])} trips, win rate {r['win_rate']:.0%}, expectancy ${r['expectancy']:,.0f}, "
                f"profit factor {r['profit_factor']:.2f}.")
        elif not good.empty:
            names = ", ".join(good.sort_values("net", ascending=False).index[:3])
            add(1.4, f"Best markets: {names}",
                f"Each with {max(min_trips, 15)}+ trips, positive expectancy and profit factor above 1.5.")
    # never praise what the mistakes list flags
    flagged = set(errors["pattern"]) if errors is not None and len(errors) else set()
    clash = {"No revenge sizing": {"Sizing up after a loss"},
             "Cuts losers before they get deep": {"Losers held too long", "Averaging down"},
             "Patient execution": {"Fees eating the edge"}}
    out = [d for d in out if not (clash.get(d["title"], set()) & flagged)]
    out.sort(key=lambda d: -d["score"])
    return [{"title": d["title"], "evidence": d["evidence"]} for d in out[:6]]


def run(trips: pd.DataFrame, fills: pd.DataFrame, funding: pd.DataFrame, prices: Prices,
        daily: pd.DataFrame | None = None, equity: pd.Series | None = None, now: pd.Timestamp | None = None) -> dict:
    """Everything the Diagnosis tab shows."""
    t = enrich(trips, prices, equity, now)
    fp = fingerprint(t)
    ex = execution(t, fills, funding, prices)
    rk = risk(t, daily, equity, now)
    sz = sizing(t)
    ed = edge(t)
    from ..indicators.trades import recurring_errors
    from .reconstruct import to_legacy
    c = closed_trips(trips)
    errs = recurring_errors(to_legacy(c)) if len(c) else pd.DataFrame()
    return {"trips": t, "fingerprint": fp, "summary": describe(fp), "execution": ex, "risk": rk, "sizing": sz,
            "edge": ed, "errors": errs, "strengths": strengths(fp, ex, rk, sz, ed, errors=errs)}


def coverage(t: pd.DataFrame) -> dict:
    """For the data-quality panel: how much of the diagnosis had market data."""
    c = closed_trips(t)
    n = len(c)
    if not n:
        return {}
    return {"trips": n,
            "excursions": int(c["mae_pct"].notna().sum()),
            "excursion_intervals": c["excursion_interval"].value_counts().to_dict(),
            "context": int(c["context"].notna().sum()),
            "funding_at_entry": int(c["funding_at_entry"].notna().sum()),
            "leverage": int(c["leverage"].notna().sum()),
            "missing_excursion_coins": sorted(c.loc[c["mae_pct"].isna(), "coin"].unique().tolist())}
