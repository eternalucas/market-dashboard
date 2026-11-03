"""Regression tests for the 2 Nov 2026 UI fixes: chart labels, sparse series,
search duplicates, trader links and Data health statuses."""
from __future__ import annotations

import re
import sqlite3

import numpy as np
import pandas as pd

from src import catalog, charts, storage, views
from src.charts import Opts
from src.charts import series_label as label


def test_label_removes_underscores_but_keeps_dates():
    assert label("fed_balance_sheet") == "Fed balance sheet"
    assert label("us_core_cpi_yoy") == "US core CPI YoY"
    assert label("age_1m_3m") == "Age 1m 3m"
    assert label("2026_10_01") == "2026_10_01"
    assert label("Gold") == "Gold"


def test_every_chart_trace_name_is_free_of_underscores():
    # synthetic frame with snake_case columns through each multi-series kind
    idx = pd.date_range("2026-01-01", periods=60, freq="D", tz="UTC")
    df = pd.DataFrame({"fed_balance_sheet": np.arange(60.0), "ecb_balance_sheet": np.arange(60.0) * 2}, index=idx)
    for kind in ("multi", "bars", "stacked", "xy"):
        spec = charts.Spec(id="t", title="T", desc="", source="", kind=kind, price=False)
        fig = charts.render(spec, df, None, Opts())
        assert all("_" not in (t.name or "") for t in fig.data), kind


def test_weekly_series_on_daily_index_is_drawn_as_a_line():
    """A weekly series on a daily index (as frames built from FRED mixes produce) used
    to be all isolated points between NaNs, i.e. invisible."""
    idx = pd.date_range("2026-01-01", periods=120, freq="D", tz="UTC")
    weekly = pd.Series(np.nan, index=idx)
    weekly[::7] = np.arange(len(weekly[::7]), dtype=float)
    df = pd.DataFrame({"fed_balance_sheet": weekly, "us_rate": np.linspace(4, 5, 120)}, index=idx)
    spec = charts.Spec(id="t", title="T", desc="", source="", kind="multi", price=False)
    fig = charts.render(spec, df, None, Opts())
    tr = next(t for t in fig.data if t.name == "Fed balance sheet")
    y = np.asarray(tr.y, dtype=float)
    assert np.isfinite(y).all() and len(y) == weekly.notna().sum()


def test_real_gaps_still_break_the_line():
    idx = pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC").delete(slice(10, 20))   # 10-day outage
    s = charts._connected(pd.Series(1.0, index=idx))
    assert s.isna().sum() == 1
    assert s.index.is_monotonic_increasing


def test_search_has_no_duplicate_labels():
    from src.ui_studio import SEARCH
    labels = [v[0] for v in SEARCH.values()]
    assert len(labels) == len(set(labels))
    assert labels.count("Social Risk") == 1


def test_tokenized_equities_removed():
    assert not [s for s in catalog.SPECS if s.id.startswith("ts_")]
    assert not [g for g, _, _ in catalog.DASHBOARDS if g == "Tokenized Equities"]


def test_trader_link_opens_trade_review_and_shows_address():
    from src.ui_review import TRADER_LINK_TEXT, trader_link
    url = trader_link("0x2eaca7c0b93064c8f5c9c67cd2415cf68724bcef")
    assert url.startswith("?tab=Trade+review&review=Trader+review&trader=")
    assert re.search(TRADER_LINK_TEXT, url).group(1) == "0x2eaca7c0b93064c8f5c9c67cd2415cf68724bcef"


def test_only_hyperliquid_in_trade_review():
    from src import venues
    assert list(venues.VENUES) == ["hyperliquid"]


def _log_db(rows):
    con = sqlite3.connect(":memory:")
    con.executescript(storage.SCHEMA) if hasattr(storage, "SCHEMA") else con.execute(
        "CREATE TABLE fetch_log (ts TEXT NOT NULL, source TEXT NOT NULL, ok INTEGER NOT NULL, rows INTEGER, message TEXT)")
    con.executemany("INSERT INTO fetch_log VALUES (?, ?, ?, ?, ?)", rows)
    return con


def test_source_health_statuses():
    now = pd.Timestamp("2026-11-02T18:00:00Z")
    con = _log_db([
        ("2026-11-02T17:00:00+00:00", "src:cftc", 1, 10, ""),                      # fresh
        ("2026-10-26T17:00:00+00:00", "price:BTC", 1, 5, ""),                      # stale: not run for a week
        ("2026-11-01T10:00:00+00:00", "src:farside", 1, 3, ""),
        ("2026-11-02T17:30:00+00:00", "src:farside", 0, 0, "HTTPError: 429"),           # failing now, worked before
        ("2026-10-27T22:55:00+00:00", "src:tokenized_stocks", 0, 0, "x"),          # retired: hidden
    ])
    h = views.source_health(con, now=now).set_index("Source")
    assert h.loc["src:cftc", "Status"] == "OK"
    assert h.loc["price:BTC", "Status"] == "Stale"
    assert h.loc["src:farside", "Status"] == "Failing"
    assert h.loc["src:farside", "Last success"] == pd.Timestamp("2026-11-01T10:00:00Z")
    assert h.loc["src:farside", "Last error"] == "HTTPError: 429"
    assert "src:tokenized_stocks" not in h.index
    assert list(h["Status"]) == ["Failing", "Stale", "OK"]


def test_ago():
    assert views.ago(pd.Timedelta(minutes=1)) == "just now"
    assert views.ago(pd.Timedelta(minutes=42)) == "42 min ago"
    assert views.ago(pd.Timedelta(hours=5)) == "5 h ago"
    assert views.ago(pd.Timedelta(days=3)) == "3 d ago"
    assert views.ago(pd.NaT) == "never"


def test_legend_says_when_a_series_ended():
    idx = pd.date_range("2022-01-01", "2026-10-01", freq="MS", tz="UTC")
    lei = pd.Series(np.arange(len(idx), dtype=float), index=idx).where(idx <= "2024-01-01")
    df = pd.DataFrame({"us_lei": lei, "us_sentiment": np.arange(len(idx), dtype=float)}, index=idx)
    spec = charts.Spec(id="t", title="T", desc="", source="", kind="multi", price=False)
    names = [t.name for t in charts.render(spec, df, None, Opts()).data]
    assert "US LEI (last data Jan 2024)" in names and "US sentiment" in names


def test_x_impressions_removed_from_social_risk():
    from src.indicators import social_risk as sr
    assert "x_impressions" not in sr.COMPONENTS
    assert "social_c_x_impressions" not in catalog.BY_ID


def test_youtube_views_counts_only_days_with_every_channel(tmp_path):
    from src.metrics import social as so
    from src.metrics.core import Ctx
    con = sqlite3.connect(tmp_path / "m.db")
    con.execute("CREATE TABLE observations (series TEXT, ts TEXT, value REAL, PRIMARY KEY (series, ts))")
    con.execute("CREATE TABLE fetch_log (ts TEXT, source TEXT, ok INTEGER, rows INTEGER, message TEXT)")
    con.execute("CREATE TABLE events (date TEXT, event TEXT)")
    rows = [("yt_views:a", "2026-10-21T00:00:00+00:00", 1000), ("yt_views:a", "2026-10-23T00:00:00+00:00", 1200),
            ("yt_views:a", "2026-10-28T00:00:00+00:00", 1700),
            ("yt_views:b", "2026-10-23T00:00:00+00:00", 50), ("yt_views:b", "2026-10-28T00:00:00+00:00", 100)]
    con.executemany("INSERT INTO observations VALUES (?, ?, ?)", rows)
    so._CACHE.clear() if hasattr(so, "_CACHE") else None
    s = so.youtube_views(Ctx(con))
    assert s.index.min() == pd.Timestamp("2026-10-24", tz="UTC")          # b starts on 23 Oct
    assert s.loc["2026-10-25"] == 100 + 10                        # a: 500/5 days, b: 50/5 days
    assert len(s) == 5


def test_reset_view_bumps_uirevision(monkeypatch):
    import plotly.graph_objects as go
    import streamlit as st
    from src import chart_reset
    monkeypatch.setattr(st, "session_state", {}, raising=False)
    monkeypatch.setattr(st, "button", lambda *a, **k: False)
    drawn = []
    draw = chart_reset.wrap(lambda fig, *a, **k: drawn.append(fig.layout.uirevision))
    chart_reset._counter["n"] = 0
    draw(go.Figure(), key="x")
    chart_reset._bump("x")                      # what the button does
    draw(go.Figure(), key="x")
    assert drawn == ["x-0", "x-1"]               # new revision -> Plotly drops the zoom


def test_times_are_shown_in_montreal_time():
    from src.localtime import fmt, fmt_col
    assert fmt("2026-11-03T21:19:00+00:00") == "03 Nov 16:19 EST"
    assert fmt(pd.Timestamp("2026-07-01 12:00")) == "01 Jul 08:00 EDT"          # naive = UTC; summer is EDT
    s = fmt_col(pd.Series([pd.Timestamp("2026-11-03T21:19Z"), pd.NaT]))
    assert s.iloc[0] == "03 Nov 16:19 EST" and pd.isna(s.iloc[1])
