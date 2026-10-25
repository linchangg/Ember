"""Hand-checked raw-arrival scores and matched synthetic streams."""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from emberline.hazard.api import Grid, ModelConfig
from emberline.hazard.evaluate_assimilation import (
    configured_assimilation,
    delayed_ground_record,
    freeze_observation_stream,
    issue_sim_ms,
    legacy_exit_code,
    load_matched_config,
    main,
    matched_config_path,
    mode_batches,
    recovery_gates,
)
from emberline.hazard.experiment_io import ConfigError, ExecutionError, repo_root
from emberline.hazard.scoring import (
    absolute_target_min,
    assert_same_grid,
    assert_single_lead,
    reliability_table,
    predicted_burned,
    rounded_quantile_burned,
    score_raw_arrival_field,
    weighted_burn_probability,
)
from emberline.contracts import ObservationRecord


T_REF = 1736294400
CONFIG = matched_config_path()
RECORDED = repo_root() / "docs/evidence/rust-hazard/assimilation-128.json"


def _record(obs_id: str, observed_min: int, available_min: int) -> ObservationRecord:
    return ObservationRecord(
        obs_id=obs_id,
        scenario_id="synthetic-twin",
        sensor_id="twin-ground",
        kind="ground_report",
        observed_sim_ms=issue_sim_ms(T_REF, observed_min),
        available_sim_ms=issue_sim_ms(T_REF, available_min),
        geometry={"type": "point_radius", "lon": -118.1, "lat": 34.2, "radius_m": 150},
        hot=True,
        trust=1.0,
        report_kind="fire",
        provenance="simulated",
        source_ref="synthetic delayed report",
    )


def _grid() -> dict[str, object]:
    return {
        "crs": "EPSG:32611",
        "x0": 0.0,
        "y0": 0.0,
        "cell_m": 100.0,
        "height": 2,
        "width": 2,
        "t_ref_s": T_REF,
    }


def test_unequal_weights_issue_lead_and_infinity():
    arrivals = np.array(
        [[[0.0, np.inf], [10.0, 30.0]], [[0.0, 20.0], [np.inf, 40.0]]], dtype=np.float64
    )
    weights = np.array([0.25, 0.75])
    target = absolute_target_min(25, 0)
    assert target == 25
    probability = weighted_burn_probability(arrivals, weights, target)
    assert probability.tolist() == [[1.0, 0.75], [0.25, 0.0]]
    scored = score_raw_arrival_field(
        arrivals=arrivals,
        weights=weights,
        truth=np.array([[0.0, 40.0], [10.0, 80.0]]),
        valid=np.ones((2, 2), dtype=bool),
        issue_min=25,
        lead_min=0,
        cell_m=100,
        persistence=False,
        prediction_grid=_grid(),
        truth_grid=_grid(),
    )
    assert scored["target_min"] == 25
    assert scored["brier"] == pytest.approx(0.28125)
    assert scored["dice"] == pytest.approx(0.5)
    assert scored["iou"] == pytest.approx(1 / 3)
    assert scored["false_positive_ha"] == pytest.approx(1)
    assert scored["false_negative_ha"] == pytest.approx(1)
    partial = score_raw_arrival_field(
        arrivals=arrivals,
        weights=weights,
        truth=np.array([[0.0, 40.0], [10.0, 80.0]]),
        valid=np.array([[True, False], [True, True]]),
        issue_min=25,
        lead_min=0,
        cell_m=100,
        persistence=False,
    )
    assert partial["valid_cells"] == 3
    assert partial["brier"] == pytest.approx(0.1875)
    assert partial["dice"] == pytest.approx(2 / 3)
    assert partial["iou"] == pytest.approx(0.5)
    assert partial["false_positive_ha"] == 0
    assert partial["false_negative_ha"] == pytest.approx(1)
    later = weighted_burn_probability(np.array([[[70.0]]]), np.array([1.0]), absolute_target_min(60, 30))
    assert later[0, 0] == 1
    assert weighted_burn_probability(np.array([[[np.inf]]]), np.array([1.0]), 90)[0, 0] == 0
    with pytest.raises(ConfigError, match="invalid raw arrivals"):
        weighted_burn_probability(np.array([[[np.nan]]]), np.array([1.0]), 10)


def test_empty_masks_validity_and_quantile_nan():
    quiet = score_raw_arrival_field(
        arrivals=np.full((1, 2, 2), np.inf),
        weights=np.array([1.0]),
        truth=np.full((2, 2), 100.0),
        valid=np.ones((2, 2), dtype=bool),
        issue_min=10,
        lead_min=0,
        cell_m=100,
        persistence=False,
    )
    assert quiet["both_empty"] is True
    assert quiet["dice"] == 1
    assert quiet["iou"] == 1
    assert quiet["brier"] == 0
    assert quiet["false_positive_ha"] == 0
    assert quiet["false_negative_ha"] == 0
    none_valid = score_raw_arrival_field(
        arrivals=np.full((1, 1, 1), np.inf),
        weights=np.array([1.0]),
        truth=np.array([[0.0]]),
        valid=np.array([[False]]),
        issue_min=0,
        lead_min=0,
        cell_m=100,
        persistence=False,
    )
    assert none_valid["brier"] is None
    assert none_valid["brier_reason"] == "no valid truth cells"
    assert none_valid["both_empty"] is True
    with pytest.raises(ConfigError, match="NaN is not observed unburned truth"):
        score_raw_arrival_field(
            arrivals=np.full((1, 1, 1), np.inf),
            weights=np.array([1.0]),
            truth=np.array([[np.nan]]),
            valid=np.array([[True]]),
            issue_min=0,
            lead_min=0,
            cell_m=100,
            persistence=False,
        )
    excluded = score_raw_arrival_field(
        arrivals=np.full((1, 1, 2), np.inf),
        weights=np.array([1.0]),
        truth=np.array([[np.nan, 100.0]]),
        valid=np.array([[False, True]]),
        issue_min=0,
        lead_min=0,
        cell_m=100,
        persistence=False,
    )
    assert excluded["valid_cells"] == 1
    assert excluded["brier"] == 0
    other = dict(_grid())
    other["width"] = 3
    with pytest.raises(ConfigError, match="prediction grid"):
        assert_same_grid(_grid(), other)
    quantile = np.full((5, 1, 4), np.nan)
    quantile[2, 0, :] = [np.nan, 0, 30, 31]
    burned = rounded_quantile_burned(quantile, 30)
    assert burned.tolist() == [[False, True, True, False]]
    with pytest.raises(ConfigError, match="unlabeled aggregate"):
        assert_single_lead([{"lead_min": 0}, {"lead_min": 60}])


def test_probability_endpoints_and_persistence_hold():
    probability = np.array([[0.0, 0.1, 0.25, 0.9, 1.0]])
    table = reliability_table(
        probability,
        np.array([[False, True, False, True, True]]),
        np.ones((1, 5), dtype=bool),
    )
    assert table["bins"][0]["count"] == 1
    assert table["bins"][0]["upper_inclusive"] is False
    assert table["bins"][0]["mean_probability"] == 0
    assert table["bins"][1]["count"] == 1
    assert table["bins"][1]["mean_probability"] == pytest.approx(0.1)
    assert table["bins"][4]["count"] == 0
    assert table["bins"][4]["mean_probability"] is None
    assert table["bins"][4]["observed_fraction"] is None
    assert table["bins"][9]["count"] == 2
    assert table["bins"][9]["upper_inclusive"] is True
    assert table["bins"][9]["mean_probability"] == pytest.approx(0.95)
    assert "do not establish real-world calibration" in table["note"]
    assert bool(predicted_burned(np.array([[0.5]]))[0, 0]) is True
    assert bool(predicted_burned(np.array([[0.499999]]))[0, 0]) is False
    held = score_raw_arrival_field(
        arrivals=np.array([[[0.0, 30.0], [np.inf, np.inf]]]),
        weights=np.array([1.0]),
        truth=np.array([[0.0, 30.0], [np.inf, 80.0]]),
        valid=np.ones((2, 2), dtype=bool),
        issue_min=10,
        lead_min=50,
        cell_m=100,
        persistence=True,
    )
    spread = score_raw_arrival_field(
        arrivals=np.array([[[0.0, 30.0], [np.inf, np.inf]]]),
        weights=np.array([1.0]),
        truth=np.array([[0.0, 30.0], [np.inf, 80.0]]),
        valid=np.ones((2, 2), dtype=bool),
        issue_min=10,
        lead_min=50,
        cell_m=100,
        persistence=False,
    )
    assert held["probability_time_min"] == 10
    assert held["target_min"] == 60
    assert held["probability_sum"] == pytest.approx(1)
    assert held["probability_sum"] != 0
    assert held["brier"] == pytest.approx(0.25)
    assert held["dice"] == pytest.approx(2 / 3)
    assert spread["probability_sum"] == pytest.approx(2)
    assert spread["brier"] == 0
    assert spread["dice"] == 1
    assert spread["rounded_quantile_q50"]["label"] == "rounded_quantile_q50"
    assert spread["rounded_quantile_q50"]["dice"] is None
    assert held["probability_source"] == "frozen_issue_probability"


def test_one_stream_holds_a_delayed_report_until_release():
    immediate = _record("now", 25, 25)
    delayed = _record("delayed", 25, 30)
    frozen, _digest, _payload = freeze_observation_stream([immediate, delayed])
    immediate.hot = False
    assert frozen[0].hot is True
    batches = mode_batches(frozen, [25, 30, 35], ["persistence", "none", "reinit", "pf"], T_REF)
    assert batches["pf"] is batches["none"] is batches["reinit"] is batches["persistence"]
    by_issue = dict(batches["pf"])
    assert [item.obs_id for item in by_issue[25]] == ["now"]
    assert [item.obs_id for item in by_issue[30]] == ["delayed"]
    assert by_issue[35] == []
    grid = Grid("EPSG:32611", 380000, 3803000, 100, 1, 2, T_REF)
    fuel = np.array([[91, 102]], dtype=np.int16)
    model = SimpleNamespace(land=SimpleNamespace(grid=grid, fuel=fuel))
    generated = delayed_ground_record(model, np.array([[0.0, 0.0]]), 30, 11, 1)
    assert generated is not None
    assert generated.observed_sim_ms == issue_sim_ms(T_REF, 25)
    assert generated.available_sim_ms == issue_sim_ms(T_REF, 30)
    assert generated.provenance == "simulated"
    assert generated.geometry.radius_m == 150
    assert delayed_ground_record(model, np.array([[np.inf, np.inf]]), 30, 11, 1) is None
    with pytest.raises(ConfigError, match="precedes its availability"):
        mode_batches([_record("backwards", 40, 30)], [40], ["pf"], T_REF)


def test_recorded_failures_and_frozen_candidate_stay_unchanged():
    payload = json.loads(RECORDED.read_text(encoding="utf-8"))
    twin = payload["twin"]
    gates = recovery_gates(
        angle_deg=twin["posterior"]["wind_dir_bias_deg"][0],
        ros_mult=twin["posterior"]["ros_mult"][0],
        ess_stable_fraction=twin["ess_stable_fraction"],
        pf_sorensen=twin["pf_sorensen_plus_2h"],
        none_sorensen=twin["none_sorensen_plus_2h"],
    )
    assert gates == twin["gates"]
    assert gates["angle_within_8_deg"] is False
    assert gates["ros_within_25_percent"] is False
    assert twin["passed"] is False
    assert payload["synthetic"] is True
    assert payload["report_effect"]["passed"] is False
    assert payload["report_effect"]["gate_m"] == 300
    assert any("historical" in item.lower() for item in twin["limits"])
    assert legacy_exit_code(payload, require_gates=False) == 0
    assert legacy_exit_code(payload, require_gates=True) == 4
    config = load_matched_config(CONFIG)
    assert config.seeds == [11, 13]
    assert config.calibration_seeds == [7, 8, 9]
    assert config.modes == ["persistence", "none", "reinit", "pf"]
    assert config.issue_minutes[0] == 5 and config.issue_minutes[-1] == 120
    assert ModelConfig().spotting is True
    assert ModelConfig().n_members == 128
    from emberline.hazard.evaluate_assimilation import inputs

    assert inputs(4, 11)[2].spotting is False


def test_smoke_profile_stays_separate_from_the_frozen_candidate(tmp_path):
    frozen = load_matched_config(CONFIG)
    assert frozen.purpose == "frozen_candidate"
    assert frozen.members == 32
    assert frozen.seeds == [11, 13]
    assert "pipeline_verification" not in CONFIG.read_text(encoding="utf-8")
    smoke_path = repo_root() / "config/hazard_experiments/synthetic-smoke.json"
    smoke = load_matched_config(smoke_path)
    assert smoke.purpose == "pipeline_verification"
    assert smoke.run_id == "synthetic-matched-smoke-v1"
    assert smoke.members == 2
    assert smoke.seeds == [11]
    assert smoke.issue_minutes == [20, 25, 30]
    assert smoke.lead_minutes == [0, 30]
    assert smoke.horizon_min == 60
    assert smoke.truth.ros_mult == frozen.truth.ros_mult
    payload = json.loads(smoke_path.read_text(encoding="utf-8"))
    payload["members"] = 32
    rejected = tmp_path / "wide.json"
    rejected.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="frozen"):
        load_matched_config(rejected)
    payload = json.loads(smoke_path.read_text(encoding="utf-8"))
    payload["seeds"] = [7]
    rejected.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="frozen"):
        load_matched_config(rejected)


def test_config_rejects_retuning_and_conflicting_flags(tmp_path):
    payload = json.loads(CONFIG.read_text(encoding="utf-8"))
    payload["truth"]["ros_mult"] = 1.0
    tuned = tmp_path / "tuned.json"
    tuned.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="frozen"):
        load_matched_config(tuned)
    payload = json.loads(CONFIG.read_text(encoding="utf-8"))
    payload["gates"]["angle_tolerance_deg"] = 30
    tuned.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="frozen"):
        load_matched_config(tuned)
    payload = json.loads(CONFIG.read_text(encoding="utf-8"))
    payload["seeds"] = [7, 13]
    tuned.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="frozen"):
        load_matched_config(tuned)
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert main(["--config", str(broken), "--out", str(tmp_path / "synthetic-matched-v1")]) == 2
    assert (
        main(
            [
                "--config",
                str(CONFIG),
                "--out",
                str(tmp_path / "synthetic-matched-v1"),
                "--members",
                "4",
            ]
        )
        == 2
    )
    existing = tmp_path / "synthetic-matched-v1"
    existing.mkdir()
    assert main(["--config", str(CONFIG), "--out", str(existing)]) == 2
    assert not (tmp_path / "synthetic-matched-v1.failed").exists()


def test_matched_report_pairs_seeds_and_leads_without_pooling(tmp_path):
    config = load_matched_config(CONFIG)
    grid = {
        "crs": config.grid.crs,
        "x0": float(config.grid.x0),
        "y0": float(config.grid.y0),
        "cell_m": float(config.grid.cell_m),
        "height": config.grid.height,
        "width": config.grid.width,
        "t_ref_s": config.grid.t_ref_s,
    }
    shape = (config.grid.height, config.grid.width)
    spread = np.full(shape, np.inf, dtype=np.float32)
    spread[0, 0] = 0
    spread[0, 1] = 30
    stuck = np.full(shape, np.inf, dtype=np.float32)
    stuck[0, 0] = 0
    weights = np.full(config.members, 1.0 / config.members)
    spread_arrivals = np.broadcast_to(spread, (config.members, *shape)).copy()
    stuck_arrivals = np.broadcast_to(stuck, (config.members, *shape)).copy()
    evidence = json.loads(RECORDED.read_text(encoding="utf-8"))
    measurement = {
        "angle_deg": evidence["twin"]["posterior"]["wind_dir_bias_deg"][0],
        "ros_mult": evidence["twin"]["posterior"]["ros_mult"][0],
        "ess_stable_fraction": evidence["twin"]["ess_stable_fraction"],
        "pf_sorensen": evidence["twin"]["pf_sorensen_plus_2h"],
        "none_sorensen": evidence["twin"]["none_sorensen_plus_2h"],
    }
    seen = {}

    def prepare(seed):
        records = [_record("now", 5, 5), _record("delayed", 25, 30)]
        return {"records": records, "truth_arrivals": spread, "grid": grid}

    def simulate(seed, frozen, batches, prepared):
        if seed == config.seeds[0]:
            seen["same"] = batches["pf"] is batches["none"] is batches["reinit"] is batches["persistence"]
            by_issue = dict(batches["pf"])
            seen["early"] = [item.obs_id for item in by_issue[5]]
            seen["released"] = [item.obs_id for item in by_issue[30]]
            seen["later"] = [item.obs_id for item in by_issue[35]]
            prepared["records"][0].hot = False
            seen["frozen_hot"] = frozen[0].hot
        snapshots = {
            "none": {"arrivals": spread_arrivals, "weights": weights, "grid": grid},
            "pf": {"arrivals": spread_arrivals, "weights": weights, "grid": grid},
            "reinit": {"arrivals": stuck_arrivals, "weights": weights, "grid": grid},
        }
        return {
            "modes": {mode: {issue: snapshots[mode] for issue in config.issue_minutes} for mode in snapshots},
            "parameter_measurements": measurement,
        }

    destination = tmp_path / "synthetic-matched-v1"
    code = configured_assimilation(
        CONFIG, destination, prepare=prepare, simulate=simulate, require_gates=True
    )
    assert code == 4
    assert seen == {
        "same": True,
        "early": ["now"],
        "released": ["delayed"],
        "later": [],
        "frozen_hot": True,
    }
    report = json.loads((destination / "assimilation.json").read_text(encoding="utf-8"))
    assert report["dataset_kind"] == "synthetic"
    assert report["scientific_acceptance"] == "not_established_by_synthetic_twin"
    assert "aggregate" not in report
    assert "mean_brier" not in report
    assert report["difference_directions"]["brier_pf_minus_none"] == "negative_is_better"
    assert report["difference_directions"]["dice_pf_minus_none"] == "positive_is_better"
    recorded = report["scientific_gates"]["recorded_128_member"]
    assert recorded["rewritten"] is False
    assert recorded["gates"]["angle_within_8_deg"] is False
    assert recorded["gates"]["ros_within_25_percent"] is False
    assert recorded["report_effect_passed"] is False
    assert report["scientific_gates"]["this_run"]["passed"] is False
    assert report["scientific_gates"]["this_run"]["by_seed"][0]["gates"]["angle_within_8_deg"] is False
    leads = {row["lead_min"] for row in report["paired_differences"]}
    assert leads == {0, 60, 120}
    assert len(report["paired_differences"]) == len(config.seeds) * len(config.issue_minutes) * len(config.lead_minutes)
    early = next(
        row
        for row in report["paired_differences"]
        if row["seed"] == 11 and row["issue_min"] == 5 and row["lead_min"] == 60
    )
    assert early["brier_pf_minus_none"] == 0
    assert early["brier_pf_minus_persistence"] < 0
    assert early["dice_pf_minus_persistence"] > 0
    held = [
        row
        for row in report["scores"]
        if row["seed"] == 11 and row["mode"] == "persistence" and row["issue_min"] == 5
    ]
    assert {row["probability_sum"] for row in held} == {1}
    assert 0 not in {row["probability_sum"] for row in held}
    with np.load(destination / "seed-11-mode-persistence-issue-5.npz", allow_pickle=False) as stored:
        assert stored["burn_probability_at_issue"].shape == shape
        assert np.isposinf(stored["arrivals_abs_min"][0, 0, 2])
        assert float(stored["burn_probability_at_issue"][0, 0]) == 1
    observations = json.loads((destination / "observations.json").read_text(encoding="utf-8"))
    assert observations["provenance"] == "simulated"
    assert observations["frozen_before_modes"] is True


def test_execution_failure_keeps_a_failed_directory(tmp_path, monkeypatch):
    def fail(_config):
        def prepare(_seed):
            raise ExecutionError("solver failed")

        return prepare

    monkeypatch.setattr("emberline.hazard.evaluate_assimilation.native_prepare", fail)
    destination = tmp_path / "synthetic-matched-v1"
    code = main(["--config", str(CONFIG), "--out", str(destination)])
    assert code == 3
    failure = json.loads((tmp_path / "synthetic-matched-v1.failed" / "failure.json").read_text())
    assert failure["status"] == "failed"
    assert not destination.exists()
