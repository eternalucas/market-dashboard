"""Venue adapter interface. Every venue declares what it can actually
provide (checked against its docs, see docs/venues.md); the review code asks
for a capability and degrades with a visible note when a venue lacks it.

Read-only: adapters only call public data endpoints. Nothing here signs,
places orders or touches wallets.
"""
from __future__ import annotations

import os
import random
import threading
import time
from dataclasses import dataclass, field

import requests

USER_AGENT = "market-dashboard/0.2 (read-only research; trade review)"


@dataclass(frozen=True)
class Capabilities:
    leaderboard: bool = False
    fills_history: bool = False
    funding_history: bool = False
    liquidations: bool = False
    account_state: bool = False
    candles: bool = False
    orderbook_history: bool = False
    notes: dict = field(default_factory=dict)   # capability -> limitation shown in the UI

    def missing(self) -> list[str]:
        return [k for k in ("leaderboard", "fills_history", "funding_history", "liquidations", "account_state",
                            "candles", "orderbook_history") if not getattr(self, k)]


class RateLimiter:
    """Weight budget per rolling minute, shared by every thread using the
    same venue. `spend(w)` blocks until w fits in the last 60 seconds."""

    def __init__(self, per_minute: float):
        self.per_minute = per_minute
        self._events: list[tuple[float, float]] = []
        self._lock = threading.Lock()

    def spend(self, weight: float) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._events = [(t, w) for t, w in self._events if now - t < 60]
                used = sum(w for _, w in self._events)
                if used + weight <= self.per_minute or not self._events:
                    self._events.append((now, weight))
                    return
                wait = 60 - (now - self._events[0][0]) + 0.05
            time.sleep(max(wait, 0.05))


class RetryableError(Exception):
    pass


def with_backoff(fn, retries: int = 5, base: float = 1.0, cap: float = 60.0):
    """Call fn(); on rate limiting (429), server errors and timeouts, wait
    base * 2^attempt seconds (with jitter, capped) and try again."""
    for attempt in range(retries + 1):
        try:
            return fn()
        except (requests.Timeout, requests.ConnectionError, RetryableError) as e:
            err = e
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            if code != 429 and code < 500:
                raise
            err = e
        if attempt == retries:
            raise err
        retry_after = None
        if isinstance(err, requests.HTTPError) and err.response is not None:
            retry_after = err.response.headers.get("Retry-After")
        delay = float(retry_after) if retry_after and str(retry_after).replace(".", "").isdigit() else \
            min(cap, base * 2 ** attempt) * (0.5 + random.random())
        time.sleep(delay)


def secret(name: str) -> str | None:
    """API keys come from environment variables only (none are needed today)."""
    return os.environ.get(name) or None


class VenueAdapter:
    """Every method returns pandas objects in the normalized schema of
    src/review/store.py, or raises NotImplementedError when the venue lacks
    the capability."""
    name: str = ""
    label: str = ""          # badge text
    caps = Capabilities()

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    # ----- per-account
    def fills(self, account_id: str, start_ms: int = 0, end_ms: int | None = None):
        raise NotImplementedError

    def funding(self, account_id: str, start_ms: int = 0):
        raise NotImplementedError

    def ledger(self, account_id: str, start_ms: int = 0):
        raise NotImplementedError

    def account_state(self, account_id: str):
        raise NotImplementedError

    def equity_history(self, account_id: str):
        raise NotImplementedError

    # ----- market
    def leaderboard(self):
        raise NotImplementedError

    def candles(self, coin: str, interval: str, start_ms: int, end_ms: int):
        raise NotImplementedError

    def market_funding(self, coin: str, start_ms: int):
        raise NotImplementedError
