"""Measure the actual configured adapter workload; never assert a latency target.

``--out`` is a JSON file in legacy mode. In ``--config`` mode it is a new run
directory. ``--members``, ``--size``, and ``--workers`` keep their legacy
defaults (128, 340, 2). Passing any of them with ``--config`` is rejected.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import math
import os
import platform
import resource
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pyproj import Transformer

from emberline.contracts import ObservationRecord

from .api import Grid, Ignition, Landscape, ModelConfig, Weather
from .bridge import BackendOptions, verify_backend
from .experiment_io import (
    ConfigError,
    ExecutionError,
    RunSession,
    canonical_json,
    pinned_rust_channel,
    read_strict_json,
    repo_root,
    write_strict_json,
)
from .export import write_json
from .model import RustHazardModel

LEGACY_MEMBERS = 128
LEGACY_SIZE = 340
LEGACY_WORKERS = 2
WORKLOADS = ("no_observation", "satellite", "delayed_ground")
LEGACY_REPORT_KEYS = (
    "synthetic",
    "model_version",
    "physics_sha256",
    "platform",
    "machine",
    "logical_cpus",
    "members",
    "grid",
    "cell_m",
    "horizon_min",
    "workers",
    "initialization_s",
    "forecast_duration_ms",
    "target_ms",
    "latency_target_met",
    "python_peak_rss_bytes",
    "child_peak_rss_bytes",
    "native_steps",
    "limits",
)
_STRICT = ConfigDict(extra="forbid", frozen=True)
_NUMBER_FIELDS = (
    "schema_version",
    "members",
    "height",
    "width",
    "cell_m",
    "horizon_min",
    "workers",
    "timeout_s",
    "memory_budget_bytes",
    "warmup_cycles",
    "measured_cycles",
    "interactive_budget_ms",
    "original_target_ms",
)


class Invocation:
    def __init__(
        self,
        mode: str,
        out: Path,
        out_kind: str,
        config: Path | None = None,
        job: Path | None = None,
        members: int = LEGACY_MEMBERS,
        size: int = LEGACY_SIZE,
        workers: int = LEGACY_WORKERS,
    ) -> None:
        self.mode = mode
        self.out = out
        self.out_kind = out_kind
        self.config = config
        self.job = job
        self.members = members
        self.size = size
        self.workers = workers


class BenchmarkConfig(BaseModel):
    """One labeled workload. It does not change production ModelConfig defaults."""

    model_config = _STRICT
    schema_version: int = 1
    run_id: str = Field(min_length=1)
    members: int = Field(ge=1)
    height: int = Field(ge=1)
    width: int = Field(ge=1)
    cell_m: float
    horizon_min: int = Field(ge=1)
    workers: int = Field(ge=1)
    timeout_s: float
    memory_budget_bytes: int = Field(ge=1)
    seeds: list[int] = Field(min_length=1)
    warmup_cycles: int = Field(ge=0)
    measured_cycles: int = Field(ge=1)
    workloads: list[str] = Field(min_length=1)
    interactive_budget_ms: int = Field(ge=1)
    original_target_ms: int = Field(ge=1)

    @field_validator(*_NUMBER_FIELDS, mode="before")
    @classmethod
    def _no_bool(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("benchmark numbers must not be booleans")
        return value

    @field_validator("schema_version")
    @classmethod
    def _schema(cls, value: int) -> int:
        if value != 1:
            raise ValueError("benchmark schema_version must be 1")
        return value

    @field_validator("cell_m", "timeout_s")
    @classmethod
    def _positive(cls, value: float) -> float:
        if not np.isfinite(value) or value <= 0:
            raise ValueError("benchmark sizes and timeouts must be finite and positive")
        return float(value)

    @field_validator("workloads")
    @classmethod
    def _workloads(cls, value: list[str]) -> list[str]:
        unknown = [item for item in value if item not in WORKLOADS]
        if unknown:
            raise ValueError(f"unknown benchmark workload: {unknown[0]}")
        if len(value) != len(set(value)):
            raise ValueError("benchmark workloads must be unique")
        return value

    @field_validator("seeds")
    @classmethod
    def _seeds(cls, value: list[int]) -> list[int]:
        if any(isinstance(item, bool) or item < 0 for item in value):
            raise ValueError("benchmark seeds must be nonnegative integers")
        return value


class SystemClock:
    def ns(self) -> int:
        return time.perf_counter_ns()


def parse_invocation(argv: list[str] | None = None) -> Invocation:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--child-job", type=Path)
    parser.add_argument("--members", type=int, default=None, help="legacy member count (default 128)")
    parser.add_argument("--size", type=int, default=None, help="legacy grid edge (default 340)")
    parser.add_argument("--workers", type=int, default=None, help="legacy worker count (default 2)")
    args = parser.parse_args(argv)
    explicit = [
        name for name in ("members", "size", "workers") if getattr(args, name) is not None
    ]
    if args.child_job is not None:
        if args.config is not None or explicit:
            raise ConfigError("a child job does not accept --config or legacy workload flags")
        return Invocation("child", args.out, "json_file", job=args.child_job)
    if args.config is not None:
        if explicit:
            names = ", ".join(f"--{name}" for name in explicit)
            raise ConfigError(
                f"config mode rejects {names}; those flags would conflict with the config"
            )
        return Invocation("config", args.out, "directory", args.config)
    return Invocation(
        "legacy",
        args.out,
        "json_file",
        members=LEGACY_MEMBERS if args.members is None else args.members,
        size=LEGACY_SIZE if args.size is None else args.size,
        workers=LEGACY_WORKERS if args.workers is None else args.workers,
    )


def load_benchmark_config(path: Path) -> BenchmarkConfig:
    try:
        return BenchmarkConfig.model_validate(read_strict_json(path))
    except ValidationError as exc:
        raise ConfigError("malformed benchmark config") from exc
    except (OSError, ValueError) as exc:
        raise ConfigError(f"benchmark config could not be read: {exc}") from exc


def physical_extent(config: BenchmarkConfig) -> dict[str, object]:
    same_domain = (
        config.members == 128
        and config.height == 340
        and config.width == 340
        and config.cell_m == 90
        and config.horizon_min == 360
    )
    return {
        "east_m": config.width * config.cell_m,
        "north_m": config.height * config.cell_m,
        "cell_m": config.cell_m,
        "height": config.height,
        "width": config.width,
        "members": config.members,
        "horizon_min": config.horizon_min,
        "domain_label": "target" if same_domain else "reduced_resolution_or_members",
        "same_physical_domain_as_target": same_domain,
    }


def issue_schedule(config: BenchmarkConfig, cycle_min: int = 5) -> list[tuple[str, int]]:
    """First forecast is cold. Extra warmup cycles stay out of the warm set."""
    if cycle_min < 1:
        raise ConfigError("cycle_min must be positive")
    schedule: list[tuple[str, int]] = []
    minute = cycle_min
    if config.warmup_cycles == 0:
        schedule.append(("cold", minute))
        minute += cycle_min
        for _ in range(config.measured_cycles - 1):
            schedule.append(("warm", minute))
            minute += cycle_min
        return schedule
    schedule.append(("cold", minute))
    minute += cycle_min
    for _ in range(config.warmup_cycles - 1):
        schedule.append(("warmup", minute))
        minute += cycle_min
    for _ in range(config.measured_cycles):
        schedule.append(("warm", minute))
        minute += cycle_min
    return schedule


def synthetic_inputs(config: BenchmarkConfig, seed: int) -> tuple[Landscape, Weather, ModelConfig, Ignition]:
    grid = Grid(
        "EPSG:32611",
        380000,
        3803000,
        config.cell_m,
        config.height,
        config.width,
        1736294400,
    )
    shape = (grid.H, grid.W)
    zero = np.zeros(shape, np.float32)
    land = Landscape(grid, np.full(shape, 102, np.int16), zero, zero.copy(), zero.copy())
    weather = Weather(
        np.array([0, 600], np.float32),
        np.full((2, *shape), 6, np.float32),
        np.zeros((2, *shape), np.float32),
        np.full(2, 22, np.float32),
        np.full(2, 0.25, np.float32),
    )
    model_config = ModelConfig(
        n_members=config.members,
        horizon_min=config.horizon_min,
        spotting=False,
        filter="pf",
        seed=seed,
    )
    ignition = Ignition(
        0,
        np.array([[grid.x0 + grid.W * grid.cell_m * 0.4, grid.y0 - grid.H * grid.cell_m * 0.5]]),
        300,
    )
    return land, weather, model_config, ignition


def synthetic_observations(
    workload: str, grid: Grid, issue_min: float, seed: int
) -> list[ObservationRecord]:
    if workload not in WORKLOADS:
        raise ConfigError(f"unknown benchmark workload: {workload}")
    if workload == "no_observation":
        return []
    issue_ms = round((grid.t_ref_s + issue_min * 60) * 1000)
    observed_min = issue_min if workload == "satellite" else issue_min - 5
    if workload == "delayed_ground" and observed_min < 0:
        raise ConfigError("delayed ground report has no earlier observation time")
    observed_ms = round((grid.t_ref_s + observed_min * 60) * 1000)
    if workload == "delayed_ground" and not observed_ms < issue_ms:
        raise ConfigError("delayed ground report has no earlier observation time")
    column = min(grid.W - 1, max(0, grid.W // 2))
    row = min(grid.H - 1, max(0, grid.H // 2))
    east = grid.x0 + (column + 0.5) * grid.cell_m
    north = grid.y0 - (row + 0.5) * grid.cell_m
    inverse = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
    lon, lat = inverse.transform(east, north)
    identifier = f"bench-{workload}-{seed}-{int(issue_min)}"
    if workload == "satellite":
        ring = [
            (float(lon), float(lat)),
            (float(lon) + 0.01, float(lat)),
            (float(lon) + 0.01, float(lat) + 0.01),
            (float(lon), float(lat) + 0.01),
            (float(lon), float(lat)),
        ]
        return [
            ObservationRecord(
                obs_id=identifier,
                scenario_id="benchmark-synthetic",
                sensor_id="bench-goes",
                kind="goes_pixel",
                observed_sim_ms=observed_ms,
                available_sim_ms=issue_ms,
                geometry={"type": "footprint", "ring": ring},
                hot=True,
                trust=1.0,
                confidence=0.9,
                fdc_class=10,
                footprint_key=identifier,
                provenance="simulated",
                source_ref="deterministic synthetic GOES record",
            )
        ]
    return [
        ObservationRecord(
            obs_id=identifier,
            scenario_id="benchmark-synthetic",
            sensor_id="bench-report",
            kind="ground_report",
            observed_sim_ms=observed_ms,
            available_sim_ms=issue_ms,
            geometry={
                "type": "point_radius",
                "lon": float(lon),
                "lat": float(lat),
                "radius_m": 150,
            },
            hot=True,
            trust=1.0,
            report_kind="fire",
            provenance="simulated",
            source_ref="deterministic synthetic delayed ground report",
        )
    ]


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return float(ordered[mid - 1] + ordered[mid]) / 2


def nearest_rank_p95(values: list[float]) -> tuple[float | None, str | None]:
    if not values:
        return None, "no samples"
    ordered = sorted(values)
    index = math.ceil(0.95 * len(ordered)) - 1
    label = "descriptive/low-sample" if len(ordered) < 20 else "nearest-rank"
    return float(ordered[index]), label


def rss_bytes(who: int) -> tuple[int | None, str | None]:
    system = platform.system()
    if system == "Darwin":
        return int(resource.getrusage(who).ru_maxrss), None
    if system == "Linux":
        return int(resource.getrusage(who).ru_maxrss) * 1024, None
    return None, f"peak RSS is not converted on {system or 'this platform'}"


def _sample(
    *,
    seed: int,
    workload: str,
    category: str,
    elapsed_ns: int | None,
    run_id: str,
    issue_min: int | None = None,
    native_steps: int | None = None,
    accepted_observations: int | None = None,
    python_peak_rss_bytes: int | None = None,
    child_peak_rss_bytes: int | None = None,
    memory_reason: str | None = None,
    internal_duration_ms: int | None = None,
    timeout: bool = False,
    error: str | None = None,
) -> dict[str, object]:
    return {
        "seed": seed,
        "issue_min": issue_min,
        "workload": workload,
        "category": category,
        "native_steps": native_steps,
        "accepted_observations": accepted_observations,
        "python_peak_rss_bytes": python_peak_rss_bytes,
        "child_peak_rss_bytes": child_peak_rss_bytes,
        "memory_reason": memory_reason,
        "elapsed_ns": elapsed_ns,
        "internal_duration_ms": internal_duration_ms,
        "timeout": timeout,
        "error": error,
        "run_id": run_id,
    }


def memory_summary(samples: list[dict]) -> dict[str, object]:
    python_peaks = [
        sample["python_peak_rss_bytes"]
        for sample in samples
        if isinstance(sample.get("python_peak_rss_bytes"), int)
    ]
    child_peaks = [
        sample["child_peak_rss_bytes"]
        for sample in samples
        if isinstance(sample.get("child_peak_rss_bytes"), int)
    ]
    reasons = [sample["memory_reason"] for sample in samples if sample.get("memory_reason")]
    return {
        "python_peak_rss_bytes": max(python_peaks) if python_peaks else None,
        "native_child_peak_rss_bytes": max(child_peaks) if child_peaks else None,
        "reason": None if python_peaks and child_peaks else (reasons[0] if reasons else None),
        "peaks_are_independent": True,
        "rss_unit_note": (
            "Darwin ru_maxrss is bytes. Linux ru_maxrss is converted from KiB to bytes. "
            "Other platforms are null. These peaks are not added together."
        ),
    }


def summarize(samples: list[dict]) -> dict[str, object]:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for sample in samples:
        if sample.get("category") not in {"cold", "warm"}:
            continue
        grouped.setdefault((str(sample["workload"]), str(sample["category"])), []).append(sample)
    forecasts = {}
    for workload, category in sorted(grouped):
        rows = grouped[(workload, category)]
        finished = [row for row in rows if not row.get("timeout") and not row.get("error")]
        elapsed = [row["elapsed_ns"] / 1e6 for row in finished if isinstance(row.get("elapsed_ns"), int)]
        internal = [
            row["internal_duration_ms"]
            for row in finished
            if isinstance(row.get("internal_duration_ms"), int)
        ]
        p95, p95_label = nearest_rank_p95(elapsed)
        forecasts[f"{workload}:{category}"] = {
            "count": len(rows),
            "finished_count": len(finished),
            "timeout_count": sum(bool(row.get("timeout")) for row in rows),
            "error_count": sum(bool(row.get("error")) for row in rows),
            "median_elapsed_ms": median(elapsed),
            "p95_elapsed_ms": p95,
            "p95_label": p95_label,
            "max_elapsed_ms": max(elapsed) if elapsed else None,
            "median_internal_duration_ms": median(internal),
            "max_internal_duration_ms": max(internal) if internal else None,
        }
    timeouts = [sample for sample in samples if sample.get("timeout")]
    errors = [sample for sample in samples if sample.get("error")]
    return {
        "forecasts": forecasts,
        "timeout_count": len(timeouts),
        "error_count": len(errors),
        "eligible_for_profile_selection": not timeouts and not errors,
        "eligibility_meaning": (
            "True only when every stored sample finished without a timeout or child error. "
            "It does not mean latency, memory, checkpoint parity, or export checks passed. "
            "E2 must evaluate those requirements separately."
        ),
        "warmup_excluded": True,
        "warmup_policy": (
            "The first forecast is cold. Further warmup cycles are stored and excluded "
            "from cold and warm aggregates. measured_cycles are warm."
        ),
    }


def measure_repetition(
    job: dict,
    model,
    clock,
    observations_for: Callable[..., list[ObservationRecord]] = synthetic_observations,
    cycle_min: int = 5,
) -> list[dict]:
    """Time one fresh model. Warmup samples stay in the raw list and out of the aggregates."""
    config = BenchmarkConfig.model_validate(job["config"])
    timeout_ns = int(config.timeout_s * 1e9)
    samples: list[dict] = []

    def record(category: str, issue: int | None, elapsed_ns: int, **extra) -> None:
        python_rss, python_reason = rss_bytes(resource.RUSAGE_SELF)
        child_rss, child_reason = rss_bytes(resource.RUSAGE_CHILDREN)
        timed_out = bool(extra.get("timeout")) or elapsed_ns > timeout_ns
        samples.append(
            _sample(
                seed=job["seed"],
                workload=job["workload"],
                category=category,
                elapsed_ns=int(elapsed_ns),
                run_id=config.run_id,
                issue_min=issue,
                native_steps=extra.get("native_steps"),
                accepted_observations=extra.get("accepted_observations"),
                python_peak_rss_bytes=python_rss,
                child_peak_rss_bytes=child_rss,
                memory_reason=python_reason or child_reason,
                internal_duration_ms=extra.get("internal_duration_ms"),
                timeout=timed_out,
                error=extra.get("error"),
            )
        )

    started = clock.ns()
    model.init(job["land"], job["weather"], job["model_config"], job["ignition"])
    record("init", None, clock.ns() - started)
    for category, issue in issue_schedule(config, cycle_min):
        observations = observations_for(job["workload"], job["land"].grid, issue, job["seed"])
        started = clock.ns()
        try:
            forecast = model.step(issue, observations)
        except Exception as exc:
            elapsed = clock.ns() - started
            message = str(exc)
            timed_out = "exceeded" in message and "seconds" in message
            record(
                category,
                issue,
                elapsed,
                timeout=timed_out,
                error=None if timed_out else message,
            )
            raise ExecutionError(message) from exc
        elapsed = clock.ns() - started
        accepted = 0
        obs_used = getattr(forecast, "obs_used", None) or []
        accepted = sum(bool(item.get("used")) for item in obs_used)
        native = getattr(model, "native_diagnostics", {}) or {}
        steps = native.get("steps")
        record(
            category,
            issue,
            elapsed,
            native_steps=int(steps) if isinstance(steps, (int, np.integer)) and not isinstance(steps, bool) else None,
            accepted_observations=accepted,
            internal_duration_ms=getattr(forecast, "duration_ms", None),
        )
        if samples[-1]["timeout"]:
            return samples
    if hasattr(model, "save_state") and hasattr(model, "load_state"):
        started = clock.ns()
        blob = model.save_state()
        record("checkpoint_save", None, clock.ns() - started)
        started = clock.ns()
        model.load_state(blob)
        record("checkpoint_load", None, clock.ns() - started)
    return samples


def repetition_samples(
    seed: int,
    workload: str,
    wall_ns: int,
    returncode: int | None,
    payload: object,
    timed_out: bool,
    run_id: str,
) -> list[dict]:
    error = None
    if timed_out:
        error = "child timed out"
    elif returncode not in (0, None):
        error = f"child exited {returncode}"
    elif payload is None:
        error = "child produced no result"
    process = _sample(
        seed=seed,
        workload=workload,
        category="process",
        elapsed_ns=wall_ns,
        run_id=run_id,
        timeout=timed_out,
        error=error,
    )
    child: list[dict] = []
    if isinstance(payload, dict) and isinstance(payload.get("samples"), list):
        child = [sample for sample in payload["samples"] if isinstance(sample, dict)]
    return [process, *child]


def latency_budgets(config: BenchmarkConfig, summary: dict) -> dict[str, object]:
    """Compare stored medians with the labeled budgets. This does not select a profile."""
    forecasts = summary.get("forecasts")
    if not isinstance(forecasts, dict):
        forecasts = {}
    series = []
    interactive_failures: list[str] = []
    target_failures: list[str] = []
    for name in sorted(forecasts):
        stats = forecasts[name]
        if not isinstance(stats, dict):
            continue
        median_ms = stats.get("median_elapsed_ms")
        has_median = isinstance(median_ms, (int, float)) and not isinstance(median_ms, bool)
        within_interactive = has_median and median_ms <= config.interactive_budget_ms
        within_target = has_median and median_ms <= config.original_target_ms
        series.append(
            {
                "series": name,
                "median_elapsed_ms": median_ms if has_median else None,
                "interactive_budget_ms": config.interactive_budget_ms,
                "original_target_ms": config.original_target_ms,
                "within_interactive_budget": within_interactive if has_median else None,
                "within_original_target": within_target if has_median else None,
            }
        )
        if not within_interactive:
            interactive_failures.append(name)
        if not within_target:
            target_failures.append(name)
    return {
        "evaluated_by": "E1 records the comparison only. E2 decides profile selection.",
        "series": series,
        "qualifies_for_interactive_budget": bool(series) and not interactive_failures,
        "interactive_budget_failures": interactive_failures,
        "qualifies_for_original_target": bool(series) and not target_failures,
        "original_target_failures": target_failures,
        "not_evaluated": ["memory budget", "checkpoint parity", "export checks"],
    }


def build_report(config: BenchmarkConfig, samples: list[dict], identity: dict | None) -> dict[str, object]:
    summary = summarize(samples)
    return {
        "schema_version": 1,
        "experiment_kind": "benchmark",
        "run_id": config.run_id,
        "status": "complete",
        "resolved_config": config.model_dump(mode="json"),
        "physical_extent": physical_extent(config),
        "interactive_budget_ms": config.interactive_budget_ms,
        "original_target_ms": config.original_target_ms,
        "budget_note": (
            "interactive_budget_ms is a development-demo requirement. "
            "original_target_ms is unchanged and this report is not end-to-end UI latency."
        ),
        "backend": identity,
        "samples": samples,
        "summary": summary,
        "latency_budgets": latency_budgets(config, summary),
        "memory": memory_summary(samples),
        "limitations": [
            "Synthetic observations and terrain, not a historical incident",
            "Outer step timing includes validation and is not ForecastArrays.duration_ms",
            "Python and native-child RSS peaks are separate, not a summed process tree",
            "Fewer than 20 finished samples makes p95 descriptive/low-sample",
            "eligible_for_profile_selection means the run finished without a timeout or child error",
            "Latency, memory, checkpoint parity, and export checks are separate and are not implied by that flag",
        ],
    }


def execute_config_run(
    config: BenchmarkConfig,
    out: Path,
    *,
    runner: Callable[[Path, Path, dict], tuple[int, int | None, bool, object]],
    identity: dict | None = None,
    binary: Path | None = None,
) -> dict[str, object]:
    session = RunSession(out.parent, out.name)
    session.open()
    try:
        samples: list[dict] = []
        for seed in config.seeds:
            for workload in config.workloads:
                job = {
                    "seed": seed,
                    "workload": workload,
                    "config": config.model_dump(mode="json"),
                    "binary": None if binary is None else str(binary),
                }
                job_path = session.staging / f"job-{seed}-{workload}.json"
                result_path = session.staging / f"result-{seed}-{workload}.json"
                write_strict_json(job_path, job)
                wall_ns, returncode, timed_out, payload = runner(job_path, result_path, job)
                samples.extend(
                    repetition_samples(
                        seed, workload, wall_ns, returncode, payload, timed_out, config.run_id
                    )
                )
        report = build_report(config, samples, identity)
        write_strict_json(session.staging / "benchmark.json", report)
        session.commit()
    except Exception as exc:
        session.fail(exc)
        if isinstance(exc, (ConfigError, ExecutionError)):
            raise
        raise ExecutionError(str(exc)) from exc
    return report


def child_command(job: Path, result: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "emberline.hazard.benchmark",
        "--child-job",
        str(job),
        "--out",
        str(result),
    ]


def child_timeout_s(config: BenchmarkConfig) -> float:
    forecasts = config.warmup_cycles + config.measured_cycles
    return config.timeout_s * (forecasts + 2) + 120


def subprocess_runner(
    job_path: Path,
    result_path: Path,
    *,
    env: dict[str, str],
    timeout_s: float,
) -> tuple[int, int | None, bool, object]:
    started = time.perf_counter_ns()
    try:
        completed = subprocess.run(
            child_command(job_path, result_path),
            check=False,
            env=env,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return time.perf_counter_ns() - started, None, True, None
    payload = None
    if result_path.is_file():
        try:
            payload = read_strict_json(result_path)
        except (OSError, ValueError):
            payload = None
    return time.perf_counter_ns() - started, completed.returncode, False, payload


def _require_installed_toolchain(channel: str) -> None:
    try:
        listed = subprocess.run(
            ["rustup", "toolchain", "list"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ExecutionError("rustup is required to build the pinned release bridge") from exc
    installed = False
    for line in listed.stdout.splitlines():
        name = line.split()[0] if line.split() else ""
        if name == channel or name.startswith(channel + "-"):
            installed = True
            break
    if not installed:
        raise ExecutionError(f"Rust toolchain {channel} is not installed")


def bridge_build_command(workspace: Path, channel: str) -> list[str]:
    """Build into the workspace target directory, not an ambient CARGO_TARGET_DIR."""
    return [
        "rustup",
        "run",
        channel,
        "cargo",
        "build",
        "--manifest-path",
        str(workspace / "Cargo.toml"),
        "--target-dir",
        str(workspace / "target"),
        "-p",
        "fire-bridge",
        "--release",
        "--locked",
    ]


def build_release_bridge(repo: Path | None = None) -> Path:
    """Build the pinned release bridge before any measurement clock starts.

    Unit tests must not call this. It does not download a missing toolchain.
    """
    root = Path(repo or repo_root())
    channel = pinned_rust_channel(root)
    _require_installed_toolchain(channel)
    workspace = root / "physics/surface_v1"
    binary_name = "fire-bridge.exe" if os.name == "nt" else "fire-bridge"
    binary = workspace / "target" / "release" / binary_name
    command = bridge_build_command(workspace, channel)
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as exc:
        raise ExecutionError(f"could not start the pinned cargo build: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-2000:]
        raise ExecutionError(f"pinned release bridge build failed: {detail}")
    if not binary.is_file():
        raise ExecutionError(f"pinned release bridge missing after build: {binary}")
    return binary


def configured_benchmark(config_path: Path, out: Path) -> dict[str, object]:
    config = load_benchmark_config(config_path)
    binary = build_release_bridge()
    try:
        identity = verify_backend(
            BackendOptions(
                binary=binary,
                workers=config.workers,
                memory_budget_bytes=config.memory_budget_bytes,
                timeout_s=15,
            )
        )
    except (RuntimeError, ValueError, OSError) as exc:
        raise ExecutionError(str(exc)) from exc
    env = os.environ.copy()
    env["EMBERLINE_RUST_BRIDGE"] = str(binary)

    def runner(job_path: Path, result_path: Path, job: dict):
        return subprocess_runner(
            job_path, result_path, env=env, timeout_s=child_timeout_s(config)
        )

    return execute_config_run(config, out, runner=runner, identity=identity, binary=binary)


def run_child(job_path: Path, out_path: Path) -> int:
    samples: list[dict] = []
    try:
        started = time.perf_counter_ns()
        job = read_strict_json(job_path)
        config = BenchmarkConfig.model_validate(job["config"])
        seed = job["seed"]
        workload = job["workload"]
        if not isinstance(seed, int) or isinstance(seed, bool) or workload not in WORKLOADS:
            raise ConfigError("child job seed or workload is invalid")
        land, weather, model_config, ignition = synthetic_inputs(config, seed)
        samples.append(
            _sample(
                seed=seed,
                workload=workload,
                category="launch",
                elapsed_ns=time.perf_counter_ns() - started,
                run_id=config.run_id,
            )
        )
        binary = job.get("binary")
        model = RustHazardModel(
            BackendOptions(
                binary=None if not binary else Path(binary),
                workers=config.workers,
                memory_budget_bytes=config.memory_budget_bytes,
                timeout_s=config.timeout_s,
            )
        )
        samples.extend(
            measure_repetition(
                {
                    "seed": seed,
                    "workload": workload,
                    "config": config.model_dump(mode="json"),
                    "land": land,
                    "weather": weather,
                    "model_config": model_config,
                    "ignition": ignition,
                },
                model,
                SystemClock(),
                cycle_min=model_config.cycle_min,
            )
        )
        serial_started = time.perf_counter_ns()
        canonical_json({"samples": samples})
        samples.append(
            _sample(
                seed=seed,
                workload=workload,
                category="serialize",
                elapsed_ns=time.perf_counter_ns() - serial_started,
                run_id=config.run_id,
            )
        )
        write_strict_json(out_path, {"samples": samples})
        return 0
    except Exception as exc:
        try:
            if samples:
                write_strict_json(out_path, {"samples": samples, "error": str(exc)})
        except Exception:
            pass
        print(str(exc), file=sys.stderr)
        return 3


def legacy_benchmark(out: Path, members: int, size: int, workers: int) -> None:
    if out.exists():
        raise FileExistsError(out)
    grid = Grid("EPSG:32611", 380000, 3803000, 90, size, size, 1736294400)
    shape = (grid.H, grid.W)
    zero = np.zeros(shape, np.float32)
    land = Landscape(grid, np.full(shape, 102, np.int16), zero, zero.copy(), zero.copy())
    weather = Weather(
        np.array([0, 600], np.float32),
        np.full((2, *shape), 6, np.float32),
        np.zeros((2, *shape), np.float32),
        np.full(2, 22, np.float32),
        np.full(2, 0.25, np.float32),
    )
    model_config = ModelConfig(n_members=members, spotting=False, filter="pf")
    ignition = Ignition(
        0, np.array([[grid.x0 + grid.W * 90 * 0.4, grid.y0 - grid.H * 90 * 0.5]]), 300
    )
    model = RustHazardModel(
        BackendOptions(workers=workers, memory_budget_bytes=2 * 1024**3, timeout_s=600)
    )
    started = time.perf_counter()
    model.init(land, weather, model_config, ignition)
    initialization = time.perf_counter() - started
    result = model.step(5, [])
    unit = 1 if platform.system() == "Darwin" else 1024
    report = {
        "synthetic": True,
        "model_version": model.model_version,
        "physics_sha256": model.backend_identity["physics_sha256"],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "logical_cpus": os.cpu_count(),
        "members": members,
        "grid": [grid.H, grid.W],
        "cell_m": 90,
        "horizon_min": 360,
        "workers": workers,
        "initialization_s": initialization,
        "forecast_duration_ms": result.duration_ms,
        "target_ms": 500,
        "latency_target_met": bool(result.duration_ms <= 500),
        "python_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit,
        "child_peak_rss_bytes": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * unit,
        "native_steps": model.native_diagnostics["steps"],
        "limits": [
            "Single cold forecast, not a p95 latency benchmark",
            "No observations or reanchoring; assimilation can require another solve",
            "Entire-domain replay, not incremental member advancement",
        ],
    }
    if tuple(report) != LEGACY_REPORT_KEYS:
        raise ExecutionError("legacy benchmark report fields changed")
    write_json(out, report)
    print(report, flush=True)


def main(argv: list[str] | None = None) -> int:
    try:
        invocation = parse_invocation(argv)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if invocation.mode == "legacy":
        legacy_benchmark(invocation.out, invocation.members, invocation.size, invocation.workers)
        return 0
    if invocation.mode == "child":
        return run_child(invocation.job, invocation.out)
    try:
        report = configured_benchmark(invocation.config, invocation.out)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ExecutionError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    budgets = report["latency_budgets"]
    print(
        invocation.out,
        "execution_eligible",
        report["summary"]["eligible_for_profile_selection"],
        "interactive_budget",
        budgets["qualifies_for_interactive_budget"],
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
