"""Backtest & Improve (Phase 3): golden replay, look-ahead, intrabar rules,
execution and sizing variants, validation statistics and rule extraction."""
import numpy as np
import pandas as pd
import pytest

from src.review import backtest as bt
from src.review import costs as cst
from src.review import market as mk
from src.review import replay as rp
from src.review import rules as rl
from src.review import validation as val
from src.review.reconstruct import reconstruct

RATES = {"taker_bps": 4.5, "maker_bps": 1.5, "half_spread_bps": 1.0, "impact_k_bps": 0.0}
T0 = pd.Timestamp("2026-03-01 00:00", tz="UTC")


def mk_trip(legs, bars=None, side=1, coin="BTC", atr=1.0, pays=(), equity=1e6, i=0):
    """legs: (minutes after T0, signed qty, px, fee). bars: list of (minute, o, h, l, c), 1-minute."""
    lt = np.array([(T0 + pd.Timedelta(minutes=m)).value for m, *_ in legs], np.int64)
    lq = np.array([q for _, q, _, _ in legs], float)
    lp = np.array([p for _, _, p, _ in legs], float)
    lf = np.array([f for *_, f in legs], float)
    net = float(-(lq * lp).sum() - lf.sum())
    pt = np.array([(T0 + pd.Timedelta(minutes=m)).value for m, _ in pays], np.int64)
    pu = np.array([u for _, u in pays], float)
    cum = np.cumsum(lq)
    k = np.searchsorted(lt, pt, side="right") - 1
    pp = np.where(k >= 0, cum[np.clip(k, 0, None)], 0.0)
    t = rp.Trip(i=i, trip_id=f"{coin}:{i}", coin=coin, side=side, open_ts=pd.Timestamp(lt[0], tz="UTC"),
                close_ts=pd.Timestamp(lt[-1], tz="UTC"), leg_t=lt, leg_q=lq, leg_px=lp, leg_fee=lf,
                pay_t=pt, pay_usdc=pu, pay_pos=pp, net_actual=net + float(pu.sum()),
                max_notional=float(np.abs(cum).max() * lp[0]), equity=equity, atr=atr, vol_d=0.02,
                minute_notional=1e9)
    if bars:
        t.bar_t = np.array([(T0 + pd.Timedelta(minutes=m)).value for m, *_ in bars], np.int64)
        t.bar_o, t.bar_h, t.bar_l, t.bar_c = (np.array([b[j] for b in bars], float) for j in (1, 2, 3, 4))
        t.interval = "1m"
    return t


def flat_bars(n, px=100.0, start=1):
    return [(start + m, px, px, px, px) for m in range(n)]


# --------------------------------------------------------------------------- #
# Golden
# --------------------------------------------------------------------------- #

def test_golden_no_change_replay_matches_actual_exactly_from_fills():
    """Random fills -> reconstruct (legs) -> replay with no change == the
    venue's realized P&L - fees + funding, trip by trip."""
    rng = np.random.default_rng(1)
    rows, pos, fid, px = [], 0.0, 0, 100.0
    t = pd.Timestamp("2026-02-01", tz="UTC")
    cost = 0.0
    for _ in range(400):
        t += pd.Timedelta(minutes=int(rng.integers(1, 120)))
        px *= float(np.exp(rng.normal(0, 0.01)))
        dq = float(rng.choice([-1, 1]) * rng.integers(1, 4))
        if rng.random() < 0.2:
            dq = -pos if pos else dq                         # back to flat now and then
        if rng.random() < 0.05 and pos:
            dq = -2 * pos                                    # flip in one fill
        if dq == 0:
            continue
        start = pos
        pnl = 0.0
        if pos and np.sign(dq) != np.sign(pos):
            closing = min(abs(dq), abs(pos)) * np.sign(dq)
            avg = cost / pos
            pnl = (px - avg) * -closing
            cost += avg * closing
            if abs(dq) > abs(pos):
                cost = px * (dq + pos)
        else:
            cost += px * dq
        pos += dq
        if abs(pos) < 1e-12:
            pos, cost = 0.0, 0.0
        fid += 1
        rows.append({"coin": "BTC", "ts": t, "side": "B" if dq > 0 else "A", "px": px, "sz": abs(dq),
                     "start_position": start, "closed_pnl": pnl, "fee": abs(dq) * px * 4.5e-4, "is_taker": 1,
                     "fill_id": str(fid), "liq_role": None})
    fills = pd.DataFrame(rows)
    hours = pd.date_range(fills["ts"].min().ceil("h"), fills["ts"].max(), freq="1h", tz="UTC")
    # funding: the position held at each hour pays 0.01% of its notional
    pos_at = np.interp(hours.asi8, fills["ts"].astype("datetime64[ns, UTC]").astype("int64"),
                       (fills["start_position"] + np.where(fills["side"] == "B", fills["sz"], -fills["sz"])).to_numpy(),
                       left=0)
    funding = pd.DataFrame({"coin": "BTC", "ts": hours, "usdc": -pos_at * 100 * 1e-4, "position_size": pos_at})
    funding = funding[funding["position_size"] != 0]
    trips, _ = reconstruct(fills, funding, legs=True)
    T = rp.prepare(trips, funding, mk.Prices())
    g = rp.golden(T, RATES)
    assert len(T) > 30
    assert g["pass"] and g["error"] < 1e-9 and g["per_trip_max_abs"] < 1e-6


# --------------------------------------------------------------------------- #
# Intrabar and execution rules
# --------------------------------------------------------------------------- #

def test_stop_fills_before_target_in_the_same_bar():
    bars = flat_bars(5) + [(6, 100, 106, 94, 100)] + flat_bars(10, start=7)
    t = mk_trip([(0, 1, 100, 0), (30, -1, 100, 0)], bars)
    r = rp.simulate(t, rp.Variant(stop=("pct", 0.05), tp=0.05), RATES)
    assert r["reason"] == "stop"
    fill = 95 * (1 - 1e-4)                                             # half spread on the stop order
    assert r["net"] == pytest.approx(fill - 100 - fill * 4.5e-4, rel=1e-9)


def test_a_gap_through_the_stop_fills_at_the_open():
    bars = flat_bars(5) + [(6, 90, 91, 89, 90)] + flat_bars(10, px=90, start=7)
    t = mk_trip([(0, 1, 100, 0), (30, -1, 90, 0)], bars)
    r = rp.simulate(t, rp.Variant(stop=("pct", 0.05)), RATES)
    fill = 90 * (1 - 1e-4)
    assert r["reason"] == "stop" and r["net"] == pytest.approx(fill - 100 - fill * 4.5e-4)


def test_time_stop_only_closes_losing_trades_and_scales_funding():
    bars = [(m, 99, 99, 99, 99) for m in range(1, 300)]
    pays = [(60, -1.0), (120, -1.0), (180, -1.0)]
    t = mk_trip([(0, 1, 100, 0), (240, -1, 99, 0)], bars, pays=pays)
    r = rp.simulate(t, rp.Variant(time_h=1.5), RATES)
    assert r["reason"] == "time"
    fill = 99 * (1 - 1e-4)
    assert r["net"] == pytest.approx(fill - 100 - fill * 4.5e-4 - 1.0)  # only the 1h payment
    up = mk_trip([(0, 1, 100, 0), (240, -1, 101, 0)], [(m, 101, 101, 101, 101) for m in range(1, 300)])
    assert rp.simulate(up, rp.Variant(time_h=1.5), RATES)["reason"] is None


def test_limit_entry_fills_only_if_price_trades_through():
    bars = flat_bars(60, px=100.0)
    t = mk_trip([(0, 1, 100, 0.045), (60, -1, 102, 0.0459)], bars)
    assert rp.simulate(t, rp.Variant(limit_offset=0.001), RATES)["reason"] == "not filled"
    dip = flat_bars(5) + [(6, 100, 100, 99.8, 100)] + flat_bars(50, start=7)
    t2 = mk_trip([(0, 1, 100, 0.045), (60, -1, 102, 0.0459)], dip)
    r = rp.simulate(t2, rp.Variant(limit_offset=0.001), RATES)
    assert r["net"] == pytest.approx((102 - 99.9) - 99.9 * 1.5e-4 - 0.0459)


def test_no_adding_to_losers_drops_the_add_and_ends_flat():
    t = mk_trip([(0, 1, 100, 0), (10, 1, 95, 0), (20, -2, 97, 0)])
    r = rp.simulate(t, rp.Variant(drop_losing_adds=True), RATES)
    assert r["net"] == pytest.approx(-3)                                # 1 unit from 100 to 97
    assert t.net_actual == pytest.approx(-1)                            # averaging down actually helped here


def test_liquidation_only_when_a_variant_sizes_up():
    bars = flat_bars(5) + [(6, 100, 100, 80, 85)] + flat_bars(20, px=85, start=7)
    t = mk_trip([(0, 10, 100, 0), (30, -10, 85, 0)], bars, equity=500, coin="DOGE")   # 2x leverage
    assert rp.simulate(t, rp.Variant(), RATES)["reason"] is None
    big = rp.simulate(t, rp.Variant(scale=np.array([4.0])), RATES)                    # 8x: liq ~ -7.5%
    assert big["reason"] == "liquidated"


# --------------------------------------------------------------------------- #
# Look-ahead and the train/test split
# --------------------------------------------------------------------------- #

def _book(n=80, seed=3):
    rng = np.random.default_rng(seed)
    trips = []
    for i in range(n):
        start = i * 120
        path = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, 90)))
        bars = [(start + m + 1, path[m], path[m] * 1.001, path[m] * 0.999, path[m]) for m in range(90)]
        side = 1 if rng.random() < 0.6 else -1
        exit_px = path[60]
        t = mk_trip([(start, side, 100, 0.05), (start + 60, -side, exit_px, 0.05)], bars, side=side, i=i,
                    coin=["BTC", "ETH", "SOL"][i % 3], atr=0.3)
        t.feats = {"hour": (start // 60) % 24, "mfe_pct": 0.004, "hold_hours": 1.0, "context": "Flat",
                   "vol_regime": "Normal", "funding_cost_at_entry": 0.0,
                   "return_on_notional": t.net_actual / t.max_notional}
        trips.append(t)
    return trips


def test_changing_prices_after_t_changes_nothing_before_t():
    trips = _book()
    k = val.split_index(len(trips))
    cut = trips[k].open_ts
    a = cst.Assumptions()
    before = {v.name: (v.param, rp.run_variant(trips, v, RATES)) for v in bt.candidates(trips, k, a)}
    for t in trips:                                     # crash every bar from `cut` on
        late = t.bar_t >= cut.value
        t.bar_o[late] *= 0.5
        t.bar_h[late] *= 0.5
        t.bar_l[late] *= 0.5
        t.bar_c[late] *= 0.5
    after = {v.name: (v.param, rp.run_variant(trips, v, RATES)) for v in bt.candidates(trips, k, a)}
    assert before.keys() == after.keys()
    early = np.array([t.close_ts < cut for t in trips])
    for name in before:
        assert before[name][0] == after[name][0]        # every learned threshold is unchanged
        b, c = before[name][1], after[name][1]
        assert np.allclose(b["net"][early], c["net"][early]), name


def test_learned_thresholds_never_see_the_test_window():
    trips = _book()
    k = val.split_index(len(trips))
    a = cst.Assumptions()
    p1 = {v.name: v.param for v in bt.candidates(trips, k, a)}
    for t in trips[k:]:                                 # rewrite every test trip's outcome
        t.net_actual = -1e6
        t.feats["mfe_pct"] = 0.5
        t.feats["hold_hours"] = 99.0
    p2 = {v.name: v.param for v in bt.candidates(trips, k, a)}
    assert p1 == p2


def test_contexts_use_only_bars_closed_before_entry():
    idx = pd.date_range("2026-01-01", periods=24 * 20, freq="1h", tz="UTC")
    c = np.linspace(100, 120, len(idx))
    b = pd.DataFrame({"o": c, "h": c * 1.01, "l": c * 0.99, "c": c, "v": 10.0}, index=idx)
    p = mk.Prices()
    p.bars[("BTC", "1h")] = b
    entry = idx[300] + pd.Timedelta(minutes=20)
    x1 = rp._context(p, "BTC", entry)
    b2 = b.copy()
    b2.loc[idx[300]:, ["o", "h", "l", "c"]] *= 3                       # the entry's own bar and later
    p.bars[("BTC", "1h")] = b2
    assert rp._context(p, "BTC", entry) == pytest.approx(x1)


# --------------------------------------------------------------------------- #
# Validation statistics
# --------------------------------------------------------------------------- #

def test_block_bootstrap_interval_covers_a_known_mean():
    rng = np.random.default_rng(0)
    hits = 0
    for s in range(60):
        d = rng.normal(1.0, 5.0, 120)
        idx = val.stationary_indices(120, n_boot=400, seed=s)
        boot = d[idx].sum(axis=1)
        lo, hi = np.percentile(boot, [5, 95])
        hits += lo <= 120 <= hi
    assert 0.8 <= hits / 60 <= 0.98


def test_deflated_sharpe_falls_as_more_variants_are_tried():
    rng = np.random.default_rng(1)
    r = rng.normal(0.002, 0.01, 200)
    d = [val.deflated_sharpe(r, n, 0.01) for n in (1, 10, 100, 1000)]
    assert d[0] > d[1] > d[2] > d[3]


def test_confidence_labels():
    assert val.label((10, 50), 0.97, 60) == "High"
    assert val.label((10, 50), 0.5, 60) == "Medium"
    assert val.label((-10, 50), 0.99, 60) == "Low"


def test_backtest_needs_30_trips_and_reports_out_of_sample():
    trips = _book(20)
    assert bt.run(trips, RATES, cst.Assumptions(), 1e5)["status"] == "too_few_trips"
    res = bt.run(_book(80), RATES, cst.Assumptions(), 1e5)
    assert res["status"] == "done" and res["golden"]["pass"]
    assert res["split"]["train_trips"] == 56 and res["split"]["test_trips"] == 24
    assert res["variants_tried"] >= len(res["findings"])
    for f in res["findings"]:
        assert set(f) >= {"train_delta", "test_delta", "test_delta_ci", "dsr", "label"}


# --------------------------------------------------------------------------- #
# Rule extraction
# --------------------------------------------------------------------------- #

def test_rule_extraction_finds_a_planted_rule():
    """A trader who buys every time the last 5 hours fell more than 2%:
    the search should find a rule on ret_5."""
    rng = np.random.default_rng(2)
    idx = pd.date_range("2026-01-01", periods=24 * 120, freq="1h", tz="UTC")
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.008, len(idx))))
    b = pd.DataFrame({"o": np.r_[c[0], c[:-1]], "h": c * 1.002, "l": c * 0.998, "c": c, "v": 1.0}, index=idx)
    p = mk.Prices()
    p.bars[("BTC", "1h")] = b
    ret5 = np.log(b["c"] / b["c"].shift(5))
    opens = idx[(ret5 < -0.02).to_numpy()] + pd.Timedelta(hours=1, minutes=5)
    trips = pd.DataFrame({"open_ts": opens, "coin": "BTC", "side": "Long", "hold_hours": 3.0,
                          "max_notional": 1000.0, "return_on_notional": 0.001})
    out = rl.extract(trips, p, ["BTC"], idx[int(len(idx) * 0.7)], RATES)
    assert out["status"] == "done"
    best = max(out["rules"], key=lambda r: r["train_f1"])
    assert best["side"] == "Long" and any(c[0] == "ret_5" and c[1] == "<=" for c in best["rule"])
    assert best["train_f1"] > 0.6


def test_summary_only_lists_rules_that_helped_on_train():
    f = [{"name": "a", "train_delta": -5, "test_delta": 100, "label": "Low"},
         {"name": "b", "train_delta": 5, "test_delta": 50, "label": "Low"},
         {"name": "c", "train_delta": 5, "test_delta": 10, "label": "Medium"},
         {"name": "d", "train_delta": 5, "test_delta": -10, "label": "Medium"}]
    assert [x["name"] for x in bt.summary(f)] == ["c", "b"]
