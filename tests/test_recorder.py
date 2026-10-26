"""Recorder tests: parsing of real response samples (tests/fixtures), the
order-book maths, and idempotent writes."""
import json
import math
import os
import tempfile
from pathlib import Path

import pandas as pd
import pytest

FIX = Path(__file__).parent / "fixtures"
os.environ.setdefault("MARKET_DB", str(Path(tempfile.mkdtemp()) / "t.db"))

from src import recorder, storage  # noqa: E402
from src.collectors import deribit, futures_curve, mempool_space  # noqa: E402
from src.indicators import orderbook as ob  # noqa: E402


def fx(name):
    return json.loads((FIX / name).read_text())


def book(mid=100.0, levels=50, step=0.1, size=1.0):
    bids = pd.DataFrame({"price": [mid - step / 2 - i * step for i in range(levels)], "size": size})
    asks = pd.DataFrame({"price": [mid + step / 2 + i * step for i in range(levels)], "size": size})
    return bids, asks


# ---------------------------------------------------------------- order book
def test_spread_and_depth():
    bids, asks = book()
    m = ob.snapshot_metrics(bids, asks)
    assert m["mid"] == pytest.approx(100.0)
    assert m["spread_bps"] == pytest.approx(10.0)          # 0.1 / 100 * 1e4
    # 25 bps of 100 = 0.25 -> levels at 99.95, 99.85, 99.75 on the bid side
    assert m["bid_25"] == pytest.approx(99.95 + 99.85 + 99.75)
    assert m["ask_25"] == pytest.approx(100.05 + 100.15 + 100.25)


def test_price_impact_walks_book():
    asks = pd.DataFrame({"price": [101.0, 102.0], "size": [1.0, 10.0]})
    # buy $305: 1 coin at 101 ($101), then $204 at 102 = 2 coins -> 3 coins, vwap 101.667
    imp = ob.price_impact_bps(asks, 100.0, 305.0)
    assert imp == pytest.approx((305 / 3 - 100) / 100 * 1e4)
    assert math.isnan(ob.price_impact_bps(asks, 100.0, 1e9))   # too thin -> NaN


def test_imbalance_and_slope():
    assert ob.imbalance(3.0, 1.0) == pytest.approx(0.5)
    assert ob.slope(10.0, 2.0) == pytest.approx(5.0)


def test_heatmap_fixed_grid():
    bids, asks = book(mid=100.0, step=0.1)
    g = ob.heatmap_buckets(bids, asks, step=1.0, range_pct=2.0)
    assert set(g["price"]) <= {98.0, 99.0, 100.0, 101.0}
    assert (g["price"] % 1.0 == 0).all()                    # grid doesn't move with mid


def test_aggregate_min_max_close():
    idx = pd.date_range("2026-01-01", periods=20, freq="min", tz="UTC")
    s = pd.DataFrame({"spread_bps": range(20), "bid_25": 1.0}, index=idx)
    a = ob.aggregate(s, "10min")
    assert list(a["spread_min"]) == [0, 10] and list(a["spread_max"]) == [9, 19] and list(a["spread_close"]) == [9, 19]


# ---------------------------------------------------------------- parsers
def test_deribit_chain(monkeypatch):
    monkeypatch.setattr(deribit, "get_json", lambda *a, **k: fx("deribit_option_summary.json"))
    df = deribit.option_chain()
    assert len(df) == 2
    r = df.iloc[0]
    assert r["type"] == "P" and r["strike"] == 73000 and r["iv"] == pytest.approx(0.4192)
    assert r["expiry"] == "2026-11-27T08:00:00+00:00"


def test_deribit_trades(monkeypatch):
    monkeypatch.setattr(deribit, "get_json", lambda *a, **k: fx("deribit_trades.json"))
    df = deribit.option_trades(0, 1)
    assert len(df) == 2 and df["combo"].tolist() == [0, 1]
    assert df["iv"].iloc[0] == pytest.approx(0.25)


def test_mempool_state(monkeypatch):
    def fake(url, *a, **k):
        return fx("fees_recommended.json") if "recommended" in url else fx("mempool.json")
    monkeypatch.setattr(mempool_space, "get_json", fake)
    m = mempool_space.mempool_state()
    assert m["count"] == 56847 and m["rec_fastestFee"] == 2
    assert m["vsize_band:5-10"] == 50543 and m["vsize_band:1-2"] == 26_000_000
    assert m["median_fee_rate"] == 1.0                     # most vsize sits at 1 sat/vB


def test_futures_curves(monkeypatch):
    monkeypatch.setattr(futures_curve, "get_json",
                        lambda url, *a, **k: fx("binance_premium.json") if "binance" in url
                        else fx("okx_index.json") if "index" in url else fx("okx_futures.json"))
    b = futures_curve.binance_curve()
    assert list(b["instrument"]) == ["BTCUSDT_261225", "BTCUSDT_270326"]   # perp excluded
    assert b["expiry"].iloc[0] == pd.Timestamp("2026-12-25 08:00", tz="UTC")
    o = futures_curve.okx_curve()
    assert o["index"].iloc[0] == pytest.approx(84404.7)
    monkeypatch.setattr(deribit, "get_json", lambda *a, **k: fx("deribit_futures.json"))
    d = deribit.futures()
    assert d["expiry"].isna().sum() == 1                    # the perpetual


# ---------------------------------------------------------------- recorder writes
def test_job_books_idempotent(monkeypatch):
    rec = recorder.Recorder()
    rec.con = storage.connect()
    fake = {v: (lambda a: book(mid=80000, levels=400, step=10, size=2)) for v in ("coinbase", "binance")}
    monkeypatch.setattr(recorder.books, "SPOT", fake)
    monkeypatch.setattr(recorder.books, "PERP", {"bybit": lambda a: book(mid=80000, levels=400, step=10, size=2)})
    monkeypatch.setattr(recorder, "_now", lambda: pd.Timestamp("2026-10-21 12:10:30", tz="UTC"))
    rec.job_books()
    rec.job_books()   # same minute again: upsert, no duplicates
    n = rec.con.execute("SELECT COUNT(*) FROM book_metrics").fetchone()[0]
    assert n == 3
    h = pd.read_sql_query("SELECT * FROM heatmap WHERE market='spot'", rec.con)
    assert not h.empty and (h["price"] % 50 == 0).all()
    # spot heatmap sums both venues: each bucket = 2 venues x level notional
    assert h["usd"].max() > 0


def test_lock_is_exclusive():
    a = recorder.acquire_lock()
    b = recorder.acquire_lock()
    assert a is not None and b is None
    a.close()
