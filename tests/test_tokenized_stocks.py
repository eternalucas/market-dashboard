"""Tokenized equities DEX volume: issuer matching, pool selection and
aggregation by issuer and chain (network mocked)."""
import os
import tempfile
from pathlib import Path

import pandas as pd
import pytest

os.environ.setdefault("MARKET_DB", str(Path(tempfile.mkdtemp()) / "t.db"))

from src import storage  # noqa: E402
from src.sources import tokenized_stocks as ts  # noqa: E402


@pytest.mark.parametrize("cid,issuer", [
    ("nvidia-robinhood-tokenized-stock", "Robinhood"), ("snap-inc-robinhood-token", "Robinhood"),
    ("coinbase-xstock", "xStocks"), ("wrapped-nvidia-xstock", "xStocks"), ("tesla-coinbase-tokenized-stocks", "Coinbase"),
    ("applied-materials-ondo-tokenized-stocks", "Ondo"), ("nebius-group-ondo-tokenized", "Ondo"),
    ("securitize-bstocks-tokenized-stock", "bStocks"), ("circle-rstock-2", "Remora"),
    ("spacex-backpack-securities", "Backpack"), ("apple-reality-protocol", "reality"), ("currenc-group-inc", None),
])
def test_issuer_from_coin_id(cid, issuer):
    assert ts.issuer_of(cid, {}) == issuer


def test_category_beats_id_pattern():
    assert ts.issuer_of("odd-name", {"odd-name": "Dinari"}) == "Dinari"


def test_update_sums_pools_by_issuer_and_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "REGISTRY", tmp_path / "reg.json")
    reg = {"as_of": pd.Timestamp.now(tz="UTC").isoformat(), "tokens": 2, "unmatched_coins": 0, "pools": {
        "solana_p1": {"network": "solana", "address": "p1", "issuer": "xStocks", "coin": "tesla-xstock", "name": "TSLAx/USDC"},
        "solana_p2": {"network": "solana", "address": "p2", "issuer": "xStocks", "coin": "nvidia-xstock", "name": "NVDAx/USDC"},
        "robinhood_p3": {"network": "robinhood", "address": "p3", "issuer": "Robinhood", "coin": "x", "name": "NVDA/USDG"}}}
    (tmp_path / "reg.json").write_text(__import__("json").dumps(reg))
    days = pd.date_range(pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=3), periods=4, freq="D")
    vols = {"p1": [10.0, 20, 30, 999], "p2": [5.0, 5, 5, 999], "p3": [15.0, 25, 35, 999]}
    monkeypatch.setattr(ts, "pool_daily_volume", lambda net, addr, n: pd.Series(vols[addr], index=days))
    con = storage.connect(tmp_path / "db.sqlite")
    ts.update(con, say=lambda *_: None)
    x = storage.load_series(con, "tsv:issuer:xStocks")
    assert list(x) == [15.0, 25.0, 35.0]                       # today's partial bucket left out
    assert list(storage.load_series(con, "tsv:chain:robinhood")) == [15.0, 25.0, 35.0]
    assert list(storage.load_series(con, "tsv:total")) == [30.0, 50.0, 70.0]
