"""StateSink that writes files only (precompute). spec/03 §5, §12; spec/00 §4.

`cache/{scn}/{branch}/{k}/bundle.json` + `index.json`, the layout the fixture uses, so
file mode (FileSource / the local API) can replay a precompute without a hub. It mirrors
the alerts it publishes, so cooldown and supersede work across a precompute run.
"""

from __future__ import annotations

from pathlib import Path
import hashlib

from ember.contracts import ActivityEvent, Briefing, CycleBundle, Report
from ember.runtime import cache
from ember.sync.sink import HubSnapshot, apply_alerts


class CacheSink:
    def __init__(self, root: Path, zones: list[dict] | None = None) -> None:
        self.root = Path(root)
        self.zones = {z["zone_id"]: {**z, "evac_min": z["evac_min_default"]} for z in zones or []}
        self.alerts: dict = {}
        self.written: list[str] = []  # bundle_ids, in publish order

    def _write(self, b: CycleBundle) -> None:
        path = cache.cycle_dir(self.root, b.scenario_id, b.branch_id, b.knowledge_sim_ms) / "bundle.json"
        raw = b.model_dump_json().encode("utf-8")
        # Immutable version first; index references this exact payload after the pointer commits.
        version = path.parent / "bundles" / (hashlib.sha256(raw).hexdigest() + ".json")
        cache._write_atomic(version, raw)
        cache._write_atomic(path, raw)
        apply_alerts(self.alerts, b.alerts)
        self.written.append(b.bundle_id)

    async def publish(self, b: CycleBundle) -> None:
        self._write(b)
        cache.write_index(self.root, b.scenario_id, b.branch_id)

    async def publish_many(self, bs: list[CycleBundle]) -> None:
        for b in bs:
            self._write(b)
        for scn, branch in {(b.scenario_id, b.branch_id) for b in bs}:
            cache.write_index(self.root, scn, branch)

    async def poll(self) -> HubSnapshot:
        return HubSnapshot(zones=self.zones, alerts=list(self.alerts.values()))

    async def load_scenario(self, payload: dict) -> None: ...
    async def rewind(self, scn: str, branch: str, sim_ms: int) -> None: ...
    async def set_clock_status(self, scn: str, status: str) -> None: ...
    async def create_branch(self, branch: dict) -> None: ...
    async def upsert_alert_draft(self, alert_id: str, text: str, voice: str, source: str, briefing: Briefing) -> None: ...
    async def update_alert_call(self, alert_id: str, state: str, call_id: str | None, call_status: str | None) -> None: ...
    async def append_events(self, events: list[ActivityEvent]) -> None: ...
    async def submit_report_engine(self, report: Report, transcript: str | None) -> None: ...
    async def mark_command(self, command_id: str, status: str, result: dict) -> None: ...
