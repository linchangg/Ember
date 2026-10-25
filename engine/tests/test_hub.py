"""Hub integration tests, spec/04 §6. Local `spacetime start` only.

EMBERLINE_HUB_TESTS=1 uv run pytest -q tests/test_hub.py
Needs STDB_TOKEN, STDB_SERVICE_TOKEN and OPERATOR_PIN (engine/.env is read if present).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from emberline.contracts import ActivityEvent, CycleBundle
from emberline.sync.stdb_http import ReducerRejected, StdbHttp

pytestmark = pytest.mark.skipif(not os.environ.get("EMBERLINE_HUB_TESTS"), reason="hub")

ENGINE = Path(__file__).resolve().parents[1]
FIXTURES = ENGINE.parent / "fixtures"
STATIC = FIXTURES / "static" / "fixture"
SCN = "fixture"
CYCLE_MS = 300_000


def _load_env() -> None:
    env = ENGINE / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                os.environ.setdefault(key.strip(), value.split("#")[0].strip())


_load_env()
URI = os.environ.get("STDB_URI", "http://localhost:3000")
DB = os.environ.get("STDB_DB", "emberline")
PIN = os.environ.get("OPERATOR_PIN", "")
MANIFEST = json.loads((STATIC / "manifest.json").read_text(encoding="utf-8"))
IGN = MANIFEST["t_ignition_sim_ms"]


def bundles() -> list[CycleBundle]:
    index = json.loads((FIXTURES / "cache" / SCN / "main" / "index.json").read_text(encoding="utf-8"))
    out = [CycleBundle.model_validate_json((FIXTURES / c["bundle"]).read_text(encoding="utf-8")) for c in index["cycles"]]
    return sorted(out, key=lambda b: b.knowledge_sim_ms)


ALL = bundles()
BY_K = {b.knowledge_sim_ms: b for b in ALL}


def cycle(n: int) -> CycleBundle:
    """Fixture cycle n is issued at ignition + 5n min."""
    return BY_K[IGN + n * CYCLE_MS]


def load_payload() -> dict[str, Any]:
    zones = json.loads((STATIC / "zones.geojson").read_text(encoding="utf-8"))["features"]
    spawns = json.loads((STATIC / "spawn_points.json").read_text(encoding="utf-8"))
    return {
        "scenario_id": SCN,
        "title": MANIFEST["title"],
        "tz": MANIFEST["tz"],
        "config_hash": MANIFEST["config_hash"],
        "manifest": MANIFEST,
        "replay_start_sim_ms": MANIFEST["replay_start_sim_ms"],
        "replay_end_sim_ms": MANIFEST["replay_end_sim_ms"],
        "zones": [{**f["properties"], "evac_min": f["properties"]["evac_min_default"]} for f in zones],
        "sensors": json.loads((STATIC / "sensors.json").read_text(encoding="utf-8")),
        "spawn_points": [{**sp, "join_code": code} for code, lst in spawns.items() for sp in lst],
    }


def run(fn: Callable[[StdbHttp, StdbHttp], Awaitable[None]]) -> None:
    """Fresh scenario (load -> reset -> load), then the test body with engine + service clients."""

    async def main() -> None:
        engine = StdbHttp(URI, DB, os.environ["STDB_TOKEN"])
        service = StdbHttp(URI, DB, os.environ["STDB_SERVICE_TOKEN"])
        try:
            await engine.load_scenario(load_payload())
            await engine.call("reset_scenario", SCN, PIN)
            await engine.load_scenario(load_payload())
            await engine.call("set_pause_on_proposal", SCN, True, PIN)
            await fn(engine, service)
        finally:
            await engine.aclose()
            await service.aclose()

    asyncio.run(main())


async def rows(hub: StdbHttp, query: str) -> list[dict[str, Any]]:
    return (await hub.sql(query))[0]


async def alert(hub: StdbHttp, alert_id: str) -> dict[str, Any]:
    (a,) = await rows(hub, f"SELECT * FROM alert WHERE alert_id = '{alert_id}'")
    return a


async def clock(hub: StdbHttp) -> dict[str, Any]:
    (c,) = await rows(hub, f"SELECT * FROM clock WHERE scenario_id = '{SCN}'")
    return c


def sim_now(c: dict[str, Any], wall_ms: float) -> float:
    raw = c["anchor_sim_ms"] + ((wall_ms - c["anchor_wall_ms"]) * c["speed"] if c["playing"] else 0)
    return min(max(raw, c["min_sim_ms"]), c["max_sim_ms"])


async def publish_all(hub: StdbHttp) -> None:
    for i in range(0, len(ALL), 10):
        await hub.publish_bundles(ALL[i : i + 10])


COUNTED = ["cycle", "cycle_head", "zone_risk", "observation", "scan_event", "sensor", "alert", "activity_event"]


async def counts(hub: StdbHttp) -> dict[str, int]:
    return {t: len(await rows(hub, f"SELECT * FROM {t} WHERE scenario_id = '{SCN}'")) for t in COUNTED}


def z2_r1() -> str:
    (a,) = cycle(20).alerts
    return a.alert_id


# ---------------------------------------------------------------- 04 §6


def test_1_publish_is_idempotent() -> None:
    async def body(hub: StdbHttp, _: StdbHttp) -> None:
        await hub.publish_bundle(cycle(20))
        first = await counts(hub)
        await hub.publish_bundle(cycle(20))
        assert await counts(hub) == first
        assert first["cycle"] == 1 and first["zone_risk"] == 6 and first["alert"] == 1

    run(body)


def test_2_no_approve_superseded_no_downgrade() -> None:
    async def body(hub: StdbHttp, _: StdbHttp) -> None:
        r1 = cycle(18).alerts[0]  # FIX-Z3 R1, superseded by R2 at cycle 30
        await hub.publish_bundle(cycle(18))
        await hub.publish_bundle(cycle(30))
        assert (await alert(hub, r1.alert_id))["state"] == "superseded"
        with pytest.raises(ReducerRejected):
            await hub.call("approve_alert", r1.alert_id, "Tester", "", PIN)

        await hub.publish_bundle(cycle(20))
        await hub.call("approve_alert", z2_r1(), "Tester", "", PIN)
        await hub.publish_bundle(cycle(20))  # carries the alert as `proposed` again
        assert (await alert(hub, z2_r1()))["state"] == "approved"

    run(body)


def test_3_reset_then_republish() -> None:
    async def body(hub: StdbHttp, service: StdbHttp) -> None:
        await hub.publish_bundle(cycle(20))
        await hub.call("join_as_person", SCN, "per_test0001", "demo", "Judge")
        await service.submit_report(
            {
                "report_id": "rep_test0001", "scenario_id": SCN, "branch_id": "main", "person_id": "per_test0001",
                "channel": "tap", "kind": "smoke", "summary": "Smoke seen", "lon": -118.14, "lat": 34.18,
                "radius_m": 300, "trust": 0.5, "observed_sim_ms": cycle(20).knowledge_sim_ms,
                "created_wall_ms": 0, "provenance": "injected",
            },
            "I can see smoke",
        )
        await hub.create_branch({"branch_id": "b1", "scenario_id": SCN, "parent_id": "main",
                                 "fork_sim_ms": cycle(20).knowledge_sim_ms, "reason": "test"})
        await hub.call("approve_alert", z2_r1(), "Tester", "", PIN)
        await service.ack_alert(z2_r1(), "phone")
        acked = await alert(hub, z2_r1())
        assert acked["state"] == "acknowledged" and acked["acked_sim_ms"] is not None

        await hub.call("reset_scenario", SCN, PIN)
        assert await rows(hub, f"SELECT * FROM person WHERE scenario_id = '{SCN}'") == []
        assert await rows(hub, f"SELECT * FROM report WHERE scenario_id = '{SCN}'") == []
        assert [b["branch_id"] for b in await rows(hub, f"SELECT * FROM branch WHERE scenario_id = '{SCN}'")] == ["main"]
        assert await rows(hub, f"SELECT * FROM alert WHERE scenario_id = '{SCN}'") == []
        assert all(s["taken_by"] is None for s in await rows(hub, f"SELECT * FROM spawn_point WHERE scenario_id = '{SCN}'"))
        c = await clock(hub)
        assert not c["playing"] and c["anchor_sim_ms"] == MANIFEST["replay_start_sim_ms"]
        assert c["active_branch_id"] == "main"
        cmds = await rows(hub, f"SELECT * FROM command WHERE scenario_id = '{SCN}'")
        assert [x["kind"] for x in cmds] == ["reset"]

        await hub.publish_bundle(cycle(20))
        assert (await alert(hub, z2_r1()))["state"] == "proposed"

    run(body)


def test_4_rewind() -> None:
    async def body(hub: StdbHttp, _: StdbHttp) -> None:
        await publish_all(hub)
        epoch = (await clock(hub))["epoch"]
        cut = cycle(20).knowledge_sim_ms
        await hub.rewind(SCN, "main", cut)

        checks = {
            "cycle": "knowledge_sim_ms", "zone_risk": "knowledge_sim_ms", "observation": "available_sim_ms",
            "scan_event": "t_start_sim_ms", "alert": "triggered_sim_ms",
        }
        for table, col in checks.items():
            got = await rows(hub, f"SELECT * FROM {table} WHERE scenario_id = '{SCN}'")
            assert got and all(r[col] <= cut for r in got), table
        expected_cycles = sum(1 for b in ALL if b.cycle and b.knowledge_sim_ms <= cut)
        assert len(await rows(hub, f"SELECT * FROM cycle WHERE scenario_id = '{SCN}'")) == expected_cycles
        (head,) = await rows(hub, f"SELECT * FROM cycle_head WHERE scenario_id = '{SCN}'")
        assert head["knowledge_sim_ms"] == cut
        assert (await clock(hub))["epoch"] == epoch + 1

    run(body)


def test_5_computing_keeps_sim_time_continuous() -> None:
    async def body(hub: StdbHttp, _: StdbHttp) -> None:
        await hub.call("clock_play", SCN, PIN)
        await asyncio.sleep(0.3)
        playing = await clock(hub)
        before = sim_now(playing, time.time() * 1000)
        await hub.set_clock_status(SCN, "computing")
        computing = await clock(hub)
        assert not computing["playing"] and computing["resume_playing"]
        # Hub-only continuity: re-anchoring never moves sim time backwards.
        assert computing["anchor_sim_ms"] >= playing["anchor_sim_ms"]
        # Cross-process check: Python's and the hub's wall clocks differ by a few ms, so allow 50 wall-ms of skew.
        assert computing["anchor_sim_ms"] >= before - 50 * playing["speed"]
        await asyncio.sleep(0.3)
        await hub.set_clock_status(SCN, "ok")
        ok = await clock(hub)
        assert ok["playing"] and ok["status"] == "ok"
        assert ok["anchor_sim_ms"] == computing["anchor_sim_ms"]  # frozen while computing, no jump back

    run(body)


def test_rewind_crosses_nested_forks_to_parent_without_deleting_parent_before_fork() -> None:
    async def body(hub: StdbHttp, _: StdbHttp) -> None:
        await publish_all(hub)
        first, second = cycle(10).knowledge_sim_ms, cycle(20).knowledge_sim_ms
        for child, parent, fork in [('b1','main',first),('b2','b1',second)]:
            await hub.create_branch(dict(branch_id=child,scenario_id=SCN,parent_id=parent,
                                         fork_sim_ms=fork,reason='synthetic seek test'))
        # Exactly at the fork keeps the child active.
        await hub.rewind(SCN,'b2',second)
        assert (await clock(hub))['active_branch_id']=='b2'
        # Crossing both forks restores main and only removes its future cycles.
        cut=cycle(5).knowledge_sim_ms
        await hub.rewind(SCN,'b2',cut)
        assert (await clock(hub))['active_branch_id']=='main'
        got=await rows(hub,"SELECT * FROM cycle WHERE branch_id = 'main'")
        assert got and all(r['knowledge_sim_ms']<=cut for r in got)
        assert any(r['knowledge_sim_ms']==cut for r in got)
    run(body)


def test_6_new_proposal_pauses_clock() -> None:
    async def body(hub: StdbHttp, _: StdbHttp) -> None:
        await hub.call("clock_play", SCN, PIN)
        await hub.publish_bundle(cycle(17))  # no alert
        assert (await clock(hub))["playing"]
        await hub.publish_bundle(cycle(18))  # new FIX-Z3 R1 proposal
        assert not (await clock(hub))["playing"]
        msgs = [e["message"] for e in await rows(hub, f"SELECT * FROM activity_event WHERE scenario_id = '{SCN}'")]
        assert "Clock paused for operator review" in msgs

    run(body)


def test_7_activity_event_pruned_and_ordered() -> None:
    async def body(hub: StdbHttp, _: StdbHttp) -> None:
        for start in range(0, 1000, 100):
            await hub.append_events([
                ActivityEvent(
                    event_id=f"ev_t7_{i:04d}", scenario_id=SCN, branch_id="main", sim_ms=IGN + i, wall_ms=0,
                    stage="system", actor="test", message=f"event {i}",
                )
                for i in range(start, start + 100)
            ])
        got = await rows(hub, f"SELECT * FROM activity_event WHERE scenario_id = '{SCN}'")
        assert len(got) <= 300
        ordered = sorted(got, key=lambda e: e["event_id"])
        seqs = [e["seq"] for e in ordered]
        assert all(a < b for a, b in zip(seqs, seqs[1:], strict=False))
        assert ordered[-1]["event_id"] == "ev_t7_0999"

    run(body)


def test_8_identity_and_pin_checks() -> None:
    async def body(hub: StdbHttp, service: StdbHttp) -> None:
        await hub.publish_bundle(cycle(20))
        await hub.call("approve_alert", z2_r1(), "Tester", "", PIN)
        _, token = await StdbHttp(URI, DB, None).new_identity()
        stranger = StdbHttp(URI, DB, token)
        try:
            with pytest.raises(ReducerRejected):
                await stranger.publish_bundle(cycle(20))
            with pytest.raises(ReducerRejected):
                await service.publish_bundle(cycle(20))  # service is not the engine
            with pytest.raises(ReducerRejected):
                await stranger.ack_alert(z2_r1(), "phone")
            with pytest.raises(ReducerRejected):
                await stranger.call("clock_play", SCN, PIN + "x")
            with pytest.raises(ReducerRejected):
                await stranger.call("reset_scenario", SCN, "")
            with pytest.raises(ReducerRejected):
                await stranger.call("set_identities", "00", "00", "1234")
        finally:
            await stranger.aclose()
        assert (await alert(hub, z2_r1()))["state"] == "approved"

    run(body)


def test_9_phone_payload_small() -> None:
    async def body(hub: StdbHttp, _: StdbHttp) -> None:
        await publish_all(hub)
        await hub.call("join_as_person", SCN, "per_phone001", "demo", "Judge")
        payload = [
            await rows(hub, f"SELECT * FROM clock WHERE scenario_id = '{SCN}'"),
            await rows(hub, f"SELECT * FROM cycle_head WHERE scenario_id = '{SCN}'"),
            await rows(hub, "SELECT * FROM person WHERE person_id = 'per_phone001'"),
            await rows(hub, f"SELECT * FROM alert WHERE scenario_id = '{SCN}'"),
        ]
        size = len(json.dumps(payload))
        print(f"phone payload {size} bytes")
        assert size < 100_000

    run(body)


# ---------------------------------------------------------------- beyond §6: every other reducer once


def test_10_remaining_reducers_smoke() -> None:
    async def body(hub: StdbHttp, service: StdbHttp) -> None:
        await hub.publish_bundle(cycle(20))
        a_id = z2_r1()

        await hub.call("clock_set_speed", SCN, 5000, PIN)
        assert (await clock(hub))["speed"] == 900  # clamped 30-900
        await hub.call("clock_seek", SCN, 0, PIN)
        c = await clock(hub)
        assert c["anchor_sim_ms"] == c["min_sim_ms"] and c["epoch"] >= 1
        await hub.call("clock_play", SCN, PIN)
        await hub.call("clock_pause", SCN, PIN)
        assert not (await clock(hub))["playing"]
        await hub.set_clock_status(SCN, "computing")
        with pytest.raises(ReducerRejected):
            await hub.call("clock_play", SCN, PIN)  # rejected while computing
        await hub.set_clock_status(SCN, "ok")

        await hub.call("set_evac_min", "FIX-Z2", 5, PIN)
        (z,) = await rows(hub, "SELECT * FROM zone WHERE zone_id = 'FIX-Z2'")
        assert z["evac_min"] == 10
        await hub.call("drop_sensor", "viirs_n20", False, PIN)
        (s,) = await rows(hub, "SELECT * FROM sensor WHERE sensor_id = 'viirs_n20'")
        assert not s["enabled"] and s["status"] == "down"

        brf = {
            "briefing_id": f"brf_{a_id}", "scenario_id": SCN, "branch_id": "main", "cycle_id": cycle(20).cycle.cycle_id,
            "alert_id": a_id, "model": "test", "prompt_version": "t", "input_hash": "h", "headline": "H",
            "bullets": ["b"], "text": "T", "voice_script": "V", "grounded": True, "unmatched_tokens": [],
            "fallback_used": False, "latency_ms": 5, "created_wall_ms": 0,
        }
        await hub.call("upsert_alert_draft", a_id, "Claude text", "Claude voice", "claude", json.dumps(brf))
        a = await alert(hub, a_id)
        assert a["text"] == "Claude text" and a["briefing_id"] == f"brf_{a_id}"

        await hub.call("approve_alert", a_id, "", "Edited text", PIN)
        a = await alert(hub, a_id)
        assert a["state"] == "approved" and a["decided_by"] == "Operator" and a["text"] == "Edited text"
        await hub.call("upsert_alert_draft", a_id, "late draft", "v", "claude", json.dumps(brf))
        assert (await alert(hub, a_id))["text"] == "Edited text"  # only while proposed
        with pytest.raises(ReducerRejected):
            await hub.update_alert_call(a_id, "called", "c1", "x")  # approved -> called is illegal
        await hub.update_alert_call(a_id, "calling", "c1", "ringing")
        await service.ack_alert(a_id, "phone")
        await hub.update_alert_call(a_id, "called", "c1", "completed")  # acknowledged: no error, state kept
        a = await alert(hub, a_id)
        assert a["state"] == "acknowledged" and a["call_status"] == "completed"

        await hub.publish_bundle(cycle(18))
        r1 = cycle(18).alerts[0].alert_id
        await hub.call("decline_alert", r1, "Tester", "not needed", PIN)
        assert (await alert(hub, r1))["state"] == "declined"

        await hub.call("join_as_person", SCN, "per_smoke001", "demo", "Judge")
        await hub.call("join_as_person", SCN, "per_smoke001", "demo", "Judge")  # idempotent
        (p,) = await rows(hub, f"SELECT * FROM person WHERE scenario_id = '{SCN}'")
        assert p["zone_id"] == "FIX-Z3" and p["kind"] == "judge" and p["provenance"] == "simulated"
        await hub.call("set_person_status", "per_smoke001", "safe")
        assert (await rows(hub, f"SELECT * FROM person WHERE scenario_id = '{SCN}'"))[0]["status"] == "safe"

        await hub.submit_report_engine(
            {"report_id": "rep_smoke001", "scenario_id": SCN, "branch_id": "main", "person_id": None,
             "channel": "dashboard", "kind": "fire", "summary": "Fire", "lon": -118.1, "lat": 34.2,
             "radius_m": 200, "observed_sim_ms": IGN, "created_wall_ms": 0},
            "transcript",
        )
        polled = await hub.poll(SCN)
        assert polled["clock"]["scenario_id"] == SCN and len(polled["zones"]) == 6 and len(polled["people"]) == 1
        kinds = [x["kind"] for x in polled["commands"]]
        assert "report" in kinds and "person_joined" in kinds and "alert_declined" in kinds
        stamps = [x["created_wall_ms"] for x in polled["commands"]]
        assert stamps == sorted(stamps)
        first = polled["commands"][0]["command_id"]
        await hub.mark_command(first, "done", {"ok": True})
        assert first not in [x["command_id"] for x in (await hub.poll(SCN))["commands"]]

    run(body)


# ---------------------------------------------------------------- review fixes


def test_11_proposal_while_computing_stays_paused() -> None:
    async def body(hub: StdbHttp, _: StdbHttp) -> None:
        await hub.call("clock_play", SCN, PIN)
        await hub.set_clock_status(SCN, "computing")  # saves resume_playing = True
        await hub.publish_bundle(cycle(18))  # new proposal while computing
        await hub.set_clock_status(SCN, "ok")
        c = await clock(hub)
        assert not c["playing"] and not c["resume_playing"]

    run(body)


def test_12_reset_clears_feed_and_pause_logs_again() -> None:
    async def body(hub: StdbHttp, _: StdbHttp) -> None:
        paused = "Clock paused for operator review"

        async def pause_events() -> int:
            return sum(e["message"] == paused for e in await rows(hub, f"SELECT * FROM activity_event WHERE scenario_id = '{SCN}'"))

        await hub.publish_bundle(cycle(18))
        assert await pause_events() == 1
        await hub.call("reset_scenario", SCN, PIN)
        assert await rows(hub, f"SELECT * FROM activity_event WHERE scenario_id = '{SCN}'") == []
        await hub.publish_bundle(cycle(18))  # same alert_id is proposed again
        assert await pause_events() == 1

    run(body)


def test_13_branch_scan_does_not_take_mains_row() -> None:
    async def body(hub: StdbHttp, _: StdbHttp) -> None:
        main_bundle = cycle(20)
        await hub.publish_bundle(main_bundle)
        (scan,) = main_bundle.scan_events
        branch_copy = main_bundle.model_copy(update={
            "branch_id": "b1", "cycle": None, "zone_risk": [], "alerts": [], "events": [],
            "scan_events": [scan.model_copy(update={"branch_id": "b1"})],
        })
        await hub.publish_bundle(branch_copy)
        (row,) = await rows(hub, f"SELECT * FROM scan_event WHERE scan_id = '{scan.scan_id}'")
        assert row["branch_id"] == "main"
        await hub.rewind(SCN, "b1", main_bundle.knowledge_sim_ms - 1)
        assert await rows(hub, f"SELECT * FROM scan_event WHERE scan_id = '{scan.scan_id}'")

    run(body)


def test_14_main_branch_follows_loaded_scenario() -> None:
    async def body(hub: StdbHttp, _: StdbHttp) -> None:
        other = {**load_payload(), "scenario_id": "fixture_b", "zones": [], "sensors": [], "spawn_points": []}
        await hub.load_scenario(other)
        (main,) = await rows(hub, "SELECT * FROM branch WHERE branch_id = 'main'")
        assert main["scenario_id"] == "fixture_b"
        await hub.load_scenario(load_payload())
        (main,) = await rows(hub, "SELECT * FROM branch WHERE branch_id = 'main'")
        assert main["scenario_id"] == SCN

    run(body)
