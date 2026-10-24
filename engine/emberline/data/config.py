"""Scenario config helpers (00_contracts §2). Plain dicts, no model."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]  # repo root


def scenario_dir(scn: str) -> Path:
    return REPO / "data" / "scenarios" / scn


def load_scenario(scn: str) -> dict:
    with open(scenario_dir(scn) / "scenario.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def utc_ms(iso: str) -> int:
    """'2025-01-08T02:15:00Z' -> epoch ms UTC."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    assert dt.tzinfo is not None, f"timestamp must carry a timezone: {iso!r}"
    return int(round(dt.timestamp() * 1000))
