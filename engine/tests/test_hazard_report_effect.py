"""Checkpoint controls for one ground report. These tests do not run the seed matrix."""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from emberline.hazard.api import ModelConfig
from emberline.hazard.evaluate_assimilation import REPORT_SHIFT_GATE_M, inputs, model_for
from emberline.hazard.experiments import snapshot_prediction
from emberline.hazard.experiment_io import ConfigError, ExecutionError, repo_root
from emberline.hazard.report_effect import (
    INJECTION_ISSUE_MIN,
    MATRIX_LEADS_MIN,
    MATRIX_SEEDS,
    MATRIX_TRUSTS,
    PARENT_ISSUE_MIN,
    effect_at_lead,
    ground_report,
    injection_point,
    matrix_cases,
    reject_extra,
    run_restored_case,
)

T_REF = 1736294400
CELL_M = 100.0
CONTROL = np.array([[[0, 1000], [1000, 1000]]], np.float32)
TREATMENT = np.array([[[0, 1000], [0, 1000]]], np.float32)
WEIGHTS = np.array([1.0])
TRUTH = np.array([[0, np.inf], [np.inf, np.inf]], np.float32)


class Forecast:
    def __init__(self, arrivals, ids):
        self.arrivals = arrivals
        self.weights = WEIGHTS.copy()
        self.ess = 1.0
        self.obs_used = [
            {"obs_id": obs_id, "used": True, "reanchored": False} for obs_id in ids
        ]


class World:
    def __init__(self, arrivals_for):
        self.calls = []
        self.records = []
        self.loads = 0
        self.arrivals_for = arrivals_for

    def restore(self, blob):
        assert blob == b"parent"
        self.loads += 1
        world = self

        class Model:
            def step(self, issue, records):
                assert issue == INJECTION_ISSUE_MIN
                world.calls.append([record.obs_id for record in records])
                world.records.append(records)
                return Forecast(
                    world.arrivals_for(records), [record.obs_id for record in records]
                )

        return Model()


def _report(obs_id="report-7-0.25-on_time", observed=60, available=60, hot=True, trust=0.25):
    record = ground_report(
        obs_id=obs_id,
        t_ref_s=T_REF,
        observed_min=observed,
        available_min=available,
        lon=-118.0,
        lat=34.2,
        trust=trust,
    )
    if hot is not True:
        record = record.model_copy(update={"hot": hot})
    return record


def _normal():
    return [_report("goes-60-0", trust=1.0)]


def _run(world, normal, extra, **kwargs):
    return run_restored_case(
        world.restore,
        b"parent",
        INJECTION_ISSUE_MIN,
        normal,
        extra,
        t_ref_s=T_REF,
        leads_min=(0,),
        cell_m=CELL_M,
        **kwargs,
    )


def _by_report(records):
    return TREATMENT if any(record.obs_id.startswith("report") for record in records) else CONTROL


def test_matrix_schedule_is_fixed_and_unexecuted():
    cases = matrix_cases()
    assert [case["seed"] for case in cases] == [seed for seed in MATRIX_SEEDS for _ in range(8)]
    assert cases[0]["obs_id"] == "report-7-0.1-on_time"
    assert cases[1]["timing"] == "delayed"
    assert cases[1]["observed_min"] == 50
    assert cases[1]["available_min"] == 60
    assert cases[-1]["obs_id"] == "report-9-1-delayed"
    assert len({case["obs_id"] for case in cases}) == 24
    assert {case["trust"] for case in cases} == set(MATRIX_TRUSTS)
    assert cases[0]["leads_min"] == list(MATRIX_LEADS_MIN)
    assert cases[0]["parent_issue_min"] == PARENT_ISSUE_MIN
    payload = json.loads(
        (repo_root() / "config/hazard_experiments/report-effect-v1.json").read_text()
    )
    assert payload["full_matrix_run"] == "not_executed"
    assert payload["seeds"] == list(MATRIX_SEEDS)
    assert payload["trust"] == list(MATRIX_TRUSTS)
    assert payload["leads_min"] == list(MATRIX_LEADS_MIN)
    assert payload["legacy_gate"]["threshold_m"] == REPORT_SHIFT_GATE_M == 300.0
    assert payload["legacy_gate"]["comparable_to_matched_checkpoint"] is False
    assert payload["movement_is_success_criterion"] is False
    assert payload["trust_monotonicity_required"] is False
    assert "spotting" not in payload
    assert ModelConfig().spotting is True


def test_no_report_twin_matches_control_and_keeps_its_own_copies():
    normal = _normal()
    world = World(lambda _records: CONTROL.copy())
    result = _run(world, normal, [])
    assert result["parity"] == "matched"
    assert world.loads == 2
    assert result["control_observation_ids"] == ["goes-60-0"]
    assert result["leads"][0]["mean_abs_probability_difference"] == 0
    assert result["movement_is_success_criterion"] is False
    assert result["legacy_gate"]["threshold_m"] == 300.0
    assert world.records[0][0] is not normal[0]
    normal[0] = _report("later")
    assert result["control_observation_ids"] == ["goes-60-0"]


def test_one_report_is_scored_apart_from_skill():
    world = World(_by_report)
    result = _run(world, _normal(), [_report()], truth=TRUTH)
    lead = result["leads"][0]
    assert world.loads == 3
    assert world.calls[-1] == ["goes-60-0", "report-7-0.25-on_time"]
    assert result["report_status"] == "accepted"
    assert result["treatment_stepped"] is True
    assert lead["mean_abs_probability_difference"] == pytest.approx(0.25)
    assert lead["max_abs_probability_difference"] == pytest.approx(1)
    assert lead["changed_area_ha"] == pytest.approx(1)
    assert lead["signed_burned_area_difference_ha"] == pytest.approx(1)
    assert lead["boundary_distance_m"] == pytest.approx(100 / 3)
    assert lead["boundary_censored"] is True
    assert lead["boundary_erosion"] == "4-connected"
    assert lead["boundary_border_value"] == 0
    assert lead["east_head_displacement_m"] == pytest.approx(0)
    assert lead["movement_is_success_criterion"] is False
    assert lead["skill"]["label"].startswith("synthetic self-consistency")
    assert lead["skill"]["control"]["brier"] == pytest.approx(0)
    assert lead["skill"]["treatment"]["brier"] == pytest.approx(0.25)
    again = _run(World(_by_report), _normal(), [_report()], truth=TRUTH)
    assert again["leads"][0]["mean_abs_probability_difference"] == lead[
        "mean_abs_probability_difference"
    ]


def test_future_duplicate_and_changed_reports_are_not_consumed():
    future = World(_by_report)
    future_result = _run(future, _normal(), [_report(observed=70, available=70)])
    assert future_result["report_status"] == "rejected"
    assert future_result["report_reason"] == "unavailable at issue"
    assert future.loads == 2
    assert all(not any(obs_id.startswith("report") for obs_id in ids) for ids in future.calls)

    duplicate = World(_by_report)
    duplicate_result = _run(duplicate, _normal(), [_normal()[0].model_copy(deep=True)])
    assert duplicate_result["report_reason"] == "duplicate observation id"
    assert duplicate.loads == 2

    changed = World(_by_report)
    changed_result = _run(changed, _normal(), [_report("goes-60-0", hot=False)])
    assert changed_result["report_reason"] == "observation id changed its evidence"
    assert changed.loads == 2


def test_delayed_release_stays_causal():
    held = _report(observed=50, available=60)
    assert reject_extra(held, 60, T_REF, _normal()) is None
    early = _report(observed=50, available=70)
    assert reject_extra(early, 60, T_REF, _normal()) == "unavailable at issue"
    backwards = _report(observed=60, available=50)
    assert (
        reject_extra(backwards, 60, T_REF, _normal()) == "observation precedes its availability"
    )
    world = World(_by_report)
    result = _run(world, _normal(), [held])
    assert result["report_status"] == "accepted"
    assert world.calls[-1][-1] == held.obs_id


def test_invalid_frontier_is_not_relocated():
    cell = 100.0
    height, width = 20, 20
    rows = np.arange(height)[:, None]
    cols = np.arange(width)[None, :]
    xs = np.broadcast_to((cols + 0.5) * cell, (height, width)).copy()
    ys = np.broadcast_to(2000 - (rows + 0.5) * cell, (height, width)).copy()
    burnable = np.ones((height, width), dtype=bool)
    frontier = np.zeros_like(burnable)
    frontier[5, 2] = True
    frontier[4, 15] = True
    point, reason = injection_point(
        frontier, xs, ys, x0=0, y0=2000, cell_m=cell, burnable=burnable
    )
    assert point is None
    assert reason == "offset point is outside the domain"

    supported = np.zeros_like(burnable)
    supported[5, 2] = True
    burnable[5, 12] = False
    point, reason = injection_point(
        supported, xs, ys, x0=0, y0=2000, cell_m=cell, burnable=burnable
    )
    assert point is None
    assert reason == "offset point is not on supported terrain"
    assert (
        injection_point(
            np.zeros_like(burnable), xs, ys, x0=0, y0=2000, cell_m=cell, burnable=burnable
        )[1]
        == "no burned frontier at the injection issue"
    )

    world = World(lambda _records: CONTROL.copy())
    result = _run(
        world,
        _normal(),
        [_report()],
        injection_reason="offset point is outside the domain",
    )
    assert result["report_status"] == "non_evaluable"
    assert result["treatment_stepped"] is False
    assert world.loads == 2
    assert all(not any(obs_id.startswith("report") for obs_id in ids) for ids in world.calls)


def test_empty_and_interior_boundaries():
    empty = np.full((1, 3, 3), np.inf, np.float32)
    moved = empty.copy()
    moved[0, 1, 1] = 0
    empty_edge = effect_at_lead(
        empty, WEIGHTS, moved, WEIGHTS, issue_min=60, lead_min=0, cell_m=CELL_M
    )
    assert empty_edge["boundary_distance_m"] is None
    assert empty_edge["boundary_distance_reason"] == "control burned edge is empty"
    assert empty_edge["east_head_displacement_m"] is None

    control = np.full((1, 5, 5), 1000, np.float32)
    treatment = control.copy()
    control[0, 2, 2] = 0
    treatment[0, 2, 3] = 0
    interior = effect_at_lead(
        control, WEIGHTS, treatment, WEIGHTS, issue_min=0, lead_min=0, cell_m=CELL_M
    )
    assert interior["boundary_distance_m"] == pytest.approx(100)
    assert interior["boundary_censored"] is False
    assert interior["east_head_displacement_m"] == pytest.approx(100)
    assert interior["east_head_reason"] is None


def test_mismatched_restore_and_two_reports_are_refused():
    calls = {"n": 0}

    def restore(_blob):
        calls["n"] += 1

        class Model:
            def step(self, _issue, _records):
                arrivals = CONTROL if calls["n"] == 1 else TREATMENT
                return Forecast(arrivals, ["goes-60-0"])

        return Model()

    with pytest.raises(ExecutionError, match="no-report restore"):
        run_restored_case(
            restore,
            b"parent",
            60,
            _normal(),
            [],
            t_ref_s=T_REF,
            leads_min=(0,),
            cell_m=CELL_M,
        )
    with pytest.raises(ConfigError, match="one report"):
        _run(World(_by_report), _normal(), [_report("one"), _report("two")])


def test_native_restored_pair_matches_without_a_report():
    values = inputs(members=2, seed=11, horizon_min=60)
    parent = model_for(values, "pf", workers=2, timeout_s=120)
    parent.step(5, [])
    blob = parent.save_state()

    def restore(saved):
        model = model_for(values, "pf", workers=2, timeout_s=120)
        model.load_state(saved)

        class Arm:
            def step(self, issue, records):
                forecast = model.step(issue, records)
                snapshot = snapshot_prediction(model, forecast)
                return SimpleNamespace(
                    arrivals=snapshot.arrivals_abs_min.copy(),
                    weights=snapshot.weights.copy(),
                    ess=float(forecast.ess),
                    obs_used=list(forecast.obs_used),
                )

        return Arm()

    result = run_restored_case(
        restore,
        blob,
        10,
        [],
        [],
        t_ref_s=values[0].grid.t_ref_s,
        leads_min=(0,),
        cell_m=values[0].grid.cell_m,
    )
    assert result["parity"] == "matched"
    assert result["report_status"] == "no_report"
    assert result["leads"][0]["mean_abs_probability_difference"] == 0
