"""Reproducible synthetic skill checks, with explicit pass/fail gates (not historical skill).

Legacy ``--out`` is a JSON file and keeps its existing fields. ``--config`` writes a
new run directory. Its ``assimilation.json`` is the versioned matched-scoring
section. Persistence, none, reinit, and pf share one observation stream frozen
before any of those modes steps. Solver parameters are not tuned here.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import time
from typing import Callable, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator
from pyproj import Transformer
from scipy.ndimage import binary_erosion, distance_transform_edt

from emberline.contracts import ObservationRecord

from .api import Grid, Ignition, Landscape, ModelConfig, Weather
from .bridge import BackendOptions
from .experiment_io import (
    ConfigError,
    ExecutionError,
    repo_root,
    read_strict_json,
    validate_run_id,
    write_strict_json,
    RunSession,
    sha256_canonical,
)
from .experiments import apply_synthetic_truth_override, completion_exit_code, snapshot_prediction
from .export import write_json
from .likelihoods import SatelliteLikelihood
from .model import RustHazardModel
from .scoring import (
    DIFFERENCE_DIRECTIONS,
    RELIABILITY_NOTE,
    absolute_target_min,
    assert_same_grid,
    score_raw_arrival_field,
    weighted_burn_probability,
)


SYNTHETIC_CRS = "EPSG:32611"
SYNTHETIC_X0 = 380000
SYNTHETIC_Y0 = 3803000
SYNTHETIC_CELL_M = 90
SYNTHETIC_HEIGHT = 65
SYNTHETIC_WIDTH = 81
SYNTHETIC_T_REF_S = 1736294400
ANGLE_TRUTH_DEG = 20.0
ANGLE_TOLERANCE_DEG = 8.0
ROS_TRUTH = 1.6
ROS_RELATIVE_TOLERANCE = 0.25
ESS_FLOOR = 0.2
ESS_CYCLE_FRACTION = 0.8
REPORT_SHIFT_GATE_M = 300.0
FROZEN_TRUTH = {
    "ros_mult": 1.6,
    "wind_speed_mult": 1.0,
    "wind_dir_bias_deg": 20.0,
    "dfm_1h": 0.04,
    "urban_ros": 2.0,
    "member": 0,
}
MODES = ("persistence", "none", "reinit", "pf")
ENGINE_MODES = ("none", "reinit", "pf")
PAIRS = (
    ("pf", "none", "pf_minus_none"),
    ("pf", "reinit", "pf_minus_reinit"),
    ("pf", "persistence", "pf_minus_persistence"),
    ("reinit", "none", "reinit_minus_none"),
    ("none", "persistence", "none_minus_persistence"),
)
RECORDED_GATES = "docs/evidence/rust-hazard/assimilation-128.json"
LABELS = [
    "Synthetic surface experiment only; no historical data",
    "Shared solver and likelihood; this is a self-consistency twin, not independent historical validation",
    RELIABILITY_NOTE,
    "Parameter-recovery gates are separate from outline skill",
    "No solver or likelihood parameters were tuned for this scoring task",
]
_STRICT = ConfigDict(extra="forbid", frozen=True)


def inputs(members=32, seed=7, horizon_min=360):
    grid = Grid(
        SYNTHETIC_CRS,
        SYNTHETIC_X0,
        SYNTHETIC_Y0,
        SYNTHETIC_CELL_M,
        SYNTHETIC_HEIGHT,
        SYNTHETIC_WIDTH,
        SYNTHETIC_T_REF_S,
    )
    shape = (grid.H, grid.W)
    fuel = np.full(shape, 102, np.int16)
    fuel[38:50, 35:65] = 91
    zeros = np.zeros(shape, np.float32)
    land = Landscape(grid, fuel, zeros, zeros.copy(), zeros.copy())
    weather = Weather(
        np.array([0, 600], np.float32),
        np.full((2, *shape), 6, np.float32),
        np.zeros((2, *shape), np.float32),
        np.full(2, 22, np.float32),
        np.full(2, 0.25, np.float32),
    )
    cfg = ModelConfig(
        n_members=members, spotting=False, seed=seed, horizon_min=horizon_min
    )
    ign = Ignition(0, np.array([[grid.x0 + 20.5 * 90, grid.y0 - 25.5 * 90]]), 90)
    return land, weather, cfg, ign


def model_for(values, mode="pf", workers=2, timeout_s=300):
    if mode not in ENGINE_MODES:
        raise ConfigError("persistence is a frozen-probability baseline, not a solver filter")
    land, weather, cfg, ign = values
    model = RustHazardModel(BackendOptions(workers=workers, timeout_s=timeout_s))
    model.init(land, weather, replace(cfg, filter=mode, spotting=False), ign)
    return model


def issue_sim_ms(t_ref_s: int, minute: float) -> int:
    return round((t_ref_s + minute * 60) * 1000)


def pixel(model, identifier, now, bounds, kind, hot, repeated=False):
    x0, y0, x1, y1 = bounds
    inverse = Transformer.from_crs(model.land.grid.crs, "EPSG:4326", always_xy=True)
    ring = [
        inverse.transform(x, y)
        for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    ]
    stamp = issue_sim_ms(model.land.grid.t_ref_s, now)
    return ObservationRecord(
        obs_id=identifier,
        scenario_id="synthetic-twin",
        sensor_id="twin-goes" if kind == "goes_pixel" else "twin-viirs",
        kind=kind,
        observed_sim_ms=stamp,
        available_sim_ms=stamp,
        geometry={"type": "footprint", "ring": ring},
        hot=hot,
        trust=1.0,
        confidence=0.9,
        fdc_class=(30 if repeated else 10)
        if hot and kind == "goes_pixel"
        else 15
        if kind == "goes_pixel"
        else None,
        footprint_key=f"{kind}-{bounds}",
        provenance="simulated",
        source_ref="synthetic hidden-parameter experiment",
    )


def delayed_ground_record(model, truth_arrivals, issue_min, seed, delay_cycles, cycle_min=5):
    """One synthetic report observed earlier and released on this issue. No draw if nothing has burned."""
    observed_min = int(issue_min) - int(delay_cycles) * int(cycle_min)
    if observed_min < 0:
        return None
    truth = np.asarray(truth_arrivals)
    if truth.ndim == 3:
        truth = truth[0]
    active = np.isfinite(truth) & (truth <= observed_min) & (np.asarray(model.land.fuel) != 91)
    candidates = np.argwhere(active)
    if len(candidates) == 0:
        return None
    chosen = candidates[np.lexsort((candidates[:, 0], candidates[:, 1]))[-1]]
    row, col = int(chosen[0]), int(chosen[1])
    grid = model.land.grid
    x = grid.x0 + (col + 0.5) * grid.cell_m
    y = grid.y0 - (row + 0.5) * grid.cell_m
    lon, lat = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True).transform(x, y)
    return ObservationRecord(
        obs_id=f"ground-{seed}-{int(issue_min)}",
        scenario_id="synthetic-twin",
        sensor_id="twin-ground",
        kind="ground_report",
        observed_sim_ms=issue_sim_ms(grid.t_ref_s, observed_min),
        available_sim_ms=issue_sim_ms(grid.t_ref_s, issue_min),
        geometry={"type": "point_radius", "lon": float(lon), "lat": float(lat), "radius_m": 150},
        hot=True,
        trust=1.0,
        confidence=0.9,
        report_kind="fire",
        provenance="simulated",
        source_ref="synthetic delayed report",
    )


def synthetic_observation_records(
    model,
    actual,
    issue_minutes,
    rng,
    *,
    include_delayed_ground=False,
    seed=0,
    delay_cycles=1,
):
    """Build every observation before a scored mode steps. The rng order matches the original twin."""
    grid = model.land.grid
    cell = int(grid.cell_m)
    bounds = [
        (grid.x0 + x, grid.y0 - y - 2800, grid.x0 + x + 2300, grid.y0 - y)
        for x in range(0, grid.W * cell, 2300)
        for y in range(0, grid.H * cell, 2800)
    ]
    previous = {}
    records = []
    for now in issue_minutes:
        for k, box in enumerate(bounds):
            rec = pixel(model, f"goes-{now}-{k}", now, box, "goes_pixel", True, previous.get(k, False))
            prepared = model.geometry.prepare(rec)
            logp = SatelliteLikelihood.loglik(
                actual, rec, prepared, model._tau, grid.cell_m**2 / 10000, now
            )
            hot = bool(rng.random() < np.exp(logp[0]))
            rec = rec.model_copy(
                update={
                    "hot": hot,
                    "fdc_class": 30
                    if hot and previous.get(k, False)
                    else 10
                    if hot
                    else 15,
                }
            )
            previous[k] = hot
            records.append(rec)
        if now in {55, 115}:
            active = (actual[0] <= now) & (actual[0] >= now - 15) & (model.land.fuel != 91)
            candidates = np.argwhere(active)
            selected = candidates[:: max(1, len(candidates) // 8)] if len(candidates) else []
            for k, (r, c) in enumerate(selected):
                x, y = model.geometry.x[r, c], model.geometry.y[r, c]
                records.append(
                    pixel(
                        model,
                        f"viirs-{now}-{k}",
                        now,
                        (x - 187.5, y - 187.5, x + 187.5, y + 187.5),
                        "viirs_pixel",
                        True,
                    )
                )
        if include_delayed_ground:
            delayed = delayed_ground_record(model, actual, now, seed, delay_cycles)
            if delayed is not None:
                records.append(delayed)
    return records


def newly_available(records, issue_min, prior_issue_min, t_ref_s):
    current = issue_sim_ms(t_ref_s, issue_min)
    prior = None if prior_issue_min is None else issue_sim_ms(t_ref_s, prior_issue_min)
    if prior is not None and prior >= current:
        raise ConfigError("issue times must be strictly increasing")
    selected = []
    for record in records:
        if record.observed_sim_ms > record.available_sim_ms:
            raise ConfigError("observation precedes its availability")
        if record.available_sim_ms > current:
            continue
        if prior is not None and record.available_sim_ms <= prior:
            continue
        selected.append(record)
    return selected


def freeze_observation_stream(records):
    """Copy the stream before any mode can rebuild it."""
    payload = [record.model_dump(mode="json") for record in records]
    copies = [ObservationRecord.model_validate(item) for item in payload]
    return copies, sha256_canonical(payload), payload


def mode_batches(records, issues, modes, t_ref_s):
    """Every mode receives the same batch list. A later issue does not see a private stream."""
    shared = []
    prior = None
    for issue in issues:
        shared.append((int(issue), newly_available(records, issue, prior, t_ref_s)))
        prior = issue
    return {mode: shared for mode in modes}


def sorensen(a, b):
    total = int(a.sum() + b.sum())
    return 1.0 if total == 0 else float(2 * np.sum(a & b) / total)


def recovery_gates(*, angle_deg, ros_mult, ess_stable_fraction, pf_sorensen, none_sorensen):
    """Existing synthetic thresholds. Outline overlap does not relax a parameter miss."""
    return {
        "angle_within_8_deg": abs(float(angle_deg) - ANGLE_TRUTH_DEG) <= ANGLE_TOLERANCE_DEG,
        "ros_within_25_percent": abs(float(ros_mult) - ROS_TRUTH) <= ROS_RELATIVE_TOLERANCE * ROS_TRUTH,
        "ess_stable_80_percent_cycles": float(ess_stable_fraction) >= ESS_CYCLE_FRACTION,
        "pf_beats_none_sorensen": float(pf_sorensen) > float(none_sorensen),
    }


def legacy_exit_code(result: dict, require_gates: bool) -> int:
    failed = (not result["twin"]["passed"]) or (not result["report_effect"]["passed"])
    if require_gates and failed:
        return 4
    return 0


def twin(members, workers):
    values = inputs(members)
    pf = model_for(values, workers=workers)
    baseline = model_for(values, "none", workers)
    truth = model_for(
        (*values[:2], replace(values[2], n_members=1), values[3]), "none", workers
    )
    apply_synthetic_truth_override(
        truth,
        ros_mult=FROZEN_TRUTH["ros_mult"],
        wind_speed_mult=FROZEN_TRUTH["wind_speed_mult"],
        wind_dir_bias_deg=FROZEN_TRUTH["wind_dir_bias_deg"],
        dfm_1h=FROZEN_TRUTH["dfm_1h"],
        urban_ros=FROZEN_TRUTH["urban_ros"],
        member=FROZEN_TRUTH["member"],
    )
    truth._ignition_xy[:] = values[3].points_xy
    truth_forecast = truth.step(0, [])
    actual = snapshot_prediction(truth, truth_forecast).arrivals_abs_min
    grid = values[0].grid
    rng = np.random.default_rng(20261024)
    issues = list(range(5, 121, 5))
    stream = synthetic_observation_records(
        pf, actual, issues, rng, include_delayed_ground=False, seed=values[2].seed
    )
    cycles = []
    result = None
    prior = None
    for now in issues:
        records = newly_available(stream, now, prior, grid.t_ref_s)
        result = pf.step(now, records)
        prior = now
        cycles.append(
            {
                "now_min": now,
                "ess_fraction": result.ess / members,
                "alpha": result.tempering_alpha,
                "resampled": result.resampled,
                "params": result.params_summary,
                "duration_ms": result.duration_ms,
            }
        )
        print(
            f"Twin {now}/120 min: ESS {result.ess:.2f}/{members}, alpha {result.tempering_alpha:.5f}",
            flush=True,
        )
    open_loop = baseline.step(120, [])
    true_mask = actual[0] <= 240
    pf_score = sorensen(result.t_q[2] <= 120, true_mask)
    baseline_score = sorensen(open_loop.t_q[2] <= 120, true_mask)
    angle = result.params_summary["wind_dir_bias_deg"][0]
    ros = result.params_summary["ros_mult"][0]
    ess_fraction = float(np.mean([c["ess_fraction"] >= ESS_FLOOR for c in cycles]))
    gates = recovery_gates(
        angle_deg=angle,
        ros_mult=ros,
        ess_stable_fraction=ess_fraction,
        pf_sorensen=pf_score,
        none_sorensen=baseline_score,
    )
    return {
        "members": members,
        "truth": {"wind_dir_bias_deg": 20, "ros_mult": 1.6, "urban_ros": 2.0},
        "posterior": result.params_summary,
        "pf_sorensen_plus_2h": pf_score,
        "none_sorensen_plus_2h": baseline_score,
        "ess_stable_fraction": ess_fraction,
        "gates": gates,
        "passed": all(gates.values()),
        "cycles": cycles,
        "limits": [
            "Synthetic surface experiment only; no historical data",
            "GOES draws use model likelihood; VIIRS positives are idealized",
            "Urban parameter recovery is not an acceptance gate",
        ],
    }


def report_effect(members, workers, seeds=(7, 8, 9)):
    shifts = []
    heads = []
    for seed in seeds:
        values = inputs(members, seed)
        baseline = model_for(values, "none", workers)
        treatment = model_for(values, workers=workers)
        before = baseline.step(60, [])
        frontier = np.argwhere(before.t_q[2] == 0)
        r, c = frontier[np.argmax(frontier[:, 1])]
        x, y = baseline.geometry.x[r, c] + 1000, baseline.geometry.y[r, c]
        lon, lat = Transformer.from_crs(
            values[0].grid.crs, "EPSG:4326", always_xy=True
        ).transform(x, y)
        stamp = issue_sim_ms(values[0].grid.t_ref_s, 60)
        report = ObservationRecord(
            obs_id=f"report-{seed}",
            scenario_id="synthetic-twin",
            sensor_id="report",
            kind="ground_report",
            observed_sim_ms=stamp,
            available_sim_ms=stamp,
            geometry={"type": "point_radius", "lon": lon, "lat": lat, "radius_m": 150},
            hot=True,
            trust=0.25,
            report_kind="fire",
            provenance="simulated",
        )
        after = treatment.step(60, [report])
        a = before.t_q[2] <= 120
        b = after.t_q[2] <= 120
        edge_a = a & ~binary_erosion(a)
        edge_b = b & ~binary_erosion(b)
        symmetric = np.r_[
            distance_transform_edt(~edge_a, sampling=90)[edge_b],
            distance_transform_edt(~edge_b, sampling=90)[edge_a],
        ]
        shifts.append(float(symmetric.mean()))
        heads.append(float((np.argwhere(b)[:, 1].max() - np.argwhere(a)[:, 1].max()) * 90))
    return {
        "seeds": list(seeds),
        "members": members,
        "mean_symmetric_boundary_shift_m": float(np.mean(shifts)),
        "per_seed_boundary_shift_m": shifts,
        "per_seed_east_head_shift_m": heads,
        "gate_m": REPORT_SHIFT_GATE_M,
        "passed": bool(np.mean(shifts) >= REPORT_SHIFT_GATE_M),
        "definition": "Mean nearest-boundary distance in both directions for the q50 +2h contour, 90m grid",
    }


class _GridSpec(BaseModel):
    model_config = _STRICT
    crs: str
    x0: float
    y0: float
    cell_m: float
    height: int
    width: int
    t_ref_s: int


class _TruthSpec(BaseModel):
    model_config = _STRICT
    ros_mult: float
    wind_speed_mult: float
    wind_dir_bias_deg: float
    dfm_1h: float
    urban_ros: float
    member: int


class _GateSpec(BaseModel):
    model_config = _STRICT
    angle_truth_deg: float
    angle_tolerance_deg: float
    ros_truth: float
    ros_relative_tolerance: float
    ess_floor: float
    ess_cycle_fraction: float
    report_shift_gate_m: float


class MatchedConfig(BaseModel):
    model_config = _STRICT
    schema_version: int
    run_id: str
    purpose: Literal["frozen_candidate", "pipeline_verification"] = "frozen_candidate"
    dataset_kind: str
    members: int
    seeds: list[int]
    calibration_seeds: list[int]
    modes: list[str]
    issue_start_min: int
    issue_stop_min: int
    issue_step_min: int
    lead_minutes: list[int]
    workers: int
    timeout_s: float
    horizon_min: int
    grid: _GridSpec
    truth: _TruthSpec
    include_delayed_ground: bool
    delayed_ground_cycles: int
    gates: _GateSpec

    @model_validator(mode="after")
    def _frozen_candidate(self) -> MatchedConfig:
        try:
            validate_run_id(self.run_id)
        except ConfigError as exc:
            raise ValueError(str(exc)) from exc
        self._shared_science()
        if self.purpose == "pipeline_verification":
            self._smoke_schedule()
            return self
        if self.purpose != "frozen_candidate":
            raise ValueError("unknown matched-scoring purpose")
        expected = {
            "schema_version": 1,
            "run_id": "synthetic-matched-v1",
            "dataset_kind": "synthetic",
            "members": 32,
            "seeds": [11, 13],
            "calibration_seeds": [7, 8, 9],
            "modes": list(MODES),
            "issue_start_min": 5,
            "issue_stop_min": 120,
            "issue_step_min": 5,
            "lead_minutes": [0, 60, 120],
            "workers": 2,
            "timeout_s": 300.0,
            "horizon_min": 360,
            "include_delayed_ground": True,
            "delayed_ground_cycles": 1,
        }
        actual = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "dataset_kind": self.dataset_kind,
            "members": self.members,
            "seeds": list(self.seeds),
            "calibration_seeds": list(self.calibration_seeds),
            "modes": list(self.modes),
            "issue_start_min": self.issue_start_min,
            "issue_stop_min": self.issue_stop_min,
            "issue_step_min": self.issue_step_min,
            "lead_minutes": list(self.lead_minutes),
            "workers": self.workers,
            "timeout_s": self.timeout_s,
            "horizon_min": self.horizon_min,
            "include_delayed_ground": self.include_delayed_ground,
            "delayed_ground_cycles": self.delayed_ground_cycles,
        }
        if actual != expected or set(self.seeds) & set(self.calibration_seeds):
            raise ValueError(
                "test seeds must stay disjoint from calibration seeds 7, 8, and 9, "
                "and the matched candidate stays frozen"
            )
        return self

    def _shared_science(self) -> None:
        grid = self.grid
        if (
            self.schema_version != 1
            or self.dataset_kind != "synthetic"
            or list(self.calibration_seeds) != [7, 8, 9]
            or list(self.modes) != list(MODES)
            or self.include_delayed_ground is not True
            or self.delayed_ground_cycles != 1
            or set(self.seeds) & set(self.calibration_seeds)
        ):
            raise ValueError(
                "test seeds must stay disjoint from calibration seeds 7, 8, and 9, "
                "and the matched candidate stays frozen"
            )
        if (
            grid.crs != SYNTHETIC_CRS
            or float(grid.x0) != float(SYNTHETIC_X0)
            or float(grid.y0) != float(SYNTHETIC_Y0)
            or float(grid.cell_m) != float(SYNTHETIC_CELL_M)
            or grid.height != SYNTHETIC_HEIGHT
            or grid.width != SYNTHETIC_WIDTH
            or grid.t_ref_s != SYNTHETIC_T_REF_S
        ):
            raise ValueError("the synthetic grid is frozen")
        if self.truth.model_dump() != FROZEN_TRUTH:
            raise ValueError("hidden truth parameters are frozen")
        gates = self.gates
        if (
            gates.angle_truth_deg != ANGLE_TRUTH_DEG
            or gates.angle_tolerance_deg != ANGLE_TOLERANCE_DEG
            or gates.ros_truth != ROS_TRUTH
            or gates.ros_relative_tolerance != ROS_RELATIVE_TOLERANCE
            or gates.ess_floor != ESS_FLOOR
            or gates.ess_cycle_fraction != ESS_CYCLE_FRACTION
            or gates.report_shift_gate_m != REPORT_SHIFT_GATE_M
        ):
            raise ValueError("scientific gate thresholds are frozen")

    def _smoke_schedule(self) -> None:
        issues = self.issue_minutes
        if (
            self.run_id != "synthetic-matched-smoke-v1"
            or self.members != 2
            or list(self.seeds) != [11]
            or self.issue_start_min != 20
            or self.issue_stop_min != 30
            or self.issue_step_min != 5
            or issues != [20, 25, 30]
            or list(self.lead_minutes) != [0, 30]
            or self.horizon_min != 60
            or self.workers != 2
            or self.timeout_s != 300
        ):
            raise ValueError(
                "the pipeline smoke profile stays at 2 members, seed 11, "
                "and issues 20, 25, and 30"
            )

    @property
    def issue_minutes(self) -> list[int]:
        return list(range(self.issue_start_min, self.issue_stop_min + 1, self.issue_step_min))


def matched_config_path() -> Path:
    return repo_root() / "config/hazard_experiments/synthetic-v1.json"


def recorded_gate_snapshot() -> dict[str, object]:
    """Read the existing 128-member result. Failed gates stay failed."""
    path = repo_root() / RECORDED_GATES
    if not path.is_file():
        raise ConfigError("recorded synthetic gate file is missing")
    payload = json.loads(path.read_text(encoding="utf-8"))
    gates = payload["twin"]["gates"]
    report_passed = bool(payload["report_effect"]["passed"])
    if payload.get("synthetic") is not True:
        raise ConfigError("recorded assimilation result is not labeled synthetic")
    if (
        gates["angle_within_8_deg"]
        or gates["ros_within_25_percent"]
        or report_passed
        or payload["twin"]["passed"]
    ):
        raise ConfigError("recorded synthetic failures were changed")
    if not gates["ess_stable_80_percent_cycles"] or not gates["pf_beats_none_sorensen"]:
        raise ConfigError("recorded synthetic gate file does not match the known result")
    if payload["report_effect"]["gate_m"] != REPORT_SHIFT_GATE_M:
        raise ConfigError("recorded report gate threshold changed")
    return {
        "path": RECORDED_GATES,
        "rewritten": False,
        "synthetic": True,
        "twin_passed": False,
        "report_effect_passed": False,
        "gates": {
            "angle_within_8_deg": False,
            "ros_within_25_percent": False,
            "ess_stable_80_percent_cycles": True,
            "pf_beats_none_sorensen": True,
        },
        "gate_m": REPORT_SHIFT_GATE_M,
        "note": "E3 does not recompute or relax this file. Its failed gates stay failed.",
    }


def load_matched_config(path: Path) -> MatchedConfig:
    try:
        return MatchedConfig.model_validate(read_strict_json(path))
    except ValidationError as exc:
        raise ConfigError("matched candidate does not match the frozen synthetic experiment") from exc
    except ConfigError:
        raise
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ConfigError(f"matched-scoring config could not be read: {exc}") from exc


def config_grid_dict(config: MatchedConfig) -> dict[str, object]:
    grid = config.grid
    return {
        "crs": grid.crs,
        "x0": float(grid.x0),
        "y0": float(grid.y0),
        "cell_m": float(grid.cell_m),
        "height": int(grid.height),
        "width": int(grid.width),
        "t_ref_s": int(grid.t_ref_s),
    }


def _as_grid_dict(grid) -> dict[str, object]:
    if isinstance(grid, dict):
        return {
            "crs": grid["crs"],
            "x0": float(grid["x0"]),
            "y0": float(grid["y0"]),
            "cell_m": float(grid["cell_m"]),
            "height": int(grid["height"]),
            "width": int(grid["width"]),
            "t_ref_s": int(grid["t_ref_s"]),
        }
    height = grid.H if hasattr(grid, "H") else grid.height
    width = grid.W if hasattr(grid, "W") else grid.width
    return {
        "crs": grid.crs,
        "x0": float(grid.x0),
        "y0": float(grid.y0),
        "cell_m": float(grid.cell_m),
        "height": int(height),
        "width": int(width),
        "t_ref_s": int(grid.t_ref_s),
    }


def _write_arrays(directory: Path, seed: int, mode: str, issue: int, arrivals, weights, probability):
    path = directory / f"seed-{seed}-mode-{mode}-issue-{issue}.npz"
    np.savez(
        path,
        arrivals_abs_min=np.asarray(arrivals),
        weights=np.asarray(weights, dtype=np.float64),
        burn_probability_at_issue=np.asarray(probability, dtype=np.float64),
    )
    return path.name


def _difference(left, right):
    if left is None or right is None:
        return None
    return float(left) - float(right)


def _pair_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[int, int, int], dict[str, dict[str, object]]] = {}
    for row in rows:
        key = (int(row["seed"]), int(row["issue_min"]), int(row["lead_min"]))
        grouped.setdefault(key, {})[str(row["mode"])] = row
    paired = []
    for seed, issue, lead in sorted(grouped):
        modes = grouped[(seed, issue, lead)]
        item: dict[str, object] = {"seed": seed, "issue_min": issue, "lead_min": lead}
        for left_name, right_name, label in PAIRS:
            left = modes[left_name]
            right = modes[right_name]
            for metric in DIFFERENCE_DIRECTIONS:
                item[f"{metric}_{label}"] = _difference(left[metric], right[metric])
        paired.append(item)
    return paired


def _blank_cycle(mode: str, issue: int, reason: str) -> dict[str, object]:
    return {
        "mode": mode,
        "issue_min": issue,
        "ess_fraction": None,
        "alpha": None,
        "resampled": None,
        "accepted_observations": None,
        "rejected_observations": None,
        "duration_ms": None,
        "reason": reason,
    }


def _score_seed(config: MatchedConfig, seed: int, prepared: dict, result: dict, array_dir: Path | None):
    truth_grid = _as_grid_dict(prepared["grid"])
    assert_same_grid(truth_grid, config_grid_dict(config))
    truth = np.asarray(prepared["truth_arrivals"])
    modes = result["modes"]
    if "persistence" in modes:
        raise ConfigError("persistence is scored from the none ensemble, not a separate solver")
    for mode in ENGINE_MODES:
        if mode not in modes:
            raise ExecutionError(f"matched mode {mode} did not return a forecast")
    rows = []
    cycles = []
    array_names = []
    valid = prepared.get("valid")
    for issue in config.issue_minutes:
        none_snapshot = modes["none"].get(issue)
        if none_snapshot is None:
            raise ExecutionError(f"none did not return issue {issue}")
        none_grid = _as_grid_dict(none_snapshot["grid"])
        assert_same_grid(none_grid, truth_grid)
        none_weights = np.asarray(none_snapshot["weights"])
        if none_weights.shape != (config.members,):
            raise ExecutionError("ensemble size does not match the frozen candidate")
        persistence_probability = weighted_burn_probability(
            none_snapshot["arrivals"], none_weights, absolute_target_min(issue, 0)
        )
        if array_dir is not None:
            array_names.append(
                _write_arrays(
                    array_dir,
                    seed,
                    "persistence",
                    issue,
                    none_snapshot["arrivals"],
                    none_weights,
                    persistence_probability,
                )
            )
        for mode in MODES:
            if mode == "persistence":
                arrivals = none_snapshot["arrivals"]
                weights = none_weights
                grid = none_grid
                t_q = None
                cycles.append(
                    _blank_cycle(
                        "persistence",
                        int(issue),
                        "persistence freezes the open-loop burned-at-issue probability and does not run a separate filter",
                    )
                )
            else:
                snapshot = modes[mode].get(issue)
                if snapshot is None:
                    raise ExecutionError(f"{mode} did not return issue {issue}")
                grid = _as_grid_dict(snapshot["grid"])
                assert_same_grid(grid, truth_grid)
                arrivals = snapshot["arrivals"]
                weights = np.asarray(snapshot["weights"])
                if weights.shape != (config.members,):
                    raise ExecutionError("ensemble size does not match the frozen candidate")
                t_q = snapshot.get("t_q")
                cycle = snapshot.get("cycle")
                cycles.append(
                    cycle
                    if cycle is not None
                    else _blank_cycle(mode, int(issue), "cycle diagnostics were not recorded")
                )
                if array_dir is not None:
                    issue_probability = weighted_burn_probability(
                        arrivals, weights, absolute_target_min(issue, 0)
                    )
                    array_names.append(
                        _write_arrays(array_dir, seed, mode, issue, arrivals, weights, issue_probability)
                    )
            for lead in config.lead_minutes:
                scored = score_raw_arrival_field(
                    arrivals=arrivals,
                    weights=weights,
                    truth=truth,
                    valid=valid,
                    issue_min=issue,
                    lead_min=lead,
                    cell_m=float(grid["cell_m"]),
                    persistence=mode == "persistence",
                    prediction_grid=grid,
                    truth_grid=truth_grid,
                    t_q=t_q,
                )
                scored["seed"] = int(seed)
                scored["mode"] = mode
                rows.append(scored)
    return rows, cycles, array_names


def _this_run_gates(config: MatchedConfig, measurements_by_seed: dict) -> dict[str, object]:
    if not measurements_by_seed:
        return {
            "evaluated": False,
            "passed": None,
            "reason": "parameter recovery is not inferred from outline scores",
            "by_seed": [],
        }
    by_seed = []
    for seed in config.seeds:
        measurement = measurements_by_seed.get(seed)
        if measurement is None:
            by_seed.append(
                {
                    "seed": int(seed),
                    "passed": None,
                    "gates": None,
                    "reason": "parameter recovery was not recorded for this seed",
                }
            )
            continue
        gates = recovery_gates(
            angle_deg=measurement["angle_deg"],
            ros_mult=measurement["ros_mult"],
            ess_stable_fraction=measurement["ess_stable_fraction"],
            pf_sorensen=measurement["pf_sorensen"],
            none_sorensen=measurement["none_sorensen"],
        )
        by_seed.append(
            {
                "seed": int(seed),
                "passed": all(gates.values()),
                "gates": gates,
                "reason": None,
            }
        )
    evaluated = [item["passed"] for item in by_seed]
    passed = None if any(item is None for item in evaluated) else all(evaluated)
    return {"evaluated": passed is not None, "passed": passed, "reason": None, "by_seed": by_seed}


def run_matched(config: MatchedConfig, *, prepare, simulate, array_dir: Path | None):
    rows = []
    cycles = []
    archives = []
    array_names = []
    measurements = {}
    for seed in config.seeds:
        prepared = prepare(seed)
        frozen, digest, payload = freeze_observation_stream(prepared["records"])
        batches = mode_batches(frozen, config.issue_minutes, MODES, config.grid.t_ref_s)
        if any(batches[mode] is not batches[MODES[0]] for mode in MODES):
            raise ExecutionError("modes did not receive one observation stream")
        result = simulate(seed, frozen, batches, prepared)
        seed_rows, seed_cycles, names = _score_seed(config, int(seed), prepared, result, array_dir)
        rows.extend(seed_rows)
        cycles.extend(seed_cycles)
        array_names.extend(names)
        archives.append({"seed": int(seed), "sha256": digest, "records": payload})
        if result.get("parameter_measurements") is not None:
            measurements[int(seed)] = result["parameter_measurements"]
    paired = _pair_rows(rows)
    labels = list(LABELS)
    if config.purpose == "pipeline_verification":
        labels.append(
            "Pipeline verification of the matched runner. Not forecast-skill evidence. "
            "The frozen 32-member candidate was not executed."
        )
    report = {
        "schema_version": 1,
        "section": "matched_scoring",
        "experiment_kind": "synthetic_twin",
        "dataset_kind": "synthetic",
        "run_id": config.run_id,
        "purpose": config.purpose,
        "evidence_class": (
            "pipeline_verification"
            if config.purpose == "pipeline_verification"
            else "synthetic_scoring"
        ),
        "full_32_member_run": (
            "not_executed"
            if config.purpose == "pipeline_verification"
            else "not_executed_by_the_smoke_profile"
        ),
        "scientific_acceptance": "not_established_by_synthetic_twin",
        "primary_score": "raw_weighted_arrivals",
        "primary_definition": (
            "p = sum(weights * (arrivals_abs_min <= issue_min + lead_min)); "
            "predicted burned is p >= 0.5. Persistence uses issue_min for p and the lead for truth."
        ),
        "labels": labels,
        "modes": list(MODES),
        "persistence": {
            "definition": "weighted burned-at-issue probability from the matched none ensemble, held for every lead",
            "source_mode": "none",
            "not_a_zero_raster": True,
        },
        "observation_stream": {
            "frozen_before_modes": True,
            "seeds": [{"seed": item["seed"], "sha256": item["sha256"]} for item in archives],
        },
        "difference_directions": {
            f"{metric}_{label}": DIFFERENCE_DIRECTIONS[metric]
            for _left, _right, label in PAIRS
            for metric in DIFFERENCE_DIRECTIONS
        },
        "scores": rows,
        "paired_differences": paired,
        "cycles": cycles,
        "arrays": sorted(array_names),
        "scientific_gates": {
            "thresholds_unchanged": True,
            "recorded_128_member": recorded_gate_snapshot(),
            "this_run": _this_run_gates(config, measurements),
        },
        "limits": labels,
    }
    observations = {
        "schema_version": 1,
        "dataset_kind": "synthetic",
        "provenance": "simulated",
        "frozen_before_modes": True,
        "seeds": archives,
    }
    return report, observations


def native_prepare(config: MatchedConfig) -> Callable:
    def prepare(seed: int):
        values = inputs(config.members, seed, horizon_min=config.horizon_min)
        truth = model_for(
            (*values[:2], replace(values[2], n_members=1), values[3]),
            "none",
            config.workers,
            config.timeout_s,
        )
        apply_synthetic_truth_override(truth, **FROZEN_TRUTH)
        truth._ignition_xy[:] = values[3].points_xy
        forecast = truth.step(0, [])
        actual = snapshot_prediction(truth, forecast).arrivals_abs_min
        sensor = model_for(values, "none", config.workers, config.timeout_s)
        rng = np.random.default_rng([20261024, int(seed)])
        records = synthetic_observation_records(
            sensor,
            actual,
            config.issue_minutes,
            rng,
            include_delayed_ground=True,
            seed=seed,
            delay_cycles=config.delayed_ground_cycles,
        )
        return {
            "records": records,
            "truth_arrivals": actual[0],
            "grid": _as_grid_dict(values[0].grid),
        }

    return prepare


def _cycle_from_forecast(mode: str, issue: int, forecast, members: int) -> dict[str, object]:
    accepted = sum(1 for item in forecast.obs_used if item.get("used"))
    rejected = sum(1 for item in forecast.obs_used if not item.get("used"))
    return {
        "mode": mode,
        "issue_min": int(issue),
        "ess_fraction": float(forecast.ess / members),
        "alpha": float(forecast.tempering_alpha),
        "resampled": bool(forecast.resampled),
        "accepted_observations": int(accepted),
        "rejected_observations": int(rejected),
        "duration_ms": int(forecast.duration_ms),
        "observation_ids": [str(item["obs_id"]) for item in forecast.obs_used],
        "reason": None,
    }


def native_simulate(config: MatchedConfig) -> Callable:
    def simulate(seed: int, frozen, batches, prepared):
        values = inputs(config.members, seed, horizon_min=config.horizon_min)
        modes = {}
        forecasts = {}
        for mode in ENGINE_MODES:
            model = model_for(values, mode, config.workers, config.timeout_s)
            modes[mode] = {}
            forecasts[mode] = {}
            for issue, records in batches[mode]:
                copies = [record.model_copy(deep=True) for record in records]
                print(f"{config.run_id} {mode} issue {issue}", flush=True)
                forecast = model.step(issue, copies)
                snap = snapshot_prediction(model, forecast)
                modes[mode][int(issue)] = {
                    "arrivals": snap.arrivals_abs_min,
                    "weights": snap.weights,
                    "grid": _as_grid_dict(model.land.grid),
                    "t_q": np.asarray(forecast.t_q),
                    "cycle": _cycle_from_forecast(mode, int(issue), forecast, config.members),
                }
                forecasts[mode][int(issue)] = forecast
        pf_cycles = [
            modes["pf"][issue]["cycle"]["ess_fraction"] for issue in config.issue_minutes
        ]
        measurement = None
        if 120 in forecasts["pf"] and 120 in forecasts["none"]:
            truth = np.asarray(prepared["truth_arrivals"])
            true_mask = truth <= 240
            measurement = {
                "angle_deg": forecasts["pf"][120].params_summary["wind_dir_bias_deg"][0],
                "ros_mult": forecasts["pf"][120].params_summary["ros_mult"][0],
                "ess_stable_fraction": float(np.mean([value >= ESS_FLOOR for value in pf_cycles])),
                "pf_sorensen": sorensen(forecasts["pf"][120].t_q[2] <= 120, true_mask),
                "none_sorensen": sorensen(forecasts["none"][120].t_q[2] <= 120, true_mask),
            }
        return {"modes": modes, "parameter_measurements": measurement}

    return simulate


def verify_pipeline(config: MatchedConfig, report: dict, observations: dict, array_dir: Path) -> dict:
    """Check the smoke runner. This does not judge forecast skill."""
    failures: list[str] = []
    if report.get("purpose") != "pipeline_verification" or report.get("evidence_class") != "pipeline_verification":
        failures.append("report is not labeled pipeline verification")
    if report.get("full_32_member_run") != "not_executed":
        failures.append("the smoke report must keep the 32-member run unexecuted")
    if report.get("scientific_acceptance") != "not_established_by_synthetic_twin":
        failures.append("pipeline verification must not claim forecast skill")
    issues = config.issue_minutes
    leads = list(config.lead_minutes)
    expected_cases = {
        (int(seed), int(issue), int(lead))
        for seed in config.seeds
        for issue in issues
        for lead in leads
    }
    paired = report["paired_differences"]
    actual_cases = {(int(row["seed"]), int(row["issue_min"]), int(row["lead_min"])) for row in paired}
    if actual_cases != expected_cases or len(paired) != len(expected_cases):
        failures.append("paired differences are not aligned one-to-one with seed, issue, and lead")
    for row in paired:
        if "brier_pf_minus_none" not in row or "dice_pf_minus_none" not in row:
            failures.append("a paired row is missing the pf-minus-none scores")
            break
    score_cases = {
        (int(row["seed"]), str(row["mode"]), int(row["issue_min"]), int(row["lead_min"]))
        for row in report["scores"]
    }
    expected_scores = {
        (seed, mode, issue, lead)
        for seed, issue, lead in expected_cases
        for mode in MODES
    }
    if score_cases != expected_scores:
        failures.append("score rows do not cover every mode, seed, issue, and lead")
    for row in report["scores"]:
        for metric in ("brier", "dice", "iou", "false_positive_ha", "false_negative_ha", "probability_sum"):
            value = row[metric]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
                failures.append(f"non-finite {metric} for {row['mode']} at issue {row['issue_min']}")
                break
    held: dict[tuple[int, int], set[float]] = {}
    for row in report["scores"]:
        if row["mode"] != "persistence":
            continue
        key = (int(row["seed"]), int(row["issue_min"]))
        held.setdefault(key, set()).add(float(row["probability_sum"]))
        if int(row["probability_time_min"]) != int(row["issue_min"]):
            failures.append("persistence probability moved off the issue time")
    if any(len(values) != 1 for values in held.values()) or set(held) != {
        (int(seed), int(issue)) for seed in config.seeds for issue in issues
    }:
        failures.append("persistence probability changed across forecast leads")
    cycles = {
        (str(row["mode"]), int(row["issue_min"])): row for row in report["cycles"]
    }
    for archive in observations["seeds"]:
        payload = archive["records"]
        digest = sha256_canonical(payload)
        if digest != archive["sha256"]:
            failures.append(f"observation hash mismatch for seed {archive['seed']}")
        records = [ObservationRecord.model_validate(item) for item in payload]
        batches = mode_batches(records, issues, MODES, config.grid.t_ref_s)
        id_rows = [[item.obs_id for item in chunk] for _issue, chunk in batches["pf"]]
        if any(
            [[item.obs_id for item in chunk] for _issue, chunk in batches[mode]] != id_rows
            for mode in MODES
        ):
            failures.append("modes received different observation batches")
        by_issue = {int(issue): chunk for issue, chunk in batches["pf"]}
        release = int(issues[1])
        tracked = f"ground-{archive['seed']}-{release}"
        tracked_record = next((item for item in records if item.obs_id == tracked), None)
        if tracked_record is None:
            failures.append(f"missing delayed report {tracked}")
        else:
            if tracked_record.available_sim_ms != issue_sim_ms(config.grid.t_ref_s, release):
                failures.append("delayed report availability does not match its release issue")
            if tracked_record.observed_sim_ms != issue_sim_ms(config.grid.t_ref_s, release - 5):
                failures.append("delayed report was not observed one cycle earlier")
            if tracked_record.provenance != "simulated":
                failures.append("delayed report is not labeled simulated")
        early_ids = [item.obs_id for item in by_issue[int(issues[0])]]
        release_ids = [item.obs_id for item in by_issue[release]]
        later_ids = [item.obs_id for item in by_issue[int(issues[2])]]
        if tracked in early_ids or tracked not in release_ids or tracked in later_ids:
            failures.append("delayed report was visible before release or repeated later")
        prior = None
        for issue, chunk in batches["pf"]:
            current = issue_sim_ms(config.grid.t_ref_s, issue)
            previous = None if prior is None else issue_sim_ms(config.grid.t_ref_s, prior)
            for record in chunk:
                if record.available_sim_ms > current or record.observed_sim_ms > record.available_sim_ms:
                    failures.append(f"observation {record.obs_id} was not available at issue {issue}")
                if previous is not None and record.available_sim_ms <= previous:
                    failures.append(f"observation {record.obs_id} was repeated after its release")
            prior = issue
            expected_ids = [
                item.obs_id
                for item in sorted(
                    chunk,
                    key=lambda record: (
                        record.available_sim_ms,
                        record.observed_sim_ms,
                        record.obs_id,
                    ),
                )
            ]
            for mode in ENGINE_MODES:
                seen = cycles.get((mode, int(issue)), {}).get("observation_ids")
                if seen != expected_ids:
                    failures.append(f"{mode} did not step the frozen batch at issue {issue}")
        for issue in issues:
            persistence_path = array_dir / f"seed-{archive['seed']}-mode-persistence-issue-{issue}.npz"
            none_path = array_dir / f"seed-{archive['seed']}-mode-none-issue-{issue}.npz"
            with np.load(persistence_path, allow_pickle=False) as stored, np.load(
                none_path, allow_pickle=False
            ) as source:
                arrivals = stored["arrivals_abs_min"]
                weights = np.asarray(stored["weights"])
                probability = stored["burn_probability_at_issue"]
                if arrivals.shape != (config.members, config.grid.height, config.grid.width):
                    failures.append("snapshot shape does not match the ensemble grid")
                if arrivals.dtype.kind != "f" or np.isnan(arrivals).any() or np.isneginf(arrivals).any():
                    failures.append("snapshot arrivals contain NaN or negative infinity")
                if (
                    weights.shape != (config.members,)
                    or not np.isfinite(weights).all()
                    or abs(float(weights.sum()) - 1.0) > 1e-8
                ):
                    failures.append("snapshot weights are not a normalized ensemble")
                if not np.array_equal(arrivals, source["arrivals_abs_min"]) or not np.array_equal(
                    probability, source["burn_probability_at_issue"]
                ):
                    failures.append("persistence snapshot is not the open-loop issue probability")
                recomputed = weighted_burn_probability(arrivals, weights, float(issue))
                if not np.allclose(recomputed, probability):
                    failures.append("stored issue probability does not match the snapshot arrivals")
    return {
        "passed": not failures,
        "failures": failures,
        "evidence_class": "pipeline_verification",
        "cases": len(expected_cases),
    }


def configured_assimilation(
    config_path: Path,
    out: Path,
    *,
    prepare=None,
    simulate=None,
    require_gates: bool = False,
) -> int:
    config = load_matched_config(config_path)
    if Path(out).name != config.run_id:
        raise ConfigError("config --out directory name must be the run_id")
    if prepare is None:
        prepare = native_prepare(config)
    if simulate is None:
        simulate = native_simulate(config)
    session = RunSession(Path(out).parent, config.run_id)
    session.open()
    started = time.perf_counter()
    try:
        report, observations = run_matched(
            config, prepare=prepare, simulate=simulate, array_dir=session.staging
        )
        report["wall_seconds"] = time.perf_counter() - started
        verification = None
        if config.purpose == "pipeline_verification":
            verification = verify_pipeline(config, report, observations, session.staging)
            report["pipeline_verification"] = verification
        write_strict_json(session.staging / "assimilation.json", report)
        write_strict_json(session.staging / "observations.json", observations)
        if verification is not None and not verification["passed"]:
            raise ExecutionError(
                "pipeline verification failed: " + "; ".join(verification["failures"])
            )
        published = session.commit()
    except Exception as exc:
        session.fail(exc)
        if isinstance(exc, (ConfigError, ExecutionError)):
            raise
        raise ExecutionError(str(exc)) from exc
    print(
        published,
        report["purpose"],
        "wall_seconds",
        round(report["wall_seconds"], 3),
        "full_32_member_run",
        report["full_32_member_run"],
        flush=True,
    )
    return completion_exit_code(
        gates_passed=report["scientific_gates"]["this_run"]["passed"],
        require_gates=require_gates,
    )


def _legacy_result(members: int, workers: int) -> dict:
    started = time.perf_counter()
    result = {
        "synthetic": True,
        "twin": twin(members, workers),
        "report_effect": report_effect(members, workers),
    }
    result["wall_seconds"] = time.perf_counter() - started
    return result


def parse_invocation(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--members", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--require-gates", action="store_true")
    args = parser.parse_args(argv)
    explicit = [
        name for name in ("members", "workers") if getattr(args, name) is not None
    ]
    if args.config is not None:
        if explicit:
            names = ", ".join(f"--{name}" for name in explicit)
            raise ConfigError(
                f"config mode rejects {names}; those flags would conflict with the config"
            )
        return {
            "mode": "config",
            "out": args.out,
            "config": args.config,
            "require_gates": args.require_gates,
        }
    return {
        "mode": "legacy",
        "out": args.out,
        "members": 32 if args.members is None else args.members,
        "workers": 2 if args.workers is None else args.workers,
        "require_gates": args.require_gates,
    }


def main(argv: list[str] | None = None) -> int:
    try:
        invocation = parse_invocation(argv)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if invocation["mode"] == "legacy":
        if invocation["out"].exists():
            raise FileExistsError(invocation["out"])
        result = _legacy_result(invocation["members"], invocation["workers"])
        write_json(invocation["out"], result)
        print(
            f"Evaluation saved to {invocation['out']}: twin={result['twin']['passed']}, report={result['report_effect']['passed']}",
            flush=True,
        )
        return legacy_exit_code(result, invocation["require_gates"])
    try:
        return configured_assimilation(
            invocation["config"],
            invocation["out"],
            require_gates=invocation["require_gates"],
        )
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ExecutionError as exc:
        print(str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
