"""Rule extraction (experimental): can the trader's entries be written as a
simple rule on market features, and does that rule make money by itself?

1. Features at the close of every context bar (1 hour where available) of
   the markets they trade: returns over the last 5, 15, 60 and 240 bars,
   the ATR's percentile over the last 240 bars, the last settled funding
   rate, and the hour (UTC). Only data up to that bar's close is used.
2. Label: did the trader open a trip (long or short) on that market during
   the next bar?
3. Search, on the training window only: one or two thresholds (AND), at the
   training deciles, scored by F1 against the trader's entries, separately
   for longs and shorts.
4. The best rule is traded on its own: enter at the next bar's open, hold
   for the trader's median holding time, one position per market at a time,
   taker fee plus half the spread each way. Reported on the test window
   next to the trader's own trips, as return on position size.
"Doesn't reproduce their edge" is a valid result and is reported as such.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .market import INTERVAL_TD, Prices

FEATURES = {"ret_5": "return, last 5 bars", "ret_15": "return, last 15 bars", "ret_60": "return, last 60 bars",
            "ret_240": "return, last 240 bars", "atr_pct": "ATR percentile (240 bars)", "funding": "funding rate",
            "hour": "hour (UTC)"}
QUANTILES = np.arange(0.1, 0.91, 0.1)
MATCH_F1 = 0.2                     # below this on the test window, the rule doesn't describe their entries


def features(prices: Prices, coin: str) -> tuple[str | None, pd.DataFrame]:
    civ, b = prices.context(coin)
    if civ is None:
        return None, pd.DataFrame()
    step = INTERVAL_TD[civ]
    c = b["c"]
    f = pd.DataFrame(index=b.index + step)             # known at the bar's close
    for n in (5, 15, 60, 240):
        f[f"ret_{n}"] = np.log(c / c.shift(n)).to_numpy()
    prev = c.shift(1)
    tr = np.maximum(b["h"] - b["l"], np.maximum((b["h"] - prev).abs(), (b["l"] - prev).abs()))
    atr = tr.rolling(14).mean()
    f["atr_pct"] = atr.rolling(240, min_periods=60).rank(pct=True).to_numpy()
    fs = prices.funding.get(coin)
    if fs is not None and len(fs):
        k = fs.index.searchsorted(f.index, side="right") - 1
        vals = np.where(k >= 0, fs.to_numpy()[np.clip(k, 0, None)], np.nan)
        stale = np.where(k >= 0, (f.index - fs.index[np.clip(k, 0, None)]) > pd.Timedelta(hours=2), True)
        f["funding"] = np.where(stale, np.nan, vals)
    else:
        f["funding"] = np.nan
    f["hour"] = f.index.hour
    f["o_next"] = b["o"].shift(-1).to_numpy()           # entry price for the rule: next bar's open
    f["coin"] = coin
    return civ, f


def _labels(f: pd.DataFrame, trips: pd.DataFrame, step: pd.Timedelta, side: str) -> np.ndarray:
    t = pd.DatetimeIndex(trips[(trips["coin"] == f["coin"].iloc[0]) & (trips["side"] == side)]["open_ts"])
    if t.empty:
        return np.zeros(len(f), bool)
    k = f.index.searchsorted(t, side="right") - 1               # the bar close at or before the entry
    y = np.zeros(len(f), bool)
    ok = (k >= 0) & ((t - f.index[np.clip(k, 0, None)]) < step)
    y[k[ok]] = True
    return y


def _f1(pred: np.ndarray, y: np.ndarray) -> float:
    tp = (pred & y).sum()
    if tp == 0:
        return 0.0
    p, r = tp / pred.sum(), tp / y.sum()
    return float(2 * p * r / (p + r))


def _conds(X: pd.DataFrame, train: np.ndarray) -> list[tuple]:
    out = []
    for col in FEATURES:
        x = X[col].to_numpy(float)
        tx = x[train & np.isfinite(x)]
        if len(tx) < 50 or np.nanstd(tx) == 0:
            continue
        for q in QUANTILES:
            thr = float(np.quantile(tx, q))
            out.append((col, ">=", thr))
            out.append((col, "<=", thr))
    return out


def _mask(X: pd.DataFrame, cond: tuple) -> np.ndarray:
    col, op, thr = cond
    x = X[col].to_numpy(float)
    with np.errstate(invalid="ignore"):
        return np.where(np.isfinite(x), x >= thr if op == ">=" else x <= thr, False)


def describe(rule: tuple, side: str) -> str:
    parts = []
    for col, op, thr in rule:
        if col.startswith("ret_"):
            v = f"{thr:+.2%}"
        elif col == "funding":
            v = f"{thr * 100:.4f}%/h"
        elif col == "atr_pct":
            v = f"{thr:.0%}"
        else:
            v = f"{thr:.0f}"
        parts.append(f"{FEATURES[col]} {'at least' if op == '>=' else 'at most'} {v}")
    return f"{side} when " + " and ".join(parts)


def extract(trips: pd.DataFrame, prices: Prices, coins: list[str], test_start: pd.Timestamp, rates: dict) -> dict:
    """trips: closed trips (open_ts, coin, side, hold_hours, max_notional,
    return_on_notional). Returns the best rule per side and its results."""
    frames, ivs = [], set()
    for coin in coins:
        civ, f = features(prices, coin)
        if civ is None or f.empty:
            continue
        ivs.add(civ)
        step = INTERVAL_TD[civ]
        f["y_Long"] = _labels(f, trips, step, "Long")
        f["y_Short"] = _labels(f, trips, step, "Short")
        f["step_h"] = step / pd.Timedelta(hours=1)
        frames.append(f)
    if not frames:
        return {"status": "no_data", "message": "No candles for the traded markets."}
    X = pd.concat(frames)
    train = (X.index < test_start)
    test = ~train
    train_trips = trips[trips["open_ts"] < test_start]
    test_trips = trips[trips["open_ts"] >= test_start]
    hold_h = float(train_trips["hold_hours"].median()) if len(train_trips) else np.nan
    cost = 2 * (rates["taker_bps"] + rates["half_spread_bps"]) / 1e4
    tn = test_trips["max_notional"].sum() if len(test_trips) else 0
    out = {"status": "done", "interval": ",".join(sorted(ivs)), "hold_hours": hold_h, "rules": [],
           "rules_tried": 0, "trader_test_trips": int(len(test_trips)),
           # per dollar traded (net / size), so a few big trades count as much as they did
           "trader_test_ron": float((test_trips["return_on_notional"] * test_trips["max_notional"]).sum() / tn)
           if tn else np.nan}
    conds = _conds(X, train)
    for side in ("Long", "Short"):
        y = X[f"y_{side}"].to_numpy()
        if y[train].sum() < 10:
            continue
        masks = {c: _mask(X, c) for c in conds}
        scored = sorted(((_f1(m[train], y[train]), (c,)) for c, m in masks.items()), reverse=True)
        top = [r[0] for _, r in scored[:15]]
        pairs = []
        for i in range(len(top)):
            for j in range(i + 1, len(top)):
                if top[i][0] == top[j][0]:
                    continue
                m = masks[top[i]] & masks[top[j]]
                pairs.append((_f1(m[train], y[train]), (top[i], top[j])))
        out["rules_tried"] += len(scored) + len(pairs)
        best_f1, best = max(scored[:1] + pairs, key=lambda x: x[0]) if scored else (0, None)
        if best is None or best_f1 <= 0:
            continue
        m = np.ones(len(X), bool)
        for c in best:
            m &= masks[c]
        res = {"side": side, "rule": [list(c) for c in best], "text": describe(best, side), "train_f1": best_f1,
               "test_f1": _f1(m[test], y[test]) if y[test].sum() else np.nan}
        for name, part in (("train", train), ("test", test)):
            r = _trade(X[part], m[part], side, hold_h, cost)
            res[f"{name}_trades"], res[f"{name}_ron"], res[f"{name}_total_ron"] = len(r), \
                float(np.mean(r)) if len(r) else np.nan, float(np.sum(r)) if len(r) else 0.0
        out["rules"].append(res)
    if not out["rules"]:
        out["status"] = "no_rule"
        out["message"] = "Too few entries in the training window to search for a rule."
        return out
    best = max(out["rules"], key=lambda r: r["train_f1"])
    tr_ron = out["trader_test_ron"]
    f1 = best["test_f1"] if best["test_f1"] == best["test_f1"] else 0.0
    profitable = best["test_trades"] >= 10 and best["test_ron"] == best["test_ron"] and best["test_ron"] > 0
    if f1 < MATCH_F1:
        reproduces = False
        out["verdict"] = (f"No simple rule describes their entries: the closest one matches them poorly on the test "
                          f"window (F1 {f1:.2f}; 1 would be a perfect match), so their edge isn't captured by these "
                          f"features." + (" The closest rule did make money on its own, but it isn't what they do."
                                          if profitable else ""))
    else:
        reproduces = profitable and (tr_ron != tr_ron or best["test_ron"] >= 0.5 * max(tr_ron, 0))
        out["verdict"] = ("The rule matches their entries and earns at least half their return per dollar on the "
                          "test window: part of their edge can be written down as this rule." if reproduces else
                          "The rule matches their entries but doesn't earn what they earn on the test window: their "
                          "edge comes from something else (timing within the hour, exits or sizing).")
    out["reproduces"] = bool(reproduces)
    return out


def _trade(X: pd.DataFrame, signal: np.ndarray, side: str, hold_h: float, cost: float) -> list[float]:
    """Return on size of each rule trade: next bar's open to the open
    hold_h later; one position per market at a time."""
    if hold_h != hold_h:
        return []
    sgn = 1 if side == "Long" else -1
    out = []
    for coin, g in X.groupby("coin", sort=False):
        sig = signal[(X["coin"] == coin).to_numpy()]
        o = g["o_next"].to_numpy()
        step_h = float(g["step_h"].iloc[0])
        n_hold = max(int(round(hold_h / step_h)), 1)
        i = 0
        while i < len(g) - n_hold:
            if sig[i] and np.isfinite(o[i]) and np.isfinite(o[i + n_hold]) and o[i] > 0:
                out.append(sgn * (o[i + n_hold] / o[i] - 1) - cost)
                i += n_hold
            else:
                i += 1
    return out
