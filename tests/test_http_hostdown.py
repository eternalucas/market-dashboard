"""An unreachable host fails fast and is skipped for a while (no 2-minute waits per call)."""
import pytest
import requests

from src.collectors import http


@pytest.fixture(autouse=True)
def _clean():
    http._down.clear()
    yield
    http._down.clear()


def test_connect_timeout_is_not_retried_and_host_is_then_skipped(monkeypatch):
    calls, sleeps = [], []
    def boom(url, params=None, timeout=None):
        calls.append(url)
        raise requests.exceptions.ConnectTimeout("no route")
    monkeypatch.setattr(http._session, "get", boom)
    monkeypatch.setattr(http.time, "sleep", lambda s: sleeps.append(s))
    for _ in range(2):
        with pytest.raises(requests.exceptions.ConnectionError):
            http.get_json("https://dead.example/a")
    assert len(calls) == 2 and sleeps == []          # one attempt per call, no backoff waits
    with pytest.raises(http.HostDown):
        http.get_json("https://dead.example/b")
    assert len(calls) == 2                            # skipped without touching the network


def test_host_recovers_after_success(monkeypatch):
    class R:
        status_code = 200
        headers = {}
        def raise_for_status(self): pass
        def json(self): return {"ok": 1}
    monkeypatch.setattr(http._session, "get", lambda *a, **k: R())
    http._connect_failed("ok.example")
    assert http.get_json("https://ok.example/x") == {"ok": 1}
    assert "ok.example" not in http._down


def test_persistent_429_rests_the_host(monkeypatch):
    class R:
        status_code = 429
        headers = {"Retry-After": "0"}
        def raise_for_status(self): raise requests.exceptions.HTTPError("429")
    monkeypatch.setattr(http._session, "get", lambda *a, **k: R())
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    with pytest.raises(requests.exceptions.HTTPError):
        http.get_json("https://busy.example/a")
    with pytest.raises(http.HostDown):
        http.get_json("https://busy.example/b")


def test_btc_calls_rotate_and_skip_dead_explorers(monkeypatch):
    from src.collectors import onchain
    used = []
    def fake(url, params=None):
        used.append(url.split("/api")[0])
        if "mempool.space" in url:
            raise requests.exceptions.ConnectionError("blocked")
        return {"ok": 1}
    monkeypatch.setattr(onchain, "get_json", fake)
    monkeypatch.setattr(onchain.time, "sleep", lambda s: None)
    for _ in range(6):
        assert onchain._btc_get("/address/x") == {"ok": 1}
    ok = [u for u in used if "mempool.space" not in u]
    assert {"https://mempool.emzy.de", "https://blockstream.info"} <= set(ok)   # load is shared


def test_mempool_state_uses_mirror(monkeypatch):
    from src.collectors import mempool_space as ms
    def fake(url, params=None):
        if url.startswith("https://mempool.space"):
            raise requests.exceptions.ConnectionError("blocked")
        if url.endswith("/mempool"):
            return {"count": 3, "vsize": 300, "total_fee": 600, "fee_histogram": []}
        return {"fastestFee": 4, "halfHourFee": 3, "hourFee": 2, "economyFee": 1, "minimumFee": 1}
    monkeypatch.setattr(ms, "get_json", fake)
    assert ms.mempool_state()["rec_fastestFee"] == 4.0
