"""Round-trip reconstruction, fee and funding attribution, liquidations,
and the daily mark-to-market identity (Trade Review v2, Phase 1)."""
import numpy as np
import pandas as pd
import pytest

from src.review import equity as eq
from src.review.reconstruct import reconstruct


def F(t, side, px, sz, start, pnl=0.0, fee=0.1, coin="BTC", taker=1, fid=None, liq=None):
    return {"coin": coin, "ts": pd.Timestamp(t) if isinstance(t, pd.Timestamp) else pd.Timestamp(t, tz="UTC"), "side": side, "px": float(px), "sz": float(sz),
            "start_position": float(start), "closed_pnl": float(pnl), "fee": float(fee), "is_taker": taker,
            "fill_id": fid, "liq_role": liq}


def fills(rows):
    df = pd.DataFrame(rows)
    df["fill_id"] = [str(i + 1) if f is None else f for i, f in enumerate(df["fill_id"])]
    return df


def test_partial_closes_accumulate_into_one_trip():
    t, rep = reconstruct(fills([
        F("2026-01-01 00:00", "B", 100, 3, 0),
        F("2026-01-01 01:00", "A", 110, 1, 3, pnl=10),
        F("2026-01-01 02:00", "A", 120, 1, 2, pnl=20),
        F("2026-01-01 03:00", "A", 90, 1, 1, pnl=-10),
    ]))
    assert len(t) == 1 and not t["is_open"].iloc[0]
    r = t.iloc[0]
    assert r["realized_pnl"] == 20 and r["fees"] == pytest.approx(0.4) and r["fills"] == 4
    assert r["avg_exit"] == pytest.approx(320 / 3) and r["exit_qty"] == 3


def test_flip_in_one_fill_splits_the_fee_by_quantity():
    t, _ = reconstruct(fills([
        F("2026-01-01 00:00", "B", 100, 1, 0, fee=0.1),
        F("2026-01-01 01:00", "A", 110, 4, 1, pnl=10, fee=0.4),     # closes 1, opens a 3 short
        F("2026-01-01 02:00", "B", 100, 3, -3, pnl=30, fee=0.3),
    ]))
    assert list(t["side"]) == ["Long", "Short"]
    assert t.iloc[0]["fees"] == pytest.approx(0.1 + 0.1)          # 1/4 of the flip fee
    assert t.iloc[1]["fees"] == pytest.approx(0.3 + 0.3)          # 3/4 of it + the close
    assert t.iloc[1]["entry_qty"] == 3 and t.iloc[1]["avg_entry"] == 110
    assert t["net"].sum() == pytest.approx(40 - 0.8)


def test_scale_ins_average_entry_and_flag_adding_while_losing():
    t, _ = reconstruct(fills([
        F("2026-01-01 00:00", "A", 100, 1, 0),                      # short 1 @100
        F("2026-01-01 01:00", "A", 105, 1, -1),                     # adds at a worse price (losing)
        F("2026-01-01 02:00", "A", 95, 2, -2),                      # adds in profit
        F("2026-01-01 03:00", "B", 90, 4, -4, pnl=30),
    ]))
    r = t.iloc[0]
    assert r["side"] == "Short" and r["adds"] == 2 and bool(r["added_while_losing"])
    assert r["avg_entry"] == pytest.approx((100 + 105 + 190) / 4) and r["max_qty"] == 4


def test_history_starting_mid_position_is_excluded_and_counted():
    t, rep = reconstruct(fills([
        F("2026-01-01 00:00", "A", 110, 1, 2, pnl=5),               # position of 2 existed before the history
        F("2026-01-01 01:00", "A", 110, 1, 1, pnl=5),               # ...now flat
        F("2026-01-01 02:00", "B", 100, 1, 0),
        F("2026-01-01 03:00", "A", 105, 1, 1, pnl=5),
    ]))
    assert len(t) == 1 and rep["excluded_fills"] == 2 and rep["excluded_realized_pnl"] == 10


def test_funding_goes_to_the_open_trip_on_that_coin_and_side():
    f = fills([F("2026-01-01 00:30", "B", 100, 1, 0), F("2026-01-01 05:30", "A", 101, 1, 1, pnl=1)])
    funding = pd.DataFrame({
        "coin": ["BTC", "BTC", "BTC", "ETH"],
        "ts": pd.to_datetime(["2026-01-01 01:00", "2026-01-01 02:00", "2026-01-01 07:00", "2026-01-01 02:00"], utc=True),
        "usdc": [-0.5, -0.5, -9.0, -1.0], "position_size": [1.0, 1.0, 1.0, 2.0]})
    t, rep = reconstruct(f, funding)
    assert t.iloc[0]["funding"] == pytest.approx(-1.0)
    assert rep["unattributed_funding_n"] == 2 and rep["unattributed_funding_usd"] == pytest.approx(-10.0)
    assert t.iloc[0]["net"] == pytest.approx(1 - 0.2 - 1.0)


def test_funding_with_the_wrong_side_is_not_attributed():
    f = fills([F("2026-01-01 00:30", "B", 100, 1, 0)])
    funding = pd.DataFrame({"coin": ["BTC"], "ts": pd.to_datetime(["2026-01-01 01:00"], utc=True),
                            "usdc": [-0.5], "position_size": [-1.0]})
    t, rep = reconstruct(f, funding)
    assert t.iloc[0]["funding"] == 0 and rep["unattributed_funding_n"] == 1


def test_liquidation_role_marks_only_the_liquidated_wallet():
    t, rep = reconstruct(fills([
        F("2026-01-01 00:00", "B", 100, 1, 0),
        F("2026-01-01 01:00", "A", 80, 1, 1, pnl=-20, liq="liquidated"),
        F("2026-01-01 02:00", "B", 80, 1, 0, liq="counterparty"),
        F("2026-01-01 03:00", "A", 85, 1, 1, pnl=5),
    ]))
    assert list(t["liquidated"]) == [True, False] and rep["liquidations"] == 1


def test_spot_fills_are_skipped():
    t, rep = reconstruct(fills([F("2026-01-01", "B", 1, 10, 0, coin="@107"), F("2026-01-01", "B", 1, 10, 0, coin="PURR/USDC")]))
    assert t.empty and rep["spot_fills_skipped"] == 2


def test_fees_and_pnl_reconcile_with_the_fills():
    rng = np.random.default_rng(3)
    rows, pos, t0 = [], 0.0, pd.Timestamp("2026-01-01", tz="UTC")
    for i in range(400):                                             # random walk of trades incl. flips
        sz = float(rng.integers(1, 4))
        side = "B" if rng.random() < 0.5 else "A"
        rows.append(F(t0 + pd.Timedelta(minutes=10 * i), side, 100 + rng.normal(), sz, pos,
                      pnl=rng.normal() if pos else 0.0, fee=0.01 * sz))
        pos += sz if side == "B" else -sz
    f = fills(rows)
    t, rep = reconstruct(f)
    total_fees = t["fees"].sum() + rep["excluded_fees"]
    assert total_fees == pytest.approx(f["fee"].sum())
    assert t["realized_pnl"].sum() + rep["excluded_realized_pnl"] == pytest.approx(f["closed_pnl"].sum())


# ------------------------------- equity ------------------------------------ #

def test_daily_mtm_equals_realized_plus_unrealized_change():
    f = fills([
        F("2026-01-01 10:00", "B", 100, 2, 0, fee=0.2),
        F("2026-01-02 10:00", "A", 110, 1, 2, pnl=10, fee=0.1),
        F("2026-01-03 10:00", "A", 90, 1, 1, pnl=-10, fee=0.1),
    ])
    closes = {"BTC": pd.Series([105.0, 108.0, 95.0], index=pd.date_range("2026-01-01", periods=3, tz="UTC"))}
    d = eq.daily_pnl(f, None, closes, pd.Timestamp("2026-01-03", tz="UTC"))
    assert d.loc["2026-01-01", "mtm"] == pytest.approx(2 * (105 - 100) - 0.2)
    assert d.loc["2026-01-02", "mtm"] == pytest.approx(2 * (108 - 105) - 1 * (108 - 110) - 0.1)
    # flat at the end: total mark-to-market = realized - fees
    assert d["mtm"].sum() == pytest.approx(0 - 0.4)


def test_mtm_handles_history_starting_mid_position():
    f = fills([F("2026-01-02 12:00", "A", 110, 1, 1, pnl=7)])        # a 1-lot long existed before
    closes = {"BTC": pd.Series([100.0, 105.0], index=pd.date_range("2026-01-01", periods=2, tz="UTC"))}
    d = eq.daily_pnl(f, None, closes, pd.Timestamp("2026-01-02", tz="UTC"))
    assert d["mtm"].sum() == pytest.approx(-0.1)          # sold at 110 vs the first fill price 110: only the fee


def test_window_metrics_returns_and_drawdown():
    days = pd.date_range("2026-01-01", periods=10, tz="UTC")
    daily = pd.DataFrame({"mtm": [100, -50, 100, -200, 50, 0, 0, 100, 0, 0], "realized": 0.0, "fees": 0.0,
                          "funding": 0.0, "fills": 1.0, "missing_price": 0.0}, index=days, dtype=float)
    equity = pd.Series(10_000.0, index=days)
    trips = pd.DataFrame(columns=["open", "close_time", "net", "pnl", "fees", "hold_hours"])
    m = eq.window_metrics(daily, equity, trips, None, days[-1])
    assert m["pnl"] == 100 and m["max_dd_usd"] == -200          # peak 150 -> trough -50
    # equity is end-of-day: day 1 starts at 10,000 - 100 (no earlier observation)
    r = daily["mtm"] / 10_000
    r.iloc[0] = 100 / 9_900
    assert m["roe"] == pytest.approx(np.prod(1 + r) - 1)
    assert m["sharpe"] == pytest.approx(r.mean() / r.std() * np.sqrt(365))
    m7 = eq.window_metrics(daily, equity, trips, 7, days[-1])
    assert m7["pnl"] == -50 and m7["days"] == 7


# ------------------------- a real Hyperliquid wallet ----------------------- #
# Fixture: one wallet's complete public history (756 fills, 301 funding
# payments, Hyperliquid's own portfolio P&L and daily candles), fetched
# 26 Oct 2026 and anonymized.

def _real():
    import json
    from pathlib import Path
    from src.venues.hyperliquid import normalize_fills
    d = json.loads((Path(__file__).parent / "fixtures" / "hl_real_wallet.json").read_text())
    f = normalize_fills(d["fills"], d["address"])
    fund = pd.DataFrame([{"ts": pd.to_datetime(x["time"], unit="ms", utc=True), "coin": x["delta"]["coin"],
                          "usdc": float(x["delta"]["usdc"]), "position_size": float(x["delta"]["szi"])}
                         for x in d["funding"]])
    closes = {c: pd.Series({pd.to_datetime(t, unit="ms", utc=True): float(v) for t, v in k})
              for c, k in d["candles"].items()}
    pf = {w: v for w, v in d["portfolio"]}
    return f, fund, closes, pf


def test_real_wallet_every_dollar_is_attributed():
    f, fund, _, _ = _real()
    t, rep = reconstruct(f, fund)
    assert t["realized_pnl"].sum() + rep["excluded_realized_pnl"] == pytest.approx(f["closed_pnl"].sum(), abs=0.01)
    assert t["fees"].sum() + rep["excluded_fees"] == pytest.approx(f["fee"].sum(), abs=0.01)
    assert t["funding"].sum() + rep["unattributed_funding_usd"] == pytest.approx(fund["usdc"].sum(), abs=0.01)
    # this wallet's 1,240.885 xyz:SKHX long disappears without a fill on 24-25 Jul: detected, not hidden
    assert len(rep["position_gaps"]) == 1 and bool(t["exit_unobserved"].sum() == 1)


def test_real_wallet_30d_pnl_matches_hyperliquid_within_1pct():
    f, fund, closes, pf = _real()
    d = eq.daily_pnl(f, fund, closes, pd.Timestamp("2026-10-26", tz="UTC"))
    p = pd.Series({pd.to_datetime(t, unit="ms", utc=True): float(v) for t, v in pf["perpMonth"]["pnlHistory"]}).sort_index()
    venue = p.iloc[-1] - p.iloc[0]
    ours = d.loc[d.index >= p.index[0].floor("D") + pd.Timedelta(days=1), "mtm"].sum()
    assert ours == pytest.approx(venue, rel=0.01)
