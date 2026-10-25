"""Frozen-artifact historical evaluation. No downloads, fitting, or default model changes.

Run: python -m emberline.hazard.historical --manifest experiment.json --out report.json
Create an explicitly synthetic harness example with --make-example DIRECTORY.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from scipy.ndimage import binary_erosion, distance_transform_edt

MODES = ("persistence", "none", "reinit", "pf")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Artifact(StrictModel):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Source(Artifact):
    source_id: str
    role: Literal["static", "weather", "observation", "truth", "config"]
    available_sim_ms: StrictInt


class Grid(StrictModel):
    crs: Literal["EPSG:32611"] = "EPSG:32611"
    nx: int = Field(gt=0)
    ny: int = Field(gt=0)
    cell_m: float = Field(gt=0)
    x0: float
    y0: float
    row_order: Literal["north_first"] = "north_first"


class Incident(StrictModel):
    incident_id: str
    split: Literal["train", "calibration", "test"]


class Prediction(Artifact):
    input_ids: list[str]
    model_version: str = Field(min_length=1)
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_ms: float = Field(ge=0)
    seed: StrictInt = Field(ge=0)
    model_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Case(StrictModel):
    case_id: str
    incident_id: str
    issue_sim_ms: StrictInt
    lead_min: Literal[30, 60, 120, 360]
    truth_source_id: str
    predictions: dict[str, Prediction]


class Experiment(StrictModel):
    schema_version: Literal[1]
    dataset_kind: Literal["historical", "synthetic"]
    experiment_id: str = Field(min_length=1)
    frozen: Literal[True]
    threshold: float = Field(default=0.5, gt=0, lt=1)
    grid: Grid
    incidents: list[Incident] = Field(min_length=1)
    sources: list[Source] = Field(min_length=1)
    cases: list[Case] = Field(min_length=1)


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def grid_digest(grid):
    return hashlib.sha256(
        json.dumps(grid.model_dump(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def artifact_path(root, artifact):
    path = Path(artifact.path)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("artifact path must be confined and relative")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise ValueError("missing or escaped artifact")
    if digest(resolved) != artifact.sha256:
        raise ValueError(f"checksum mismatch: {artifact.path}")
    return resolved


def score(probability, truth, valid, cell_m, threshold=0.5):
    """Equal-area grid metrics; unknown truth is excluded, not treated as unburned."""
    p, y, valid = np.asarray(probability), np.asarray(truth), np.asarray(valid)
    if (
        p.ndim != 2
        or p.shape != y.shape
        or p.shape != valid.shape
        or valid.dtype != bool
        or not valid.any()
        or not np.isfinite(p).all()
        or ((p < 0) | (p > 1)).any()
        or not np.isin(y, [0, 1]).all()
        or cell_m <= 0
        or not np.isfinite(cell_m)
        or not 0 < threshold < 1
    ):
        raise ValueError("invalid scoring arrays")
    predicted, observed = (p >= threshold) & valid, y.astype(bool) & valid
    n_pred, n_true = int(predicted.sum()), int(observed.sum())
    intersection = int((predicted & observed).sum())
    union = int((predicted | observed).sum())
    # Exclude validity/domain edges from the boundary-distance metric.
    interior = binary_erosion(valid, border_value=0)
    pb = (predicted & ~binary_erosion(predicted)) & interior
    tb = (observed & ~binary_erosion(observed)) & interior
    boundary_mean = boundary_p95 = None
    if pb.any() and tb.any():
        distances = (
            np.concatenate(
                [distance_transform_edt(~tb)[pb], distance_transform_edt(~pb)[tb]]
            )
            * cell_m
        )
        boundary_mean = float(distances.mean())
        boundary_p95 = float(np.quantile(distances, 0.95))
    reliability = []
    for i in range(10):
        chosen = valid & (p >= i / 10) & ((p < (i + 1) / 10) if i < 9 else (p <= 1))
        reliability.append(
            {
                "lower": i / 10,
                "upper": (i + 1) / 10,
                "n": int(chosen.sum()),
                "mean_probability": float(p[chosen].mean()) if chosen.any() else None,
                "observed_frequency": float(y[chosen].mean()) if chosen.any() else None,
            }
        )
    return {
        "n_valid": int(valid.sum()),
        "brier": float(np.mean((p[valid] - y[valid]) ** 2)),
        "iou": intersection / union if union else 1.0,
        "sorensen": 2 * intersection / (n_pred + n_true) if n_pred + n_true else 1.0,
        "both_empty": n_pred + n_true == 0,
        "predicted_ha": n_pred * cell_m**2 / 10000,
        "observed_ha": n_true * cell_m**2 / 10000,
        "area_bias_ha": (n_pred - n_true) * cell_m**2 / 10000,
        "area_ratio": n_pred / n_true if n_true else None,
        "boundary_mean_m": boundary_mean,
        "boundary_p95_m": boundary_p95,
        "reliability": reliability,
    }


def write_prediction(
    path, arrivals, weights, *, grid, issue_sim_ms, t_ref_sim_ms, horizon_min
):
    """Archive raw ensemble output; never reconstruct exact probabilities from 5 quantiles.

    The runtime supplies its post-update arrivals and matching weights. This internal
    evaluation format is separate from, and does not change, the frozen HazardModel API.
    """
    np.savez_compressed(
        path,
        arrival_min=np.asarray(arrivals),
        weights=np.asarray(weights),
        grid_sha256=grid_digest(grid),
        issue_sim_ms=issue_sim_ms,
        t_ref_sim_ms=t_ref_sim_ms,
        horizon_min=horizon_min,
    )


def _scalar(data, key):
    value = data[key]
    if value.shape != ():
        raise ValueError(f"{key} must be scalar")
    return value.item()


def evaluate(manifest_path):
    manifest_path = Path(manifest_path)
    exp = Experiment.model_validate_json(manifest_path.read_text())
    root = manifest_path.parent.resolve()
    sources = {s.source_id: s for s in exp.sources}
    incidents = {i.incident_id: i.split for i in exp.incidents}
    if len(sources) != len(exp.sources) or len(incidents) != len(exp.incidents):
        raise ValueError(
            "duplicate source or incident; incident splits must be disjoint"
        )
    if len({c.case_id for c in exp.cases}) != len(exp.cases):
        raise ValueError("duplicate case")
    paths = {key: artifact_path(root, source) for key, source in sources.items()}
    shape = (exp.grid.ny, exp.grid.nx)
    rows = []
    truth_sources = {s.source_id for s in exp.sources if s.role == "truth"}
    truth_hashes = {sources[key].sha256 for key in truth_sources}
    for case in exp.cases:
        if case.incident_id not in incidents or set(case.predictions) != set(MODES):
            raise ValueError("unknown incident or incomplete matched baselines")
        truth_source = sources.get(case.truth_source_id)
        if truth_source is None or truth_source.role != "truth":
            raise ValueError("truth must be an independent registered truth artifact")
        target_ms = case.issue_sim_ms + case.lead_min * 60000
        with np.load(paths[case.truth_source_id], allow_pickle=False) as data:
            truth, valid = data["burned"], data["valid"]
            if (
                truth.shape != shape
                or valid.shape != shape
                or valid.dtype != bool
                or _scalar(data, "valid_sim_ms") != target_ms
                or _scalar(data, "grid_sha256") != grid_digest(exp.grid)
            ):
                raise ValueError("truth time, grid, or coverage mismatch")
        for mode, prediction in case.predictions.items():
            if not prediction.input_ids or len(set(prediction.input_ids)) != len(
                prediction.input_ids
            ):
                raise ValueError("input audit must be nonempty and unique")
            for key in prediction.input_ids:
                source = sources.get(key)
                if source is None or source.available_sim_ms > case.issue_sim_ms:
                    raise ValueError("unknown or future input")
                if key in truth_sources or source.sha256 in truth_hashes:
                    raise ValueError("evaluation truth reused as model input")
            if prediction.config_sha256 not in {
                sources[k].sha256
                for k in prediction.input_ids
                if sources[k].role == "config"
            }:
                raise ValueError("model config must be checksummed in the input audit")
            path = artifact_path(root, prediction)
            with np.load(path, allow_pickle=False) as data:
                arrivals, weights = data["arrival_min"], data["weights"]
                if (
                    arrivals.ndim != 3
                    or arrivals.shape[0] == 0
                    or arrivals.shape[1:] != shape
                    or weights.shape != (arrivals.shape[0],)
                    or np.isnan(arrivals).any()
                    or np.isneginf(arrivals).any()
                    or not np.isfinite(weights).all()
                    or (weights < 0).any()
                    or not np.isclose(weights.sum(), 1)
                    or _scalar(data, "issue_sim_ms") != case.issue_sim_ms
                    or _scalar(data, "grid_sha256") != grid_digest(exp.grid)
                ):
                    raise ValueError("invalid ensemble, issue, weights or grid")
                reference = _scalar(data, "t_ref_sim_ms")
                horizon = _scalar(data, "horizon_min")
                if (
                    not isinstance(reference, int)
                    or not isinstance(horizon, (float, int))
                    or not np.isfinite(horizon)
                    or horizon < case.lead_min
                ):
                    raise ValueError(
                        "invalid reference or insufficient forecast horizon"
                    )
                target_min = (target_ms - reference) / 60000
                weights = weights / weights.sum()
                probability = np.sum(
                    weights[:, None, None] * (arrivals <= target_min), axis=0
                )
                if mode == "persistence":
                    now = (case.issue_sim_ms - reference) / 60000
                    initial = np.sum(weights[:, None, None] * (arrivals <= now), axis=0)
                    if not np.array_equal(probability, initial):
                        raise ValueError(
                            "persistence baseline must not advance the fire"
                        )
            metrics = score(probability, truth, valid, exp.grid.cell_m, exp.threshold)
            rows.append(
                {
                    "case_id": case.case_id,
                    "incident_id": case.incident_id,
                    "split": incidents[case.incident_id],
                    "mode": mode,
                    "issue_sim_ms": case.issue_sim_ms,
                    "lead_min": case.lead_min,
                    "model_version": prediction.model_version,
                    "seed": prediction.seed,
                    "n_members": len(weights),
                    "model_source_sha256": prediction.model_source_sha256,
                    "prediction_sha256": prediction.sha256,
                    "config_sha256": prediction.config_sha256,
                    "runtime_ms": prediction.runtime_ms,
                    **metrics,
                }
            )
    for row in rows:
        persistence = next(
            r
            for r in rows
            if r["case_id"] == row["case_id"] and r["mode"] == "persistence"
        )
        row["brier_skill_vs_persistence"] = (
            1 - row["brier"] / persistence["brier"]
            if persistence["brier"] > 0
            else None
        )
    summary = []
    for split in sorted(set(incidents.values())):
        for lead in sorted({c.lead_min for c in exp.cases}):
            for mode in MODES:
                selected = [
                    r
                    for r in rows
                    if r["split"] == split
                    and r["lead_min"] == lead
                    and r["mode"] == mode
                ]
                if not selected:
                    continue
                # Equal incident weight; repeated cycles do not become independent fires.
                per_fire = [
                    np.mean([r["brier"] for r in selected if r["incident_id"] == fire])
                    for fire in sorted({r["incident_id"] for r in selected})
                ]
                summary.append(
                    {
                        "split": split,
                        "lead_min": lead,
                        "mode": mode,
                        "n_cases": len(selected),
                        "n_incidents": len(per_fire),
                        "incident_macro_brier": float(np.mean(per_fire)),
                    }
                )
    return {
        "schema_version": 1,
        "experiment_id": exp.experiment_id,
        "manifest_sha256": digest(manifest_path),
        "evaluator_sha256": digest(__file__),
        "dataset_kind": exp.dataset_kind,
        "status": "synthetic_harness_only"
        if exp.dataset_kind == "synthetic"
        else "historical_artifacts_scored",
        "scientific_acceptance": "not_established_by_this_report",
        "cases": rows,
        "summary": summary,
        "limitations": [
            "Input independence and completeness require external provenance review",
            "Boundary metrics use interior grid-cell boundaries, not exact vector geometry",
            "Arrival-time error requires independent arrival/interval truth; not inferred from one perimeter",
            "No uncertainty interval or held-out skill claim from one incident",
            "Runtime is supplied metadata; use the dedicated benchmark for performance acceptance",
        ],
    }


def make_example(root):
    """Explicitly synthetic, analytical harness fixture; never historical fire evidence."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError("example destination must be empty")
    grid = Grid(nx=5, ny=5, cell_m=90, x0=400000, y0=3800000)
    cfg = root / "config.json"
    cfg.write_text('{"synthetic":true,"spotting":false}\n')
    source = Source(
        path=cfg.name,
        sha256=digest(cfg),
        source_id="config",
        role="config",
        available_sim_ms=0,
    )
    truth = np.zeros((5, 5), dtype=np.uint8)
    truth[1:4, 1:4] = 1
    truth_path = root / "truth.npz"
    np.savez_compressed(
        truth_path,
        burned=truth,
        valid=np.ones_like(truth, dtype=bool),
        grid_sha256=grid_digest(grid),
        valid_sim_ms=3600000,
    )
    truth_source = Source(
        path=truth_path.name,
        sha256=digest(truth_path),
        source_id="independent-truth",
        role="truth",
        available_sim_ms=3660000,
    )
    predictions = {}
    for mode in MODES:
        arrivals = np.full((2, 5, 5), np.inf)
        arrivals[:, 2, 2] = 0
        if mode != "persistence":
            arrivals[:, 1:4, 1:4] = 30
        path = root / f"{mode}.npz"
        write_prediction(
            path,
            arrivals,
            [0.5, 0.5],
            grid=grid,
            issue_sim_ms=0,
            t_ref_sim_ms=0,
            horizon_min=360,
        )
        predictions[mode] = Prediction(
            path=path.name,
            sha256=digest(path),
            input_ids=["config"],
            config_sha256=source.sha256,
            model_version=f"synthetic-{mode}",
            runtime_ms=0,
            seed=7,
            model_source_sha256=digest(__file__),
        )
    exp = Experiment(
        schema_version=1,
        dataset_kind="synthetic",
        experiment_id="harness-smoke-only",
        frozen=True,
        grid=grid,
        incidents=[Incident(incident_id="synthetic-example", split="test")],
        sources=[source, truth_source],
        cases=[
            Case(
                case_id="issue0-lead60",
                incident_id="synthetic-example",
                issue_sim_ms=0,
                lead_min=60,
                truth_source_id=truth_source.source_id,
                predictions=predictions,
            )
        ],
    )
    path = root / "experiment.json"
    path.write_text(exp.model_dump_json(indent=2) + "\n")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--manifest", type=Path)
    group.add_argument("--make-example", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    manifest = make_example(args.make_example) if args.make_example else args.manifest
    experiment = Experiment.model_validate_json(manifest.read_text())
    protected = {manifest.resolve()}
    protected.update(
        (manifest.parent / source.path).resolve() for source in experiment.sources
    )
    protected.update(
        (manifest.parent / prediction.path).resolve()
        for case in experiment.cases
        for prediction in case.predictions.values()
    )
    if args.out.resolve() in protected:
        parser.error(
            "report must not overwrite the frozen manifest or source artifacts"
        )
    report = evaluate(manifest)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                k: report[k]
                for k in ["status", "scientific_acceptance", "manifest_sha256"]
            }
        )
    )


if __name__ == "__main__":
    main()
