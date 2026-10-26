"""Social Risk adapters (src/sources/social.py) with the network mocked.
Response shapes match live responses checked 2026-10-21 (Wikimedia
aggregate, Hacker News Algolia, Apple RSS); GDELT's follows its DOC 2.0 docs."""
import os
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

os.environ.setdefault("MARKET_DB", str(Path(tempfile.mkdtemp()) / "t.db"))

from src import storage, views  # noqa: E402
from src.sources import social  # noqa: E402


@pytest.fixture
def con(tmp_path):
    return storage.connect(tmp_path / "db.sqlite")


def test_hn_weeks_are_complete_mondays():
    w = social.hn_weeks(date(2026, 10, 21))           # a Wednesday
    assert all(d.weekday() == 0 for d in w)
    assert w[-1] == date(2026, 10, 12)                # last complete week


def test_hacker_news_backfills_whole_weeks_newest_first(con, monkeypatch):
    calls = []
    monkeypatch.setattr(social, "hn_count", lambda term, kind, a, b: calls.append((term, kind, a)) or 7)
    monkeypatch.setattr(social, "HN_CALLS_PER_RUN", 12)          # two weeks x 6 series
    monkeypatch.setattr(social.time, "sleep", lambda *_: None)
    social.update_hacker_news(con, {})
    s = storage.load_series(con, "hn:story:bitcoin")
    assert len(s) == 2 and (s == 7).all()
    # stamped at the END of each week (the Monday after)
    last_week = social.hn_weeks()[-1]
    assert s.index[-1].date() == last_week + pd.Timedelta(days=7)
    assert len(calls) == 12


def test_gdelt_volume_parses_timeline(monkeypatch):
    days = pd.date_range("2026-10-01", periods=5, freq="D", tz="UTC")
    data = [{"date": d.strftime("%Y%m%dT%H%M%SZ"), "value": 10 + i, "norm": 1000} for i, d in enumerate(days)]
    monkeypatch.setattr(social, "get_json", lambda url, params=None: {"timeline": [{"series": "Article Count", "data": data}]})
    df = social.gdelt_volume()
    assert list(df["value"]) == [10, 11, 12, 13, 14] and (df["norm"] == 1000).all()


def test_app_store_stores_country_and_absent_rank(con, monkeypatch):
    monkeypatch.setattr(social.attention, "app_store_chart",
                        lambda cc, chart: ["Coinbase: Buy Crypto & Stocks", "Other app"] if cc == "us" else ["Other"])
    social.update_app_store(con, {"social_risk": {"app_store": {"country": "ca", "apps": []}}})
    assert storage.load_series(con, "appstore_finance:us:Coinbase").iloc[-1] == 1
    assert storage.load_series(con, "appstore_finance:ca:Coinbase").iloc[-1] == 2      # absent = len + 1
    assert storage.load_series(con, "appstore_overall:gb:Trust").iloc[-1] == 2           # "Trust:" stored without colon


def test_old_canadian_app_series_merge_into_ca(con):
    t = pd.Timestamp("2026-10-16", tz="UTC")
    storage.save_point(con, "appstore_finance:Coinbase", 40, ts=t.to_pydatetime())
    storage.save_point(con, "appstore_finance:IBKR", 10, ts=t.to_pydatetime())          # not a crypto app
    apps = views._apps(con, "finance")
    assert "ca:Coinbase" in apps and "ca:IBKR" not in apps


def test_wiki_articles_cover_languages():
    arts = social.wiki_articles({"social_risk": {"wikipedia": {"articles": ["Bitcoin", "Tether_(cryptocurrency)"]}}})
    names = [n for _, _, n in arts]
    assert "wiki:Bitcoin" in names and names.count("wiki:Bitcoin") == 1
    assert "wiki:Tether_(cryptocurrency)" in names and "wiki:ja:ビットコイン" in names
    assert len({p for p, _, _ in arts}) == 13                                             # en + 12 languages
