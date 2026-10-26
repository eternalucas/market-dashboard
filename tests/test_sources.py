"""Source adapter tests. Fixtures are real responses captured 2026-10-21
(trimmed). Each adapter is checked for schema, timestamps (UTC days, no
duplicates), and plausible ranges; BGeometrics also for its call budget."""
import json
import os
import tempfile
from pathlib import Path

import pandas as pd
import pytest

FIX = Path(__file__).parent / "fixtures"
os.environ.setdefault("MARKET_DB", str(Path(tempfile.mkdtemp()) / "t.db"))

from src import storage  # noqa: E402
from src.sources import (alternative_me, base, bgeometrics, blockchain_com, cftc, coinmetrics,  # noqa: E402
                         defillama, farside)


def fx(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture
def con(tmp_path):
    return storage.connect(tmp_path / "db.sqlite")


def utc_days(s: pd.Series) -> bool:
    return bool((s.index == s.index.normalize()).all() and str(s.index.tz) == "UTC")


# ------------------------------------------------------------ base checks
def test_check_series_flags_problems():
    idx = pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC")
    clean = pd.Series(range(30), index=idx, dtype=float)
    assert base.check_series(clean, lo=0, hi=100) == []
    gappy = clean.drop(idx[10:20])
    assert any("gaps" in p for p in base.check_series(gappy))
    flat = pd.Series(1.0, index=idx)
    assert any("flat" in p for p in base.check_series(flat))
    assert any("above" in p for p in base.check_series(clean, hi=10))


def test_to_daily_stamps_midnight_utc():
    s = pd.Series([1.0, 2.0, 3.0], index=pd.to_datetime(
        ["2026-10-01 03:00", "2026-10-01 22:00", "2026-10-02 01:00"], utc=True))
    d = base.to_daily(s)
    assert list(d.values) == [2.0, 3.0] and utc_days(d)


# ------------------------------------------------------------ Coin Metrics
def test_coinmetrics(monkeypatch, con):
    monkeypatch.setattr(coinmetrics, "get_json", lambda *a, **k: fx("cm.json"))
    df = coinmetrics.fetch(["PriceUSD", "CapMrktCurUSD", "CapMVRVCur"])
    assert list(df.columns) == ["CapMVRVCur", "CapMrktCurUSD", "PriceUSD"]   # status columns dropped
    assert utc_days(df["PriceUSD"]) and not df.index.has_duplicates
    assert 1 < df["CapMVRVCur"].iloc[-1] < 10
    assert coinmetrics.update(con) > 0
    rc = storage.load_series(con, "cm:CapMrktCurUSD") / storage.load_series(con, "cm:CapMVRVCur")
    assert 0.5e12 < rc.iloc[-1] < 2e12      # realized cap ~ $1.08T


# ------------------------------------------------------------ BGeometrics
def test_bgeometrics_parse_generic():
    df = bgeometrics.parse(fx("bg_hodl.json"))
    assert set(df.columns) == {"age_0d_1d", "age_1d_1w", "age_10y"}
    assert utc_days(df["age_10y"]) and (df > 0).all().all()
    last = bgeometrics.parse([fx("bg_last.json")])
    assert list(last.columns) == ["mvrv"]      # delayed / message ignored


def test_bgeometrics_budget_and_queue(monkeypatch, con):
    calls = []
    monkeypatch.setattr(bgeometrics, "get_json", lambda url, params=None: calls.append(url) or fx("bg_hodl.json"))
    first = bgeometrics.update(con)
    assert len(calls) == bgeometrics.HOURLY_BUDGET          # hourly cap respected
    assert calls[0].endswith("/" + bgeometrics.PRIORITY[0])  # priority order
    assert first > 0
    calls.clear()
    bgeometrics.update(con)
    assert calls == []                                       # nothing left this hour
    st = bgeometrics.queue_status(con)
    assert st["backfilled"] == bgeometrics.HOURLY_BUDGET and st["budget_left_now"] == 0


def test_bgeometrics_daily_cap(monkeypatch, con):
    # pretend 14 calls happened 2 hours ago: daily budget exhausted even though the hour is fresh
    old = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=2)).isoformat()
    con.executemany("INSERT INTO fetch_log VALUES (?, ?, 1, 1, '')", [(old, f"bgeo:x{i}") for i in range(14)])
    con.commit()
    assert bgeometrics.budget_left(con) == 0


def test_bgeometrics_incremental_startday(monkeypatch, con):
    seen = []
    monkeypatch.setattr(bgeometrics, "get_json", lambda url, params=None: seen.append(params) or fx("bg_hodl.json"))
    bgeometrics.fetch_endpoint(con, "hodl-waves-supply")
    bgeometrics.fetch_endpoint(con, "hodl-waves-supply")
    assert seen[0] is None and seen[1] == {"startday": "2026-09-28"}   # 14 days before last stored day


# ------------------------------------------------------------ others
def test_blockchain_com(monkeypatch):
    monkeypatch.setattr(blockchain_com, "get_json", lambda *a, **k: fx("bc_utxo.json"))
    s = base.to_daily(blockchain_com.fetch("utxo-count", "2026-10-01"))
    assert utc_days(s) and s.iloc[-1] == 165851583 and not s.index.has_duplicates


def test_defillama_total(monkeypatch):
    s = defillama._chart(fx("dl_all.json"))
    assert utc_days(s) and s.iloc[-1] == pytest.approx(301.2e9)


def test_fear_greed(monkeypatch, con):
    monkeypatch.setattr(alternative_me, "get_json", lambda *a, **k: fx("fng.json"))
    alternative_me.update(con)
    s = storage.load_series(con, "fng")
    assert list(s.values) == [58.0, 63.0] and s.between(0, 100).all()   # sorted ascending


def test_cftc(monkeypatch, con):
    monkeypatch.setattr(cftc, "get_json", lambda *a, **k: fx("cftc.json"))
    cftc.update(con)
    oi = storage.load_series(con, "cftc:btc:open_interest_all")
    assert list(oi.values) == [20100, 20752] and utc_days(oi)


def test_farside_parser():
    df = farside.parse((FIX / "farside.html").read_text())
    assert list(df.columns) == ["IBIT", "FBTC", "Total"]
    assert df.loc["2024-01-12", "FBTC"] == -10.5          # (x) = outflow
    assert df.loc["2024-01-12", "Total"] == 0.0            # "-" = no flow
    assert len(df) == 2                                    # the Total row is not a date
