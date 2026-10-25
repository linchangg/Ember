"""Research-only subprocess bridge to the surface_v1 ``fire-cli`` executable.

The parent timeout terminates the child. It is not solver-level resumable
cancellation: Rust does not write a checkpoint because a wall-clock limit
fired in this process. Build the executable once and reuse that path.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from adapter.conversions import RustGrid
from adapter.errors import BridgeError, ValidationError

MODEL_ID = "surface_v1"
SOLVER_ID = "level_set_lf_euler_v1"
RUN_SCHEMA = 2
# How long a terminated child may take to exit before SIGKILL.
TERMINATE_GRACE_S = 5.0
RETENTION_POLICY = "preserve_published_delete_scratch_on_success"

# Parent-process limitation, recorded for callers and the benchmark protocol.
TIMEOUT_LIMITATION = (
    "A parent-process timeout terminates the child and does not establish "
    "solver-level resumable cancellation. No checkpoint is published for that stop."
)

_VALIDATION_CODES = {
    "SchemaMismatch",
    "InvalidArrayLength",
    "UnsupportedFuel",
    "InvalidUnits",
    "InvalidWindReference",
    "MissingCoverage",
    "NonFiniteInput",
    "InvalidFuelParameter",
    "InvalidInitialFront",
    "CheckpointMismatch",
}
_NUMERICAL_CODES = {"TimeStepTooSmall", "NumericalOverflow"}


@dataclass(frozen=True)
class PublishedRun:
    """A verified ``simulate`` directory. Incomplete staging is never returned here."""

    out_dir: Path
    run: dict
    diagnostics: dict
    arrival_time_s: np.ndarray
    arrival_status: np.ndarray
    grid: RustGrid
    stdout: str
    stderr: str
    returncode: int
    wall_s: float
    peak_memory_bytes: int | None
    memory_method: str


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    wall_s: float
    peak_memory_bytes: int | None
    memory_method: str


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def invoke(
    executable: Path,
    args: list[str],
    *,
    timeout_s: float,
    work_dir: Path | None = None,
    measure_memory: bool = False,
) -> CommandResult:
    """Run ``executable`` with ``args``. ``shell`` is never enabled.

    ``args`` are the argument vector after the executable. Paths in ``args``
    should already be absolute because the working directory is a fresh scratch
    directory.
    """
    if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
        raise ValidationError("arguments must be a list of strings")
    exe = _require_executable(executable)
    if not np.isfinite(timeout_s) or timeout_s <= 0.0:
        raise ValidationError("timeout_s must be positive and finite")
    scratch = Path(work_dir) if work_dir is not None else Path(
        tempfile.mkdtemp(prefix="surface-v1-bridge-")
    )
    scratch.mkdir(parents=True, exist_ok=True)
    command = [str(exe), *args]
    memory_file = None
    memory_method = "unavailable"
    if measure_memory:
        memory_file = scratch / "rusage.txt"
        wrapped = _time_wrapper(memory_file, command)
        if wrapped is not None:
            command = wrapped
            memory_method = "usr_bin_time_max_rss"
    started = time.perf_counter()
    process = subprocess.Popen(
        command,
        cwd=scratch,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as expired:
        _terminate_process_group(process)
        try:
            stdout, stderr = process.communicate(timeout=TERMINATE_GRACE_S + 1)
        except subprocess.TimeoutExpired:
            stdout, stderr = expired.stdout, expired.stderr
        stdout = stdout or expired.stdout or ""
        stderr = stderr or expired.stderr or ""
        raise BridgeError(
            "timeout",
            f"child exceeded {timeout_s} s and was terminated. {TIMEOUT_LIMITATION}",
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            work_dir=str(scratch),
        ) from None
    except Exception:
        _terminate_process_group(process)
        raise
    wall = time.perf_counter() - started
    memory_bytes = None
    if memory_file is not None and memory_file.is_file():
        memory_bytes = _parse_time_memory(memory_file)
        if memory_bytes is None:
            memory_method = "unavailable"
    return CommandResult(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=stdout or "",
        stderr=stderr or "",
        wall_s=wall,
        peak_memory_bytes=memory_bytes,
        memory_method=memory_method if memory_bytes is not None else "unavailable",
    )


def classify_failure(result: CommandResult, *, work_dir: str | None = None) -> BridgeError:
    """Map a non-zero Rust exit onto a bridge failure class."""
    payload = _stderr_json(result.stderr)
    code = payload.get("code") if payload else None
    message = payload.get("message") if payload else None
    if not isinstance(message, str) or not message:
        message = (result.stderr or result.stdout or "child process failed").strip()
    if code == "Cancelled" or result.returncode == 130:
        kind = "cancellation"
    elif code in _NUMERICAL_CODES or result.returncode == 3:
        kind = "numerical_failure"
    elif code == "MemoryBudgetExceeded" or result.returncode == 4:
        kind = "memory_budget"
    elif code in _VALIDATION_CODES or result.returncode == 2:
        kind = "validation_failure"
    elif payload is None and result.returncode != 0:
        kind = "malformed_output"
        message = "child failed without a Rust error object: " + message
    else:
        kind = "malformed_output"
    return BridgeError(
        kind,
        message,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        work_dir=work_dir,
    )


def simulate(
    executable: Path,
    manifest: Path,
    out_dir: Path,
    *,
    timeout_s: float,
    measure_memory: bool = False,
) -> PublishedRun:
    """Validate, run ``simulate``, and return only a verified published directory.

    On timeout or a failed verification the caller's ``out_dir`` is left absent.
    Scratch logs are deleted after success and kept after failure.
    """
    manifest = Path(manifest).resolve()
    out_dir = Path(out_dir).resolve()
    _validate_before_simulate(manifest, out_dir)
    staging = _staging_path(out_dir)
    if staging.exists():
        raise ValidationError(f"incomplete staging already exists: {staging}")
    scratch = Path(tempfile.mkdtemp(prefix="surface-v1-bridge-"))
    try:
        result = invoke(
            executable,
            ["simulate", "--input", str(manifest), "--out", str(out_dir)],
            timeout_s=timeout_s,
            work_dir=scratch,
            measure_memory=measure_memory,
        )
    except BridgeError as exc:
        _quarantine_incomplete(out_dir, staging, scratch)
        exc.work_dir = str(scratch)
        raise
    if result.returncode != 0:
        _quarantine_incomplete(out_dir, staging, scratch)
        failure = classify_failure(result, work_dir=str(scratch))
        raise failure
    try:
        published = verify_published_run(
            out_dir,
            bundle_root=manifest.parent,
            manifest_path=manifest,
        )
    except BridgeError as exc:
        _quarantine_incomplete(out_dir, staging, scratch)
        exc.work_dir = str(scratch)
        exc.stdout = result.stdout
        exc.stderr = result.stderr
        raise
    shutil.rmtree(scratch, ignore_errors=True)
    return PublishedRun(
        out_dir=out_dir,
        run=published["run"],
        diagnostics=published["diagnostics"],
        arrival_time_s=published["arrival_time_s"],
        arrival_status=published["arrival_status"],
        grid=published["grid"],
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
        wall_s=result.wall_s,
        peak_memory_bytes=result.peak_memory_bytes,
        memory_method=result.memory_method,
    )


def verify_published_run(out_dir: Path, *, bundle_root: Path, manifest_path: Path) -> dict:
    """Check schema, grid, model identity, completion metadata, and artifact hashes."""
    out_dir = Path(out_dir)
    if not out_dir.is_dir():
        raise BridgeError("malformed_output", f"published directory is missing: {out_dir}")
    if _staging_path(out_dir).exists():
        raise BridgeError(
            "malformed_output",
            "staging still exists beside the published directory",
        )
    try:
        run = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))
        diagnostics = json.loads((out_dir / "diagnostics.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeError("malformed_output", f"unreadable run metadata: {exc}") from exc
    if not isinstance(run, dict) or not isinstance(diagnostics, dict):
        raise BridgeError("malformed_output", "run.json or diagnostics.json is not an object")
    if run.get("schema_version") != RUN_SCHEMA:
        raise BridgeError("malformed_output", f"unexpected run schema {run.get('schema_version')}")
    if run.get("model") != MODEL_ID or run.get("solver") != SOLVER_ID:
        raise BridgeError(
            "malformed_output",
            f"unexpected model identity {run.get('model')}/{run.get('solver')}",
        )
    if run.get("status") not in {"complete", "partial"}:
        raise BridgeError("malformed_output", f"unexpected status {run.get('status')!r}")
    if "completed_until_s" not in run or "quality_flags" not in run or "horizons_s" not in run:
        raise BridgeError("malformed_output", "completion metadata is incomplete")
    grid_json = run.get("configuration", {}).get("grid")
    if not isinstance(grid_json, dict):
        raise BridgeError("malformed_output", "run.json is missing configuration.grid")
    try:
        grid = RustGrid(
            crs=str(grid_json["crs"]),
            x0_m=float(grid_json["x0_m"]),
            y0_m=float(grid_json["y0_m"]),
            cell_size_m=float(grid_json["cell_size_m"]),
            height=int(grid_json["height"]),
            width=int(grid_json["width"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BridgeError("malformed_output", f"run grid is malformed: {exc}") from exc
    count = grid.height * grid.width
    try:
        arrival_time = np.fromfile(out_dir / "arrival_time_s.f64le", dtype="<f8")
        arrival_status = np.fromfile(out_dir / "arrival_status.u8", dtype=np.uint8)
    except OSError as exc:
        raise BridgeError("malformed_output", f"arrival arrays are missing: {exc}") from exc
    if arrival_time.size != count or arrival_status.size != count:
        raise BridgeError(
            "malformed_output",
            f"arrival length {arrival_time.size}/{arrival_status.size} != {count}",
        )
    arrival_time = arrival_time.reshape(grid.height, grid.width)
    arrival_status = arrival_status.reshape(grid.height, grid.width)
    try:
        recorded = (out_dir / "manifest.sha256").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise BridgeError("malformed_output", f"manifest.sha256 is missing: {exc}") from exc
    if recorded != run.get("input_manifest_sha256"):
        raise BridgeError("malformed_output", "manifest.sha256 does not match run.json")
    actual_manifest, manifest_bytes = sha256_file(manifest_path)
    if actual_manifest != recorded:
        raise BridgeError(
            "malformed_output",
            "published manifest hash does not match the input manifest bytes",
        )
    artifacts = run.get("input_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise BridgeError("malformed_output", "input artifact hashes are missing")
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise BridgeError("malformed_output", "artifact entry is not an object")
        relative = artifact.get("path")
        expected = artifact.get("sha256")
        expected_bytes = artifact.get("bytes")
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise BridgeError("malformed_output", "artifact hash entry is incomplete")
        path = (bundle_root / relative).resolve()
        if bundle_root.resolve() not in path.parents and path != bundle_root.resolve():
            raise BridgeError("malformed_output", f"artifact path escapes the bundle: {relative}")
        if not path.is_file():
            raise BridgeError("malformed_output", f"hashed artifact is missing: {relative}")
        digest, size = sha256_file(path)
        if not isinstance(expected_bytes, int) or digest != expected or expected_bytes != size:
            raise BridgeError("malformed_output", f"artifact hash mismatch for {relative}")
    return {
        "run": run,
        "diagnostics": diagnostics,
        "arrival_time_s": arrival_time,
        "arrival_status": arrival_status,
        "grid": grid,
    }


def ensemble_publishes_member_arrivals(ensemble_dir: Path) -> bool:
    """True only when per-member arrival arrays are in the ensemble directory.

    The current CLI publishes conditional quantiles and scenario frequencies.
    It does not publish ``arrival_time_s`` / ``arrival_status`` per member.
    """
    directory = Path(ensemble_dir)
    return (directory / "arrival_time_s.f64le").is_file() or (
        directory / "arrival_status.u8"
    ).is_file()


def _validate_before_simulate(manifest: Path, out_dir: Path) -> None:
    if not manifest.is_file():
        raise ValidationError(f"manifest does not exist: {manifest}")
    if out_dir.exists():
        raise ValidationError(f"output destination already exists: {out_dir}")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"manifest is not JSON: {exc}") from exc
    if payload.get("schema_version") != 1:
        raise ValidationError("manifest schema_version must be 1")
    grid = payload.get("grid", {})
    try:
        width = int(grid["width"])
        height = int(grid["height"])
        cell = float(grid["cell_size_m"])
        crs = str(grid["crs"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError(f"manifest grid is incomplete: {exc}") from exc
    if width < 3 or height < 3 or not np.isfinite(cell) or cell <= 0.0:
        raise ValidationError("manifest grid fails the metric extent checks")
    token = crs.upper()
    if "4326" in token or "WGS84" in token or "LATLON" in token:
        raise ValidationError(f"manifest CRS {crs!r} is geographic")
    horizons = payload.get("simulation", {}).get("horizons_s")
    if not isinstance(horizons, list) or not horizons:
        raise ValidationError("manifest is missing horizons")


def _require_executable(executable: Path) -> Path:
    path = Path(executable).resolve()
    if not path.is_file():
        raise ValidationError(f"executable does not exist: {path}")
    if not os.access(path, os.X_OK):
        raise ValidationError(f"executable is not runnable: {path}")
    return path


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=TERMINATE_GRACE_S)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait(timeout=TERMINATE_GRACE_S)


def _stderr_json(stderr: str) -> dict | None:
    text = stderr.strip()
    if not text:
        return None
    # Rust prints one JSON object. Keep the last object if warnings precede it.
    start = text.rfind("{")
    if start < 0:
        return None
    try:
        payload = json.loads(text[start:])
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and "code" in payload:
        return payload
    return None


def _staging_path(out_dir: Path) -> Path:
    # Rust uses `with_extension("staging")`, replacing an existing suffix.
    return out_dir.with_suffix(".staging") if out_dir.suffix else Path(str(out_dir) + ".staging")


def _quarantine_incomplete(out_dir: Path, staging: Path, scratch: Path) -> None:
    """Move unpublished bytes into the retained scratch directory."""
    scratch.mkdir(parents=True, exist_ok=True)
    if staging.exists():
        destination = scratch / "incomplete_staging"
        if destination.exists():
            shutil.rmtree(destination)
        staging.rename(destination)
    if out_dir.exists():
        destination = scratch / "unverified_output"
        if destination.exists():
            shutil.rmtree(destination)
        out_dir.rename(destination)


def _time_wrapper(memory_file: Path, command: list[str]) -> list[str] | None:
    timer = Path("/usr/bin/time")
    if not timer.is_file():
        return None
    if os.uname().sysname == "Darwin":
        return [str(timer), "-l", "-o", str(memory_file), *command]
    return [str(timer), "-v", "-o", str(memory_file), *command]


def _parse_time_memory(path: Path) -> int | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    if os.uname().sysname == "Darwin":
        for line in text.splitlines():
            if "maximum resident set size" in line:
                token = line.strip().split()[0]
                if token.isdigit():
                    return int(token)
        return None
    for line in text.splitlines():
        if "Maximum resident set size" in line and ":" in line:
            token = line.split(":", 1)[1].strip().split()[0]
            if token.isdigit():
                return int(token) * 1024
    return None
