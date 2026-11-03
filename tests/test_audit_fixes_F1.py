"""Regression tests for audit fixes A1-01, A1-02, A1-03, A2-01, A2-03, A2-08."""
import sqlite3

import numpy as np
import pandas as pd

from src import charts
from src.catalog import SPECS

SPEC = {s.id: s for s in SPECS}
TODAY = pd.Timestamp.now(tz="UTC").normalize()


def _hourly(days_back: int, values) -> pd.Series:
    idx = pd.date_range(TODAY - pd.Timedelta(days=days_back), periods=len(values), freq="1h")
    return pd.Series(values, index=idx, dtype=float)


# A1-01: order-book levels average over the day; they are not summed or sampled at the last hour.
def test_a1_01_orderbook_daily_is_mean_not_sum():
    s = _hourly(3, [-1.0, -3.0] + [np.nan] * 22)          # one day, two hourly readings
    out = charts.resample(s.to_frame("delta").dropna(), charts.Opts(), SPEC["ob_delta"])
    assert out.iloc[0, 0] == -2.0                          # old code: -4.0 (sum)
    depth = pd.DataFrame({"bid_100": [10.0, 20.0], "ask_100": [30.0, 10.0]},
                         index=s.dropna().index)
    out = charts.resample(depth, charts.Opts(), SPEC["ob_depth_cb"])
    assert out.iloc[0].tolist() == [15.0, 20.0]            # old code: last hour [20, 10]
    assert all(SPEC[k].agg == "mean" for k in SPEC if k.startswith(("ob_", "perp_ob_")) and k != "ob_heatmap")


# A1-02: today's unfinished UTC day is neither drawn nor headlined.
def test_a1_02_partial_today_dropped():
    idx = pd.date_range(TODAY - pd.Timedelta(days=3), TODAY, freq="1D")
    vol = pd.Series([6.0, 5.0, 6.0, 1.0], index=idx)      # today's bar only 1/6 done
    assert charts.latest_value(vol, "num", "bars") == charts.fmt(6.0, "num")
    out = charts.resample(vol.to_frame("v"), charts.Opts(), SPEC["spot_volume"])
    assert out.index.max() < TODAY
    hourly = pd.Series(1.0, index=pd.date_range(TODAY - pd.Timedelta(days=2), TODAY + pd.Timedelta(hours=1), freq="1h"))
    out = charts.resample(hourly.to_frame("x"), charts.Opts(), SPEC["mempool_count"])
    assert out.index.max() == TODAY - pd.Timedelta(days=1)


# A1-03 / A2-03: a stale series shows its last-data date (single-column charts too).
def test_a1_03_last_data_note():
    stale = pd.Series(1.0, index=pd.date_range(TODAY - pd.Timedelta(days=300), TODAY - pd.Timedelta(days=64), freq="1D"))
    note = charts.last_data_note(stale)
    assert note.startswith("Last data: ") and f"{stale.index[-1]:%d %b %Y}" in note
    fresh = pd.Series(1.0, index=pd.date_range(TODAY - pd.Timedelta(days=300), TODAY - pd.Timedelta(days=1), freq="1D"))
    assert charts.last_data_note(fresh) == ""
    monthly = pd.Series(1.0, index=pd.date_range(end=TODAY - pd.Timedelta(days=40), periods=24, freq="30D"))
    assert charts.last_data_note(monthly) == ""            # monthly: 45 days allowed
    df = pd.DataFrame({"live": fresh, "dead": stale})
    assert "dead" in charts._ended(df) and "live" not in charts._ended(df)   # old: only after 400 days


# A2-01: YoY against the same month a year earlier; a missing month gives NaN.
def test_a2_01_yoy_calendar_offset():
    from src.metrics.markets import yoy
    idx = pd.date_range("2024-09-01", "2026-09-01", freq="MS")
    s = pd.Series(np.linspace(100, 110, len(idx)), index=idx).drop(pd.Timestamp("2025-10-01"))
    y = yoy(s)
    exp = (s[pd.Timestamp("2026-09-01")] / s[pd.Timestamp("2025-09-01")] - 1) * 100
    assert abs(y[pd.Timestamp("2026-09-01")] - exp) < 1e-12   # old code compared with 2025-08
    assert not pd.isna(y[pd.Timestamp("2025-11-01")])     # Nov 2024 exists
    assert pd.Timestamp("2025-10-01") not in y.index


# A2-03: a Lightning history that stopped updating is logged as a failure.
def test_a2_08_no_single_group_score():
    from src.indicators import social_risk as sr
    idx = pd.date_range("2026-01-01", periods=10, freq="1D", tz="UTC")
    att = [k for k, v in sr.COMPONENTS.items() if v[1] == "attention"][:2]
    spe = [k for k, v in sr.COMPONENTS.items() if v[1] == "speculation"][:3]
    comps = {k: pd.Series(0.2, index=idx) for k in att} | {k: pd.Series(0.8, index=idx) for k in spe}
    both = sr.grouped_composite(comps)
    for k in att:                                          # attention stops after day 5 (+3 ffill)
        comps[k] = comps[k].iloc[:5]
    out = sr.grouped_composite(comps)
    pd.testing.assert_series_equal(out["score"].iloc[:8], both["score"].iloc[:8])   # both live: unchanged
    assert out["score"].iloc[8:].isna().all()             # old code: 0.8 (speculation only)
    assert out["n_components"].iloc[-1] == 3


def test_real_rate_never_repeats_last_cpi_month():
    """A2-02: Oct policy rate exists, Oct CPI not yet published -> no Oct real rate."""
    import pandas as pd
    from src.metrics import extra, markets as mk
    idx = pd.date_range("2025-01-01", "2026-10-01", freq="MS", tz="UTC")
    m = pd.DataFrame({"us_rate": 4.0, "us_core_cpi_yoy": 3.0}, index=idx)
    m.loc[idx[-1], "us_core_cpi_yoy"] = float("nan")
    orig = mk.macro
    mk.macro = lambda c: m
    try:
        out = extra.real_policy_rate(None)
    finally:
        mk.macro = orig
    assert out.index.max() == idx[-2]
    assert (out["Real rate"] == 1.0).all()


def test_coverage_counts_each_group():
    import pandas as pd
    from src.indicators import social_risk as sr
    from src.metrics import social as so
    keys = list(sr.COMPONENTS)
    df = pd.DataFrame({k: [0.5, None] for k in keys}, index=pd.date_range("2026-10-01", periods=2, tz="UTC"))
    df["n_components"] = [len(keys), 0]
    orig = so.frame
    so.frame = lambda c: (df, {})
    try:
        cov = so.coverage(None)
    finally:
        so.frame = orig
    att = [c for c in cov if c.startswith("Attention")][0]
    spec = [c for c in cov if c.startswith("Speculation")][0]
    n_att = sum(1 for _, g, _ in sr.COMPONENTS.values() if g == "attention")
    assert cov[att].iloc[0] == n_att and cov[spec].iloc[0] == len(keys) - n_att and cov["Total"].iloc[1] == 0
