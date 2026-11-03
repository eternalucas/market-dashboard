"""Venue adapters: Hyperliquid is built (Lighter and GMX were built and then
removed from Trade review on 2 Nov 2026: their boards were never usable). The
others are listed with no capabilities (docs/venues.md). Nado,
Variational, Paradex, Aster, Extended and edgeX keep per-trader history
behind the account's login; dYdX v4 has no way to find top traders; Drift
was exploited in April 2026 and is relaunching as Velocity."""
from __future__ import annotations

from .hyperliquid import Hyperliquid
from .later import Aster, DydxV4, EdgeX, Paradex

VENUES = {"hyperliquid": Hyperliquid}
LABELS = {"hyperliquid": "Hyperliquid"}
PLANNED = {c.name: c for c in (DydxV4, Paradex, Aster, EdgeX)}
_instances: dict = {}


def get(name: str):
    if name not in _instances:
        _instances[name] = VENUES[name]()
    return _instances[name]
