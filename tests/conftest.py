"""Test isolation: every test session gets its own throwaway database, trade
store, recorder lock and config path. These env vars must be set before any
`src` module is imported (paths are read at import time), and conftest.py is
loaded by pytest before any test module, so this is the one place to do it."""
import pytest
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="md-tests-"))
os.environ["MARKET_DB"] = str(_TMP / "market.db")
os.environ["TRADES_DB"] = str(_TMP / "trades.db")
os.environ["RECORDER_LOCK"] = str(_TMP / "recorder.lock")
os.environ["MARKET_CONFIG"] = str(_TMP / "config.yaml")   # absent -> config.example.yaml only


@pytest.fixture(autouse=True)
def _fresh_http_host_state():
    """Hosts marked down or rate-limited by one test must not leak into the next."""
    from src.collectors import http
    http._down.clear()
    yield
    http._down.clear()
