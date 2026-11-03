"""Regression tests for audit findings A4-01 (same-millisecond fill order),
A4-02 (ledger fetched, transfer-adjusted returns) and A4-03 (truncated
histories, windowed fill paging). Synthetic fixtures only, no network."""
import json

import numpy as np
import pandas as pd
import pytest

from src import venues
from src.review import equity as eq
from src.review import leaderboard as lbm
from src.review import store
from src.review.reconstruct import reconstruct
from src.venues import hyperliquid as hl
from tests.test_review_leaderboard import NOW, FakeVenue, trader_fills
from tests.test_review_reconstruct import F, fills


# ------------------------------------------------------------------ A4-01
def test_a4_01_same_ms_fills_are_ordered_by_position_chain_not_tid():
    t = "2025-03-25 18:04:57.234"
    # true order 0 -> 30 -> 28 -> 26 -> 0, but the trade ids say 26, 30, 28 (not sequential within a ms)
    f = fills([F("2025-03-25 18:00", "B", 100, 30, 0, fid="1"),
               F(t, "A", 101, 2, 28, pnl=2, fid="35655"),
               F(t, "A", 101, 2, 30, pnl=2, fid="246247"),
               F(t, "A", 101, 2, 26, pnl=2, fid="288633"),
               F("2025-03-25 19:00", "A", 102, 24, 24, pnl=48, fid="300000")])
    trips, rep = reconstruct(f)
    assert rep["position_gaps"] == []
    assert len(trips) == 1 and not trips.iloc[0]["exit_unobserved"] and not trips.iloc[0]["is_open"]
    assert trips.iloc[0]["realized_pnl"] == pytest.approx(54)
    assert rep["excluded_fills"] == 0


# ------------------------------------------------------------------ A4-02
LEDGER_RAW = [
    {"time": 1_000, "hash": "0xa", "delta": {"type": "deposit", "usdc": "1000"}},
    {"time": 2_000, "hash": "0xb", "delta": {"type": "withdraw", "usdc": "200", "fee": "1"}},
    {"time": 3_000, "hash": "0xc", "delta": {"type": "accountClassTransfer", "usdc": "50", "toPerp": False}},
    {"time": 4_000, "hash": "0xd", "delta": {"type": "vaultDeposit", "vault": "0xv", "usdc": "300"}},
    {"time": 5_000, "hash": "0xe", "delta": {"type": "vaultWithdraw", "vault": "0xv", "user": "0xme",
                                              "requestedUsd": "310", "netWithdrawnUsd": "305"}},
    {"time": 6_000, "hash": "0xf", "delta": {"type": "internalTransfer", "usdc": "40", "user": "0xme",
                                              "destination": "0xother", "fee": "1"}},
    {"time": 7_000, "hash": "0xg", "delta": {"type": "subAccountTransfer", "usdc": "70", "user": "0xmaster",
                                              "destination": "0xme"}},
    {"time": 8_000, "hash": "0xh", "delta": {"type": "send", "user": "0xme", "destination": "0xme", "token": "USDC",
                                              "amount": "9", "usdcValue": "9", "sourceDex": "", "destinationDex": "xyz"}},
    {"time": 9_000, "hash": "0xi", "delta": {"type": "spotTransfer", "token": "HYPE", "amount": "2", "usdcValue": "80",
                                              "user": "0xother", "destination": "0xme"}},
    {"time": 10_000, "hash": "0xj", "delta": {"type": "liquidation", "accountValue": "10", "liquidatedNtlPos": "5"}},
]


def test_a4_02_ledger_signs_and_types():
    ad = hl.Hyperliquid()
    ad.info = lambda payload: LEDGER_RAW if payload["startTime"] == 0 else []
    lg = ad.ledger("0xME").set_index("kind")["usdc"]
    assert lg["deposit"] == 1000 and lg["withdraw"] == -200
    assert lg["accountClassTransfer"] == -50                  # into spot: internal
    assert lg["vaultDeposit"] == -300 and lg["vaultWithdraw"] == 305
    assert lg["internalTransfer"] == -40 and lg["subAccountTransfer"] == 70
    assert lg["send"] == 0                                     # to its own other dex
    assert lg["spotTransfer"] == 80 and lg["liquidation"] == 0
    ext = {k for k in lg.index if k in hl.EXTERNAL_FLOWS}
    assert "accountClassTransfer" not in ext and "liquidation" not in ext and "deposit" in ext


def test_a4_02_returns_are_transfer_adjusted():
    days = pd.date_range("2026-10-20", periods=4, tz="UTC")
    daily = pd.DataFrame({"mtm": [0.0, -1_000.0, 0.0, 0.0], "realized": 0.0, "fees": 0.0, "funding": 0.0,
                          "fills": 1.0, "missing_price": 0.0}, index=days)
    # equity rises 10k -> 18k on day 2 although the trader lost 1k: a 9k deposit
    av = pd.Series([10_000.0, 10_000.0, 18_000.0, 18_000.0, 18_000.0],
                   index=[days[0] - pd.Timedelta(hours=1)] + [d + pd.Timedelta(hours=23) for d in days])
    equity = eq.daily_equity([av], days)
    ledger = pd.DataFrame({"ts": [days[1] + pd.Timedelta(hours=2)], "kind": ["deposit"], "usdc": [9_000.0]})
    flows = eq.daily_flows(ledger, [av], days)
    m = eq.window_metrics(daily, equity, pd.DataFrame(columns=["open", "close_time", "net"]), None, days[-1], flows)
    assert m["transfer_adjusted"] and m["transfers"] == 9_000
    assert m["roe"] == pytest.approx(-0.10)                    # (18k - 10k - 9k) / 10k


class LedgerVenue(FakeVenue):
    def ledger(self, a, start_ms=0):
        return hl.normalize_ledger([x for x in LEDGER_RAW if x["time"] >= start_ms], a, venue=self.name)


def test_a4_02_sync_fills_the_ledger_and_metrics_say_adjusted(tmp_path, monkeypatch):
    fake = LedgerVenue({"0xme": {"fills": trader_fills(), "pnl": 1.0}})
    monkeypatch.setitem(venues.VENUES, "fake", lambda: fake)
    monkeypatch.setitem(venues._instances, "fake", fake)
    con = store.connect(tmp_path / "t.db")
    lbm.sync_account(con, "0xme", "fake")
    assert con.execute("SELECT COUNT(*) FROM tr_ledger").fetchone()[0] == len(LEDGER_RAW)
    lbm.sync_account(con, "0xme", "fake")                     # incremental: nothing duplicated
    assert con.execute("SELECT COUNT(*) FROM tr_ledger").fetchone()[0] == len(LEDGER_RAW)
    lbm.compute_account(con, "0xme", "fake", now=NOW)
    m = json.loads(con.execute("SELECT metrics_json FROM tr_metrics WHERE window='30d'").fetchone()[0])
    assert m["transfer_adjusted"] is True


# ------------------------------------------------------------------ A4-03
def test_a4_03_fills_walk_back_past_short_aggregated_pages(monkeypatch):
    """A venue that reaches only the 3,000 fills before a query's endTime and
    aggregates pages (fewer than 2,000 rows while more remain)."""
    t0 = 1_700_000_000_000
    raw = [{"time": t0 + i * 60_000, "coin": "BTC", "side": "B", "px": "1", "sz": "1", "startPosition": "0",
            "closedPnl": "0", "fee": "0", "tid": i, "crossed": True, "oid": i} for i in range(9_000)]
    end = raw[-1]["time"] + 1

    def info(p):
        hi = p.get("endTime", end)
        reach = [f for f in raw if f["time"] <= hi][-3_000:]
        page = [f for f in reach if f["time"] >= p["startTime"]][:2_000]
        return page[:-7] if len(page) == 2_000 else page          # "aggregated": a short page, more remain
    ad = hl.Hyperliquid()
    ad.info = info
    monkeypatch.setattr(hl.time, "time", lambda: end / 1000)
    got = ad.fills("0xme", 0)
    assert len(got) == 9_000 and got["fill_id"].nunique() == 9_000


def test_a4_03_truncated_history_not_ranked_on_uncovered_windows(tmp_path, monkeypatch):
    # fills start 80 days ago: 7d/30d are covered, 90d and all are not
    fake = FakeVenue({"0xt": {"fills": trader_fills(), "pnl": 1.0}})
    fake.portfolio = lambda a: {"allTime": {"av": pd.Series(50_000.0, index=pd.date_range(
        NOW - pd.Timedelta(days=100), NOW, freq="D", tz="UTC")), "pnl": pd.Series(dtype=float), "vlm": 1e12}}
    monkeypatch.setitem(venues.VENUES, "fake", lambda: fake)
    monkeypatch.setitem(venues._instances, "fake", fake)
    con = store.connect(tmp_path / "t.db")
    lbm.refresh_candidates(con, "fake", per_window=10)
    lbm.sync_account(con, "0xt", "fake")
    lbm.compute_account(con, "0xt", "fake", now=NOW)
    for w in ("90d", "all"):
        top, allrows = lbm.board(con, w, venue="fake")
        assert top.empty and "history truncated" in allrows.iloc[0]["exclusion"]
        top, _ = lbm.board(con, w, venue="fake", el=lbm.Eligibility(rank_truncated=True))
        assert list(top["account_id"]) == ["0xt"]
    top, _ = lbm.board(con, "30d", venue="fake")
    assert list(top["account_id"]) == ["0xt"]
    assert lbm.Eligibility.from_cfg({"trade_review": {"eligibility": {"rank_truncated": "false"}}}).rank_truncated is False
