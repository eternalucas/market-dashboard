"""Shared HTTP helpers: one session, a timeout, and a clear User-Agent.

Wikimedia's API policy asks every client to identify itself, so the
User-Agent names this project.
"""
from __future__ import annotations

import random
import time

import requests

USER_AGENT = "market-dashboard/0.1 (personal research tool)"
TIMEOUT = 30

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})


RETRY_STATUS = (429, 503)   # rate limited / temporarily unavailable: wait and try again
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


def get_json(url: str, params: dict | None = None):
    """GET and decode JSON. Timeouts and 429/503 answers are retried up to
    RETRIES times with bounded exponential backoff (honouring Retry-After);
    other HTTP errors raise at once."""
    for attempt in range(RETRIES + 1):
        try:
            r = _session.get(url, params=params, timeout=TIMEOUT)
        except requests.exceptions.Timeout:
            if attempt == RETRIES:
                raise
            time.sleep(_wait(attempt))
            continue
        if r.status_code in RETRY_STATUS and attempt < RETRIES:
            time.sleep(_wait(attempt, r))
            continue
        r.raise_for_status()
        return r.json()


def post_json(url: str, payload: dict):
    r = _session.post(url, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()
