"""Order-flow, options-OI and order-book-bucket formulas, checked by hand."""
import numpy as np
import pandas as pd
import pytest

from src.collectors.coinalyze import pick_markets
from src.indicators import flow
from src.indicators import positioning as pos

T = pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC")


def bars(v, bv, c=None):
    return pd.DataFrame({"v": v, "bv": bv, "c": c or [100.0] * len(v)}, index=T[: len(v)])


def test_cvd_is_running_buys_minus_sells():
    # bar 1: 10 traded, 7 bought -> +4; bar 2: 10 traded, 2 bought -> -6
    assert list(flow.cvd(bars([10, 10], [7, 2]))) == [4, -2]


def test_aggregate_cvd_adds_exchanges_bar_by_bar():
    a = bars([10, 10, 10], [7, 2, 5])        # deltas +4, -6, 0
    b = bars([4, 4], [4, 0])                 # deltas +4, -4 (missing third bar)
    assert list(flow.aggregate_cvd({"a": a, "b": b})) == [8, -2, -2]


def test_oi_in_coins_divides_each_exchange_by_its_own_price():
    oi = {"a": pd.DataFrame({"c": [1000.0, 2000.0]}, index=T[:2]),
          "b": pd.DataFrame({"c": [500.0, 500.0]}, index=T[:2])}
    closes = {"a": pd.Series([100.0, 100.0], index=T[:2]), "b": pd.Series([50.0, 50.0], index=T[:2])}
    assert list(flow.oi_in_coins(oi, closes)) == [20.0, 30.0]


def test_oi_weighted_funding_scales_hourly_exchanges():
    fr = {"Binance": pd.DataFrame({"c": [0.01]}, index=T[:1]),
          "Hyperliquid": pd.DataFrame({"c": [0.00125]}, index=T[:1])}   # hourly -> 0.01 per 8h
    oi = {"Binance": pd.DataFrame({"c": [3e9]}, index=T[:1]),
          "Hyperliquid": pd.DataFrame({"c": [1e9]}, index=T[:1])}
    assert flow.oi_weighted_funding(fr, oi).iloc[0] == pytest.approx(0.01)


def test_as_percent_converts_fractions_only():
    assert flow.as_percent(pd.Series([0.0001, 0.0002])).iloc[0] == pytest.approx(0.01)
    assert flow.as_percent(pd.Series([0.01, 0.02])).iloc[0] == pytest.approx(0.01)


def test_coinbase_premium():
    cb, bn = pd.Series([99.9], index=T[:1]), pd.Series([100.0], index=T[:1])
    assert flow.coinbase_premium(cb, bn).iloc[0] == pytest.approx(-0.1)


def test_pick_markets_prefers_usdt_perps_and_coinbase_usd_spot():
    fut = pd.DataFrame([
        {"symbol": "BTCUSDT_PERP.A", "base_asset": "BTC", "quote_asset": "USDT", "is_perpetual": True, "margined": "STABLE", "exchange_name": "Binance"},
        {"symbol": "BTCUSD_PERP.A", "base_asset": "BTC", "quote_asset": "USD", "is_perpetual": True, "margined": "COIN", "exchange_name": "Binance"},
        {"symbol": "BTCUSDC_PERP.A", "base_asset": "BTC", "quote_asset": "USDC", "is_perpetual": True, "margined": "STABLE", "exchange_name": "Binance"},
    ])
    spot = pd.DataFrame([
        {"symbol": "BTCUSDT.C", "base_asset": "BTC", "quote_asset": "USDT", "exchange_name": "Coinbase"},
        {"symbol": "BTCUSD.C", "base_asset": "BTC", "quote_asset": "USD", "exchange_name": "Coinbase"},
    ])
    assert pick_markets(fut, "BTC", ["Binance"], perp=True) == {"Binance": "BTCUSDT_PERP.A"}
    assert pick_markets(spot, "BTC", ["Coinbase"], perp=False) == {"Coinbase": "BTCUSD.C"}


def test_net_delta_by_expiry():
    now = pd.Timestamp("2026-01-01", tz="UTC")
    exp = now + pd.Timedelta(days=30)
    opts = pd.DataFrame([
        {"expiry": exp, "strike": 100.0, "type": "C", "iv": 0.5, "forward": 100.0, "oi": 10.0},
        {"expiry": exp, "strike": 100.0, "type": "P", "iv": 0.5, "forward": 100.0, "oi": 10.0},
    ])
    g = pos.options_with_delta(opts, now)
    t = pos.net_delta_by_expiry(g)
    # call delta d, put delta d-1 -> net = 10d + 10(d-1) = 20d - 10
    d = g.loc[g["type"] == "C", "delta"].iloc[0]
    assert t["net_delta"].iloc[0] == pytest.approx(20 * d - 10)
    assert t["put_call_ratio"].iloc[0] == 1.0


def test_bucket_book_sums_usd_per_bucket():
    bids = pd.DataFrame({"price": [99_990.0, 99_960.0, 90_000.0], "size": [1.0, 1.0, 5.0]})
    asks = pd.DataFrame({"price": [100_010.0], "size": [2.0]})
    out = pos.bucket_book(bids, asks, range_pct=1.0).set_index("price")
    assert pos.nice_step(100_000) == 50
    assert out.loc[99_950.0, "bid_usd"] == pytest.approx(99_990 + 99_960)   # both in the 99,950 bucket
    assert 90_000.0 not in out.index                                       # outside +/-1%
    assert out.loc[100_000.0, "ask_usd"] == pytest.approx(200_020)
