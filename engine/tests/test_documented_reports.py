"""Documented reports as ground_report observations (01 §9) and precompute's per-branch input overrides."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import hazard_standin
from hazard_standin import flat_landscape
from test_engine import FlatForcing, synth_source, write_scenario
from test_sources import MIN, SCN, T0, _build

from emberline.data.config import scenario_dir
from emberline.runtime import publisher
from emberline.runtime.loop import load_scenario_dir
from emberline.runtime.sources import ReplaySource, documented_reports
from emberline.scripts.precompute import apply_overrides, precompute

T_REP = T0 + 7 * MIN
TIMELINE = [
    {"t_sim_ms": T0, "label": "Ignition", "source": "x", "kind": "official"},
    {"t_sim_ms": T_REP, "label": "Structure fire at A and B", "source": "x", "kind": "report",
     "as_observation": True, "lon": -118.147, "lat": 34.203},
]


def _scn(tmp_path: Path) -> Path:
    _build(tmp_path)
    (tmp_path / "truth").mkdir()
    (tmp_path / "truth" / "timeline.json").write_text(json.dumps(TIMELINE), encoding="utf-8")
    return tmp_path


def _reports(obs) -> list:
    return [o for o in obs if o.kind == "ground_report"]


def test_reports_only_when_enabled_at_documented_time(tmp_path: Path) -> None:
    scn = _scn(tmp_path)
    off = ReplaySource(scn, SCN, ["goes18_fdcc"])
    assert not _reports(off.collect(T0 - MIN, T0 + 60 * MIN)[0])
    on = ReplaySource(scn, SCN, ["goes18_fdcc"], reports=True)
    assert not _reports(on.collect(T0 - MIN, T_REP - 1)[0])  # never before it was reported
    (rep,) = _reports(on.collect(T_REP - 1, T_REP)[0])
    assert not _reports(on.collect(T_REP, T0 + 60 * MIN)[0])  # exactly once
    assert rep.obs_id == f"obs_rep_doc_{T_REP}"
    assert rep.observed_sim_ms == rep.available_sim_ms == T_REP
    assert (rep.provenance, rep.trust, rep.geometry.radius_m, rep.hot) == ("real", 0.9, 400.0, True)
    assert rep.source_ref == "Structure fire at A and B" and publisher.publishable(rep)


def test_eaton_reports_are_located() -> None:
    reps = documented_reports(scenario_dir("eaton"), "eaton")
    assert [r.observed_sim_ms for r in reps] == [1736322540000, 1736331480000]  # 23:49 and 02:18 PST
    assert all(-118.2 < r.geometry.lon < -118.1 and 34.15 < r.geometry.lat < 34.25 for r in reps)


def test_sensor_override_changes_assimilated(tmp_path: Path) -> None:
    scn = _scn(tmp_path)
    for sensors, want in [("goes18_fdcc", {"goes18_fdcc"}), ("goes18_fdcm", {"goes18_fdcm"})]:
        cfg = {"assimilate_sensors": ["goes18_fdcc", "goes18_fdcm"]}
        apply_overrides(cfg, sensors, None)
        obs, scans = ReplaySource(scn, SCN, cfg["assimilate_sensors"]).collect(T0 - MIN, T0 + 60 * MIN)
        assert {o.sensor_id for o in obs} == want
        assert {s.sensor_id for s in scans if s.assimilated} == want
        assert len(scans) == 4  # display-only scans still published


def test_overrides_give_distinct_cycle_keys(tmp_path: Path) -> None:
    keys = []
    for i, (sensors, reports) in enumerate([(None, None), ("synthetic_goes,other", None), (None, True)]):
        ctx = load_scenario_dir(write_scenario(tmp_path / f"s{i}", n_phys=2))
        apply_overrides(ctx.cfg, sensors, reports)
        loop = asyncio.run(precompute(ctx, synth_source(ctx), FlatForcing(ctx), hazard_standin.FakeHazardModel,
                                      flat_landscape(ctx), tmp_path / f"cache{i}", "main",
                                      model_version=hazard_standin.MODEL_VERSION))
        keys.append(loop.state.keys[ctx.t_ign_ms])
    assert len(set(keys)) == 3
