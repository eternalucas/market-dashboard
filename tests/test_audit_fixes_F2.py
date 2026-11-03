"""Regression tests for audit fixes A3-01..A3-05 (agent F2)."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MARKET_DB", str(Path(tempfile.mkdtemp()) / "market.db"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from src import storage  # noqa: E402
from src.indicators import flow  # noqa: E402

H = pd.date_range("2026-10-01", periods=72, freq="h", tz="UTC")


@pytest.fixture
def con(tmp_path):
    c = storage.connect(tmp_path / "t.db")
    yield c
    c.close()


# A3-01: Bybit Coinalyze OI is two-sided -> halved wherever it is read
def test_a3_01_bybit_coinalyze_oi_one_sided():
    from src.collectors.coinalyze import one_sided_oi
    s = pd.Series([67_828.507])
    assert one_sided_oi("BTCUSDT.6", s).iloc[0] == pytest.approx(33_914.2535)
    assert one_sided_oi("BTCUSDT_PERP.A", s).iloc[0] == 67_828.507


def test_a3_01_load_flow_and_studio_use_one_sided(con, monkeypatch):
    from src import flows
    from src.metrics import futures
    for sym, oi in (("BTCUSDT.6", 5.2e9), ("BTCUSDT_PERP.A", 7.5e9)):
        storage.save_series(con, f"cz:1hour:{sym}:oi_c", pd.Series(oi, index=H))
        storage.save_series(con, f"cz:1hour:{sym}:px_c", pd.Series(100.0, index=H))
        storage.save_series(con, f"czd:{sym}:oi_usd", pd.Series(oi, index=H[:1]))
    monkeypatch.setattr(flows, "client_for", lambda cfg: None)
    monkeypatch.setattr(flows, "_markets", lambda cfg, cl, a: ({}, {"Bybit": "BTCUSDT.6", "Binance": "BTCUSDT_PERP.A"}))
    out = flows.load_flow(con, {}, "BTC", "1 hour", None)
    assert out["oi_coins"].iloc[-1] == pytest.approx((2.6e9 + 7.5e9) / 100)

    class C:
        def s(self, n):
            return storage.load_series(con, n)
    monkeypatch.setattr(futures, "_perps", lambda asset="BTC": [
        {"symbol": "BTCUSDT.6", "exchange": "Bybit"}, {"symbol": "BTCUSDT_PERP.A", "exchange": "Binance"}])
    assert futures.open_interest(C()).iloc[0] == pytest.approx(2.6e9 + 7.5e9)


# A3-02: low genuine-percent funding must not be multiplied by 100
def test_a3_02_no_magnitude_guessing():
    assert not hasattr(flow, "as_percent")
    fr = {"Hyperliquid": pd.DataFrame({"c": [0.0005]}, index=H[:1])}   # %/h, below old 0.001 cut-off
    oi = {"Hyperliquid": pd.DataFrame({"c": [1e9]}, index=H[:1])}
    assert flow.oi_weighted_funding(fr, oi, {"Hyperliquid": 1.0}).iloc[0] == pytest.approx(0.004)


# A3-03: 4h contracts scaled x2 using the interval read from the data
def test_a3_03_four_hour_interval_from_data():
    vals = np.repeat([0.004, 0.005, 0.003, 0.006, 0.002, 0.007], 4)[:24]
    r = pd.Series(np.tile(vals, 3), index=H)
    assert flow.payment_interval_hours(r, "Binance") == 4.0
    r8 = pd.Series(np.repeat(np.arange(9) * 0.001, 8), index=H)
    assert flow.payment_interval_hours(r8, "Bybit") == 8.0
    assert flow.payment_interval_hours(r8, "OKX") == 8.0
    assert flow.payment_interval_hours(pd.Series(0.00125, index=H), "Hyperliquid") == 1.0
    fr = {"Binance": pd.DataFrame({"c": [0.005]}, index=H[:1])}
    oi = {"Binance": pd.DataFrame({"c": [1e9]}, index=H[:1])}
    assert flow.oi_weighted_funding(fr, oi, {"Binance": 4.0}).iloc[0] == pytest.approx(0.01)


# A3-04 / A3-05: live price + stale wallets flagged
def test_a3_04_05_live_price_and_stale_wallets(con):
    from src import ui_onchain
    now = pd.Timestamp("2026-11-02 18:00", tz="UTC")
    storage.save_series(con, "price:BTC", pd.Series([80_201.6], index=[pd.Timestamp("2026-10-26", tz="UTC")]))
    storage.save_series(con, "cb1h:BTC-USD:close", pd.Series([76_900.0], index=[pd.Timestamp("2026-11-02 17:00", tz="UTC")]))
    storage.save_series(con, "price:ETH", pd.Series([2_323.39], index=[pd.Timestamp("2026-10-26", tz="UTC")]))
    storage.save_series(con, "czd:ETHUSDT_PERP.A:close", pd.Series([2_500.0], index=[pd.Timestamp("2026-11-02", tz="UTC")]))
    storage.save_series(con, "onchain_bal:eth:0xold", pd.Series([198_128.97], index=[pd.Timestamp("2026-10-19", tz="UTC")]))
    storage.save_series(con, "onchain_bal:btc:bc1new", pd.Series([10.0], index=[pd.Timestamp("2026-11-02 17:00", tz="UTC")]))
    wl = pd.DataFrame([{"label": "Binance 14", "entity": "Binance", "category": "cex", "chain": "eth", "address": "0xold"},
                       {"label": "W", "entity": "X", "category": "cex", "chain": "btc", "address": "bc1new"}])
    bals, _ = ui_onchain._balances(con, wl, {}, now=now)
    b = bals.set_index("Address")
    assert b.loc["bc1new", "USD"] == pytest.approx(769_000.0)        # live cb1h, not 10-26 price:BTC
    assert b.loc["0xold", "USD"] == pytest.approx(198_128.97 * 2_500)
    assert bool(b.loc["0xold", "Stale"]) and not bool(b.loc["bc1new", "Stale"])
    assert ui_onchain._live_prices(con)["btc"][1] == pd.Timestamp("2026-11-02 17:00", tz="UTC")
