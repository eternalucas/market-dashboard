"""Shared HTTP helpers: one session, a timeout, and a clear User-Agent.

Wikimedia's API policy asks every client to identify itself, so the
User-Agent names this project.
"""
from __future__ import annotations

import random
import threading
import time
from urllib.parse import urlsplit

import requests

USER_AGENT = "market-dashboard/0.1 (personal research tool)"
TIMEOUT = (5, 30)            # (connect, read) seconds: an unreachable host fails in 5 s, not 30

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})


RETRY_STATUS = (429, 503)   # rate limited / temporarily unavailable: wait and try again
SERVER_ERRORS = (500, 502, 504)   # transient server-side errors (e.g. Blockscout): retried, host not rested
RETRIES = 3
MAX_WAIT = 20.0             # seconds per wait, so one call never blocks for more than about a minute


def _wait(attempt: int, resp=None) -> float:
    """Seconds to wait before retry `attempt` (0-based): the server's
    Retry-After when it gives one in seconds, else 1, 2, 4 s with jitter;
    always capped at MAX_WAIT."""
    ra = resp.headers.get("Retry-After") if resp is not None else None
    try:
        delay = float(ra)
    except (TypeError, ValueError):
        delay = 2 ** attempt * (0.5 + random.random())
    return max(0.0, min(MAX_WAIT, delay))


# Hosts that can't be reached are skipped for a while instead of being
# retried on every call: one dead explorer used to cost minutes per wallet.
DOWN_AFTER = 2               # consecutive connection failures before a host is marked down
DOWN_FOR = 300.0             # seconds a down host is skipped
_down: dict[str, list] = {}  # host -> [consecutive failures, skip-until time]
_down_lock = threading.Lock()


class HostDown(requests.exceptions.ConnectionError):
    """Raised at once for a host that failed to connect repeatedly just now."""


def _host(url: str) -> str:
    return urlsplit(url).netloc


def _check_host(host: str) -> None:
    with _down_lock:
        fails, until = _down.get(host, [0, 0.0])
    if until > time.time():
        raise HostDown(f"{host} unreachable (skipped for {until - time.time():.0f} s more)")


def _connect_failed(host: str) -> None:
    with _down_lock:
        fails, _ = _down.get(host, [0, 0.0])
        fails += 1
        _down[host] = [fails, time.time() + DOWN_FOR if fails >= DOWN_AFTER else 0.0]


def _cool(host: str) -> None:
    """Skip a host that keeps rate-limiting us for DOWN_FOR seconds (callers
    with a fallback move on to it at once)."""
    with _down_lock:
        _down[host] = [DOWN_AFTER, time.time() + DOWN_FOR]


def _connect_ok(host: str) -> None:
    with _down_lock:
        _down.pop(host, None)


def get_json(url: str, params: dict | None = None):
    """GET and decode JSON. 429/503 and 500/502/504 answers and read timeouts are retried up
    to RETRIES times with bounded exponential backoff (honouring
    Retry-After). A host that can't be connected to is not retried: after
    DOWN_AFTER such failures in a row it is skipped for DOWN_FOR seconds, so
    callers fall back (or fail) at once. Other HTTP errors raise at once."""
    host = _host(url)
    for attempt in range(RETRIES + 1):
        _check_host(host)
        try:
            r = _session.get(url, params=params, timeout=TIMEOUT)
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError):
            _connect_failed(host)
            raise
        except requests.exceptions.Timeout:          # connected, but the answer was slow
            if attempt == RETRIES:
                raise
            time.sleep(_wait(attempt))
            continue
        _connect_ok(host)
        if r.status_code in RETRY_STATUS + SERVER_ERRORS and attempt < RETRIES:
            time.sleep(_wait(attempt, r))
            continue
        if r.status_code in RETRY_STATUS:          # still refusing after every retry: rest this host
            _cool(host)
        r.raise_for_status()
        return r.json()


def post_json(url: str, payload: dict):
    r = _session.post(url, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()
