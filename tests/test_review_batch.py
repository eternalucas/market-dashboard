"""Batch mode (Phase 5): aggregation across traders and the persistence test."""
import numpy as np
import pandas as pd
import pytest

from src.review import batch, store
from src.review import leaderboard as lbm


def _res(wrong, right, findings, golden_pass=True, actual_test=1000.0):
    return {"status": "done", "golden": {"pass": golden_pass, "error": 0.001}, "wrong": wrong, "right": right,
            "findings": findings, "actual_test": actual_test, "trips": 100, "rules": {"reproduces": False}}


def test_summarise_counts_traders_not_occurrences():
    w = lambda p, imp: {"pattern": p, "impact_usd": imp, "trades_flagged": 5, "rule": "r"}   # noqa: E731
    f = lambda fam, tr, te, lab="Medium": {"family": fam, "category": "exits", "train_delta": tr,   # noqa: E731
                                           "test_delta": te, "label": lab, "name": fam}
    per = [("a", _res([w("Losing market: XRP", -10), w("Losing market: ZEC", -5), w("Averaging down", -3)],
                      [{"title": "Never liquidated"}], [f("ATR stop", 10, 50), f("Time stop", -1, 99)])),
           ("b", _res([w("Losing market: BTC", -7)], [{"title": "Never liquidated"}],
                      [f("ATR stop", 5, -20), f("Time stop", 3, 10, "Low")])),
           ("c", {"status": "too_few_trips", "trips": 10})]
    s = batch.summarise(per)
    assert s["traders"] == 3 and s["backtested"] == 2 and s["too_few_trips"] == 1 and s["golden_pass"] == 2
    lm = next(m for m in s["mistakes"] if m["pattern"] == "Losing market")
    assert lm["traders"] == 2 and lm["share"] == 1.0 and lm["impact"] == -22
    assert next(x for x in s["strengths"] if x["title"] == "Never liquidated")["traders"] == 2
    atr = next(r for r in s["rules"] if r["family"] == "ATR stop")
    assert atr["adopted"] == 2 and atr["helped"] == 1 and atr["confident"] == 1
    ts = next(r for r in s["rules"] if r["family"] == "Time stop")
    assert ts["adopted"] == 1 and ts["helped"] == 1 and ts["confident"] == 0      # a's time stop wasn't adopted
    assert batch.pattern_kind("Losing time of day: 08-12") == "Losing time of day"


def test_persistence_ranks_on_the_first_window_and_measures_the_second(tmp_path, monkeypatch):
    con = store.connect(tmp_path / "t.db")
    now = pd.Timestamp("2026-03-01", tz="UTC")
    days = pd.date_range(now - pd.Timedelta(days=70), now, freq="D", tz="UTC")
    # no stored closes: daily P&L is then the realized P&L of each day's fills
    accts = [f"0x{i:040x}" for i in range(40)]
    rows = []
    for i, a in enumerate(accts):
        # account i makes i dollars a day in the first window; the best half lose in the second
        for d in days[:-1]:
            first = d < now - pd.Timedelta(days=30)
            pnl = float(i) if first else (-1.0 if i >= 20 else 1.0)
            rows.append({"venue": "hyperliquid", "account_id": a, "fill_id": f"{a}{d.value}", "ts": d + pd.Timedelta(hours=1),
                         "coin": "BTC", "side": "B", "px": 100.0, "sz": 0.0, "start_position": 0.0, "closed_pnl": pnl,
                         "fee": 0.0, "fee_token": "USDC", "builder_fee": 0.0, "is_taker": 1, "order_id": "x",
                         "liq_role": None, "liq_mark_px": None})
    store.upsert(con, "tr_fills", pd.DataFrame(rows))
    monkeypatch.setattr(lbm, "board", lambda *a, **k: (None, pd.DataFrame({"account_id": accts, "eligible": True})))
    p = batch.persistence(con, now=now, top_n=10)
    assert p["status"] == "done" and p["pool"] == 40 and p["top_n"] == 10
    assert p["top_profitable_next"] == 0.0 and p["rest_profitable_next"] == pytest.approx(20 / 30)
    assert p["rank_corr"] < 0
