"""Leaderboard pipeline with a fake venue (no network): candidate pool,
market-maker detection, metrics from our own data, eligibility, sorting."""
import json

import numpy as np
import pandas as pd
import pytest

from src import venues
from src.review import leaderboard as lbm
from src.review import store
from src.venues.base import Capabilities, VenueAdapter
from src.venues.hyperliquid import normalize_fills

NOW = pd.Timestamp("2026-10-26", tz="UTC")


def raw_fill(t, coin, side, px, sz, start, pnl, fee, tid, crossed=True):
    return {"time": int(t.timestamp() * 1000), "coin": coin, "side": side, "px": str(px), "sz": str(sz),
            "startPosition": str(start), "closedPnl": str(pnl), "fee": str(fee), "tid": tid, "crossed": crossed,
            "oid": tid}


def trader_fills(n_trips=60, win=12.0, loss=-5.0, start=NOW - pd.Timedelta(days=80)):
    out, tid = [], 1
    for i in range(n_trips):
        t = start + pd.Timedelta(hours=30 * i)
        pnl = win if i % 3 else loss
        out.append(raw_fill(t, "BTC", "B", 100, 1, 0, 0, 0.05, tid)); tid += 1
        out.append(raw_fill(t + pd.Timedelta(hours=5), "BTC", "A", 100 + pnl, 1, 1, pnl, 0.05, tid)); tid += 1
    return out


class FakeVenue(VenueAdapter):
    name, label = "fake", "FK"
    caps = Capabilities(leaderboard=True, fills_history=True)

    def __init__(self, accounts):
        super().__init__()
        self.accounts = accounts            # id -> {"fills": [...], "recent": int, "vault": bool, "pnl": {...}}

    def leaderboard(self):
        return pd.DataFrame([{"account_id": a, "account_value": 50_000.0, "week_pnl": d["pnl"],
                              "month_pnl": d["pnl"], "allTime_pnl": d["pnl"]} for a, d in self.accounts.items()])

    def recent_fill_rate(self, a, hours=24):
        return self.accounts[a].get("recent", 0)

    def is_vault(self, a):
        return self.accounts[a].get("vault", False)

    def fills(self, a, start_ms=0, end_ms=None):
        f = [x for x in self.accounts[a]["fills"] if x["time"] >= start_ms]
        return normalize_fills(f, a, venue=self.name)

    def funding(self, a, start_ms=0):
        return pd.DataFrame(columns=["venue", "account_id", "ts", "coin", "usdc", "position_size", "rate"])

    def portfolio(self, a):
        idx = pd.date_range(NOW - pd.Timedelta(days=100), NOW, freq="D", tz="UTC")
        return {"allTime": {"av": pd.Series(50_000.0, index=idx), "pnl": pd.Series(dtype=float), "vlm": 0.0}}

    def candles(self, coin, interval, start_ms, end_ms):
        idx = pd.date_range(NOW - pd.Timedelta(days=100), NOW, freq="D", tz="UTC")
        return pd.DataFrame({"venue": self.name, "coin": coin, "interval": interval, "ts": idx,
                             "o": 100.0, "h": 100.0, "l": 100.0, "c": 100.0, "v": 1.0})


@pytest.fixture
def setup(tmp_path, monkeypatch):
    accts = {
        "0xgood": {"fills": trader_fills(), "pnl": 500.0},
        "0xfew": {"fills": trader_fills(n_trips=20), "pnl": 400.0},
        "0xmm": {"fills": [], "recent": 5000, "pnl": 9e6},
        "0xvault": {"fills": [], "vault": True, "pnl": 8e6},
    }
    fake = FakeVenue(accts)
    monkeypatch.setitem(venues.VENUES, "fake", lambda: fake)
    monkeypatch.setitem(venues._instances, "fake", fake)
    con = store.connect(tmp_path / "t.db")
    return con


def test_pipeline_classifies_and_ranks(setup):
    con = setup
    lbm.refresh_candidates(con, "fake", per_window=10)
    for a in ("0xgood", "0xfew", "0xmm", "0xvault"):
        lbm.sync_account(con, a, "fake")
        lbm.compute_account(con, a, "fake", now=NOW)
    top, allrows = lbm.board(con, "all", venue="fake")
    assert list(top["account_id"]) == ["0xgood"]
    why = allrows.set_index("account_id")["exclusion"]
    assert "market maker" in why["0xmm"] and "vault" in why["0xvault"]
    assert "20 round trips" in why["0xfew"]
    good = top.iloc[0]
    # 40 winners x 12 + 20 losers x -5, minus 0.1 fees per trip; closes at 100 vs fills: realized = MTM here
    m = json.loads(good["metrics_json"])
    assert m["trips_all"] == 60 and good["trips"] == 60
    assert good["profit_factor"] == pytest.approx((40 * 11.9) / (20 * 5.1))


def test_eligibility_is_reapplied_when_reading(setup):
    con = setup
    lbm.refresh_candidates(con, "fake", per_window=10)
    for a in ("0xgood", "0xfew"):
        lbm.sync_account(con, a, "fake")
        lbm.compute_account(con, a, "fake", now=NOW)
    top, _ = lbm.board(con, "all", venue="fake", el=lbm.Eligibility(min_trips=10))
    assert set(top["account_id"]) == {"0xgood", "0xfew"}


def test_mm_behaviour_rules():
    el = lbm.Eligibility()
    assert lbm.classify({"fills_per_active_day": 5000, "maker_share": 0.5, "median_hold_min": 30,
                         "two_sided_share": 0.0}, None, el)[0] == "market_maker"
    assert lbm.classify({"fills_per_active_day": 50, "maker_share": 0.95, "median_hold_min": 0.3,
                         "two_sided_share": 0.0}, None, el)[0] == "market_maker"
    assert lbm.classify({"fills_per_active_day": 50, "maker_share": 0.95, "median_hold_min": 120,
                         "two_sided_share": 0.05}, None, el)[0] == "trader"


def test_candidates_union_of_windows():
    lb = pd.DataFrame({"account_id": list("abcd"), "week_pnl": [4, 3, 2, 1], "month_pnl": [1, 2, 3, 4],
                       "allTime_pnl": [1, 1, 1, 9]})
    pool = lbm.candidates(lb, per_window=1)
    assert set(pool["account_id"]) == {"a", "d"}
    assert pool.set_index("account_id").loc["d", "source_windows"] == "allTime,month"
