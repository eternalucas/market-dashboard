"""Shared HTTP helpers: one session, a timeout, and a clear User-Agent.

Wikimedia's API policy asks every client to identify itself, so the
User-Agent names this project.
"""
from __future__ import annotations

import requests

USER_AGENT = "market-dashboard/0.1 (personal research tool)"
TIMEOUT = 30

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})


def get_json(url: str, params: dict | None = None):
    for attempt in range(2):   # one retry: public explorers time out now and then
        try:
            r = _session.get(url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            if attempt:
                raise


def post_json(url: str, payload: dict):
    r = _session.post(url, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()
