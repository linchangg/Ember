#!/usr/bin/env python3
"""Reproducible surface_v1 workload timings.

Smoke is the default. Larger profiles must be selected by name. See
docs/BENCHMARK_PROTOCOL.md. This script does not tune the solver.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapter.arrivals import PARTIAL_REJECT, MemberOutcome, unconditional_arrival_quantiles
from adapter.bridge import TIMEOUT_LIMITATION, simulate
from adapter.conversions import (
    SPECIFIED_APPLICATION_QUANTILES,
    AppGrid,
    TimeOrigins,
    WeatherFrame,
    prepare_research_bundle,
    signed_distance_phi,
    write_research_bundle,
)

PROFILES = {
    "smoke": {"height": 64, "width": 64, "members": 1, "horizon_s": 3600.0},
    "single_grid": {"height": 340, "width": 340, "members": 1, "horizon_s": 21600.0},
    "small_ensemble": {"height": 340, "width": 340, "members": 8, "horizon_s": 21600.0},
    "target": {"height": 340, "width": 340, "members": 128, "horizon_s": 21600.0},
}
BLOCKED = {
    "five_minute_updates": (
        "Blocked: Rust corrections require observed, available, and correction times "
        "to be equal, and the application 5-minute assimilating cycle is not landed. "
        "No lookalike workload is run."
    )
}
EATON_X0 = 381480.0
EATON_Y0 = 3800280.0
CELL_M = 90.0
SEED = 7


def main() -> int:
    parser = argparse.ArgumentParser(description="Time surface_v1 research workloads")
    parser.add_argument("--profile", default="smoke", choices=[*PROFILES, *BLOCKED])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--max-wall-s", type=float, default=600.0)
    parser.add_argument("--executable", type=Path, default=ROOT / "target" / "release" / "fire-cli")
    args = parser.parse_args()
    if args.repeats < 1 or args.warmup < 0 or args.max_wall_s <= 0:
        raise SystemExit("repeats, warmup, and max-wall-s must be positive (warmup may be 0)")

    record: dict = {
        "protocol": "surface_v1_benchmark_v1",
        "profile": args.profile,
        "completed": False,
        "blocked": False,
        "member_128_gate_passed": False,
        "timeout_limitation": TIMEOUT_LIMITATION,
    }
    if args.profile in BLOCKED:
        record["blocked"] = True
        record["blocked_reason"] = BLOCKED[args.profile]
        _write(args.out, record)
        print(record["blocked_reason"])
        return 0

    spec = PROFILES[args.profile]
    record.update(_environment())
    record["grid"] = {
        "height": spec["height"],
        "width": spec["width"],
        "cell_size_m": CELL_M,
        "crs": "EPSG:32611",
        "origin": "eaton_upper_left_outer_edge",
        "window_note": "smoke is a 64x64 window of the 90 m contract grid, not the full 340x340 domain"
        if args.profile == "smoke"
        else "full specified 340x340 application grid",
    }
    record["fuel"] = "FM1"
    record["fuel_id"] = 1
    record["weather_frame_count"] = 1
    record["horizon_s"] = spec["horizon_s"]
    record["members_requested"] = spec["members"]
    record["seed"] = None if spec["members"] == 1 else SEED
    record["workers"] = 1 if spec["members"] == 1 else min(os.cpu_count() or 1, spec["members"])
    record["release_build"] = True

    started = time.perf_counter()
    build = subprocess.run(
        ["cargo", "build", "--release", "-p", "fire-cli", "--locked"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    record["build_time_s"] = time.perf_counter() - started
    record["build_command"] = "cargo build --release -p fire-cli --locked"
    record["build_note"] = "incremental when target/ already contains the release binary; not a clean rebuild"
    if build.returncode != 0 or not args.executable.is_file():
        record["incomplete_reason"] = "release build failed"
        record["build_stderr"] = build.stderr[-2000:]
        _write(args.out, record)
        print(build.stderr)
        return 1

    runs = []
    warmup_runs = []
    interrupted = False
    with tempfile.TemporaryDirectory(prefix="surface-v1-bench-") as temporary:
        work = Path(temporary)
        for index in range(args.warmup + args.repeats):
            phase = "warmup" if index < args.warmup else "repeat"
            measurement = _one_run(
                work / f"{phase}-{index}",
                args.executable,
                spec,
                workers=record["workers"],
                timeout_s=args.max_wall_s,
                t_ref_ms=record["t_ref_ms"],
            )
            measurement["phase"] = phase
            if phase == "warmup":
                warmup_runs.append(measurement)
            else:
                runs.append(measurement)
            if measurement["status"] != "complete":
                interrupted = True
                break

    record["warmup"] = warmup_runs
    record["runs"] = runs
    walls = [item["wall_s"] for item in runs if item["status"] == "complete"]
    record["summary"] = _summary(walls)
    record["members_completed"] = runs[-1]["members_completed"] if runs else 0
    record["numerical_failures"] = sum(item["numerical_failures"] for item in runs)
    record["timeouts"] = sum(1 for item in warmup_runs + runs if item["timed_out"])
    record["completed"] = bool(runs) and not interrupted and all(item["status"] == "complete" for item in runs)
    record["member_128_gate_passed"] = bool(
        args.profile == "target" and record["completed"] and record["members_completed"] == 128
    )
    if interrupted:
        record["incomplete_reason"] = "a run timed out, failed, or was interrupted before the repeat set finished"
    _write(args.out, record)
    print(json.dumps({"profile": args.profile, "completed": record["completed"], "summary": record["summary"]}, indent=2))
    return 0 if record["completed"] else 1


def _one_run(directory: Path, executable: Path, spec: dict, *, workers: int, timeout_s: float, t_ref_ms: int) -> dict:
    directory.mkdir(parents=True)
    prep_started = time.perf_counter()
    try:
        manifest = _write_input(directory / "input", spec, t_ref_ms)
    except Exception as exc:  # noqa: BLE001 — recorded as an incomplete measurement, not hidden
        return _failed(str(exc), prep_s=time.perf_counter() - prep_started)
    prep_s = time.perf_counter() - prep_started
    out_dir = directory / "out"
    solver_started = time.perf_counter()
    if spec["members"] == 1:
        try:
            published = simulate(
                executable,
                manifest,
                out_dir,
                timeout_s=timeout_s,
                measure_memory=True,
            )
        except Exception as exc:  # noqa: BLE001
            timed_out = getattr(exc, "code", "") == "timeout"
            numerical = getattr(exc, "code", "") == "numerical_failure"
            return _failed(
                str(exc),
                prep_s=prep_s,
                solver_s=time.perf_counter() - solver_started,
                timed_out=timed_out,
                numerical=1 if numerical else 0,
            )
        solver_s = published.wall_s
        agg_started = time.perf_counter()
        unconditional_arrival_quantiles(
            [MemberOutcome(status=published.arrival_status, time_s=published.arrival_time_s)],
            requested_member_count=1,
            quantile_levels=SPECIFIED_APPLICATION_QUANTILES,
            horizon_s=spec["horizon_s"],
            partial_policy=PARTIAL_REJECT,
            t_start_ms=t_ref_ms,
            t_ref_ms=t_ref_ms,
        )
        aggregation_s = time.perf_counter() - agg_started
        aggregation_note = "research unconditional quantiles for the single member; not an application weighted CDF"
        output_started = time.perf_counter()
        output_bytes = _directory_size(out_dir)
        output_s = time.perf_counter() - output_started
        return {
            "status": published.run.get("status", "unknown"),
            "input_prep_s": prep_s,
            "solver_s": solver_s,
            "aggregation_s": aggregation_s,
            "aggregation_note": aggregation_note,
            "output_s": output_s,
            "wall_s": prep_s + solver_s + aggregation_s + output_s,
            "peak_memory_bytes": published.peak_memory_bytes,
            "memory_method": published.memory_method,
            "output_bytes": output_bytes,
            "members_requested": 1,
            "members_completed": 1 if published.run.get("status") == "complete" else 0,
            "numerical_failures": 0,
            "timed_out": False,
            "quality_flags": published.run.get("quality_flags", []),
        }
    config = directory / "ensemble.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "preset": "benchmark_unweighted_v1",
                "members": spec["members"],
                "master_seed": SEED,
                "workers": workers,
                "memory_budget_bytes": 8 * 1024**3,
                "wind_bias_std_m_s": 0.5,
                "dead_moisture_log_std": 0.1,
                "live_moisture_log_std": 0.1,
                "front_bias_std_m": 0.0,
                "allow_partial": False,
            }
        ),
        encoding="utf-8",
    )
    from adapter.bridge import invoke

    try:
        result = invoke(
            executable,
            ["ensemble", "--input", str(manifest), "--config", str(config), "--out", str(out_dir)],
            timeout_s=timeout_s,
            measure_memory=True,
        )
    except Exception as exc:  # noqa: BLE001
        return _failed(
            str(exc),
            prep_s=prep_s,
            solver_s=time.perf_counter() - solver_started,
            timed_out=getattr(exc, "code", "") == "timeout",
            numerical=1 if getattr(exc, "code", "") == "numerical_failure" else 0,
        )
    solver_s = result.wall_s
    if result.returncode != 0:
        from adapter.bridge import classify_failure

        failure = classify_failure(result)
        return _failed(
            failure.message,
            prep_s=prep_s,
            solver_s=solver_s,
            timed_out=False,
            numerical=1 if failure.code == "numerical_failure" else 0,
            memory_bytes=result.peak_memory_bytes,
            memory_method=result.memory_method,
        )
    metadata = json.loads((out_dir / "ensemble.json").read_text(encoding="utf-8"))
    output_started = time.perf_counter()
    output_bytes = _directory_size(out_dir)
    output_s = time.perf_counter() - output_started
    return {
        "status": "complete" if metadata.get("n_complete") == spec["members"] else "partial",
        "input_prep_s": prep_s,
        "solver_s": solver_s,
        "aggregation_s": None,
        "aggregation_note": "ensemble aggregation runs inside fire-cli and is included in solver_s; conditional quantiles are not application quantiles",
        "output_s": output_s,
        "wall_s": prep_s + solver_s + output_s,
        "peak_memory_bytes": result.peak_memory_bytes,
        "memory_method": result.memory_method,
        "output_bytes": output_bytes,
        "members_requested": spec["members"],
        "members_completed": metadata.get("n_complete"),
        "numerical_failures": 0,
        "timed_out": False,
        "quality_flags": [],
    }


def _write_input(directory: Path, spec: dict, t_ref_ms: int) -> Path:
    grid = AppGrid("EPSG:32611", EATON_X0, EATON_Y0, CELL_M, spec["height"], spec["width"])
    elevation = np.zeros((grid.height, grid.width), dtype=np.float64)
    valid = np.ones((grid.height, grid.width), dtype=np.uint8)
    burnable = np.ones((grid.height, grid.width), dtype=np.uint8)
    fuel = np.ones((grid.height, grid.width), dtype=np.int16)
    center_x = grid.x0_m + grid.width * grid.cell_m / 2.0
    center_y = grid.y0_m - grid.height * grid.cell_m / 2.0
    phi = signed_distance_phi(grid, center_x, center_y, 400.0)
    frame = WeatherFrame(
        time_s=0.0,
        valid_time_ms=t_ref_ms,
        available_time_ms=t_ref_ms,
        wind_east_m_s=1.0,
        wind_north_m_s=0.0,
        m1=0.06,
        m10=0.07,
        m100=0.08,
        mlh=0.60,
        mlw=0.90,
    )
    bundle = prepare_research_bundle(
        grid,
        elevation_m=elevation,
        fuel_id=fuel,
        valid=valid,
        burnable=burnable,
        phi=phi,
        frames=[frame],
        origins=TimeOrigins(t_ref_ms, t_ref_ms, t_ref_ms),
        incident_id=f"benchmark-{spec['height']}x{spec['width']}-m{spec['members']}",
        horizons_s=[spec["horizon_s"]],
        coverage_end_s=spec["horizon_s"],
        components_are_midflame=True,
    )
    return write_research_bundle(directory, bundle)


def _environment() -> dict:
    rustc = subprocess.run(["rustc", "--version"], check=False, capture_output=True, text=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=False, capture_output=True, text=True)
    physical, available, available_method = _memory()
    t_ref_ms = 1_736_294_400_000
    return {
        "commit": commit.stdout.strip() if commit.returncode == 0 else "unknown",
        "model": "surface_v1",
        "solver": "level_set_lf_euler_v1",
        "rustc": rustc.stdout.strip() if rustc.returncode == 0 else "unknown",
        "python": sys.version.split()[0],
        "os": platform.platform(),
        "architecture": platform.machine(),
        "cpu_count": os.cpu_count(),
        "physical_memory_bytes": physical,
        "available_memory_bytes": available,
        "available_memory_method": available_method,
        "t_ref_ms": t_ref_ms,
    }


def _memory() -> tuple[int | None, int | None, str]:
    try:
        if sys.platform == "darwin":
            physical = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip())
            page = int(subprocess.check_output(["sysctl", "-n", "hw.pagesize"], text=True).strip())
            vm = subprocess.check_output(["vm_stat"], text=True)
            free = _vm_pages(vm, "Pages free")
            speculative = _vm_pages(vm, "Pages speculative")
            if free is None:
                return physical, None, "unavailable"
            available = (free + (speculative or 0)) * page
            return physical, available, "macos_vm_stat_pages_free_plus_speculative"
        if sys.platform.startswith("linux"):
            info = Path("/proc/meminfo").read_text(encoding="utf-8")
            physical = _meminfo_bytes(info, "MemTotal")
            available = _meminfo_bytes(info, "MemAvailable")
            return physical, available, "proc_meminfo_MemAvailable" if available is not None else "unavailable"
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None, None, "unavailable"
    return None, None, "unavailable"


def _vm_pages(text: str, label: str) -> int | None:
    for line in text.splitlines():
        if line.startswith(label):
            token = line.split(":", 1)[1].strip().strip(".")
            return int(token)
    return None


def _meminfo_bytes(text: str, key: str) -> int | None:
    for line in text.splitlines():
        if line.startswith(key):
            kib = int(line.split()[1])
            return kib * 1024
    return None


def _directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def _summary(walls: list[float]) -> dict:
    if not walls:
        return {"wall_s_median": None, "wall_s_min": None, "wall_s_max": None, "repeats_completed": 0}
    ordered = sorted(walls)
    mid = ordered[len(ordered) // 2]
    if len(ordered) % 2 == 0:
        mid = (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2.0
    return {
        "wall_s_median": mid,
        "wall_s_min": ordered[0],
        "wall_s_max": ordered[-1],
        "repeats_completed": len(ordered),
    }


def _failed(
    message: str,
    *,
    prep_s: float,
    solver_s: float = 0.0,
    timed_out: bool = False,
    numerical: int = 0,
    memory_bytes: int | None = None,
    memory_method: str = "unavailable",
) -> dict:
    return {
        "status": "incomplete",
        "input_prep_s": prep_s,
        "solver_s": solver_s,
        "aggregation_s": None,
        "aggregation_note": message,
        "output_s": 0.0,
        "wall_s": prep_s + solver_s,
        "peak_memory_bytes": memory_bytes,
        "memory_method": memory_method,
        "output_bytes": 0,
        "members_requested": None,
        "members_completed": 0,
        "numerical_failures": numerical,
        "timed_out": timed_out,
        "quality_flags": [],
    }


def _write(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
