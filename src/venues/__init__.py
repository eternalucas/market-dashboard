"""Venue adapters. Only Hyperliquid is built; the others are listed with no
capabilities until their docs are checked (docs/venues.md). Nado and
Variational were checked on 26 Oct 2026 and excluded (no open per-trader
data comparable to Hyperliquid's)."""
from __future__ import annotations

from .hyperliquid import Hyperliquid
from .later import Aster, DydxV4, EdgeX, Lighter, Paradex

VENUES = {"hyperliquid": Hyperliquid}
PLANNED = {c.name: c for c in (DydxV4, Lighter, Paradex, Aster, EdgeX)}
_instances: dict = {}


def get(name: str):
    if name not in _instances:
        _instances[name] = VENUES[name]()
    return _instances[name]
