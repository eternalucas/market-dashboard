"""Parsing tests: feed each collector a saved sample of the exchange's JSON
format (no network) and check it reads the fields correctly."""
import pandas as pd
import pytest

from src.collectors import attention, derivatives


def fake(responses):
    """Return a get_json replacement that answers by URL substring."""
    def _get(url, params=None):
        for key, value in responses.items():
            if key in url:
                return value
        raise AssertionError(f"unexpected URL {url}")
    return _get


def test_wikipedia_parse(monkeypatch):
    monkeypatch.setattr(attention, "get_json", fake({"pageviews": {"items": [
        {"timestamp": "2026010100", "views": 1000}, {"timestamp": "2026010200", "views": 1500}]}}))
    s = attention.wikipedia_pageviews("Bitcoin")
    assert list(s.values) == [1000, 1500]
    assert str(s.index[0].date()) == "2026-01-01"


def test_find_ranks_prefix_match():
    chart = ["Wealthsimple: Invest & Trade", "Some Bank", "Coinbase: Buy Bitcoin & Ether"]
    r = attention.find_ranks(chart, ["Coinbase", "Wealthsimple", "Kraken"])
    assert r["Wealthsimple"] == 1 and r["Coinbase"] == 3 and pd.isna(r["Kraken"])


def test_google_trends_csv(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("Category: All categories\n\nWeek,bitcoin: (Worldwide)\n2025-01-05,40\n2025-01-12,<1\n")
    s = attention.google_trends_from_csv(str(p))
    assert list(s.values) == [40.0, 0.5]


def test_bybit_snapshot(monkeypatch):
    monkeypatch.setattr(derivatives, "get_json", fake({"tickers": {"result": {"list": [
        {"markPrice": "100000", "indexPrice": "99950", "fundingRate": "0.0001", "openInterestValue": "5000000000"}]}}}))
    s = derivatives.bybit_snapshot("BTC")
    assert s["funding_8h"] == 0.0001 and s["oi_usd"] == 5e9 and s["exchange"] == "bybit"


def test_hyperliquid_hourly_funding_scaled_to_8h(monkeypatch):
    monkeypatch.setattr(derivatives, "post_json", lambda url, payload: [
        {"universe": [{"name": "ETH"}, {"name": "BTC"}]},
        [{"markPx": "4000", "oraclePx": "3999", "funding": "0.00001", "openInterest": "10"},
         {"markPx": "100000", "oraclePx": "99990", "funding": "0.0000125", "openInterest": "2"}]])
    s = derivatives.hyperliquid_snapshot("BTC")
    assert s["funding_8h"] == pytest.approx(0.0001)
    assert s["oi_usd"] == pytest.approx(200_000)


def test_deribit_instrument_parsing(monkeypatch):
    monkeypatch.setattr(derivatives, "get_json", fake({"get_book_summary": {"result": [
        {"instrument_name": "BTC-27DEC26-100000-C", "mark_iv": 52.5, "underlying_price": 101000, "open_interest": 12},
        {"instrument_name": "BTC-3JAN27-90000-P", "mark_iv": 55.0, "underlying_price": 101100, "open_interest": 3},
        {"instrument_name": "BTC-3JAN27-80000-P", "mark_iv": None, "underlying_price": 101100},
    ]}}))
    df = derivatives.deribit_option_summaries("BTC")
    assert len(df) == 2                                   # the row with no IV is dropped
    assert df.iloc[0]["expiry"] == pd.Timestamp("2026-12-27 08:00", tz="UTC")
    assert df.iloc[1]["expiry"] == pd.Timestamp("2027-01-03 08:00", tz="UTC")
    assert df.iloc[0]["iv"] == pytest.approx(0.525)


def test_coinbase_book(monkeypatch):
    monkeypatch.setattr(derivatives, "get_json", fake({"coinbase": {
        "bids": [["100.0", "1.5", 3]], "asks": [["100.5", "2", 1]]}}))
    bids, asks = derivatives.coinbase_book("BTC")
    assert bids.iloc[0].tolist() == [100.0, 1.5] and asks.iloc[0].tolist() == [100.5, 2.0]
