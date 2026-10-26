from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("MARKET_CONFIG", ROOT / "config.yaml"))  # override for tests
EXAMPLE_PATH = ROOT / "config.example.yaml"   # used until you create config.yaml
MANUAL_DIR = ROOT / "data" / "manual"


def load_config(path: Path = CONFIG_PATH) -> dict:
    """Your config.yaml layered over config.example.yaml.

    Settings added in a new version of the dashboard come from the example
    file, so an older config.yaml (with your API keys) keeps working."""
    with open(EXAMPLE_PATH, encoding="utf-8") as f:
        base = yaml.safe_load(f)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            base = _merge(base, yaml.safe_load(f) or {})
    return base


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out
