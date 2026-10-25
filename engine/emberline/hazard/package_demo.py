"""Package a completed synthetic demo export with its evidence and checksums.

The forecast tree is copied unchanged. This module does not install dependencies,
change the filter, or assign a runtime branch id.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import numpy as np
from pyproj import Transformer

from emberline.contracts import ObservationRecord
from emberline.hazard.bridge import BackendOptions
from emberline.hazard.checkpoint import environment
from emberline.hazard.demo import build_inputs
from emberline.hazard.experiment_io import (
    ConfigError,
    ExecutionError,
    canonical_json,
    exit_code_for,
    read_strict_json,
    repo_root,
    resolve_input_path,
    sha256_canonical,
    sha256_file,
    sha256_hex,
    write_strict_json,
)
from emberline.hazard.experiments import snapshot_prediction
from emberline.hazard.model import RustHazardModel

PACKAGE_SCHEMA = 1
HASH_MANIFEST = "artifacts.sha256.json"
PUSHED_COMMIT = "92872c01f3f90617e9aae171cc5b4c1ea02725b9"
LABELS = (
    "synthetic",
    "cached replay",
    "pipeline verification, not forecast skill",
)
LIMITATIONS = {
    "full_32_member_experiment": "not_executed",
    "report_matrix_seeds_7_8_9": "not_executed",
    "historical_validation": "unavailable",
}
REBUILD_COMMANDS = (
    "uv run --project engine python -m emberline.hazard.demo --out /tmp/emberline-engine-export-v1 --members 16 --filter pf --workers 2",
    "node tools/check_hazard_frontend.mjs /tmp/emberline-engine-export-v1",
    "uv run --project engine python -m emberline.hazard.package_demo --forecast-root /tmp/emberline-engine-export-v1 --smoke-report /tmp/emberline-e3-smoke/synthetic-matched-smoke-v1/assimilation.json --out /tmp/emberline-engine-package-v1",
    "uv run --project engine python -m emberline.hazard.package_demo --verify /tmp/emberline-engine-package-v1",
)
_REPO_EVIDENCE = (
    ("configs/synthetic-v1.json", "config/hazard_experiments/synthetic-v1.json"),
    ("configs/synthetic-smoke.json", "config/hazard_experiments/synthetic-smoke.json"),
    ("configs/report-effect-v1.json", "config/hazard_experiments/report-effect-v1.json"),
    ("benchmark/benchmark.json", "docs/evidence/rust-hazard/benchmark.json"),
    ("benchmark/README.md", "docs/evidence/rust-hazard/README.md"),
)


def interpret_ci(payload: dict, *, commit: str = PUSHED_COMMIT) -> dict[str, object]:
    """A pending or incomplete check is not a pass."""
    runs = payload.get("check_runs") or []
    jobs = [
        {
            "name": run.get("name"),
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "html_url": run.get("html_url"),
        }
        for run in runs
    ]
    completed = bool(jobs) and all(job["status"] == "completed" for job in jobs)
    passed = completed and all(job["conclusion"] == "success" for job in jobs)
    if not jobs or not completed:
        status = "pending"
    elif passed:
        status = "success"
    else:
        status = "failure"
    return {
        "commit": commit,
        "branch": "docs/deep-learning-framework",
        "workflow": "Rust HazardModel integration",
        "status": status,
        "passed": passed,
        "jobs": jobs,
        "note": "Pending or unavailable is not a pass. This records the pushed commit, not a later package commit.",
    }


def fetch_ci(commit: str = PUSHED_COMMIT) -> dict[str, object]:
    url = (
        "https://api.github.com/repos/linchangg/Legendary-Fire-Project/commits/"
        f"{commit}/check-runs?per_page=100"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "emberline-package",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode())
    except Exception as exc:
        result = interpret_ci({"check_runs": []}, commit=commit)
        result["status"] = "unavailable"
        result["passed"] = False
        result["message"] = f"{type(exc).__name__}: {exc}"[:500]
        return result
    if not isinstance(payload, dict):
        raise ExecutionError("CI response was not an object")
    return interpret_ci(payload, commit=commit)


def build_package(
    forecast_root: Path,
    out: Path,
    *,
    smoke_report: Path | None = None,
    evidence_root: Path | None = None,
    ci: dict[str, object] | None = None,
    workers: int = 2,
) -> Path:
    """Copy one completed export and write package metadata before the checksum file."""
    source = Path(forecast_root)
    destination = Path(out)
    if destination.exists() or _staging(destination).exists():
        raise ConfigError(f"package directory already exists: {destination}")
    forecast = _require_synthetic_forecast(source)
    staging = _staging(destination)
    staging.mkdir(parents=True)
    try:
        shutil.copytree(source, staging / "forecast", symlinks=False)
        decoder = _run_decoder(staging / "forecast")
        restart = _fresh_restart(int(forecast["members"]), str(forecast["filter"]), workers)
        _write_evidence(staging, smoke_report, evidence_root)
        _write_restart(staging, restart)
        _write_report_branch(staging)
        limitations = dict(LIMITATIONS)
        write_strict_json(staging / "limitations.json", limitations)
        package = _package_document(forecast, ci if ci is not None else fetch_ci(), restart)
        write_strict_json(staging / "package.json", package)
        _write_readme(staging / "README.md")
        verification = {
            "schema_version": PACKAGE_SCHEMA,
            "status": "complete",
            "dataset_kind": "synthetic",
            "execution_mode": "cached_replay",
            "forecast_skill": False,
            "labels": list(LABELS),
            "decoder": decoder,
            "restart": restart,
            "ci": package["ci"],
            "limitations": limitations,
            "hash_manifest": HASH_MANIFEST,
            "hash_manifest_excludes_itself": True,
            "provenance": "hashes were computed from the copied package files",
        }
        write_strict_json(staging / "verification.json", verification)
        _write_hash_manifest(staging)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


def verify_package(root: Path) -> dict[str, object]:
    """Recompute checksums and decode the forecast from this package copy."""
    package_root = Path(root).resolve()
    if not package_root.is_dir():
        raise ConfigError("package directory does not exist")
    manifest_path = package_root / HASH_MANIFEST
    manifest = read_strict_json(manifest_path)
    if not isinstance(manifest, dict) or not manifest:
        raise ExecutionError("hash manifest is empty")
    if HASH_MANIFEST in manifest:
        raise ExecutionError("hash manifest must exclude itself")
    present = _payload_files(package_root)
    if set(manifest) != set(present):
        raise ExecutionError("hash manifest does not match the copied package files")
    for relative, expected in manifest.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise ExecutionError("hash manifest entries must be strings")
        digest = sha256_file(resolve_input_path(package_root, relative))
        if digest != expected:
            raise ExecutionError(f"hash mismatch: {relative}")
    document = read_strict_json(resolve_input_path(package_root, "package.json"))
    limitations = read_strict_json(resolve_input_path(package_root, "limitations.json"))
    _require_labels(document, limitations)
    decoder = _run_decoder(package_root / "forecast")
    return {
        "hashes": "matched",
        "files": len(manifest),
        "decoder": decoder,
        "ci_status": document["ci"]["status"],
        "ci_passed": document["ci"]["passed"],
        "forecast_skill": document["forecast_skill"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forecast-root", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--smoke-report", type=Path)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args(argv)
    try:
        if args.verify is not None:
            if args.forecast_root is not None or args.out is not None:
                raise ConfigError("--verify does not take a forecast or output path")
            print(canonical_json(verify_package(args.verify)), flush=True)
            return 0
        if args.forecast_root is None or args.out is None:
            raise ConfigError("--forecast-root and --out are required")
        published = build_package(
            args.forecast_root,
            args.out,
            smoke_report=args.smoke_report,
            evidence_root=args.evidence_root,
            workers=args.workers,
        )
        print(str(published), flush=True)
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return exit_code_for(exc)


def _staging(out: Path) -> Path:
    return out.with_name(f".{out.name}.staging")


def _require_synthetic_forecast(source: Path) -> dict:
    verification = source / "verification.json"
    if not verification.is_file():
        raise ConfigError("forecast export has no verification.json")
    document = json.loads(verification.read_text(encoding="utf-8"))
    if document.get("synthetic") is not True:
        raise ConfigError("refusing to package a forecast that is not marked synthetic")
    if "members" not in document or "filter" not in document:
        raise ConfigError("forecast verification is missing members or filter")
    return document


def _write_evidence(staging: Path, smoke_report: Path | None, evidence_root: Path | None) -> None:
    root = repo_root()
    for packaged, relative in _REPO_EVIDENCE:
        source = root / relative
        if not source.is_file():
            raise ConfigError(f"required evidence is missing: {relative}")
        target = staging / "experiments" / packaged
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    if evidence_root is not None:
        base = Path(evidence_root)
        if not base.is_dir():
            raise ConfigError("evidence root is not a directory")
        for path in sorted(item for item in base.rglob("*") if item.is_file()):
            relative = path.relative_to(base).as_posix()
            resolve_input_path(base, relative)
            target = staging / "experiments" / "extra" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    if smoke_report is None:
        return
    report_path = Path(smoke_report)
    if not report_path.is_file():
        raise ConfigError("smoke report does not exist")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    target = staging / "experiments" / "smoke" / "assimilation.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(report_path, target)
    write_strict_json(
        staging / "experiments" / "smoke" / "probability_sum_units.json",
        _probability_sum_units(report),
    )


def _probability_sum_units(report: dict) -> dict[str, object]:
    config = json.loads(
        (repo_root() / "config/hazard_experiments/synthetic-smoke.json").read_text(
            encoding="utf-8"
        )
    )
    cell_m = float(config["grid"]["cell_m"])
    cell_ha = cell_m**2 / 10000.0
    grouped: dict[int, set[float]] = {}
    for row in report.get("scores") or []:
        if row.get("mode") != "persistence":
            continue
        grouped.setdefault(int(row["issue_min"]), set()).add(float(row["probability_sum"]))
    by_issue = []
    for issue, values in sorted(grouped.items()):
        if len(values) != 1:
            raise ExecutionError("persistence probability_sum changed across leads")
        total = values.pop()
        by_issue.append(
            {
                "issue_min": issue,
                "probability_sum_expected_burned_cells": total,
                "expected_burned_area_ha": total * cell_ha,
            }
        )
    return {
        "field": "probability_sum",
        "unit": "expected burned cells",
        "definition": "sum of per-cell burn probabilities; each cell probability is in [0, 1]",
        "cell_m": cell_m,
        "cell_area_ha": cell_ha,
        "by_issue": by_issue,
        "forecast_skill": False,
    }


def _fresh_restart(members: int, mode: str, workers: int) -> dict[str, object]:
    land, weather, cfg, ign = build_inputs(members, mode)
    parent = RustHazardModel(BackendOptions(workers=workers))
    parent.init(land, weather, cfg, ign)
    parent.step(140, [])
    blob = parent.save_state()
    report = _restart_report(parent)
    uninterrupted = parent.step(145, [report.model_copy(deep=True)])
    restored = RustHazardModel(BackendOptions(workers=workers))
    restored.load_state(blob)
    comparison = restored.step(145, [report.model_copy(deep=True)])
    left = snapshot_prediction(parent, uninterrupted)
    right = snapshot_prediction(restored, comparison)
    arrivals_match = np.array_equal(left.arrivals_abs_min, right.arrivals_abs_min, equal_nan=True)
    quantiles_match = np.array_equal(uninterrupted.t_q, comparison.t_q, equal_nan=True)
    weights_match = np.array_equal(uninterrupted.weights, comparison.weights)
    observations_match = _observation_identity(uninterrupted) == _observation_identity(comparison)
    params_match = uninterrupted.params_summary == comparison.params_summary
    if not all(
        [arrivals_match, quantiles_match, weights_match, observations_match, params_match]
    ):
        raise ExecutionError("fresh-object restart did not reproduce the next forecast")
    accepted = [
        item["obs_id"] for item in uninterrupted.obs_used if item.get("used")
    ]
    if accepted != [report.obs_id]:
        raise ExecutionError("restart observation was not accepted")
    return {
        "execution_mode": "computed_offline",
        "parity": "matched",
        "excluded_timing_fields": ["duration_ms"],
        "members": members,
        "filter": mode,
        "seed": int(cfg.seed),
        "spotting": bool(cfg.spotting),
        "last_parent_issue_min": 140,
        "next_issue_min": 145,
        "parent_checkpoint_sha256": sha256_hex(blob),
        "report_sha256": sha256_canonical(report.model_dump(mode="json")),
        "report_obs_id": report.obs_id,
        "accepted_observation_ids": accepted,
        "control_arrivals_sha256": sha256_hex(np.ascontiguousarray(left.arrivals_abs_min).tobytes()),
        "treatment_arrivals_sha256": sha256_hex(
            np.ascontiguousarray(right.arrivals_abs_min).tobytes()
        ),
        "control_weights_sha256": sha256_hex(np.ascontiguousarray(left.weights).tobytes()),
        "treatment_weights_sha256": sha256_hex(np.ascontiguousarray(right.weights).tobytes()),
        "params_summary": uninterrupted.params_summary,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "environment": environment(),
        "parity_scope": "exact on this platform and Python environment; not a portable bitwise claim",
        "forecast_skill": False,
    }


def _restart_report(model) -> ObservationRecord:
    grid = model.land.grid
    east = float(model._ignition_xy[0, 0] + 8 * grid.cell_m)
    north = float(model._ignition_xy[0, 1])
    lon, lat = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True).transform(east, north)
    return ObservationRecord(
        obs_id="restart-report",
        scenario_id="rust-fixture",
        sensor_id="package-restart",
        kind="ground_report",
        observed_sim_ms=round((grid.t_ref_s + 140 * 60) * 1000),
        available_sim_ms=round((grid.t_ref_s + 145 * 60) * 1000),
        geometry={"type": "point_radius", "lon": float(lon), "lat": float(lat), "radius_m": 150},
        hot=True,
        trust=0.25,
        report_kind="fire",
        provenance="simulated",
        source_ref="synthetic fresh-restart check",
    )


def _observation_identity(forecast) -> list[dict[str, object]]:
    return [
        {
            "obs_id": item.get("obs_id"),
            "used": bool(item.get("used")),
            "reason": item.get("reason"),
        }
        for item in forecast.obs_used
    ]


def _write_restart(staging: Path, restart: dict[str, object]) -> None:
    # The checkpoint bytes are not retained. The recorded hash and the next-forecast
    # comparison are the acceptance evidence; loading alone is not.
    write_strict_json(staging / "checkpoints" / "restart.json", restart)


def _write_report_branch(staging: Path) -> None:
    write_strict_json(
        staging / "branches" / "report-demo" / "metadata.json",
        {
            "status": "not_executed",
            "execution_mode": None,
            "allowed_execution_modes": ["cached_replay", "computed_offline"],
            "runtime_branch_activation": False,
            "branch_id": None,
            "bundle_id": None,
            "parent_checkpoint_sha256": None,
            "measured_effect": None,
            "note": "The seeds 7, 8, and 9 report matrix was not run. No canonical branch or bundle id is assigned.",
        },
    )


def _package_document(forecast: dict, ci: dict[str, object], restart: dict[str, object]) -> dict:
    if ci.get("passed") and ci.get("status") != "success":
        raise ConfigError("CI cannot be marked passed unless its status is success")
    if ci.get("status") in {"pending", "unavailable", "failure"} and ci.get("passed"):
        raise ConfigError("CI cannot be marked passed while pending, unavailable, or failed")
    return {
        "schema_version": PACKAGE_SCHEMA,
        "package_kind": "engine_artifact",
        "status": "complete",
        "dataset_kind": "synthetic",
        "execution_mode": "cached_replay",
        "evidence_class": "pipeline_verification",
        "forecast_skill": False,
        "labels": list(LABELS),
        "forecast_members": forecast.get("members"),
        "forecast_cycles": forecast.get("forecast_cycles"),
        "forecast_filter": forecast.get("filter"),
        "scenario_id": forecast.get("scenario_id"),
        "not_an_eaton_historical_forecast": True,
        "runtime_branch_activation": False,
        "restart_execution_mode": restart["execution_mode"],
        "ci": ci,
        "limitations": dict(LIMITATIONS),
    }


def _write_readme(path: Path) -> None:
    lines = [
        "# Synthetic engine package",
        "",
        "This package is a synthetic cached replay and pipeline verification, not forecast skill.",
        "It is not an Eaton historical forecast. Terrain, weather, and observations in the forecast export are synthetic.",
        "The copied forecast is a cached replay. The fresh-object restart check is computed offline.",
        "No runtime branch is activated, and no canonical branch or bundle id is assigned.",
        "",
        "Limitations:",
        "- The frozen 32-member matched experiment was not executed.",
        "- The seeds 7, 8, and 9 report-effect matrix was not executed.",
        "- Historical validation is unavailable because independent historical truth is not in this package.",
        "",
        "Rebuild and verify from the repository root:",
        *REBUILD_COMMANDS,
        "",
        "The checksum file artifacts.sha256.json is written last and excludes itself.",
        "Verify a copy of the package. Do not treat a pending CI status as a pass.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_hash_manifest(staging: Path) -> None:
    manifest = {
        relative: sha256_file(resolve_input_path(staging, relative))
        for relative in _payload_files(staging)
    }
    if HASH_MANIFEST in manifest:
        raise ExecutionError("hash manifest must exclude itself")
    write_strict_json(staging / HASH_MANIFEST, manifest)


def _payload_files(root: Path) -> list[str]:
    files = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            if path.is_symlink():
                raise ConfigError("package file must not be a symlink")
            continue
        relative = path.relative_to(root).as_posix()
        if relative == HASH_MANIFEST:
            continue
        files.append(relative)
    return sorted(files)


def _require_labels(document: object, limitations: object) -> None:
    if not isinstance(document, dict) or not isinstance(limitations, dict):
        raise ExecutionError("package metadata is incomplete")
    if document.get("status") != "complete":
        raise ExecutionError("package status is not complete")
    if document.get("dataset_kind") != "synthetic" or document.get("forecast_skill") is not False:
        raise ExecutionError("package must stay labeled synthetic and must not claim forecast skill")
    if document.get("execution_mode") != "cached_replay":
        raise ExecutionError("forecast package execution mode must be cached replay")
    if list(document.get("labels") or []) != list(LABELS):
        raise ExecutionError("package labels changed")
    if limitations != LIMITATIONS:
        raise ExecutionError("limitations record changed")
    ci = document.get("ci")
    if not isinstance(ci, dict) or ci.get("commit") != PUSHED_COMMIT:
        raise ExecutionError("CI record must name the pushed commit")
    if ci.get("passed") and ci.get("status") != "success":
        raise ExecutionError("CI cannot be marked passed unless its status is success")


def _run_decoder(forecast_dir: Path) -> dict[str, object]:
    script = repo_root() / "tools" / "check_hazard_frontend.mjs"
    if not script.is_file():
        raise ConfigError("frontend decoder is missing")
    completed = subprocess.run(
        ["node", str(script), str(forecast_dir)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout)[-2000:]
        raise ExecutionError(f"forecast decoder failed: {detail}")
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ExecutionError("forecast decoder did not return JSON") from exc
    if parsed.get("frontend_contract") != "passed":
        raise ExecutionError("forecast decoder did not pass")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
