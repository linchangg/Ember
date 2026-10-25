"""StateSink over the SpacetimeDB HTTP client. spec/03 §12, spec/04 §3.

StdbHttp already retries network errors and 5xx. Whatever still fails here (a reducer
rejection, or the hub being down) is logged, turned into an `error` activity event when
the hub accepts one, and swallowed: the engine loop never crashes because of the hub.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from emberline.contracts import ActivityEvent, Briefing, CycleBundle, Report
from emberline.sync.sink import HubSnapshot, PublicationUnavailable, alert_from_row, clock_from_row, person_from_row
from emberline.sync.stdb_http import StdbHttp

log = logging.getLogger(__name__)
MAX_BATCH = 10


class StdbSink:
    def __init__(self, hub: StdbHttp, scenario_id: str, sim_now: Callable[[], int] | None = None) -> None:
        self.hub, self.scenario_id = hub, scenario_id
        self.sim_now = sim_now or (lambda: 0)
        self.ok = True  # hub_ok for /healthz
        self.last = HubSnapshot()
        self._n_err = 0

    async def _safe(self, what: str, fn: Callable[[], Awaitable[Any]]) -> bool:
        try:
            await fn()
            self.ok = True
            return True
        except Exception as e:  # noqa: BLE001 (never crash the loop because of the hub)
            self.ok = False
            log.error("hub %s failed: %s", what, e)
            await self._error_event(f"Hub {what} failed: {type(e).__name__}")
            return False

    async def _error_event(self, message: str) -> None:
        self._n_err += 1
        wall = int(time.time() * 1000)
        ev = ActivityEvent(
            event_id=f"ev_err{wall}_{self._n_err}", scenario_id=self.scenario_id, branch_id="main",
            sim_ms=self.sim_now(), wall_ms=wall, stage="system", actor="engine", level="error", message=message[:140],
        )
        try:
            await self.hub.append_events([ev])
        except Exception as e:  # noqa: BLE001
            log.error("hub append_events (error event) failed: %s", e)

    # ------------------------------------------------------------ StateSink

    async def load_scenario(self, payload: dict) -> None:
        await self._safe("load_scenario", lambda: self.hub.load_scenario(payload))

    async def publish(self, b: CycleBundle) -> None:
        if not await self._safe("publish_bundle", lambda: self.hub.publish_bundle(b)):
            raise PublicationUnavailable("Hub did not acknowledge cycle publication")

    async def publish_many(self, bs: list[CycleBundle]) -> None:
        for i in range(0, len(bs), MAX_BATCH):
            chunk = bs[i : i + MAX_BATCH]
            if not await self._safe("publish_bundles", lambda c=chunk: self.hub.publish_bundles(c)):
                raise PublicationUnavailable("Hub did not acknowledge batch publication")

    async def rewind(self, scn: str, branch: str, sim_ms: int) -> None:
        if not await self._safe("rewind", lambda: self.hub.rewind(scn, branch, sim_ms)):
            raise PublicationUnavailable("Hub did not acknowledge rewind")

    async def set_clock_status(self, scn: str, status: str) -> None:
        await self._safe("set_clock_status", lambda: self.hub.set_clock_status(scn, status))

    async def create_branch(self, branch: dict) -> None:
        if not await self._safe("create_branch", lambda: self.hub.create_branch(branch)):
            raise PublicationUnavailable("Hub did not acknowledge branch creation")

    async def upsert_alert_draft(self, alert_id: str, text: str, voice: str, source: str, briefing: Briefing) -> None:
        await self._safe("upsert_alert_draft", lambda: self.hub.upsert_alert_draft(alert_id, text, voice, source, briefing))

    async def update_alert_call(self, alert_id: str, state: str, call_id: str | None, call_status: str | None) -> None:
        await self._safe("update_alert_call", lambda: self.hub.update_alert_call(alert_id, state, call_id, call_status))

    async def append_events(self, events: list[ActivityEvent]) -> None:
        if events:
            await self._safe("append_events", lambda: self.hub.append_events(events))

    async def submit_report_engine(self, report: Report, transcript: str | None) -> None:
        payload = report.model_dump(mode="json")
        await self._safe("submit_report_engine", lambda: self.hub.submit_report_engine(payload, transcript or ""))

    async def mark_command(self, command_id: str, status: str, result: dict) -> None:
        await self._safe("mark_command", lambda: self.hub.mark_command(command_id, status, result))

    async def poll(self) -> HubSnapshot:
        """Commands for this scenario + clock + zones + people + alerts. The last good snapshot on failure."""
        sid = self.scenario_id.replace("'", "''")
        try:
            raw = await self.hub.poll(self.scenario_id)
            (alerts,) = await self.hub.sql(f"SELECT * FROM alert WHERE scenario_id = '{sid}'")
            snap = HubSnapshot(
                commands=[c for c in raw["commands"] if c["scenario_id"] == self.scenario_id],
                clock=clock_from_row(raw["clock"]),
                zones={z["zone_id"]: z for z in raw["zones"]},
                people=[person_from_row(p) for p in raw["people"]],
                alerts=[alert_from_row(a) for a in alerts],
            )
        except Exception as e:  # noqa: BLE001
            self.ok = False
            log.error("hub poll failed: %s", e)
            return HubSnapshot(clock=self.last.clock, zones=self.last.zones, people=self.last.people, alerts=self.last.alerts)
        self.ok = True
        self.last = snap
        return snap
