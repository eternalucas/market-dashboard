"""Counterfactual replay: the trader's real entries (time, market, side),
replayed on historical prices with one thing changed.

Every trip is replayed from its own fill legs (see reconstruct legs=True),
so an unchanged replay reproduces the actual result exactly: cash flow of
the legs - their fees + the funding the account was paid or charged.

What a variant can change
- size: a scale factor per trip (sizing rules), applied to every leg
- adds: drop adds made while the position was losing (averaging down);
  later exits shrink in proportion so the trip still ends flat
- entry: a limit order at an offset from the actual first entry price,
  filled only if a later bar traded through it within a time window
  (otherwise the trip is skipped); filled trips pay the maker fee
- exits: rules laid over the trader's own exits (the earliest wins):
  stop at a % or ATR distance from the first entry, trailing stop, take
  profit, time stop. The trader's own exits before the trigger still happen.
- selection: skip trips (markets, hours, regimes, expensive funding)

Intrabar order (candles don't say what came first): a time exit happens at
the bar's open; then the stop is assumed to fill before the target; a stop
gapped through fills at the bar's open. Only bars that start after the
entry are used, so nothing inside the entry's own bar is assumed.
No look-ahead: every rule input (ATR, volatility, previous trips' results,
thresholds learned on the training window) is known at the entry.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import costs as cst
from .market import INTERVAL_TD, Prices

NS_H = 3_600 * 10**9


@dataclass
class Trip:
    i: int
    trip_id: str
    coin: str
    side: int
    open_ts: pd.Timestamp
    close_ts: pd.Timestamp
    leg_t: np.ndarray            # int64 ns
    leg_q: np.ndarray            # signed qty
    leg_px: np.ndarray
    leg_fee: np.ndarray
    pay_t: np.ndarray            # funding payments (int64 ns), usdc, actual position then
    pay_usdc: np.ndarray
    pay_pos: np.ndarray
    net_actual: float
    max_notional: float
    equity: float = np.nan
    bar_t: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.int64))
    bar_o: np.ndarray = field(default_factory=lambda: np.array([]))
    bar_h: np.ndarray = field(default_factory=lambda: np.array([]))
    bar_l: np.ndarray = field(default_factory=lambda: np.array([]))
    bar_c: np.ndarray = field(default_factory=lambda: np.array([]))
    interval: str | None = None
    exit_bar_close: float = np.nan     # close of the bar holding the last exit (candle-only check)
    atr: float = np.nan                # ATR(14) in price, from context bars closed before entry
    vol_d: float = np.nan              # daily volatility at entry (fraction)
    minute_notional: float = np.nan    # average traded notional per minute before entry
    feats: dict = field(default_factory=dict)


def _context(prices: Prices, coin: str, ts: pd.Timestamp) -> tuple[float, float, float]:
    civ, cb = prices.context(coin)
    if civ is None:
        return np.nan, np.nan, np.nan
    step = INTERVAL_TD[civ]
    k = cb.index.searchsorted(ts - step, side="right")      # bars closed by the entry: [0, k)
    if k < 15:
        return np.nan, np.nan, np.nan
    b = cb.iloc[max(0, k - 30):k]
    prev_c = b["c"].shift(1)
    tr = np.maximum(b["h"] - b["l"], np.maximum((b["h"] - prev_c).abs(), (b["l"] - prev_c).abs()))
    atr = float(tr.iloc[-14:].mean())
    per_day = max(int(pd.Timedelta(days=1) / step), 1)
    lr = np.log(cb["c"].iloc[max(0, k - per_day - 1):k]).diff().dropna()
    vol_d = float(lr.std() * np.sqrt(per_day)) if len(lr) >= 3 else np.nan
    mins = step / pd.Timedelta(minutes=1)
    vn = (b["v"] * b["c"]).iloc[-20:]
    minute_notional = float(vn.mean() / mins) if len(vn) else np.nan
    return atr, vol_d, minute_notional


def prepare(trips: pd.DataFrame, funding: pd.DataFrame, prices: Prices, equity: pd.Series | None = None,
            enriched: pd.DataFrame | None = None) -> list[Trip]:
    """Closed, fully observed trips (with legs) -> Trip objects, oldest first."""
    c = trips[~trips["is_open"].astype(bool) & ~trips["exit_unobserved"].astype(bool)].sort_values("open_ts")
    feats = enriched.set_index("trip_id") if enriched is not None and not enriched.empty else None
    out = []
    fund_by_coin = {k: g.sort_values("ts") for k, g in funding.groupby("coin")} if funding is not None and not funding.empty else {}
    for i, r in enumerate(c.itertuples(index=False)):
        legs = r.legs
        lt = pd.to_datetime([x[0] for x in legs], utc=True).as_unit("ns").asi8
        lq = np.array([x[1] for x in legs], float)
        lp = np.array([x[2] for x in legs], float)
        lf = np.array([x[3] for x in legs], float)
        side = 1 if r.side == "Long" else -1
        o_ns, c_ns = r.open_ts.value, r.close_ts.value
        pt, pu, pp = np.array([], np.int64), np.array([]), np.array([])
        g = fund_by_coin.get(r.coin)
        if g is not None:
            ts = pd.DatetimeIndex(pd.to_datetime(g["ts"], utc=True)).as_unit("ns").asi8
            m = (ts > o_ns) & (ts <= c_ns)
            if "position_size" in g:
                ps = g["position_size"].to_numpy()
                m &= ~((ps == ps) & (ps != 0) & (np.sign(ps) != side))
            pt, pu = ts[m], g["usdc"].to_numpy()[m]
            cum = np.cumsum(lq)
            k = np.searchsorted(lt, pt, side="right") - 1
            pp = np.where(k >= 0, cum[np.clip(k, 0, None)], 0.0)
        e = np.nan
        if equity is not None and not equity.empty:
            day = r.open_ts.floor("D")
            e = equity.get(day - pd.Timedelta(days=1), np.nan)
            if e != e:
                e = equity.get(day, np.nan)
        t = Trip(i=i, trip_id=r.trip_id, coin=r.coin, side=side, open_ts=r.open_ts, close_ts=r.close_ts,
                 leg_t=lt, leg_q=lq, leg_px=lp, leg_fee=lf, pay_t=pt, pay_usdc=pu, pay_pos=pp,
                 net_actual=float(r.net), max_notional=float(r.max_notional), equity=float(e))
        iv, bars = prices.finest(r.coin, r.open_ts, r.close_ts)
        if iv is not None:
            step = INTERVAL_TD[iv]
            last = bars[bars.index <= r.close_ts]
            if len(last):
                hold = last[last.index > r.close_ts - step]
                t.exit_bar_close = float(hold["c"].iloc[-1]) if len(hold) else np.nan
            b = bars[(bars.index >= r.open_ts) & (bars.index < r.close_ts)]   # bars that start after the entry
            t.bar_t = b.index.as_unit("ns").asi8
            t.bar_o, t.bar_h, t.bar_l, t.bar_c = (b[k].to_numpy(float) for k in ("o", "h", "l", "c"))
            t.interval = iv
        t.atr, t.vol_d, t.minute_notional = _context(prices, r.coin, r.open_ts)
        if feats is not None and r.trip_id in feats.index:
            f = feats.loc[r.trip_id]
            t.feats = {k: f.get(k) for k in ("context", "vol_regime", "funding_cost_at_entry", "mfe_pct", "hold_hours")}
        t.feats["hour"] = r.open_ts.hour
        t.feats["return_on_notional"] = float(r.net) / float(r.max_notional) if r.max_notional else np.nan
        out.append(t)
    return out


# --------------------------------------------------------------------------- #
# One trip under one variant
# --------------------------------------------------------------------------- #

@dataclass
class Variant:
    name: str = "Actual (no change)"
    family: str = "actual"
    category: str = "actual"
    param: object = None
    scale: np.ndarray | None = None        # per trip (index = Trip.i)
    skip: np.ndarray | None = None         # per trip bool
    drop_losing_adds: bool = False
    limit_offset: float | None = None
    limit_window_min: float = 30
    stop: tuple | None = None              # ("pct", d) | ("atr", k) | ("trail", k)
    tp: float | None = None                # take profit, fraction from first entry
    time_h: float | None = None            # exit if still losing after this many hours
    describe: str = ""


def _taker_fee(notional, rates):
    return abs(notional) * rates["taker_bps"] / 1e4


def simulate(t: Trip, v: Variant, rates: dict) -> dict:
    """{"net", "exit_ns", "reason", "simulated"}; reason: None (unchanged
    exits), "stop", "tp", "time", "liquidated", "skipped", "not filled"."""
    if v.skip is not None and v.skip[t.i]:
        return {"net": 0.0, "exit_ns": t.close_ts.value, "reason": "skipped", "simulated": True}
    lt, lq, lp, lf = t.leg_t.copy(), t.leg_q.copy(), t.leg_px.copy(), t.leg_fee.copy()
    side = t.side
    changed = False
    # 1. limit entry
    if v.limit_offset is not None:
        if not len(t.bar_t):
            return {"net": t.net_actual, "exit_ns": t.close_ts.value, "reason": None, "simulated": False}
        lim = lp[0] * (1 - side * v.limit_offset)
        win = (t.bar_t >= lt[0]) & (t.bar_t < lt[0] + int(v.limit_window_min * 60e9))
        hit = win & ((t.bar_l < lim) if side > 0 else (t.bar_h > lim))
        if not hit.any():
            return {"net": 0.0, "exit_ns": t.close_ts.value, "reason": "not filled", "simulated": True}
        j = int(np.argmax(hit))
        step = (t.bar_t[1] - t.bar_t[0]) if len(t.bar_t) > 1 else int(60e9)
        fill_t = t.bar_t[j] + step
        if len(lt) > 1 and lt[1] < fill_t:          # the trader acted again before our order filled
            return {"net": 0.0, "exit_ns": t.close_ts.value, "reason": "not filled", "simulated": True}
        lp[0], lt[0] = lim, fill_t
        lf[0] = abs(lq[0]) * lim * rates["maker_bps"] / 1e4
        changed = True
    # 2. size
    s = 1.0 if v.scale is None else float(v.scale[t.i])
    if s != 1.0:
        lq, lf, changed = lq * s, lf * s, True
    # 3. no adding to losers
    if v.drop_losing_adds and len(lq) > 2:
        keep = np.ones(len(lq), bool)
        pos, cost = lq[0], lq[0] * lp[0]
        for k in range(1, len(lq)):
            if np.sign(lq[k]) == side and abs(pos + lq[k]) > abs(pos):
                avg = cost / pos
                if (lp[k] - avg) * side < 0:
                    keep[k] = False
                    continue
                cost += lq[k] * lp[k]
            else:
                cost += lq[k] * (cost / pos) if pos else 0.0
            pos += lq[k]
        if not keep.all():
            changed = True
            entries = (np.sign(lq) == side)
            e_old, e_new = lq[entries].sum(), lq[entries & keep].sum()
            f = e_new / e_old if e_old else 1.0
            lt, lq, lp, lf = lt[keep], lq[keep].copy(), lp[keep], lf[keep].copy()
            ex = np.sign(lq) != side
            lq[ex] *= f
            lf[ex] *= f
            # keep the position from crossing zero; close any remainder on the last leg
            pos = 0.0
            for k in range(len(lq)):
                if np.sign(lq[k]) != side and abs(lq[k]) > abs(pos):
                    lf[k] *= abs(pos) / abs(lq[k]) if lq[k] else 1
                    lq[k] = -pos
                pos += lq[k]
            if abs(pos) > 1e-12:
                lq[-1] -= pos
    # 4. exits laid over the trader's own
    reason, exit_ns = None, int(lt[-1])
    rules = v.stop is not None or v.tp is not None or v.time_h is not None or s > 1.0
    if rules and len(t.bar_t):
        sel = (t.bar_t >= lt[0]) & (t.bar_t < lt[-1])
        bt, bo, bh, bl = t.bar_t[sel], t.bar_o[sel], t.bar_h[sel], t.bar_l[sel]
        if len(bt):
            p0 = lp[0]
            n = len(bt)
            cand = []                                   # (bar index, priority, kind, fill price)
            adverse = bl if side > 0 else bh
            favour = bh if side > 0 else bl
            if v.stop is not None:
                kind, val = v.stop
                if kind == "pct":
                    stop = np.full(n, p0 * (1 - side * val))
                elif kind == "atr":
                    stop = np.full(n, p0 - side * val * t.atr) if t.atr == t.atr else None
                else:                                   # trailing: from the best price up to the previous bar
                    if t.atr == t.atr:
                        best = np.maximum.accumulate(np.r_[p0, favour[:-1]]) if side > 0 else \
                            np.minimum.accumulate(np.r_[p0, favour[:-1]])
                        best = np.maximum(best, p0) if side > 0 else np.minimum(best, p0)
                        stop = best - side * val * t.atr
                    else:
                        stop = None
                if stop is not None:
                    hit = (adverse <= stop) if side > 0 else (adverse >= stop)
                    if hit.any():
                        j = int(np.argmax(hit))
                        fill = min(bo[j], stop[j]) if side > 0 else max(bo[j], stop[j])
                        cand.append((j, abs(stop[j] - p0) / p0, "stop", fill))
            if s > 1.0 and t.equity == t.equity and t.equity > 0:
                lev = float(np.abs(np.cumsum(lq)).max()) * p0 / t.equity
                liq = cst.liquidation_price(p0, side, lev, t.coin)
                if liq == liq:
                    hit = (adverse <= liq) if side > 0 else (adverse >= liq)
                    if hit.any():
                        j = int(np.argmax(hit))
                        cand.append((j, abs(liq - p0) / p0, "liquidated", liq))
            if v.tp is not None:
                tp = p0 * (1 + side * v.tp)
                hit = (favour >= tp) if side > 0 else (favour <= tp)
                if hit.any():
                    j = int(np.argmax(hit))
                    fill = max(bo[j], tp) if side > 0 else min(bo[j], tp)
                    cand.append((j, 10.0, "tp", fill))          # stop first when both in one bar
            if v.time_h is not None:
                due = bt >= lt[0] + int(v.time_h * NS_H)
                if due.any():
                    j0 = int(np.argmax(due))
                    for j in range(j0, n):              # still losing at the bar's open?
                        before = lt < bt[j]
                        pos = lq[before].sum()
                        if abs(pos) < 1e-12:
                            break
                        ent = before & (np.sign(lq) == side)
                        avg = (lq[ent] * lp[ent]).sum() / lq[ent].sum()
                        if (bo[j] - avg) * side < 0:
                            cand.append((j, -1.0, "time", bo[j]))      # at the open, before anything else
                        break
            if cand:
                j, _, kind, fill = sorted(cand, key=lambda x: (x[0], x[1]))[0]
                keep = lt < bt[j]
                pos = lq[keep].sum()
                if abs(pos) > 1e-12:
                    notional = abs(pos) * fill
                    if kind == "tp":
                        fee = notional * rates["maker_bps"] / 1e4
                    else:
                        slip = cst.slippage_bps(notional, t.minute_notional, rates) / 1e4
                        fill = fill * (1 - side * slip)
                        fee = _taker_fee(abs(pos) * fill, rates)
                    lt = np.r_[lt[keep], bt[j]]
                    lq = np.r_[lq[keep], -pos]
                    lp = np.r_[lp[keep], fill]
                    lf = np.r_[lf[keep], fee]
                    reason, exit_ns, changed = kind, int(bt[j]), True
    cash = float(-(lq * lp).sum())
    fees = float(lf.sum())
    if len(t.pay_t):
        if changed:
            cum = np.cumsum(lq)
            k = np.searchsorted(lt, t.pay_t, side="right") - 1
            sim_pos = np.where(k >= 0, cum[np.clip(k, 0, None)], 0.0)
            ratio = np.where(np.abs(t.pay_pos) > 0, sim_pos / np.where(t.pay_pos == 0, 1, t.pay_pos), 0.0)
            funding = float((t.pay_usdc * ratio).sum())
        else:
            funding = float(t.pay_usdc.sum())
    else:
        funding = 0.0
    return {"net": cash - fees + funding, "exit_ns": exit_ns, "reason": reason, "simulated": changed}


def run_variant(trips: list[Trip], v: Variant, rates: dict) -> pd.DataFrame:
    rows = [simulate(t, v, rates) for t in trips]
    df = pd.DataFrame(rows)
    df["exit_ts"] = pd.to_datetime(df["exit_ns"], utc=True)
    return df


def golden(trips: list[Trip], rates: dict) -> dict:
    """No-change replay vs the actual result; plus a candle-only replay
    (exits at the close of the bar holding the actual exit)."""
    sim = run_variant(trips, Variant(), rates)
    actual = np.array([t.net_actual for t in trips])
    tot_a, tot_s = float(actual.sum()), float(sim["net"].sum())
    err = abs(tot_s - tot_a) / abs(tot_a) if tot_a else (0.0 if abs(tot_s) < 1e-6 else np.inf)
    # candle-only: replace every exit leg's price with its bar's close (only the final exit)
    cand = []
    for t in trips:
        if t.exit_bar_close == t.exit_bar_close:
            q, p = t.leg_q.copy(), t.leg_px.copy()
            p[-1] = t.exit_bar_close
            cand.append((t, float(-(q * p).sum() - t.leg_fee.sum() + t.pay_usdc.sum())))
    c_err = np.nan
    if cand:
        a = sum(t.net_actual for t, _ in cand)
        s = sum(x for _, x in cand)
        c_err = abs(s - a) / sum(abs(t.net_actual) for t, _ in cand) if a or s else 0.0
    return {"actual": tot_a, "replay": tot_s, "error": err, "per_trip_max_abs": float(np.abs(sim["net"] - actual).max()) if len(actual) else 0.0,
            "candle_only_error_vs_gross": c_err, "candle_only_trips": len(cand), "pass": bool(err <= 0.01)}
