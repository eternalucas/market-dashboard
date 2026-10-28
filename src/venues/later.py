"""Venues not built yet. Each declares no capabilities until its docs are
checked (Step 0 in docs/venues.md), so the UI can list them honestly."""
from __future__ import annotations

from .base import Capabilities, VenueAdapter


class _Unchecked(VenueAdapter):
    caps = Capabilities(notes={"all": "Not checked yet (see docs/venues.md)"})


class Paradex(_Unchecked):
    name, label = "paradex", "Paradex"


class Aster(_Unchecked):
    name, label = "aster", "Aster"


class EdgeX(_Unchecked):
    name, label = "edgex", "edgeX"


class DydxV4(_Unchecked):
    name, label = "dydx", "dYdX"
