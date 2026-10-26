"""Checks each formula against numbers worked out by hand.

Run with:  python -m pytest -q
"""
import math

import numpy as np
import pandas as pd
import pytest

from src.indicators import positioning as pos
from src.indicators import social_risk as sr


def daily(values, start="2020-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="D", tz="UTC"), dtype=float)


# ----------------------------- Social Risk -------------------------------- #

def test_rolling_percentile_rising_series_is_always_top():
    s = daily(range(400))
    p = sr.rolling_percentile(s, window_days=365, min_obs=30)
    assert p.iloc[:29].isna().all()          # not enough history yet
    assert (p.dropna() == 1.0).all()         # each value is the highest so far


def test_rolling_percentile_middle_value():
    s = daily([1, 2, 3, 4, 5, 3])
    p = sr.rolling_percentile(s, window_days=30, min_obs=5)
    assert p.iloc[-1] == pytest.approx(4 / 6)  # 1,2,3,3 are <= 3 out of 6


def test_rank_to_score():
    r = pd.Series([1, 50, 100, 101, np.nan])
    assert list(sr.rank_to_score(r).round(2)) == [1.0, 0.51, 0.01, 0.0, 0.0]


def test_composite_uses_only_available_components_with_weights():
    a = daily([0.2, 0.2, 0.2])
    b = daily([np.nan, 0.8, 0.8])
    df = sr.composite({"a": a, "b": b}, {"a": 1.0, "b": 3.0})
    assert df["score"].iloc[0] == pytest.approx(0.2)        # only a present
    assert df["score"].iloc[1] == pytest.approx((0.2 + 2.4) / 4)
    assert list(df["n_components"]) == [1, 2, 2]


def test_to_daily_stops_carrying_stale_values():
    weekly = pd.Series([1.0, 2.0], index=pd.to_datetime(["2020-01-01", "2020-01-29"], utc=True))
    d = sr.to_daily(weekly, ffill_days=7)
    assert d["2020-01-08"] == 1.0
    assert pd.isna(d["2020-01-09"])          # more than 7 days stale


def test_band_labels():
    bands = {"low": 0.3, "elevated": 0.7, "extreme": 0.85}
    assert [sr.band(x, bands) for x in (0.1, 0.5, 0.75, 0.9)] == ["Low", "Neutral", "Elevated", "Extreme"]


def test_forward_returns_by_band():
    price = daily([100, 110, 121, 133.1])
    score = daily([0.9, 0.5, 0.5, 0.5])
    t = sr.forward_returns_by_band(score, price, {"low": 0.3, "elevated": 0.7, "extreme": 0.85}, horizons=(1,))
    assert t.loc["Extreme", "avg_next_1d"] == pytest.approx(0.10)
    assert t.loc["All days", "days"] == 4


def test_peak_lead_time_detects_score_peaking_first():
    n = 1000
    px = daily(np.r_[np.linspace(100, 500, 500), np.linspace(500, 100, 500)])   # top on day 499
    sc = daily(np.r_[np.linspace(0, 1, 470), np.linspace(1, 0, 530)])           # peak on day 469
    out = sr.peak_lead_times(sc, px, half_window_days=180)
    row = out.iloc[0]
    assert row["lead_days"] == 30 and bool(row["confirmed"])


# ----------------------------- Positioning -------------------------------- #

def test_annualize_funding():
    assert pos.annualize_funding(0.0001) == pytest.approx(0.1095)   # 0.01%/8h ~ 10.95%/yr


def test_summarize_perps_weights_by_oi():
    snaps = [
        {"exchange": "a", "asset": "BTC", "mark": 101, "index": 100, "funding_8h": 0.0003, "oi_usd": 3e9},
        {"exchange": "b", "asset": "BTC", "mark": 100, "index": 100, "funding_8h": -0.0001, "oi_usd": 1e9},
    ]
    s = pos.summarize_perps(snaps)
    assert s["total_oi_usd"] == 4e9
    assert s["oi_weighted_funding_8h"] == pytest.approx(0.0002)
    assert s["oi_weighted_basis"] == pytest.approx(0.0075)


def test_book_depth_and_imbalance():
    bids = pd.DataFrame({"price": [99.9, 99.5, 98.0], "size": [1, 2, 10]})
    asks = pd.DataFrame({"price": [100.1, 100.8, 103.0], "size": [1, 1, 10]})
    meta, d = pos.book_depth(bids, asks, [0.5, 1.0])
    assert meta["mid"] == pytest.approx(100.0)
    assert meta["spread_bps"] == pytest.approx(20.0)
    row = d.set_index("band_pct").loc[1.0]
    assert row["bid_usd"] == pytest.approx(99.9 + 199.0)
    assert row["ask_usd"] == pytest.approx(100.1 + 100.8)
    assert row["imbalance"] == pytest.approx((298.9 - 200.9) / (298.9 + 200.9))
    assert bool(row["complete"])


def test_bs_delta_atm_is_about_half():
    assert pos.bs_delta(100, 100, 0.5, 30 / 365, "C") == pytest.approx(0.5287, abs=1e-3)
    assert pos.bs_delta(100, 100, 0.5, 30 / 365, "P") == pytest.approx(0.5287 - 1, abs=1e-3)


def test_skew_25d_recovers_known_smile():
    """Build a chain where puts carry 5 vol points more than calls; the risk
    reversal should come out at about -0.05."""
    now = pd.Timestamp("2026-01-01", tz="UTC")
    exp = now + pd.Timedelta(days=30)
    rows = []
    for k in range(60_000, 140_001, 2_000):
        rows.append({"expiry": exp, "strike": k, "type": "C", "iv": 0.50, "forward": 100_000, "oi": 1})
        rows.append({"expiry": exp, "strike": k, "type": "P", "iv": 0.55, "forward": 100_000, "oi": 1})
    out = pos.skew_25d(pd.DataFrame(rows), now, target_days=30)
    assert out["risk_reversal"] == pytest.approx(-0.05, abs=1e-6)
    assert out["days_to_expiry"] == 30


def test_zscore_latest():
    s = pd.Series([0.0] * 20 + [10.0])
    assert pos.zscore_latest(s) > 4


# ----------------------------- Social Risk v2 ----------------------------- #

def test_app_ladder_orders_charts():
    idx = pd.date_range("2026-01-01", periods=4, freq="D", tz="UTC")
    overall = pd.Series([1, 101, 101, 101], index=idx, dtype=float)
    finance = pd.Series([1, 1, 200, 201], index=idx, dtype=float)
    lad = sr.app_ladder(overall, finance)
    assert lad.iloc[0] == pytest.approx(1.0)       # #1 overall
    assert lad.iloc[1] == pytest.approx(0.5)       # #1 finance, not in overall top 100
    assert lad.iloc[2] == pytest.approx(0.5 * (1 - 199 / 200))
    assert lad.iloc[3] == 0.0                      # in neither chart


def test_end_of_period_restamps_weekly_and_monthly():
    w = pd.Series([5.0], index=pd.to_datetime(["2026-01-04"], utc=True))
    m = pd.Series([5.0], index=pd.to_datetime(["2026-01-01"], utc=True))
    assert sr.end_of_period(w, "weekly").index[0] == pd.Timestamp("2026-01-11", tz="UTC")
    assert sr.end_of_period(m, "monthly").index[0] == pd.Timestamp("2026-02-01", tz="UTC")


def test_grouped_composite_weights_groups_and_needs_three_inputs():
    a, b, f = daily([0.2] * 3), daily([0.6] * 3), daily([np.nan, 1.0, 1.0])
    df = sr.grouped_composite({"wikipedia_en": a, "google_trends": b, "funding": f})
    assert pd.isna(df["score"].iloc[0])             # only 2 inputs that day
    att = (0.2 * 1.0 + 0.6 * 1.0) / 2.0
    assert df["attention"].iloc[1] == pytest.approx(att)
    assert df["score"].iloc[1] == pytest.approx(0.65 * att + 0.35 * 1.0)


def test_hacker_news_component_scores_counts():
    idx = pd.date_range("2020-01-06", periods=120, freq="7D", tz="UTC")
    counts = {"story:bitcoin": pd.Series(np.arange(120.0), index=idx),
              "comment:bitcoin": pd.Series(np.arange(120.0), index=idx)}
    hn = sr.hacker_news_component(counts, 730)
    assert hn.dropna().iloc[-1] == 1.0              # latest week is the highest


def test_wikipedia_share_divides_by_project_total():
    views = {"Bitcoin": daily([10.0] * 400)}
    totals = {"Bitcoin": daily(np.linspace(100, 200, 400))}   # site growing, article flat
    sc = sr.wikipedia_share_component(views, totals, 365, 30).dropna()
    assert sc.iloc[-1] < 0.1                         # falling share -> low score
