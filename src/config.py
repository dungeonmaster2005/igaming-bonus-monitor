"""Loading of config/operators.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

OPERATORS_PATH = Path("config/operators.yaml")
ALERTS_PATH = Path("config/alerts.yaml")


def load_operators(path: Path = OPERATORS_PATH) -> list[dict[str, Any]]:
    """All operator configs (enabled or not) with defaults merged in."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    defaults: dict[str, Any] = raw.get("defaults", {})
    return [{**defaults, **op} for op in raw.get("operators", [])]


def load_alert_config(path: Path = ALERTS_PATH) -> dict[str, Any]:
    """Alert groups, thresholds and limits (config/alerts.yaml)."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))
