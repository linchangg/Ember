"""StateSink Protocol and the HubSnapshot the publisher reads. spec/03 §12, §10."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from ember.contracts import ActivityEvent, Alert, Briefing, ClockState, CycleBundle, Person, Report


class PublicationUnavailable(RuntimeError):
    """Transient hub publication failure; the loop must retain its pending batch."""


@dataclass
class HubSnapshot:
    commands: list[dict] = field(default_factory=list)  # pending, sorted by created_wall_ms
    clock: ClockState | None = None
    zones: dict[str, dict] = field(default_factory=dict)  # zone_id -> hub row (evac_min is operator-editable)
    people: list[Person] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)  # mirror of the hub alert table (this scenario)


class StateSink(Protocol):
    async def load_scenario(self, payload: dict) -> None: ...
    async def publish(self, b: CycleBundle) -> None: ...
    async def publish_many(self, bs: list[CycleBundle]) -> None: ...
    async def rewind(self, scn: str, branch: str, sim_ms: int) -> None: ...
    async def set_clock_status(self, scn: str, status: str) -> None: ...
    async def create_branch(self, branch: dict) -> None: ...
    async def upsert_alert_draft(self, alert_id: str, text: str, voice: str, source: str, briefing: Briefing) -> None: ...
    async def update_alert_call(self, alert_id: str, state: str, call_id: str | None, call_status: str | None) -> None: ...
    async def append_events(self, events: list[ActivityEvent]) -> None: ...
    async def submit_report_engine(self, report: Report, transcript: str | None) -> None: ...
    async def poll(self) -> HubSnapshot: ...  # commands + clock + zones + people + alerts
    async def mark_command(self, command_id: str, status: str, result: dict) -> None: ...


# ---------------------------------------------------------------- hub rows -> contract types


def _adapt(row: dict[str, Any]) -> dict[str, Any]:
    """`*_json` -> parsed field without the suffix; f64 `*_ms` -> int (hub stores times as f64)."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if k.endswith("_json") and isinstance(v, str):
            out[k.removesuffix("_json")] = json.loads(v)
        elif k.endswith("_ms") and isinstance(v, float):
            out[k] = round(v)
        else:
            out[k] = v
    return out


def alert_from_row(row: dict[str, Any]) -> Alert:
    return Alert.model_validate(_adapt(row))


def person_from_row(row: dict[str, Any]) -> Person:
    return Person.model_validate(_adapt(row))


def clock_from_row(row: dict[str, Any] | None) -> ClockState | None:
    return None if row is None else ClockState.model_validate(_adapt(row))


def apply_alerts(mirror: dict[str, Alert], alerts: list[Alert]) -> None:
    """Hub publish rule (04 §2.2 step 3): insert new; existing rows only go proposed -> superseded."""
    for a in alerts:
        prev = mirror.get(a.alert_id)
        if prev is None:
            mirror[a.alert_id] = a
        elif prev.state == "proposed" and a.state == "superseded":
            mirror[a.alert_id] = prev.model_copy(update={"state": "superseded"})
