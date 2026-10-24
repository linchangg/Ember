"""Acceptance tests for the analytic fixture. spec/00_contracts.md §10."""

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from emberline.contracts import SCHEMA_VERSION, CycleBundle, lead_idx
from emberline.scripts.make_fixture import (
    HEADING,
    IGN_LAT,
    IGN_LON,
    N_CYCLES,
    NX,
    NY,
    ROS_M_PER_MIN,
    along_cross_offset,
    arrival_minutes,
    boundary_rings,
    encode_arrival,
    main,
    utm_forward,
    utm_inverse,
)

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures"
MAIN = FIXTURES / "cache" / "fixture" / "main"


def ms(year: int, month: int, day: int, hour: int, minute: int) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)


def test_contract_helpers():
    assert SCHEMA_VERSION == 2
    assert lead_idx(30) == 0
    assert lead_idx(60) == 1
    assert lead_idx(120) == 3
    assert lead_idx(360) == 11


def test_utm_and_head_arrival_are_analytic():
    east, north = utm_forward(IGN_LON, IGN_LAT)
    lon, lat = utm_inverse(east, north)
    assert abs(lon - IGN_LON) < 1e-5
    assert abs(lat - IGN_LAT) < 1e-5
    de, dn = along_cross_offset(ROS_M_PER_MIN * 60, 0)
    assert abs(float(arrival_minutes(de, dn, HEADING, ROS_M_PER_MIN)) - 60) < 0.05


def test_encode_arrival_matches_section_4_2():
    coded = encode_arrival(np.array([0.0, 0.1, 2.0, 360.0, 361.0, -3.0, np.nan]))
    assert coded.dtype == np.uint8
    assert list(coded) == [0, 1, 1, 180, 255, 0, 255]


def test_one_cell_boundary_is_a_closed_ring():
    mask = np.zeros((3, 3), dtype=bool)
    mask[1, 1] = True
    rings = boundary_rings(mask)
    assert len(rings) == 1
    assert rings[0][0] == rings[0][-1]
    assert len(rings[0]) == 5


def test_fixture_bundles_alerts_and_size():
    main()
    index = json.loads((MAIN / "index.json").read_text(encoding="utf-8"))
    assert index["scenario_id"] == "fixture"
    assert index["branch_id"] == "main"

    manifest = json.loads((FIXTURES / "static" / "fixture" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["title"] == "FIXTURE (synthetic)"
    assert manifest["is_fixture"] is True
    assert manifest["grid"]["nx"] == 120 and manifest["grid"]["ny"] == 120
    assert manifest["static"]["scans_dir"] is None
    zones = json.loads((FIXTURES / "static" / "fixture" / "zones.geojson").read_text(encoding="utf-8"))
    assert {f["properties"]["zone_id"] for f in zones["features"]} == {f"FIX-Z{i}" for i in range(1, 7)}
    spawns = json.loads((FIXTURES / "static" / "fixture" / "spawn_points.json").read_text(encoding="utf-8"))
    assert len(spawns["demo"]) == 6

    pre = []
    cycles = []
    for entry in index["cycles"]:
        path = FIXTURES / entry["bundle"]
        bundle = CycleBundle.model_validate_json(path.read_text(encoding="utf-8"))
        assert bundle.schema_version == SCHEMA_VERSION
        assert bundle.scenario_id == "fixture"
        for obs in bundle.observations:
            assert obs.available_sim_ms <= bundle.knowledge_sim_ms
            assert obs.provenance == "simulated"
        for scan in bundle.scan_events:
            assert scan.available_sim_ms <= bundle.knowledge_sim_ms
            assert scan.provenance == "simulated"
        if bundle.cycle is None:
            assert bundle.observations == []
            assert bundle.alerts == []
            assert len(bundle.scan_events) == 1
            pre.append(bundle)
            continue
        raw = path.with_name("arrival_q.bin").read_bytes()
        assert len(raw) == 5 * NX * NY
        assert bundle.cycle.assets.arrival_q.endswith("arrival_q.bin")
        assert len(json.dumps(bundle.cycle.danger)) <= 20_000
        assert 3 <= len(bundle.events) <= 5
        for event in bundle.events:
            assert event.message.startswith("FIXTURE")
            assert len(event.message) <= 140
        assert 2 <= len(bundle.observations) <= 4
        for alert in bundle.alerts:
            assert len(alert.text) <= 300
            assert len(alert.voice_script.split()) <= 110
            assert "DEMO REPLAY" in alert.text
        cycles.append(bundle)

    assert len(pre) == 3
    assert len(cycles) == N_CYCLES
    assert pre[0].knowledge_sim_ms == ms(2025, 1, 8, 2, 0)
    assert cycles[0].knowledge_sim_ms == ms(2025, 1, 8, 2, 20)
    assert cycles[19].knowledge_sim_ms == ms(2025, 1, 8, 3, 55)
    assert cycles[29].knowledge_sim_ms == ms(2025, 1, 8, 4, 45)

    def proposed(bundle: CycleBundle, rule: str, zone: str) -> bool:
        return any(a.state == "proposed" and a.rule_id == rule and a.zone_ids[0] == zone for a in bundle.alerts)

    assert proposed(cycles[19], "R1_WARNING", "FIX-Z2")
    assert proposed(cycles[29], "R2_ORDER", "FIX-Z3")
    assert not any(proposed(b, "R1_WARNING", "FIX-Z2") for b in cycles[:19])
    assert not any(proposed(b, "R2_ORDER", "FIX-Z3") for b in cycles[:29])
    # R1 looks 60 sim-minutes further ahead than R2's act-by clause, so the same
    # zone's warning is proposed 12 cycles earlier and superseded when the order fires.
    assert proposed(cycles[17], "R1_WARNING", "FIX-Z3")
    assert any(a.state == "superseded" and a.rule_id == "R1_WARNING" and a.zone_ids[0] == "FIX-Z3" for a in cycles[29].alerts)
    assert proposed(cycles[31], "R2_ORDER", "FIX-Z2")

    total = sum(path.stat().st_size for path in FIXTURES.rglob("*") if path.is_file())
    assert total < 10 * 1024 * 1024
