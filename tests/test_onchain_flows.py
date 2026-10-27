"""Watched-wallet flows: grouping, internal moves, windows, and the
incremental Bitcoin sync (network mocked)."""
import pandas as pd
import pytest

from src.collectors import onchain as col
from src.indicators import onchain as oc

WL = pd.DataFrame({"address": ["a1", "a2", "w1", "w2"], "entity": ["Binance", "Binance", "Unlabelled whale", "Unlabelled whale"],
                   "label": ["Binance-1", "Binance-2", "Whale #1", "Whale #2"], "category": ["Exchange"] * 2 + ["Whale"] * 2,
                   "chain": "btc"})
PX = pd.Series([100.0, 200.0], index=pd.to_datetime(["2026-10-20", "2026-10-21"], utc=True))


def tx(txid, t, addr, net, cp=""):
    return {"txid": txid, "time": t, "address": addr, "net_btc": net, "counterparties": cp}


def test_internal_moves_net_out_and_whales_stay_separate():
    t = pd.DataFrame([
        tx("x", "2026-10-20 10:00+00:00", "a1", -5.0), tx("x", "2026-10-20 10:00+00:00", "a2", 4.9999),   # Binance shuffle
        tx("y", "2026-10-21 09:00+00:00", "a1", 2.0),
        tx("z", "2026-10-21 09:30+00:00", "w1", -1.0), tx("z", "2026-10-21 09:30+00:00", "w2", 1.0),       # whale -> whale
    ])
    m = oc.btc_moves(t, WL, PX)
    assert bool(m.loc[m["txid"] == "x", "internal"].iloc[0])
    assert set(m.loc[m["txid"] == "z", "group"]) == {"Whale #1", "Whale #2"}          # different owners: real flows
    assert m.loc[m["txid"] == "y", "usd"].iloc[0] == pytest.approx(400.0)            # that day's close
    f = oc.window_flows(m, pd.Timestamp("2026-10-01", tz="UTC"))
    assert f.loc["Binance", "net_btc"] == pytest.approx(2.0) and f.loc["Binance", "txs"] == 1
    assert f.loc["Whale #1", "outflow_btc"] == -1.0


def test_window_and_coverage():
    t = pd.DataFrame([tx("y", "2026-10-21 09:00+00:00", "a1", 2.0)])
    m = oc.btc_moves(t, WL, PX)
    f = oc.window_flows(m, pd.Timestamp("2026-10-15", tz="UTC"), {"Binance": "2026-10-20T00:00:00+00:00"})
    assert f.loc["Binance", "coverage"].startswith("complete from 20 Oct")
    assert oc.window_flows(m, pd.Timestamp("2026-10-22", tz="UTC")).empty


def _page(ids, addr="a1"):
    return [{"txid": i, "status": {"confirmed": True, "block_time": 1_790_000_000 - n},
             "vin": [{"prevout": {"scriptpubkey_address": "other", "value": 100}}],
             "vout": [{"scriptpubkey_address": addr, "value": 100}]} for n, i in enumerate(ids)]


def test_btc_sync_pages_until_a_known_tx(monkeypatch):
    pages = {"": _page([f"t{i}" for i in range(25)]), "/t24": _page([f"t{i}" for i in range(25, 50)])}
    monkeypatch.setattr(col, "_btc_get", lambda path: pages[path.split("/txs/chain")[1]])
    monkeypatch.setattr(col.time, "sleep", lambda *_: None)
    rows, complete = col.btc_sync("a1", {"t30"})
    assert complete and len(rows) == 30 and rows["net_btc"].iloc[0] == pytest.approx(1e-6)


def test_btc_sync_reports_a_hole_when_too_busy(monkeypatch):
    monkeypatch.setattr(col, "_btc_get", lambda path: _page([f"{path}-{i}" for i in range(25)]))
    monkeypatch.setattr(col.time, "sleep", lambda *_: None)
    rows, complete = col.btc_sync("a1", {"never"}, max_pages=3)
    assert not complete and len(rows) == 75
