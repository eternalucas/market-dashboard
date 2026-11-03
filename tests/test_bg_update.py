"""'Update all data' runs in the background and reports progress."""
import threading
import time

from src import bg_update


def test_update_runs_in_background_and_reports():
    gate = threading.Event()
    def run(progress, x):
        progress(f"step {x}")
        gate.wait(5)
    assert bg_update.start(run, x=1)
    assert bg_update.running()
    assert not bg_update.start(run, x=2)          # one at a time
    time.sleep(0.05)
    assert bg_update.status()["messages"] == ["step 1"]
    assert not bg_update.take_finished()
    gate.set()
    bg_update._state["thread"].join(2)
    assert not bg_update.running()
    assert bg_update.take_finished() and not bg_update.take_finished()   # caches cleared once


def test_error_is_kept_for_the_sidebar():
    def run(progress):
        raise RuntimeError("boom")
    bg_update.start(run)
    bg_update._state["thread"].join(2)
    assert bg_update.status()["error"] == "RuntimeError: boom"


def test_data_health_retired_and_on_demand(tmp_path):
    import sqlite3
    import pandas as pd
    from src import views
    con = sqlite3.connect(tmp_path / "m.db")
    con.execute("CREATE TABLE fetch_log (ts TEXT, source TEXT, ok INTEGER, rows INTEGER, message TEXT)")
    old = "2026-10-21T16:50:00+00:00"
    con.executemany("INSERT INTO fetch_log VALUES (?, ?, ?, ?, ?)", [
        (old, "wikipedia:Bitcoin", 1, 5, ""), (old, "bgeo:delta-cap", 0, 0, "HTTPError: 500"),
        (old, "coinalyze:1min:BTC", 1, 900, ""), ("2026-11-03T17:00:00+00:00", "src:cftc", 1, 3, "")])
    h = views.source_health(con, now=pd.Timestamp("2026-11-03T18:00:00Z")).set_index("Source")
    assert "wikipedia:Bitcoin" not in h.index and "bgeo:delta-cap" not in h.index
    assert h.loc["coinalyze:1min:BTC", "Status"] == "OK"        # on demand: old is fine


def test_mempool_falls_back_to_blockstream(monkeypatch):
    from src.collectors import mempool_space as ms
    def fake(url, params=None):
        if any(url.startswith(m) for m in ms.MIRRORS):
            raise ConnectionError("down")
        if url.endswith("/mempool"):
            return {"count": 10, "vsize": 1000, "total_fee": 2000, "fee_histogram": [[2.0, 1000]]}
        return {"1": 5.0, "3": 4.0, "6": 3.0, "144": 1.5, "1008": 1.0}
    monkeypatch.setattr(ms, "get_json", fake)
    out = ms.mempool_state()
    assert out["count"] == 10 and out["rec_fastestFee"] == 5.0 and out["rec_minimumFee"] == 1.0


def test_delta_price_computed_from_coin_metrics():
    import pandas as pd
    from src.metrics import onchain as oc
    idx = pd.date_range("2026-01-01", periods=4, freq="D", tz="UTC")
    class C:
        def mcap(self): return pd.Series([100.0, 200, 300, 400], index=idx)
        def realized_cap(self): return pd.Series([50.0, 60, 70, 80], index=idx)
        def supply(self): return pd.Series(10.0, index=idx)
        def s(self, name): return pd.Series(dtype=float)
        def prefix(self, p): return {}
    out = oc.cost_basis_models(C())
    assert out["delta_price"].iloc[-1] == (80 - 250) / 10            # avg cap = mean(100..400) = 250
