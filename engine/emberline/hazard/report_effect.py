"""Matched checkpoint controls for one synthetic ground report.

The parent is saved once. Control and treatment each restore that checkpoint
and take one step at the injection issue. A no-report twin must match control.
This module does not change filter math, trust defaults, or likelihood strength.
"""

from __future__ import annotations

import hashlib
import json
from time import perf_counter
from typing import Callable

import numpy as np
from scipy.ndimage import binary_erosion, distance_transform_edt

from emberline.contracts import ObservationRecord
from emberline.hazard.evaluate_assimilation import REPORT_SHIFT_GATE_M, issue_sim_ms
from emberline.hazard.experiment_io import ConfigError, ExecutionError
from emberline.hazard.scoring import (
    absolute_target_min,
    predicted_burned,
    score_raw_arrival_field,
    weighted_burn_probability,
)

PARENT_ISSUE_MIN = 55
INJECTION_ISSUE_MIN = 60
MATRIX_SEEDS = (7, 8, 9)
MATRIX_TRUSTS = (0.1, 0.25, 0.5, 1.0)
MATRIX_LEADS_MIN = (30, 60, 120)
OFFSET_M_EAST = 1000.0
REPORT_RADIUS_M = 150.0
PROBABILITY_DIFFERENCE_THRESHOLD = 0.1
_CROSS = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)
DEFINITION = (
    "Restored checkpoints. Control and treatment share the parent through minute 55 "
    "and differ by one report at minute 60. This is not the legacy none-versus-pf comparison."
)
LEGACY_GATE = {
    "name": "trust_0.25_q50_boundary_shift",
    "threshold_m": REPORT_SHIFT_GATE_M,
    "definition": "legacy report_effect mean symmetric q50 boundary shift",
    "comparable_to_matched_checkpoint": False,
    "success_criterion_for_this_experiment": False,
}


def matrix_cases() -> list[dict[str, object]]:
    """One independent cell per seed, trust, and release timing. Leads share that cell."""
    cases = []
    for seed in MATRIX_SEEDS:
        for trust in MATRIX_TRUSTS:
            for timing, observed_min in (("on_time", 60), ("delayed", 50)):
                cases.append(
                    {
                        "seed": seed,
                        "trust": trust,
                        "timing": timing,
                        "observed_min": observed_min,
                        "available_min": INJECTION_ISSUE_MIN,
                        "leads_min": list(MATRIX_LEADS_MIN),
                        "obs_id": f"report-{seed}-{format(trust, 'g')}-{timing}",
                        "parent_issue_min": PARENT_ISSUE_MIN,
                        "injection_issue_min": INJECTION_ISSUE_MIN,
                    }
                )
    return cases


def ground_report(
    *,
    obs_id: str,
    t_ref_s: int,
    observed_min: int,
    available_min: int,
    lon: float,
    lat: float,
    trust: float,
) -> ObservationRecord:
    if isinstance(trust, bool) or not np.isfinite(trust) or not 0 <= float(trust) <= 1:
        raise ConfigError("trust must be a finite fraction from 0 to 1")
    return ObservationRecord(
        obs_id=obs_id,
        scenario_id="synthetic-twin",
        sensor_id="twin-ground",
        kind="ground_report",
        observed_sim_ms=issue_sim_ms(t_ref_s, observed_min),
        available_sim_ms=issue_sim_ms(t_ref_s, available_min),
        geometry={
            "type": "point_radius",
            "lon": float(lon),
            "lat": float(lat),
            "radius_m": REPORT_RADIUS_M,
        },
        hot=True,
        trust=float(trust),
        confidence=0.9,
        report_kind="fire",
        provenance="simulated",
        source_ref="synthetic matched-checkpoint report",
    )


def injection_point(
    frontier: np.ndarray,
    xs: np.ndarray,
    ys: np.ndarray,
    *,
    x0: float,
    y0: float,
    cell_m: float,
    burnable: np.ndarray,
    offset_m: float = OFFSET_M_EAST,
) -> tuple[dict[str, float | int] | None, str | None]:
    """Offset the easternmost frontier cell. Do not search for a replacement point."""
    mask = np.asarray(frontier, dtype=bool)
    cells = np.argwhere(mask)
    if len(cells) == 0:
        return None, "no burned frontier at the injection issue"
    chosen = cells[int(np.argmax(cells[:, 1]))]
    row, col = int(chosen[0]), int(chosen[1])
    x = float(xs[row, col]) + float(offset_m)
    y = float(ys[row, col])
    target_col = int(np.floor((x - x0) / cell_m))
    target_row = int(np.floor((y0 - y) / cell_m))
    height, width = np.asarray(burnable).shape
    point = {
        "x": x,
        "y": y,
        "row": target_row,
        "col": target_col,
        "source_row": row,
        "source_col": col,
    }
    if not (0 <= target_row < height and 0 <= target_col < width):
        return None, "offset point is outside the domain"
    if not bool(burnable[target_row, target_col]):
        return None, "offset point is not on supported terrain"
    return point, None


def effect_at_lead(
    control_arrivals: np.ndarray,
    control_weights: np.ndarray,
    treatment_arrivals: np.ndarray,
    treatment_weights: np.ndarray,
    *,
    issue_min: int,
    lead_min: int,
    cell_m: float,
    truth: np.ndarray | None = None,
) -> dict[str, object]:
    target = absolute_target_min(issue_min, lead_min)
    control_probability = weighted_burn_probability(control_arrivals, control_weights, target)
    treatment_probability = weighted_burn_probability(
        treatment_arrivals, treatment_weights, target
    )
    delta = treatment_probability - control_probability
    cell_ha = float(cell_m) ** 2 / 10000.0
    control_mask = predicted_burned(control_probability)
    treatment_mask = predicted_burned(treatment_probability)
    boundary = _boundary_distance(control_mask, treatment_mask, cell_m)
    east_m, east_reason = _east_head_m(control_mask, treatment_mask, cell_m)
    row: dict[str, object] = {
        "lead_min": int(lead_min),
        "target_min": target,
        "mean_abs_probability_difference": float(np.mean(np.abs(delta))),
        "max_abs_probability_difference": float(np.max(np.abs(delta))),
        "changed_area_ha": float(
            np.count_nonzero(np.abs(delta) > PROBABILITY_DIFFERENCE_THRESHOLD) * cell_ha
        ),
        "probability_difference_threshold": PROBABILITY_DIFFERENCE_THRESHOLD,
        "signed_burned_area_difference_ha": float(
            (np.count_nonzero(treatment_mask) - np.count_nonzero(control_mask)) * cell_ha
        ),
        "boundary_distance_m": boundary["mean_m"],
        "boundary_distance_reason": boundary["reason"],
        "boundary_censored": boundary["censored"],
        "boundary_erosion": "4-connected",
        "boundary_border_value": 0,
        "east_head_displacement_m": east_m,
        "east_head_reason": east_reason,
        "movement_is_success_criterion": False,
    }
    row["skill"] = _skill(
        control_arrivals,
        control_weights,
        treatment_arrivals,
        treatment_weights,
        truth,
        issue_min,
        lead_min,
        cell_m,
    )
    return row


def run_restored_case(
    restore: Callable[[bytes], object],
    blob: bytes,
    issue_min: int,
    normal_records: list[ObservationRecord],
    extra_records: list[ObservationRecord],
    *,
    t_ref_s: int,
    leads_min: tuple[int, ...] | list[int],
    cell_m: float,
    truth: np.ndarray | None = None,
    injection_reason: str | None = None,
) -> dict[str, object]:
    """Restore one parent for every arm. A rejected report is not passed to a step."""
    if len(extra_records) > 1:
        raise ConfigError("a matched case injects one report")
    control, control_ids = _step_copy(restore, blob, issue_min, normal_records)
    twin, twin_ids = _step_copy(restore, blob, issue_min, normal_records)
    if control_ids != twin_ids or not _same_prediction(control, twin):
        raise ExecutionError("no-report restore did not reproduce control")
    result: dict[str, object] = {
        "definition": DEFINITION,
        "parity": "matched",
        "control_observation_ids": control_ids,
        "twin_observation_ids": twin_ids,
        "treatment_stepped": False,
        "treatment_observation_ids": None,
        "report_status": None,
        "report_reason": None,
        "ess": None,
        "runtime_ms": None,
        "reanchored": None,
        "leads": [],
        "movement_is_success_criterion": False,
        "trust_monotonicity_required": False,
        "legacy_gate": dict(LEGACY_GATE),
    }
    if injection_reason:
        result["report_status"] = "non_evaluable"
        result["report_reason"] = injection_reason
        return result
    if not extra_records:
        result["treatment_stepped"] = False
        result["report_status"] = "no_report"
        result["ess"] = _optional_float(control, "ess")
        result["reanchored"] = _reanchored(control)
        result["leads"] = [
            effect_at_lead(
                control.arrivals,
                control.weights,
                twin.arrivals,
                twin.weights,
                issue_min=issue_min,
                lead_min=int(lead),
                cell_m=cell_m,
                truth=truth,
            )
            for lead in leads_min
        ]
        return result
    report = extra_records[0]
    reason = reject_extra(report, issue_min, t_ref_s, normal_records)
    if reason is not None:
        result["report_status"] = "rejected"
        result["report_reason"] = reason
        return result
    started = perf_counter()
    treatment, treatment_ids = _step_copy(
        restore, blob, issue_min, [*normal_records, report]
    )
    result["runtime_ms"] = (perf_counter() - started) * 1000.0
    result["treatment_stepped"] = True
    result["treatment_observation_ids"] = treatment_ids
    result["ess"] = _optional_float(treatment, "ess")
    result["reanchored"] = _reanchored(treatment)
    used = {
        str(item["obs_id"]): item
        for item in getattr(treatment, "obs_used", [])
        if isinstance(item, dict) and "obs_id" in item
    }
    info = used.get(report.obs_id)
    if info is None:
        result["report_status"] = "not_consumed"
        result["report_reason"] = "the step result did not list the report"
    elif info.get("used"):
        result["report_status"] = "accepted"
        result["report_reason"] = None
    else:
        result["report_status"] = "rejected"
        result["report_reason"] = info.get("reason") or "rejected"
    result["leads"] = [
        effect_at_lead(
            control.arrivals,
            control.weights,
            treatment.arrivals,
            treatment.weights,
            issue_min=issue_min,
            lead_min=int(lead),
            cell_m=cell_m,
            truth=truth,
        )
        for lead in leads_min
    ]
    return result


def reject_extra(
    report: ObservationRecord,
    issue_min: int,
    t_ref_s: int,
    normal_records: list[ObservationRecord],
) -> str | None:
    issue_ms = issue_sim_ms(t_ref_s, issue_min)
    if report.available_sim_ms > issue_ms or report.observed_sim_ms > issue_ms:
        return "unavailable at issue"
    if report.observed_sim_ms > report.available_sim_ms:
        return "observation precedes its availability"
    known = {record.obs_id: _fingerprint(record) for record in normal_records}
    if report.obs_id not in known:
        return None
    if known[report.obs_id] != _fingerprint(report):
        return "observation id changed its evidence"
    return "duplicate observation id"


def _step_copy(restore, blob, issue_min, records):
    model = restore(blob)
    copied = [record.model_copy(deep=True) for record in records]
    forecast = model.step(issue_min, copied)
    return forecast, [record.obs_id for record in copied]


def _same_prediction(left, right) -> bool:
    return np.array_equal(left.arrivals, right.arrivals) and np.array_equal(
        left.weights, right.weights
    )


def _fingerprint(record: ObservationRecord) -> str:
    payload = record.model_dump(
        mode="json", exclude={"branch_id", "status", "cycle_id", "influence"}
    )
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def _boundary_distance(control_mask: np.ndarray, treatment_mask: np.ndarray, cell_m: float):
    control_edge = _burned_edge(control_mask)
    treatment_edge = _burned_edge(treatment_mask)
    missing = [
        name
        for name, edge in (("control", control_edge), ("treatment", treatment_edge))
        if not edge.any()
    ]
    if missing:
        return {
            "mean_m": None,
            "reason": f"{' and '.join(missing)} burned edge is empty",
            "censored": None,
        }
    sampling = (float(cell_m), float(cell_m))
    from_control = distance_transform_edt(~control_edge, sampling=sampling)
    from_treatment = distance_transform_edt(~treatment_edge, sampling=sampling)
    samples = np.concatenate([from_control[treatment_edge], from_treatment[control_edge]])
    return {
        "mean_m": float(samples.mean()),
        "reason": None,
        "censored": bool(_touches_domain(control_edge) or _touches_domain(treatment_edge)),
    }


def _burned_edge(mask: np.ndarray) -> np.ndarray:
    eroded = binary_erosion(np.asarray(mask, dtype=bool), structure=_CROSS, border_value=0)
    return np.asarray(mask, dtype=bool) & ~eroded


def _touches_domain(edge: np.ndarray) -> bool:
    return bool(edge[0].any() or edge[-1].any() or edge[:, 0].any() or edge[:, -1].any())


def _east_head_m(control_mask, treatment_mask, cell_m):
    if not control_mask.any() or not treatment_mask.any():
        return None, "burned mask is empty"
    control_col = int(np.argwhere(control_mask)[:, 1].max())
    treatment_col = int(np.argwhere(treatment_mask)[:, 1].max())
    return (treatment_col - control_col) * float(cell_m), None


def _skill(
    control_arrivals,
    control_weights,
    treatment_arrivals,
    treatment_weights,
    truth,
    issue_min,
    lead_min,
    cell_m,
):
    if truth is None:
        return {
            "label": "synthetic self-consistency, separate from the movement measurement",
            "control": None,
            "treatment": None,
            "reason": "synthetic truth was not supplied",
        }
    return {
        "label": "synthetic self-consistency, separate from the movement measurement",
        "reason": None,
        "control": _skill_row(control_arrivals, control_weights, truth, issue_min, lead_min, cell_m),
        "treatment": _skill_row(
            treatment_arrivals, treatment_weights, truth, issue_min, lead_min, cell_m
        ),
    }


def _skill_row(arrivals, weights, truth, issue_min, lead_min, cell_m):
    scored = score_raw_arrival_field(
        arrivals=arrivals,
        weights=weights,
        truth=truth,
        valid=None,
        issue_min=issue_min,
        lead_min=lead_min,
        cell_m=cell_m,
        persistence=False,
    )
    return {
        "brier": scored["brier"],
        "dice": scored["dice"],
        "iou": scored["iou"],
        "false_positive_ha": scored["false_positive_ha"],
        "false_negative_ha": scored["false_negative_ha"],
    }


def _optional_float(forecast, name: str):
    value = getattr(forecast, name, None)
    if value is None:
        return None
    return float(value)


def _reanchored(forecast) -> bool:
    return any(
        bool(item.get("reanchored"))
        for item in getattr(forecast, "obs_used", [])
        if isinstance(item, dict)
    )
