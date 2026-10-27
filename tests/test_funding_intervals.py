"""Funding from contracts with different payment intervals is compared per 8h."""
import pandas as pd
import pytest

from src.collectors import derivatives as d


def test_snapshot_scales_by_interval():
    four = d._snap("binance", "ENA", 1, 1, 0.00005, 4, 1)
    hourly = d._snap("hyperliquid", "ENA", 1, 1, 0.0000125, 1, 1)
    eight = d._snap("okx", "ENA", 1, 1, 0.0001, 8, 1)
    assert four["funding_8h"] == pytest.approx(0.0001)
    assert hourly["funding_8h"] == pytest.approx(0.0001)
    assert eight["funding_8h"] == pytest.approx(0.0001)
    assert four["funding_rate"] == 0.00005 and four["interval_h"] == 4


def test_history_interval_from_spacing():
    idx = pd.date_range("2026-10-01", periods=10, freq="4h", tz="UTC")
    s = d.to_8h(pd.Series(0.00005, index=idx))
    assert s.tolist() == pytest.approx([0.0001] * 10)
    hourly = d.to_8h(pd.Series(0.0000125, index=pd.date_range("2026-10-01", periods=30, freq="h", tz="UTC")))
    assert hourly.iloc[-1] == pytest.approx(0.0001)


def test_history_interval_change_and_missing_payment():
    idx = list(pd.date_range("2026-10-01", periods=8, freq="8h", tz="UTC")) + \
        list(pd.date_range("2026-10-03 16:00", periods=12, freq="4h", tz="UTC"))
    s = pd.Series([0.0001] * 8 + [0.00005] * 12, index=pd.DatetimeIndex(idx))
    s = s.drop(s.index[14])                      # one 4h payment missing
    out = d.to_8h(s)
    assert out.iloc[:6].tolist() == pytest.approx([0.0001] * 6)
    assert out.iloc[-5:].tolist() == pytest.approx([0.0001] * 5)
