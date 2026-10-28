"""Backtest & Improve: every variant family, parameters chosen on the
training window (first 70% of trips), results reported on the test window
(last 30%) first. All results are simulated and labelled as such.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import costs as cst
from . import validation as val
from .replay import Trip, Variant, golden, run_variant

MIN_TRIPS = 30
MAX_SKIP = 0.8          # a selection rule that drops more of the training trips than this is just "stop trading"
CATEGORY = {"ATR stop": "exits", "Fixed stop": "exits", "Trailing stop": "exits", "Take profit": "exits",
            "Time stop": "exits", "Volatility targeting": "sizing", "Fixed-fractional risk": "sizing",
            "Capped Kelly": "sizing", "No adding to losers": "sizing", "Smaller after losses": "sizing",
            "Limit entries": "execution", "Skip expensive funding": "execution",
            "Drop losing markets": "selection", "Drop losing hours": "selection", "Drop losing regimes": "selection"}


def _q(x, p):
    x = np.asarray([v for v in x if v == v], float)
    return float(np.quantile(x, p)) if len(x) else np.nan


def _cap(scale: np.ndarray, trips: list[Trip], cap: float) -> np.ndarray:
    out = scale.copy()
    for t in trips:
        if t.equity == t.equity and t.equity > 0 and t.max_notional > 0:
            limit = max(cap, t.max_notional / t.equity) * t.equity / t.max_notional
            out[t.i] = min(out[t.i], limit)
    return np.clip(np.nan_to_num(out, nan=1.0), 0.0, None)


def candidates(trips: list[Trip], k: int, a: cst.Assumptions) -> list[Variant]:
    """Every variant tried (N for the deflated Sharpe). Thresholds that are
    learned (take-profit level, time limit, markets/hours/regimes to drop,
    sizing constants) come from the training trips [0, k) only."""
    tr = trips[:k]
    n = len(trips)
    out: list[Variant] = []
    fam = set(a.families)
    if "exits" in fam:
        for d in (0.005, 0.01, 0.02, 0.03, 0.05):
            out.append(Variant(f"Stop {d:.1%} from entry", "Fixed stop", "exits", d, stop=("pct", d),
                               describe=f"exit when price moves {d:.1%} against the first entry"))
        for m in (1.0, 1.5, 2.0, 3.0):
            out.append(Variant(f"ATR stop {m:g}x", "ATR stop", "exits", m, stop=("atr", m),
                               describe=f"exit when price moves {m:g}x the 14-bar ATR against the first entry"))
        for m in (1.5, 2.0, 3.0):
            out.append(Variant(f"Trailing stop {m:g}x ATR", "Trailing stop", "exits", m, stop=("trail", m),
                               describe=f"exit when price falls back {m:g}x ATR from the best price since entry"))
        mfe = [t.feats.get("mfe_pct") for t in tr]
        for p in (0.5, 0.75, 0.9):
            x = _q(mfe, p)
            if x == x and x > 0:
                out.append(Variant(f"Take profit at {x:.1%} (MFE p{int(p * 100)})", "Take profit", "exits", round(x, 5),
                                   tp=x, describe=f"take profit {x:.1%} from the entry: the {int(p * 100)}th percentile "
                                                  f"of how far training trips ran in their favour"))
        wins = [t.feats.get("hold_hours") for t in tr if t.net_actual > 0]
        for p in (0.5, 0.75, 0.9):
            h = _q(wins, p)
            if h == h and h > 0:
                out.append(Variant(f"Time stop {h:.1f}h", "Time stop", "exits", round(h, 3), time_h=h,
                                   describe=f"exit any trade still losing after {h:.1f}h ({int(p * 100)}% of training "
                                            f"winners were closed by then)"))
    if "sizing" in fam:
        notional = np.array([t.max_notional for t in trips])
        vol = np.array([t.vol_d for t in trips])
        c = np.nanmedian((notional * vol)[:k]) if np.isfinite(vol[:k]).any() else np.nan
        if c == c:
            s = np.where(np.isfinite(vol) & (vol > 0), c / vol / notional, 1.0)
            out.append(Variant("Volatility targeting", "Volatility targeting", "sizing", None, scale=_cap(s, trips, a.max_leverage_cap),
                               describe="size each trade inversely to the market's volatility at entry (same "
                                        "typical size as the trader's on the training window)"))
        eq = np.array([t.equity for t in trips])
        atr_pct = np.array([t.atr / t.leg_px[0] if t.atr == t.atr else np.nan for t in trips])
        for m in (1.5, 2.0, 3.0):
            risk = notional * m * atr_pct / eq
            c = np.nanmedian(risk[:k]) if np.isfinite(risk[:k]).any() else np.nan
            if c == c and c > 0:
                s = np.where(np.isfinite(atr_pct) & (atr_pct > 0) & (eq > 0), c * eq / (m * atr_pct) / notional, 1.0)
                out.append(Variant(f"Fixed-fractional risk, {m:g}x ATR stop", "Fixed-fractional risk", "sizing", m,
                                   scale=_cap(s, trips, a.max_leverage_cap), stop=("atr", m),
                                   describe=f"risk the same share of the account on every trade ({c:.2%}, the "
                                            f"trader's training median), with a {m:g}x ATR stop"))
        # capped Kelly from the previous 30 closed trips' returns on size (known at entry)
        ron = np.array([t.feats.get("return_on_notional", np.nan) for t in trips], float)
        close_ns = np.array([t.close_ts.value for t in trips])
        open_ns = np.array([t.open_ts.value for t in trips])
        order = np.argsort(close_ns)
        kel = np.full(n, np.nan)
        for t in trips:
            m = np.searchsorted(close_ns[order], open_ns[t.i], side="left")
            prev = ron[order[max(0, m - 30):m]]
            prev = prev[np.isfinite(prev)]
            if len(prev) >= 10 and prev.var() > 0:
                kel[t.i] = prev.mean() / prev.var()
        km = np.nanmedian(np.where(kel[:k] > 0, kel[:k], np.nan)) if np.isfinite(kel[:k]).any() else np.nan
        if km == km and km > 0:
            s = np.where(np.isfinite(kel), np.clip(kel / km, 0.25, 2.0), 1.0)
            out.append(Variant("Capped Kelly", "Capped Kelly", "sizing", None, scale=_cap(s, trips, a.max_leverage_cap),
                               describe="size by the edge of the previous 30 trips (Kelly fraction), between 0.25x "
                                        "and 2x the usual size"))
        out.append(Variant("No adding to losers", "No adding to losers", "sizing", None, drop_losing_adds=True,
                           describe="never add to a position that's losing; exits shrink in proportion"))
        net = np.array([t.net_actual for t in trips])
        for N in (2, 3, 4):
            s = np.ones(n)
            for t in trips:
                m = np.searchsorted(close_ns[order], open_ns[t.i], side="left")
                prev = net[order[max(0, m - N):m]]
                if len(prev) == N and (prev <= 0).all():
                    s[t.i] = 0.5
            out.append(Variant(f"Half size after {N} losses", "Smaller after losses", "sizing", N, scale=s,
                               describe=f"halve the size after {N} losing trades in a row, until a win"))
    if "execution" in fam:
        for off in (0.0, 0.0005, 0.002):
            nm = "Limit entry at their price" if off == 0 else f"Limit entry {off:.2%} better"
            out.append(Variant(nm, "Limit entries", "execution", off, limit_offset=off,
                               describe=f"enter with a limit order {off:.2%} better than the actual entry price, "
                                        f"filled only if price trades through it within 30 minutes (maker fee)"))
        fc = np.array([t.feats.get("funding_cost_at_entry", np.nan) for t in trips], float)
        if np.isfinite(fc).any():
            for thr in (0.2, 0.5, 1.0):
                if (np.nan_to_num(fc[:k], nan=-1) > thr).mean() > MAX_SKIP:
                    continue
                out.append(Variant(f"Skip when funding costs >{thr:.0%}/yr", "Skip expensive funding", "execution", thr,
                                   skip=np.nan_to_num(fc, nan=-1) > thr,
                                   describe=f"skip trades when the trade's side pays more than {thr:.0%} a year in funding"))
    if "selection" in fam:
        df = pd.DataFrame({"net": [t.net_actual for t in trips], "coin": [t.coin for t in trips],
                           "hour": [t.feats.get("hour") // 4 * 4 for t in trips],
                           "context": [t.feats.get("context") for t in trips],
                           "vol": [t.feats.get("vol_regime") for t in trips]})
        trn = df.iloc[:k]
        for col, name, min_n, what in (("coin", "Drop losing markets", 5, "markets"),
                                       ("hour", "Drop losing hours", 10, "entry hours (UTC)")):
            g = trn.groupby(col)["net"].agg(["size", "mean"])
            bad = g[(g["size"] >= min_n) & (g["mean"] < 0)].index.tolist()
            if bad and trn[col].isin(bad).mean() <= MAX_SKIP:
                lab = ", ".join(f"{b:02d}-{b + 4:02d}" if col == "hour" else str(b) for b in bad[:8])
                out.append(Variant(name, name, "selection", lab, skip=df[col].isin(bad).to_numpy(),
                                   describe=f"don't trade {what} that lost money on the training window: {lab}"))
        bad_mask = np.zeros(n, bool)
        labs = []
        for col in ("context", "vol"):
            g = trn.dropna(subset=[col]).groupby(col)["net"].agg(["size", "mean"])
            bad = g[(g["size"] >= 10) & (g["mean"] < 0)].index.tolist()
            if bad:
                bad_mask |= df[col].isin(bad).to_numpy()
                labs += [f"{'entry' if col == 'context' else 'volatility'} {b.lower()}" for b in bad]
        if labs and bad_mask[:k].mean() <= MAX_SKIP:
            out.append(Variant("Drop losing regimes", "Drop losing regimes", "selection", ", ".join(labs), skip=bad_mask,
                               describe=f"skip entries in market conditions that lost money on the training window: "
                                        f"{', '.join(labs)}"))
    return out


def _combine(best: dict[str, Variant], n: int) -> Variant | None:
    parts = [v for v in best.values()]
    if len(parts) < 2:
        return None
    c = Variant("Best combination", "Best combination", "combination", None,
                describe="combine " + "; ".join(v.name for v in parts))
    for v in parts:
        if v.scale is not None:
            c.scale = v.scale if c.scale is None else c.scale * v.scale
        if v.skip is not None:
            c.skip = v.skip if c.skip is None else (c.skip | v.skip)
        c.drop_losing_adds |= v.drop_losing_adds
        c.limit_offset = v.limit_offset if v.limit_offset is not None else c.limit_offset
        c.stop = v.stop or c.stop
        c.tp = v.tp if v.tp is not None else c.tp
        c.time_h = v.time_h if v.time_h is not None else c.time_h
    return c


def run(trips: list[Trip], rates: dict, a: cst.Assumptions, equity_ref: float, progress=None) -> dict:
    """The whole backtest. trips: replay.prepare output (oldest first)."""
    n = len(trips)
    res: dict = {"trips": n, "min_trips": MIN_TRIPS, "rates": rates, "assumptions": a.as_dict()}
    if n < MIN_TRIPS:
        res["status"] = "too_few_trips"
        return res
    k = val.split_index(n)
    res["split"] = {"train_trips": k, "test_trips": n - k, "test_from": trips[k].open_ts.isoformat()}
    res["golden"] = golden(trips, rates)
    res["bars_coverage"] = float(np.mean([len(t.bar_t) > 0 for t in trips]))
    actual = run_variant(trips, Variant(), rates)
    first = min(t.open_ts for t in trips).floor("D")
    last = max(t.close_ts for t in trips).floor("D")
    days = pd.date_range(first, last, freq="D", tz="UTC")
    test_start = trips[k].open_ts.floor("D")
    test_days = days[days >= test_start]
    train_days = days[days < test_start]
    is_test = np.array([t.i >= k for t in trips])
    eq_days = pd.Series(equity_ref, index=days)
    cands = candidates(trips, k, a)
    res["variants_tried"] = len(cands) + 1          # + the combination
    sims = {}
    for j, v in enumerate(cands):
        if progress:
            progress(0.05 + 0.75 * j / max(len(cands), 1), f"Replaying: {v.name}")
        sims[v.name] = run_variant(trips, v, rates)
    # choose the best parameter of each family on the training trips
    best_by_family: dict[str, Variant] = {}
    train_delta = {}
    for v in cands:
        d = float(sims[v.name]["net"].to_numpy()[~is_test].sum() - actual["net"].to_numpy()[~is_test].sum())
        train_delta[v.name] = d
        cur = best_by_family.get(v.family)
        if cur is None or d > train_delta[cur.name]:
            best_by_family[v.family] = v
    best_by_cat: dict[str, Variant] = {}
    for v in best_by_family.values():
        if train_delta[v.name] <= 0:
            continue
        cur = best_by_cat.get(v.category)
        if cur is None or train_delta[v.name] > train_delta[cur.name]:
            best_by_cat[v.category] = v
    combo = _combine(best_by_cat, n)
    if combo is not None:
        if progress:
            progress(0.82, "Replaying: best combination")
        sims[combo.name] = run_variant(trips, combo, rates)
        train_delta[combo.name] = float(sims[combo.name]["net"].to_numpy()[~is_test].sum() -
                                        actual["net"].to_numpy()[~is_test].sum())
    # validation on the test window
    if progress:
        progress(0.85, "Bootstrap and deflated Sharpe")
    a_test = val.daily(np.where(is_test, actual["net"], 0.0), actual["exit_ts"], test_days).to_numpy()
    eq_t = eq_days.reindex(test_days).to_numpy()
    idx = val.stationary_indices(len(test_days)) if len(test_days) >= 5 else None
    srs = []
    for name, s in sims.items():
        r = val.daily(np.where(is_test, s["net"], 0.0), s["exit_ts"], test_days).to_numpy() / eq_t
        if len(r) > 2 and r.std(ddof=1) > 0:
            srs.append(r.mean() / r.std(ddof=1))
    var_sr = float(np.var(srs, ddof=1)) if len(srs) > 1 else 0.0
    chosen = list(best_by_family.values()) + ([combo] if combo is not None else [])
    findings = []
    for v in chosen:
        s = sims[v.name]
        v_test = val.daily(np.where(is_test, s["net"], 0.0), s["exit_ts"], test_days).to_numpy()
        boot = val.paired_bootstrap(v_test, a_test, eq_t, idx) if idx is not None else \
            {"delta": float(v_test.sum() - a_test.sum()), "delta_ci": (np.nan, np.nan), "sharpe_delta_ci": (np.nan, np.nan),
             "sharpe_actual": np.nan, "sharpe_variant": np.nan, "p_better": np.nan}
        dsr = val.deflated_sharpe(v_test / eq_t, res["variants_tried"], var_sr)
        affected = int(((s["net"].to_numpy() != actual["net"].to_numpy()) & is_test).sum())
        skipped = int(((s["reason"] == "skipped").to_numpy() & is_test).sum())
        reasons = s.loc[is_test, "reason"].value_counts().to_dict()
        findings.append({
            "name": v.name, "family": v.family, "category": v.category, "describe": v.describe,
            "param": v.param if not isinstance(v.param, float) else round(v.param, 6),
            "train_delta": train_delta[v.name], "test_delta": boot["delta"], "test_delta_ci": boot["delta_ci"],
            "sharpe_actual": boot["sharpe_actual"], "sharpe_variant": boot["sharpe_variant"],
            "sharpe_delta_ci": boot["sharpe_delta_ci"], "p_better": boot["p_better"], "dsr": dsr,
            "label": val.label(boot["delta_ci"], dsr, n - k) if boot["delta_ci"][0] == boot["delta_ci"][0] else "Low",
            "test_trips_affected": affected, "test_skipped_share": skipped / max(n - k, 1), "reasons": {str(kk): int(vv) for kk, vv in reasons.items() if kk is not None},
            "daily": val.daily(s["net"].to_numpy(), s["exit_ts"], days).round(2).tolist()})
    findings.sort(key=lambda f: -f["test_delta"])
    res["findings"] = findings
    res["actual_daily"] = val.daily(actual["net"].to_numpy(), actual["exit_ts"], days).round(2).tolist()
    res["days"] = [d.isoformat() for d in days]
    res["test_start"] = test_start.isoformat()
    res["actual_test"] = float(a_test.sum())
    res["actual_train"] = float(val.daily(np.where(~is_test, actual["net"], 0.0), actual["exit_ts"], train_days).sum()) \
        if len(train_days) else 0.0
    res["equity_ref"] = equity_ref
    res["status"] = "done"
    return res


def summary(findings: list[dict], k: int = 5) -> list[dict]:
    """The 3-5 highest-impact findings for the summary card: biggest
    out-of-sample improvement first, Low-confidence ones after the others."""
    rank = {"High": 0, "Medium": 1, "Low": 2}
    # only rules that would have been adopted: chosen because they helped the training window
    good = [f for f in findings if f["train_delta"] > 0 and f["test_delta"] > 0]
    good.sort(key=lambda f: (rank[f["label"]], -f["test_delta"]))
    return good[:k]


def clean(x):
    """JSON-safe copy (NaN -> None, numpy -> Python)."""
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (np.floating, float)):
        return None if not math.isfinite(float(x)) else float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x
