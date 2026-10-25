"""Subprocess bridge failure classes, hashes, and timeout cleanup."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

from adapter.bridge import CommandResult, classify_failure, invoke, simulate, verify_published_run
from adapter.conversions import (
    AppGrid,
    TimeOrigins,
    WeatherFrame,
    prepare_research_bundle,
    signed_distance_phi,
    write_research_bundle,
)
from adapter.errors import BridgeError, ValidationError

STUB = Path(__file__).with_name("stub_executable.py")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _result(code: int, stderr: str) -> CommandResult:
    return CommandResult(code, "", stderr, 0.01, None, "unavailable")


def test_failure_classes_are_distinct() -> None:
    validation = classify_failure(
        _result(2, json.dumps({"code": "SchemaMismatch", "message": "bad grid"}))
    )
    numerical = classify_failure(
        _result(3, json.dumps({"code": "NumericalOverflow", "message": "overflow"}))
    )
    cancelled = classify_failure(
        _result(130, json.dumps({"code": "Cancelled", "message": "stopped"}))
    )
    memory = classify_failure(
        _result(4, json.dumps({"code": "MemoryBudgetExceeded", "message": "budget"}))
    )
    malformed = classify_failure(_result(1, "not json"))
    assert [item.code for item in (validation, numerical, cancelled, memory, malformed)] == [
        "validation_failure",
        "numerical_failure",
        "cancellation",
        "memory_budget",
        "malformed_output",
    ]


def test_arguments_are_not_passed_through_a_shell(tmp_path: Path) -> None:
    argv_file = tmp_path / "argv.json"
    marker = "literal $(echo pwned) *"
    result = invoke(
        Path(sys.executable),
        [str(STUB), "--mode", "dump-argv", "--argv-file", str(argv_file), marker],
        timeout_s=10,
    )
    assert result.returncode == 0
    recorded = json.loads(argv_file.read_text(encoding="utf-8"))
    assert marker in recorded
    assert "pwned" not in recorded[-1] or recorded[-1] == marker


def test_timeout_terminates_the_child(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    with pytest.raises(BridgeError) as caught:
        invoke(
            Path(sys.executable),
            [str(STUB), "--mode", "sleep", "--pid-file", str(pid_file)],
            timeout_s=0.4,
        )
    assert caught.value.code == "timeout"
    assert "resumable cancellation" in caught.value.message
    assert caught.value.work_dir is not None
    assert Path(caught.value.work_dir).is_dir()
    pid = int(pid_file.read_text(encoding="utf-8"))
    assert not _alive(pid)


def test_stub_modes_classify_through_invoke() -> None:
    expected = {
        "validation": "validation_failure",
        "numerical": "numerical_failure",
        "cancel": "cancellation",
        "memory": "memory_budget",
        "malformed": "malformed_output",
    }
    for mode, code in expected.items():
        result = invoke(
            Path(sys.executable),
            [str(STUB), "--mode", mode],
            timeout_s=10,
        )
        failure = classify_failure(result)
        assert failure.code == code


def _bundle(tmp_path: Path) -> tuple[Path, AppGrid]:
    grid = AppGrid("EPSG:32611", 0.0, 270.0, 90.0, 3, 3)
    phi = signed_distance_phi(grid, 135.0, 135.0, 100.0)
    frame = WeatherFrame(
        time_s=0.0,
        valid_time_ms=1_000_000_000_000,
        available_time_ms=1_000_000_000_000,
        wind_east_m_s=0.0,
        wind_north_m_s=0.0,
        m1=0.06,
        m10=0.07,
        m100=0.08,
        mlh=0.6,
        mlw=0.9,
    )
    bundle = prepare_research_bundle(
        grid,
        elevation_m=np.zeros((3, 3), dtype=np.float64),
        fuel_id=np.ones((3, 3), dtype=np.int16),
        valid=np.ones((3, 3), dtype=np.uint8),
        burnable=np.ones((3, 3), dtype=np.uint8),
        phi=phi,
        frames=[frame],
        origins=TimeOrigins(1_000_000_000_000, 1_000_000_000_000, 1_000_000_000_000),
        incident_id="hash-fixture",
        horizons_s=[60.0],
        coverage_end_s=60.0,
        components_are_midflame=True,
    )
    manifest = write_research_bundle(tmp_path / "bundle", bundle)
    return manifest, grid


def _publish_fake(out_dir: Path, manifest: Path, *, artifact_hash: str | None = None) -> None:
    from adapter.bridge import sha256_file

    out_dir.mkdir()
    grid_n = 9
    np.zeros(grid_n, dtype="<f8").tofile(out_dir / "arrival_time_s.f64le")
    np.zeros(grid_n, dtype=np.uint8).tofile(out_dir / "arrival_status.u8")
    (out_dir / "diagnostics.json").write_text("{}\n", encoding="utf-8")
    digest, _ = sha256_file(manifest)
    (out_dir / "manifest.sha256").write_text(digest + "\n", encoding="utf-8")
    artifacts = []
    root = manifest.parent
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            file_digest, size = sha256_file(path)
            relative = path.relative_to(root).as_posix()
            artifacts.append({"path": relative, "sha256": file_digest, "bytes": size})
    if artifact_hash is not None:
        artifacts[0]["sha256"] = artifact_hash
    run = {
        "schema_version": 2,
        "status": "complete",
        "model": "surface_v1",
        "solver": "level_set_lf_euler_v1",
        "completed_until_s": 60.0,
        "horizons_s": [60.0],
        "quality_flags": [],
        "input_manifest_sha256": digest,
        "input_artifacts": artifacts,
        "configuration": {
            "grid": {
                "crs": "EPSG:32611",
                "x0_m": 0.0,
                "y0_m": 0.0,
                "cell_size_m": 90.0,
                "height": 3,
                "width": 3,
            }
        },
    }
    (out_dir / "run.json").write_text(json.dumps(run), encoding="utf-8")


def test_hash_mismatch_and_wrong_model_are_malformed(tmp_path: Path) -> None:
    manifest, _grid = _bundle(tmp_path)
    bad_hash = tmp_path / "bad-hash"
    _publish_fake(bad_hash, manifest, artifact_hash="0" * 64)
    with pytest.raises(BridgeError) as caught:
        verify_published_run(bad_hash, bundle_root=manifest.parent, manifest_path=manifest)
    assert caught.value.code == "malformed_output"
    wrong_model = tmp_path / "wrong-model"
    _publish_fake(wrong_model, manifest)
    payload = json.loads((wrong_model / "run.json").read_text(encoding="utf-8"))
    payload["model"] = "other"
    (wrong_model / "run.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BridgeError) as caught:
        verify_published_run(wrong_model, bundle_root=manifest.parent, manifest_path=manifest)
    assert "model identity" in caught.value.message
    short = tmp_path / "short"
    _publish_fake(short, manifest)
    np.zeros(3, dtype="<f8").tofile(short / "arrival_time_s.f64le")
    with pytest.raises(BridgeError, match="arrival length"):
        verify_published_run(short, bundle_root=manifest.parent, manifest_path=manifest)


def test_simulate_rejects_bad_inputs_before_launch(tmp_path: Path) -> None:
    missing = tmp_path / "missing-manifest.json"
    with pytest.raises(ValidationError):
        simulate(Path(sys.executable), missing, tmp_path / "out", timeout_s=5)
    manifest, _grid = _bundle(tmp_path)
    text = json.loads(manifest.read_text(encoding="utf-8"))
    text["grid"]["crs"] = "EPSG:4326"
    geographic = tmp_path / "geographic.json"
    geographic.write_text(json.dumps(text), encoding="utf-8")
    with pytest.raises(ValidationError, match="geographic"):
        simulate(Path(sys.executable), geographic, tmp_path / "out2", timeout_s=5)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    with pytest.raises(ValidationError, match="already exists"):
        simulate(Path(sys.executable), manifest, occupied, timeout_s=5)
