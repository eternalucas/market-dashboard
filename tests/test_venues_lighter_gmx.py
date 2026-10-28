"""Lighter and GMX adapters (Phase 4): normalizing real captured responses
into fills the shared round-trip engine can use."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.review.reconstruct import reconstruct
from src.venues import gmx, lighter
from src.venues.keccak import checksum, keccak256

FX = Path(__file__).parent / "fixtures"


def test_keccak_and_checksum():
    assert keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert checksum("0x3cf70327ccbf43ce3a507c6b3aeb80f4ba20ea98") == "0x3Cf70327CcbF43Ce3a507C6b3aEB80F4bA20EA98"


def test_lighter_trades_chain_positions_and_compute_closed_pnl():
    raw = json.loads((FX / "lighter_trades.json").read_text())["trades"]
    f = lighter.normalize_trades(raw, "10762", lambda m: {1: "BTC"}[m])
    assert len(f) == 10 and (f["coin"] == "BTC").all()
    end = f["start_position"] + np.where(f["side"] == "B", f["sz"], -f["sz"])
    # every trade starts from the position the previous one ended at
    assert np.allclose(f["start_position"].iloc[1:].to_numpy(), end.iloc[:-1].to_numpy(), atol=1e-9)
    buy = f[f["side"] == "B"].iloc[0]                       # buying back part of a short at 80765.3
    avg = 4636.455225 / 0.0574
    assert buy["closed_pnl"] == pytest.approx(0.00459 * (80765.3 - avg) * -1)
    assert (f.loc[f["side"] == "A", "closed_pnl"] == 0).all()   # adding to the short realises nothing
    assert f["is_taker"].tolist()[:2] == [1, 1] and f.iloc[-1]["is_taker"] == 0
    assert (f["fee"] == 0).all()                            # standard accounts pay no fees


def test_lighter_funding_estimate_signs():
    fills = pd.DataFrame({"coin": "BTC", "ts": pd.to_datetime(["2026-03-01 00:30"], utc=True), "side": "A",
                          "sz": 2.0, "start_position": 0.0})
    hrs = pd.date_range("2026-03-01 01:00", periods=3, freq="h", tz="UTC")
    rates = {"BTC": pd.Series(0.0001, index=hrs)}               # longs pay
    px = {"BTC": pd.Series(100.0, index=hrs)}
    u = lighter.estimate_funding(fills, rates, px, "1")
    assert len(u) == 3 and np.allclose(u["usdc"], 2 * 100 * 0.0001)   # a short receives


def test_gmx_actions_become_round_trips_with_exact_pnl():
    d = json.loads((FX / "gmx_actions.json").read_text())
    f, fund = gmx.normalize_actions(d["actions"], "0x3cf70327ccbf43ce3a507c6b3aeb80f4ba20ea98",
                                    lambda a: d["markets"][a.lower()], d["tokens"])
    assert len(f) == 4 and set(f["coin"]) == {"ETH long"}
    assert f["start_position"].tolist()[0] == 0 and f["start_position"].tolist()[1] == pytest.approx(90.808629389545)
    assert f.iloc[0]["fee"] == pytest.approx(96280161124341017832696500000000 / 1e30)   # GMX's own cost of the open
    trips, rep = reconstruct(f.assign(fill_id=[str(i) for i in range(len(f))]), fund, legs=True)
    assert len(trips) == 2 and not trips["is_open"].any()
    t0 = trips.iloc[0]
    cash = sum(-q * p for _, q, p, _, _ in t0["legs"])
    assert cash == pytest.approx(t0["realized_pnl"], rel=1e-9)       # the replay engine's golden identity
    assert t0["funding"] == pytest.approx(-146562950 * 999810567824412500000000 / 1e30)            # -$146.53
    gmx_total = (-96280161124341017832696500000000 + 1501694669734014712558397191098599) / 1e30
    assert t0["net"] == pytest.approx(gmx_total, rel=1e-9)          # net == GMX's own realised P&L


class FakeLighter(lighter.Lighter):
    """Serves captured responses instead of calling the venue."""

    def __init__(self):
        super().__init__()
        self._markets, self._symbols = {"BTC": 1}, {1: "BTC"}
        self.trades = json.loads((FX / "lighter_trades.json").read_text())["trades"]

    def get(self, path, params=None):
        params = params or {}
        if path == "trades":
            return {"code": 200, "trades": self.trades, "next_cursor": None}
        if path == "pnl":
            days = pd.date_range("2026-10-20", "2026-10-28", freq="D", tz="UTC")
            return {"pnl": [{"timestamp": int(d.timestamp()), "trade_pnl": float(i), "inflow": 1000.0, "outflow": 0.0,
                             "volume": 100.0 * i} for i, d in enumerate(days)]}
        if path == "fundings":
            hrs = pd.date_range("2026-10-28 00:00", "2026-10-28 06:00", freq="h", tz="UTC")
            return {"fundings": [{"timestamp": int(h.timestamp()), "rate": "0.0012", "direction": "long"} for h in hrs]}
        if path == "candles":
            hrs = pd.date_range("2026-10-27 00:00", "2026-10-28 06:00", freq="h", tz="UTC")
            return {"c": [{"t": int(h.timestamp() * 1000), "o": 80000, "h": 80100, "l": 79900, "c": 80000, "v": 1}
                          for h in hrs]}
        if path == "account":
            return {"accounts": [{"collateral": "1008", "account_type": 0}]}
        raise AssertionError(path)


def test_lighter_sync_through_the_shared_pipeline(tmp_path, monkeypatch):
    from src import venues
    from src.review import leaderboard as lbm
    from src.review import store
    con = store.connect(tmp_path / "t.db")
    fake = FakeLighter()
    monkeypatch.setitem(venues._instances, "lighter", fake)
    info = lbm.sync_account(con, "10762", "lighter")
    assert info["fills"] == 10 and not info.get("kind")
    fills = store.load(con, "tr_fills", "lighter", "10762")
    assert len(fills) == 10
    fund = store.load(con, "tr_funding", "lighter", "10762")
    assert len(fund) > 0 and (fund["usdc"] > 0).all()          # a short receives when longs pay
    snaps = store.load(con, "tr_account_snapshots", "lighter", "10762")
    assert snaps["account_value"].iloc[-1] == pytest.approx(1008.0)   # inflow 1000 + cumulative P&L 8
    out = lbm.compute_account(con, "10762", "lighter")
    assert out["kind"] in ("trader", "market_maker")


class FakeGMX(gmx.GMX):
    def __init__(self):
        super().__init__()
        self.d = json.loads((FX / "gmx_actions.json").read_text())

    def rest(self, path, params=None):
        if path == "/tokens":
            return {"tokens": [{"address": a, **v} for a, v in self.d["tokens"].items()]}
        if path == "/prices/candles":
            return {"candles": []}
        raise AssertionError(path)

    def gql(self, query):
        if "tradeActionsConnection" in query:
            return {"tradeActionsConnection": {"totalCount": 4}}
        if "tradeActions" in query:
            assert "0x3Cf70327CcbF43Ce3a507C6b3aEB80F4bA20EA98" in query       # checksummed for the data service
            return {"tradeActions": self.d["actions"]}
        if "marketInfos" in query:
            return {"marketInfos": [{"id": "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336",
                                     "indexTokenAddress": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
                                     "longTokenAddress": "", "shortTokenAddress": ""}]}
        if "periodAccountStats" in query:
            return {"periodAccountStats": [{"maxCapital": str(50_000 * 10**30), "volume": str(700_000 * 10**30)}]}
        raise AssertionError(query[:80])


def test_gmx_sync_through_the_shared_pipeline(tmp_path, monkeypatch):
    from src import venues
    from src.review import leaderboard as lbm
    from src.review import store
    con = store.connect(tmp_path / "t.db")
    monkeypatch.setitem(venues._instances, "gmx", FakeGMX())
    acct = "0x3cf70327ccbf43ce3a507c6b3aeb80f4ba20ea98"
    info = lbm.sync_account(con, acct, "gmx")
    assert info["fills"] == 4
    trips, _ = reconstruct(store.load(con, "tr_fills", "gmx", acct), store.load(con, "tr_funding", "gmx", acct))
    assert len(trips) == 2 and trips["funding"].lt(0).all()
    snaps = store.load(con, "tr_account_snapshots", "gmx", acct)
    assert snaps["account_value"].iloc[-1] == pytest.approx(50_000)


def test_lighter_candidates_come_from_sampled_accounts(tmp_path):
    from src.review import store
    con = store.connect(tmp_path / "t.db")
    con.execute("INSERT INTO tr_seen VALUES ('lighter', '10762', 5000, 10, 'x', 'x')")
    con.commit()
    lb = FakeLighter().leaderboard(con)
    r = lb.iloc[0]
    assert r["account_id"] == "10762" and r["account_value"] == pytest.approx(1008)
    assert r["week_pnl"] == pytest.approx(7) and r["allTime_pnl"] == pytest.approx(8)   # cumulative P&L 0..8 over 9 days


def test_gmx_leaderboard_merges_windows():
    class G(FakeGMX):
        def gql(self, query):
            if "periodAccountStats" in query:
                e = 10 ** 30
                return {"periodAccountStats": [{"id": "0xAbC", "realizedPnl": str(100 * e), "realizedFees": str(10 * e),
                                                "realizedPriceImpact": str(-5 * e), "startUnrealizedPnl": "0",
                                                "volume": str(1000 * e), "maxCapital": str(50 * e), "closedCount": 3,
                                                "wins": 2, "losses": 1}]}
            return super().gql(query)
    lb = G().leaderboard()
    r = lb.iloc[0]
    assert r["account_id"] == "0xabc" and r["month_pnl"] == pytest.approx(85) and r["account_value"] == 50
