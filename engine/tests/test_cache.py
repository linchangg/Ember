import json

import numpy as np

from ember.contracts import ObservationRecord
from ember.runtime import cache as c

ARGS = dict(model_version="m1", config_hash="abc", seed=7, filter_mode="pf")
FORCING = {"file": "hrrr_2025010802.nc", "sha256": "f" * 64, "init_ms": 1, "selected_fxx": [0.0, 1.0]}


def test_cycle_key_deterministic_and_sensitive():
    k = c.cycle_key(None, ["b", "a"], **ARGS)
    assert k == c.cycle_key(None, ["a", "b"], **ARGS)
    assert len(k) == 64
    variants = [
        c.cycle_key("x", ["a", "b"], **ARGS),
        c.cycle_key(None, ["a"], **ARGS),
        c.cycle_key(None, ["a", "b"], **{**ARGS, "model_version": "m2"}),
        c.cycle_key(None, ["a", "b"], **{**ARGS, "config_hash": None}),
        c.cycle_key(None, ["a", "b"], **{**ARGS, "seed": 8}),
        c.cycle_key(None, ["a", "b"], **{**ARGS, "filter_mode": "none"}),
        c.cycle_key(None, ["a", "b"], **ARGS, forcing=FORCING),
        c.cycle_key(None, ["a", "b"], **ARGS, forcing={**FORCING, "selected_fxx": [0.0]}),
    ]
    assert len({k, *variants}) == 9
    assert c.cycle_key(None, ["a"], **ARGS, forcing=FORCING) == c.cycle_key(None, ["a"], **ARGS, forcing=dict(
        reversed(FORCING.items())))  # dict order is not identity
    # chain: an earlier difference propagates
    a2 = c.cycle_key(c.cycle_key(None, ["a"], **ARGS), ["z"], **ARGS)
    b2 = c.cycle_key(c.cycle_key(None, ["b"], **ARGS), ["z"], **ARGS)
    assert a2 != b2


def test_evidence_hash_tracks_payload_not_transport():
    rec = ObservationRecord(obs_id="obs_x", scenario_id="s", sensor_id="goes18_fdcc", kind="goes_pixel",
                            observed_sim_ms=1, available_sim_ms=2, provenance="real", hot=True,
                            geometry={"type": "footprint", "ring": [(0, 0), (1, 0), (1, 1), (0, 0)]})
    h = c.evidence_hash(rec)
    assert len(h) == 64 and h == c.evidence_hash(rec.model_copy())
    # Same obs_id, changed evidence: a different key (IDs alone do not cover changed evidence).
    assert c.evidence_hash(rec.model_copy(update={"trust": 0.5})) != h
    assert c.evidence_hash(rec.model_copy(update={"hot": False})) != h
    # Branch/status/cycle/influence are transport metadata, as in hazard/model.py's own fingerprint.
    moved = rec.model_copy(update={"branch_id": "b1", "status": "assimilated", "cycle_id": "c", "influence": 0.2})
    assert c.evidence_hash(moved) == h


def output(key: str) -> c.PhysicsOutput:
    rng = np.random.default_rng(0)
    return c.PhysicsOutput(
        cycle={"cycle_id": "cyc_fixture_main_1", "knowledge_sim_ms": 1},
        zone_arr=rng.random((8, 3), dtype=np.float32),
        area_arr=np.array([[1.5], [np.inf], [np.nan], [0], [1], [2], [3], [4]], dtype=np.float32),
        weights=np.full(8, 1 / 8, dtype=np.float64),
        obs_status=[{"obs_id": "obs_x", "status": "assimilated", "influence": 0.4}],
        scans=[{"scan_id": "G18_x"}],
        events=[{"event_id": "ev_1"}],
        key=key,
    )


def test_save_load_round_trip(tmp_path):
    out = output("k1")
    out.forcing = FORCING
    c.save_cycle(tmp_path, "fixture", "main", 1, out)
    got = c.load_cycle(tmp_path, "fixture", "main", 1, "k1")
    assert got is not None and got.cached
    for name in ("zone_arr", "area_arr", "weights"):
        a, b = getattr(out, name), getattr(got, name)
        assert a.dtype == b.dtype
        assert np.array_equal(a, b, equal_nan=True)
    assert (got.cycle, got.obs_status, got.scans, got.events) == (out.cycle, out.obs_status, out.scans, out.events)
    assert got.forcing == FORCING
    assert c.load_cycle(tmp_path, "fixture", "main", 1, "other") is None
    assert c.load_cycle(tmp_path, "fixture", "main", 2, "k1") is None
    assert not list(tmp_path.rglob("*.tmp"))


def test_latest_checkpoint(tmp_path):
    assert c.latest_checkpoint(tmp_path, "s", "main", 10**15) is None
    for k in (1000, 3000, 2000):
        c.save_checkpoint(tmp_path, "s", "main", k, str(k).encode())
    assert c.latest_checkpoint(tmp_path, "s", "main", 2500) == (2000, b"2000")
    assert c.latest_checkpoint(tmp_path, "s", "main", 3000) == (3000, b"3000")
    assert c.latest_checkpoint(tmp_path, "s", "main", 999) is None


def test_latest_checkpoint_only_from_the_same_lineage(tmp_path):
    c.save_checkpoint(tmp_path, "s", "main", 1000, b"a", key="ka")
    c.save_checkpoint(tmp_path, "s", "main", 2000, b"b", key="kb")
    lineage = {1000: "ka", 2000: "other"}
    assert c.latest_checkpoint(tmp_path, "s", "main", 2500, lineage.get) == (1000, b"a")
    assert c.latest_checkpoint(tmp_path, "s", "main", 2500, {1000: "ka", 2000: "kb"}.get) == (2000, b"b")
    c.save_checkpoint(tmp_path, "s", "main", 1000, b"new")  # unkeyed overwrite drops the stale key
    assert c.latest_checkpoint(tmp_path, "s", "main", 2500, lineage.get) is None
    assert c.latest_checkpoint(tmp_path, "s", "main", 1500) == (1000, b"new")


def test_write_index(tmp_path):
    base = tmp_path / "s" / "main"
    for k in (300, 100, 20, 200):
        (base / str(k)).mkdir(parents=True)
    for k in (300, 20, 100):
        (base / str(k) / "bundle.json").write_text("{}")
    (base / "checkpoints").mkdir()
    path = c.write_index(tmp_path, "s", "main")
    idx = json.loads(path.read_text())
    assert idx["scenario_id"] == "s" and idx["branch_id"] == "main"
    assert [e["knowledge_sim_ms"] for e in idx["cycles"]] == [20, 100, 300]
    assert idx["cycles"][0]["bundle"] == "cache/s/main/20/bundle.json"


def test_briefing_round_trip(tmp_path):
    assert c.load_briefing(tmp_path, "s", "h1") is None
    b = {"briefing_id": "brf_x", "text": "Ålder ≥ 30%"}
    c.save_briefing(tmp_path, "s", "h1", b)
    assert c.load_briefing(tmp_path, "s", "h1") == b
    assert c.briefing_path(tmp_path, "s", "h1") == tmp_path / "s" / "briefings" / "h1.json"
