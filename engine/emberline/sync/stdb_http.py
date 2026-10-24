"""SpacetimeDB HTTP client for the engine. spec/04_hub.md §3.

Observed on spacetime 2.13.0 (G1-b spike):
- A reducer that throws returns HTTP 530 with the error text as body, so 530 is a
  rejection, not a transient 5xx, and is never retried.
- SQL returns `[{"schema": {"elements": [{"name": {"some": col}, ...}]}, "rows": [[...]]}]`;
  rows are positional arrays. Options arrive as `[0, v]` (some) / `[1, []]` (none).
- Multi-statement SQL is rejected ("Multiple SQL statements are not supported"), so
  `sql()` sends one request per `;`-separated statement.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from emberline.contracts import ActivityEvent, Briefing, CycleBundle

log = logging.getLogger(__name__)
BUNDLE_WARN_BYTES = 200_000

BACKOFF_S = (0.5, 1.0, 2.0)
REJECTED_STATUS = 530


class ReducerRejected(Exception):
    def __init__(self, reducer: str, status: int, body: str) -> None:
        super().__init__(f"{reducer} rejected ({status}): {body}")
        self.reducer, self.status, self.body = reducer, status, body


def _is_option(algebraic_type: dict[str, Any]) -> bool:
    variants = algebraic_type.get("Sum", {}).get("variants", [])
    return [v["name"].get("some") for v in variants] == ["some", "none"]


def _decode(value: Any, algebraic_type: dict[str, Any]) -> Any:
    if not _is_option(algebraic_type):
        return value
    if isinstance(value, list):  # what 2.13 actually sends: [0, v] = some, [1, []] = none
        return value[1] if value[0] == 0 else None
    return value.get("some")


def rows_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Map one SQL result set (schema + positional rows) to row dicts."""
    cols = [(e["name"]["some"], e["algebraic_type"]) for e in result["schema"]["elements"]]
    return [{name: _decode(v, at) for (name, at), v in zip(cols, row, strict=True)} for row in result["rows"]]


class StdbHttp:
    def __init__(self, uri: str, db: str, token: str | None) -> None:
        self.uri, self.db, self.token = uri.rstrip("/"), db, token
        self.client = httpx.AsyncClient(timeout=10)

    async def aclose(self) -> None:
        await self.client.aclose()

    def _headers(self, content_type: str) -> dict[str, str]:
        h = {"Content-Type": content_type}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def _post(self, path: str, content: str | None, content_type: str) -> httpx.Response:
        """POST with retry on network errors and transient 5xx (never 4xx or 530)."""
        for attempt in range(len(BACKOFF_S) + 1):
            try:
                r = await self.client.post(self.uri + path, content=content, headers=self._headers(content_type))
                if r.status_code < 500 or r.status_code == REJECTED_STATUS:
                    return r
            except httpx.TransportError:
                if attempt == len(BACKOFF_S):
                    raise
            if attempt < len(BACKOFF_S):
                await asyncio.sleep(BACKOFF_S[attempt])
        return r

    async def new_identity(self) -> tuple[str, str]:
        r = await self._post("/v1/identity", None, "application/json")
        r.raise_for_status()
        d = r.json()
        return d["identity"], d["token"]

    async def call(self, reducer: str, *args: Any) -> None:
        r = await self._post(f"/v1/database/{self.db}/call/{reducer}", json.dumps(list(args)), "application/json")
        if r.status_code >= 400:
            raise ReducerRejected(reducer, r.status_code, r.text)

    async def sql(self, query: str) -> list[list[dict[str, Any]]]:
        """One result set (list of row dicts) per `;`-separated statement.

        ponytail: naive split on `;`, so no `;` inside string literals; fine for our fixed queries.
        """
        out: list[list[dict[str, Any]]] = []
        for stmt in (s.strip() for s in query.split(";")):
            if not stmt:
                continue
            r = await self._post(f"/v1/database/{self.db}/sql", stmt, "text/plain")
            if r.status_code >= 400:
                raise ReducerRejected("sql", r.status_code, r.text)
            out.extend(rows_from_result(res) for res in r.json())
        return out

    # ------------------------------------------------------------ poll loop (04 §3)

    async def poll(self, scenario_id: str) -> dict[str, Any]:
        """Pending commands (sorted by created_wall_ms), the clock, zones and people.

        Multi-statement SQL is unsupported on 2.13, so this is 4 requests.
        """
        sid = scenario_id.replace("'", "''")
        commands, clocks, zones, people = await self.sql(
            "SELECT * FROM command WHERE status = 'pending'; "
            f"SELECT * FROM clock WHERE scenario_id = '{sid}'; "
            f"SELECT * FROM zone WHERE scenario_id = '{sid}'; "
            f"SELECT * FROM person WHERE scenario_id = '{sid}'"
        )
        return {
            "commands": sorted(commands, key=lambda r: r["created_wall_ms"]),
            "clock": clocks[0] if clocks else None,
            "zones": zones,
            "people": people,
        }

    # ------------------------------------------------------------ engine reducers

    async def load_scenario(self, payload: dict[str, Any]) -> None:
        await self.call("load_scenario", json.dumps(payload))

    async def publish_bundle(self, b: CycleBundle) -> None:
        await self.call("publish_bundle", _sized(b.model_dump_json(), b.bundle_id))

    async def publish_bundles(self, bundles: list[CycleBundle]) -> None:
        assert 0 < len(bundles) <= 10, "publish_bundles takes 1-10 bundles"
        body = "[" + ",".join(b.model_dump_json() for b in bundles) + "]"
        await self.call("publish_bundles", _sized(body, f"{len(bundles)} bundles"))

    async def rewind(self, scenario_id: str, branch_id: str, sim_ms: int) -> None:
        await self.call("rewind", scenario_id, branch_id, sim_ms)

    async def set_clock_status(self, scenario_id: str, status: str) -> None:
        await self.call("set_clock_status", scenario_id, status)

    async def create_branch(self, branch: dict[str, Any]) -> None:
        await self.call("create_branch", json.dumps(branch))

    async def upsert_alert_draft(
        self, alert_id: str, text: str, voice_script: str, draft_source: str, briefing: Briefing
    ) -> None:
        await self.call("upsert_alert_draft", alert_id, text, voice_script, draft_source, briefing.model_dump_json())

    async def update_alert_call(self, alert_id: str, state: str, call_id: str | None, call_status: str | None) -> None:
        await self.call("update_alert_call", alert_id, state, call_id or "", call_status or "")

    async def append_events(self, events: list[ActivityEvent]) -> None:
        await self.call("append_events", json.dumps([e.model_dump(mode="json") for e in events]))

    async def mark_command(self, command_id: str, status: str, result: dict[str, Any] | None = None) -> None:
        await self.call("mark_command", command_id, status, "" if result is None else json.dumps(result))

    async def submit_report_engine(self, report: dict[str, Any], transcript: str) -> None:
        await self.call("submit_report_engine", json.dumps(report), transcript)

    # ------------------------------------------------------------ service reducers

    async def submit_report(self, report: dict[str, Any], transcript: str) -> None:
        await self.call("submit_report", json.dumps(report), transcript)

    async def ack_alert(self, alert_id: str, via: str) -> None:
        await self.call("ack_alert", alert_id, via)


def _sized(body: str, what: str) -> str:
    if len(body.encode()) > BUNDLE_WARN_BYTES:
        log.warning("%s is %d bytes (> %d); consider splitting observations", what, len(body.encode()), BUNDLE_WARN_BYTES)
    return body
