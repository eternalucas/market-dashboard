"""Regression tests for audit fixes B-01/M-01, B-02/M-03, B-03, B-05, B-06,
B-11 and E-04 (fix agent F4). Every database is a throwaway file in tmp_path."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import threading
from pathlib import Path

import pandas as pd
import pytest
import requests

from src import pipeline, recorder, storage, views
from src.collectors import http
from src.sources import fred_macro, registry, social

ROOT = Path(__file__).resolve().parent.parent
CFG = {"assets": ["BTC", "ETH"], "positioning": {"perp_exchanges": ["binance"]}, "api_keys": {}}


def _db(tmp_path, rows=()):
    con = storage.connect(tmp_path / "m.db")
    con.executemany("INSERT INTO fetch_log VALUES (?, ?, ?, ?, ?)", rows)
    con.commit()
    return con


# --------------------------------------------------------------------------- #
# B-01 + M-01: every part of run_all has a scheduled recorder job
# --------------------------------------------------------------------------- #
RUN_ALL_PARTS = [k for k, *_ in pipeline.steps(CFG)] + list(pipeline.RUN_ALL_EXTRAS)


@pytest.mark.parametrize("part", RUN_ALL_PARTS)
def test_every_run_all_part_has_a_recorder_job(part):
    jobs = dict(recorder.Recorder.JOBS)
    job = recorder.Recorder.PIPELINE_JOBS.get(part)
    assert job, f"{part} is only written by 'Update all data'"
    assert job in jobs and jobs[job] <= 3600, f"{part}: job {job} is not scheduled at least hourly"
    assert callable(getattr(recorder.Recorder, f"job_{job}"))


def test_recorder_jobs_really_run_every_step(tmp_path, monkeypatch):
    called = []
    for key, _label, fn, _src, _age in pipeline.steps(CFG):
        monkeypatch.setattr(pipeline, fn.__name__, lambda con, cfg, key=key: called.append(key))
    monkeypatch.setattr(pipeline, "update_flows", lambda con, cfg: called.append("flows"))
    monkeypatch.setattr(pipeline, "positioning_snapshot", lambda con, cfg, a: called.append(f"pos:{a}"))
    monkeypatch.setattr("src.config.load_config", lambda: CFG)
    real = storage.connect
    monkeypatch.setattr(storage, "connect", lambda *a, **k: real(tmp_path / "m.db"))
    r = recorder.Recorder()
    for job in ("pipeline", "onchain", "flows", "positioning"):
        getattr(r, f"job_{job}")()
        r._bg_threads[job].join(10)
    assert set(called) == {k for k, *_ in pipeline.steps(CFG)} | {"flows", "pos:BTC", "pos:ETH"}


def test_sidebar_caption_no_longer_claims_everything_refreshes():
    app = (ROOT / "app.py").read_text()
    assert "refreshes every source on its own" not in app
    assert "src:social" not in app.split("# Social Risk now lives in Studio")[0].split("if log.empty")[-1]


# --------------------------------------------------------------------------- #
# B-02 + M-03: Stale is about the last success, not the last attempt
# --------------------------------------------------------------------------- #
NOW = pd.Timestamp("2026-11-02T18:00:00Z")


def test_health_flags_old_and_empty_successes(tmp_path):
    con = _db(tmp_path, [
        ("2026-11-02T17:30:00+00:00", "perp:binance:BTC", 1, 1, ""),           # hourly, fresh
        ("2026-11-02T12:00:00+00:00", "perp:okx:BTC", 1, 1, ""),               # hourly, 6 h old: Stale
        ("2026-11-01T20:00:00+00:00", "price:BTC", 1, 5, ""),                  # daily, 22 h: OK
        *[(f"2026-11-02T1{i}:00:00+00:00", "wikipedia:en.wikipedia:X", 1, 0, "") for i in range(3)],  # 0 rows x3
        *[(f"2026-11-02T1{i}:00:00+00:00", "onchain:btc:W", 1, 0, "") for i in range(3)],  # 0 is normal here
    ])
    h = views.source_health(con, now=NOW).set_index("Source")
    assert h.loc["perp:binance:BTC", "Status"] == "OK"
    assert h.loc["perp:okx:BTC", "Status"] == "Stale"            # old rule: OK (attempt < 48 h ago)
    assert h.loc["price:BTC", "Status"] == "OK"
    assert h.loc["wikipedia:en.wikipedia:X", "Status"] == "Stale"
    assert "0 rows" in h.loc["wikipedia:en.wikipedia:X", "Last error"]
    assert h.loc["onchain:btc:W", "Status"] == "OK"


def test_banner_fires_on_old_prices_even_if_sources_ran(tmp_path):
    con = _db(tmp_path, [
        ("2026-11-02T17:42:00+00:00", "src:coinmetrics", 1, 9, ""),
        ("2026-11-02T17:42:00+00:00", "src:social", 1, 9, ""),
        ("2026-10-26T17:57:00+00:00", "price:BTC", 1, 5, ""),
    ])
    msg = views.staleness_banner(views.source_health(con, now=NOW), ["BTC"])
    assert msg and "price:BTC" in msg
    con.execute("DELETE FROM fetch_log WHERE source='price:BTC'")
    con.commit()
    assert views.staleness_banner(views.source_health(con, now=NOW), ["BTC"]) is None


# --------------------------------------------------------------------------- #
# B-03: a total failure is not a success and does not reset freshness
# --------------------------------------------------------------------------- #
def test_fred_macro_all_failed_logs_failure(tmp_path, monkeypatch):
    con = _db(tmp_path)
    monkeypatch.setattr(fred_macro.fred, "series", lambda *a, **k: (_ for _ in ()).throw(ValueError("html")))
    monkeypatch.setattr(registry, "SOURCES", [s for s in registry.SOURCES if s[0] == "fred_macro"])
    monkeypatch.setattr(registry.bgeometrics, "update", lambda con: 0)
    registry.update_all(con, {"api_keys": {"fred": "k"}}, say=lambda *_: None)
    assert con.execute("SELECT ok FROM fetch_log WHERE source='src:fred_macro'").fetchall() == [(0,)]
    assert not registry._fresh(con, "fred_macro", pd.Timedelta(hours=20))


def test_registry_zero_rows_does_not_reset_freshness(tmp_path, monkeypatch):
    con = _db(tmp_path)
    monkeypatch.setattr(registry, "SOURCES", [("dummy", lambda con, cfg: 0, pd.Timedelta(hours=12))])
    monkeypatch.setattr(registry.bgeometrics, "update", lambda con: 0)
    registry.update_all(con, {}, say=lambda *_: None)
    assert not registry._fresh(con, "dummy", pd.Timedelta(hours=12))


def test_social_all_inputs_failed_raises(tmp_path, monkeypatch):
    con = _db(tmp_path)
    boom = lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("down"))  # noqa: E731
    monkeypatch.setattr(social.attention, "wikipedia_pageviews", boom)
    monkeypatch.setattr(social, "project_total", boom)
    monkeypatch.setattr(social, "WIKI_PACE_S", 0)
    for f in ("update_app_store", "update_youtube", "update_google_trends"):
        monkeypatch.setattr(social, f, lambda con, cfg: 0)
    monkeypatch.setattr(social, "update_gdelt", boom)
    monkeypatch.setattr(social, "update_hacker_news", boom)
    with pytest.raises(RuntimeError, match="0 rows"):
        social.update(con, {})


# --------------------------------------------------------------------------- #
# B-05: a locked database neither loses the log row nor aborts the update
# --------------------------------------------------------------------------- #
def test_log_fetch_retries_a_locked_database(tmp_path, monkeypatch):
    path = tmp_path / "lock.db"
    storage.connect(path).close()
    holder = sqlite3.connect(path, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    threading.Timer(1.5, holder.rollback).start()
    con = sqlite3.connect(path, timeout=0.2)
    storage.log_fetch(con, "x", True, 1)          # old code: OperationalError after 0.2 s
    assert con.execute("SELECT COUNT(*) FROM fetch_log WHERE source='x'").fetchone()[0] == 1


def test_social_log_survives_a_logging_failure(tmp_path, monkeypatch):
    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(storage, "log_fetch", locked)
    assert social._log(None, "wikipedia:x", lambda: (_ for _ in ()).throw(ValueError("x"))) == 0
    assert social._log(None, "wikipedia:y", lambda: 3) == 3


# --------------------------------------------------------------------------- #
# B-06: 429/503 are retried with Retry-After and bounded backoff
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, code, headers=None, body=None):
        self.status_code, self.headers, self._body = code, headers or {}, body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        return self._body


def test_get_json_honours_retry_after(monkeypatch):
    answers = iter([_Resp(429, {"Retry-After": "7"}), _Resp(503), _Resp(200, body={"ok": 1})])
    slept = []
    monkeypatch.setattr(http._session, "get", lambda *a, **k: next(answers))
    monkeypatch.setattr(http.time, "sleep", slept.append)
    assert http.get_json("https://x") == {"ok": 1}
    assert slept[0] == 7 and 0 < slept[1] <= http.MAX_WAIT


def test_get_json_gives_up_after_bounded_retries(monkeypatch):
    calls, slept = [], []
    monkeypatch.setattr(http._session, "get", lambda *a, **k: calls.append(1) or _Resp(429, {"Retry-After": "999"}))
    monkeypatch.setattr(http.time, "sleep", slept.append)
    with pytest.raises(requests.HTTPError):
        http.get_json("https://x")
    assert len(calls) == http.RETRIES + 1 and max(slept) == http.MAX_WAIT


def test_wikipedia_loop_is_paced_and_stops_after_429(tmp_path, monkeypatch):
    con = _db(tmp_path)
    calls, slept = [], []

    def limited(*a, **k):
        calls.append(1)
        raise requests.HTTPError("429", response=_Resp(429))
    monkeypatch.setattr(social.attention, "wikipedia_pageviews", limited)
    monkeypatch.setattr(social, "project_total", limited)
    monkeypatch.setattr(social.time, "sleep", slept.append)
    social.update_wikipedia(con, {})
    assert len(calls) == 1                         # old code: one 429 per article and project (~48)


# --------------------------------------------------------------------------- #
# B-11: a backtest left 'running' by a dead process is shown as interrupted
# --------------------------------------------------------------------------- #
def _runs_db(tmp_path, monkeypatch):
    from src.review import store
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "trades.db")
    con = store.connect()
    con.execute("INSERT INTO tr_backtest_runs VALUES ('batch-x', 'hyperliquid', '*', '30d', 'h', 'batch', 'running', "
                "0.116, 'Backtesting trader 13 of 100', '2026-10-28T00:33:31+00:00', NULL, NULL)")
    con.commit()
    return con


def test_stuck_running_batch_is_interrupted(tmp_path, monkeypatch):
    from src.review import jobs
    con = _runs_db(tmp_path, monkeypatch)
    run = jobs.get(con, "batch-x")
    assert run["status"] == "interrupted"          # old code: 'running' forever, button disabled
    assert not (run["status"] == "running")        # ui_batch: disabled=running


def test_live_heartbeat_from_another_process_stays_running(tmp_path, monkeypatch):
    from src.review import jobs
    con = _runs_db(tmp_path, monkeypatch)
    jobs.beat(con, "batch-x")
    con.execute("UPDATE tr_job_heartbeat SET pid=? WHERE run_id='batch-x'", (os.getpid() + 1,))
    con.commit()
    assert jobs.get(con, "batch-x")["status"] == "running"
    old = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=11)).isoformat()
    con.execute("UPDATE tr_job_heartbeat SET ts=? WHERE run_id='batch-x'", (old,))
    con.commit()
    assert jobs.get(con, "batch-x")["status"] == "interrupted"


# --------------------------------------------------------------------------- #
# E-04: .gitignore covers local archives, skills copies and databases
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ["claude-skills/a.md", "x/b.zip", "md-audit-extracts/c", "src/d.db",
                                  "e.db-journal", "f.db-wal", "g.db-shm", ".backup-2026/h"])
def test_gitignore_covers(path):
    r = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT)
    assert r.returncode == 0, f"{path} is not ignored"
