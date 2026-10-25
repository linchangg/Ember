"""Local experiment contracts: identity, confined inputs, and prediction snapshots."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from emberline.contracts import SCHEMA_VERSION
from emberline.hazard.api import Grid, ModelConfig
from emberline.hazard.compatibility import check_contracts
from emberline.hazard.experiment_io import (
    ConfigError,
    ExecutionError,
    GateError,
    canonical_json,
    exit_code_for,
    pinned_rust_channel,
    repo_root,
    resolve_input_path,
    sha256_canonical,
)
from emberline.hazard.experiments import (
    EnvironmentInfo,
    ExperimentConfig,
    InputRecord,
    MetricResult,
    RunManifest,
    apply_synthetic_truth_override,
    bind_inputs,
    build_manifest,
    collect_environment,
    completion_exit_code,
    config_sha256,
    execute_run,
    load_manifest,
    manifest_from_file,
    parse_experiment_config,
    read_git_state,
    scientific_identity,
    snapshot_prediction,
    verify_bound_inputs,
)

COMMIT = "a" * 40


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


PHYSICS = _digest("physics")
PYTHON = _digest("python")
BINARY = _digest("binary")


def _environment() -> EnvironmentInfo:
    return EnvironmentInfo(
        os="Darwin",
        architecture="arm64",
        python="3.12.0",
        numpy="2.0.0",
        scipy="1.13.0",
        pyproj="3.6.1",
        pydantic="2.9.0",
        rust="rustc 1.99.1 (test)",
    )


def _config(**overrides: object) -> ExperimentConfig:
    payload: dict[str, object] = {
        "schema_version": 1,
        "experiment_kind": "benchmark",
        "dataset_kind": "synthetic",
        "seed": 7,
        "profile": "smoke",
        "filter": "pf",
        "spotting": False,
    }
    payload.update(overrides)
    return parse_experiment_config(payload)


def _run(parent: Path, *, work, inputs=(), run_id="run-1", created_wall_ms=1_700_000_000_000, **overrides):
    config = overrides.pop("config", _config())
    arguments = {
        "git_commit": COMMIT,
        "git_dirty": False,
        "physics_sha256": PHYSICS,
        "python_sha256": PYTHON,
        "binary_sha256": BINARY,
        "environment": _environment(),
        "limitations": ["synthetic contract test"],
    }
    arguments.update(overrides)
    return execute_run(
        parent,
        run_id=run_id,
        config=config,
        inputs=inputs,
        created_wall_ms=created_wall_ms,
        work=work,
        **arguments,
    )


def _model(arrivals, weights, issue=10.0, height=2, width=2):
    grid = Grid("EPSG:32611", 0.0, 1000.0, 90.0, height, width, 1_700_000_000)
    model = SimpleNamespace(
        land=SimpleNamespace(grid=grid),
        _arrivals=arrivals,
        _weights=weights,
        _last_issue=issue,
    )
    forecast = SimpleNamespace(t_issue_min=issue, weights=weights)
    return model, forecast


def test_malformed_and_unknown_config_fail():
    with pytest.raises(ConfigError, match="malformed"):
        parse_experiment_config({"schema_version": 1, "seed": 7})
    valid = {
        "schema_version": 1,
        "experiment_kind": "benchmark",
        "dataset_kind": "synthetic",
        "seed": 7,
        "profile": "smoke",
        "filter": "pf",
        "spotting": False,
    }
    with pytest.raises(ConfigError) as caught:
        parse_experiment_config({**valid, "temperature": 0.1})
    assert isinstance(caught.value.__cause__, ValidationError)
    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate({**valid, "temperature": 0.1})
    with pytest.raises(ConfigError):
        parse_experiment_config({**valid, "limits": {"timeout_s": 5, "workers": 2}})
    with pytest.raises(ConfigError):
        _config(spotting=True)
    with pytest.raises(ConfigError):
        parse_experiment_config({k: v for k, v in valid.items() if k != "spotting"})
    with pytest.raises(ConfigError):
        _config(experiment_kind="historical", dataset_kind="synthetic")
    with pytest.raises(ConfigError):
        _config(experiment_kind="benchmark", dataset_kind="historical")
    historical = _config(experiment_kind="demo_package", dataset_kind="historical")
    assert historical.dataset_kind == "historical"
    assert exit_code_for(caught.value) == 2
    with pytest.raises(ValidationError) as invalid:
        ExperimentConfig.model_validate({})
    assert exit_code_for(invalid.value) == 2


def test_production_defaults_and_wire_contract_stay_put():
    defaults = ModelConfig()
    assert defaults.spotting is True
    assert defaults.n_members == 128
    assert defaults.filter == "pf"
    assert defaults.quantiles == (0.1, 0.3, 0.5, 0.7, 0.9)
    report = check_contracts()
    assert report["wire_schema"] == SCHEMA_VERSION == 2
    assert report["model_version"] == "rust-levelset-pf-1"
    source = (repo_root() / "engine/emberline/hazard/experiments.py").read_text()
    assert source.count("model._arrivals") == 1
    io_source = (repo_root() / "engine/emberline/hazard/experiment_io.py").read_text()
    assert "model._arrivals" not in io_source


def test_canonical_hash_ignores_key_order_and_run_identity():
    assert sha256_canonical({"b": 1, "a": [2, 3]}) == sha256_canonical({"a": [2, 3], "b": 1})
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})
    left = _config(seed=7)
    right = _config(seed=7)
    other = _config(seed=8)
    assert config_sha256(left) == config_sha256(right)
    assert config_sha256(left) != config_sha256(other)
    empty: list[InputRecord] = []
    first = build_manifest(
        run_id="run-a",
        config=left,
        inputs=empty,
        created_wall_ms=10,
        git_commit=COMMIT,
        git_dirty=True,
        physics_sha256=PHYSICS,
        python_sha256=PYTHON,
        binary_sha256=BINARY,
        environment=_environment(),
        status="complete",
        limitations=[],
    )
    second = build_manifest(
        run_id="run-b",
        config=right,
        inputs=empty,
        created_wall_ms=99,
        git_commit=COMMIT,
        git_dirty=False,
        physics_sha256=PHYSICS,
        python_sha256=PYTHON,
        binary_sha256=BINARY,
        environment=_environment(),
        status="complete",
        limitations=["different note"],
    )
    assert first.scientific_identity == second.scientific_identity
    assert first.config_sha256 == second.config_sha256
    assert scientific_identity(
        left, empty, physics_sha256=PHYSICS, python_sha256=PYTHON, binary_sha256=BINARY
    ) != scientific_identity(
        other, empty, physics_sha256=PHYSICS, python_sha256=PYTHON, binary_sha256=BINARY
    )
    other_engine = build_manifest(
        run_id="run-a",
        config=left,
        inputs=empty,
        created_wall_ms=10,
        git_commit=COMMIT,
        git_dirty=True,
        physics_sha256=_digest("other-engine"),
        python_sha256=PYTHON,
        binary_sha256=BINARY,
        environment=_environment(),
        status="complete",
        limitations=[],
    )
    assert other_engine.scientific_identity != first.scientific_identity
    swapped = first.model_dump(mode="json")
    swapped["physics_sha256"] = _digest("other-engine")
    with pytest.raises(ConfigError, match="malformed run manifest"):
        load_manifest(swapped)
    with pytest.raises(ConfigError):
        build_manifest(
            run_id="run-a",
            config=left,
            inputs=empty,
            created_wall_ms=True,
            git_commit=COMMIT,
            git_dirty=False,
            physics_sha256=PHYSICS,
            python_sha256=PYTHON,
            binary_sha256=BINARY,
            environment=_environment(),
            status="complete",
            limitations=[],
        )
    with pytest.raises(ConfigError):
        build_manifest(
            run_id="run-a",
            config=left,
            inputs=empty,
            created_wall_ms=10.0,
            git_commit=COMMIT,
            git_dirty=False,
            physics_sha256=PHYSICS,
            python_sha256=PYTHON,
            binary_sha256=BINARY,
            environment=_environment(),
            status="complete",
            limitations=[],
        )
    tampered = first.model_dump(mode="json")
    tampered["config"]["seed"] = 9
    with pytest.raises(ConfigError, match="malformed run manifest"):
        load_manifest(tampered)
    tampered = first.model_dump(mode="json")
    tampered["leak"] = "secret"
    with pytest.raises(ConfigError):
        load_manifest(tampered)
    with pytest.raises(ValidationError):
        EnvironmentInfo.model_validate({**_environment().model_dump(), "environ": {"TOKEN": "no"}})


def test_future_inputs_hash_tampering_and_path_escape(tmp_path: Path):
    root = tmp_path / "inputs"
    root.mkdir()
    payload = root / "obs.json"
    payload.write_text('{"hot": true}\n', encoding="utf-8")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    declaration = {
        "relative_path": "obs.json",
        "role": "observation",
        "source_id": "synthetic-goes",
        "provenance": "simulated",
        "available_sim_ms": 1_000,
        "availability_reason": None,
    }
    bound = bind_inputs(root, [declaration], issue_sim_ms=1_000)
    assert bound[0].sha256 == digest
    with pytest.raises(ConfigError, match="future input"):
        bind_inputs(root, [{**declaration, "available_sim_ms": 1_001}], issue_sim_ms=1_000)
    with pytest.raises(ConfigError, match="hash"):
        bind_inputs(
            root,
            [{**declaration, "expected_sha256": _digest("other")}],
            issue_sim_ms=1_000,
        )
    with pytest.raises(ConfigError, match="available_sim_ms"):
        bind_inputs(
            root,
            [
                {
                    "relative_path": "obs.json",
                    "role": "forecast",
                    "source_id": "forecast",
                    "provenance": "simulated",
                    "available_sim_ms": None,
                    "availability_reason": "missing on purpose",
                }
            ],
            issue_sim_ms=1_000,
        )
    frozen = root / "scenario.json"
    frozen.write_text("{}\n", encoding="utf-8")
    construction = bind_inputs(
        root,
        [
            {
                "relative_path": "scenario.json",
                "role": "construction",
                "source_id": "synthetic-scenario",
                "provenance": "simulated",
                "available_sim_ms": None,
                "availability_reason": "synthetic construction frozen before the run",
            }
        ],
        issue_sim_ms=None,
    )
    assert construction[0].available_sim_ms is None
    payload.write_text('{"hot": false}\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="hash"):
        verify_bound_inputs(root, bound)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(ConfigError, match="escapes"):
        resolve_input_path(root, "../secret.txt")
    with pytest.raises(ConfigError, match="escapes"):
        resolve_input_path(root, str(outside))
    with pytest.raises(ConfigError, match="escapes"):
        resolve_input_path(root, str(payload))
    link = root / "escape"
    link.symlink_to(outside)
    with pytest.raises(ConfigError, match="symlink"):
        resolve_input_path(root, "escape")
    inside = root / "inside.txt"
    inside.write_text("kept", encoding="utf-8")
    alias = root / "alias"
    alias.symlink_to(inside)
    assert resolve_input_path(root, "alias").read_text(encoding="utf-8") == "kept"
    with pytest.raises(ConfigError, match="malformed"):
        bind_inputs(root, [{**declaration, "token": "x"}], issue_sim_ms=1_000)


def test_atomic_run_publishes_once_and_keeps_failures(tmp_path: Path):
    parent = tmp_path / "runs"

    def write_note(staging: Path) -> None:
        (staging / "note.txt").write_text("ok", encoding="utf-8")

    manifest = _run(parent, work=write_note)
    final = parent / "run-1"
    assert final.is_dir()
    assert not (parent / ".run-1.staging").exists()
    assert manifest.status == "complete"
    stored = manifest_from_file(final / "manifest.json")
    assert stored == manifest
    config_text = (final / "config.json").read_text(encoding="utf-8")
    assert config_text.endswith("\n")
    assert sha256_canonical(json.loads(config_text)) == manifest.config_sha256
    original = (final / "manifest.json").read_text(encoding="utf-8")
    with pytest.raises(ConfigError, match="already exists"):
        _run(parent, work=write_note)
    assert (final / "manifest.json").read_text(encoding="utf-8") == original

    def explode(staging: Path) -> None:
        (staging / "partial.txt").write_text("partial", encoding="utf-8")
        raise RuntimeError("solver blew up")

    with pytest.raises(RuntimeError, match="solver blew up"):
        _run(parent, work=explode, run_id="run-2")
    assert not (parent / "run-2").exists()
    assert not (parent / ".run-2.staging").exists()
    failed = parent / "run-2.failed"
    failure = json.loads((failed / "failure.json").read_text(encoding="utf-8"))
    assert failure["status"] == "failed"
    assert failure["error_type"] == "RuntimeError"
    assert "solver blew up" in failure["message"]
    assert set(failure) == {"schema_version", "status", "run_id", "error_type", "message"}
    failed_manifest = manifest_from_file(failed / "manifest.json")
    assert failed_manifest.status == "failed"
    assert "not a result" in failed_manifest.limitations[-1]
    assert (failed / "partial.txt").read_text(encoding="utf-8") == "partial"
    assert exit_code_for(RuntimeError("solver blew up")) == 3
    assert exit_code_for(ExecutionError("disk")) == 3
    assert exit_code_for(GateError("gate")) == 4
    assert completion_exit_code(gates_passed=False, require_gates=False) == 0
    assert completion_exit_code(gates_passed=False, require_gates=True) == 4
    assert completion_exit_code(gates_passed=None, require_gates=True) == 0
    with pytest.raises(ConfigError):
        _run(parent, work=write_note, run_id="run-3", git_commit="short")
    assert not (parent / "run-3").exists()
    assert not (parent / ".run-3.staging").exists()


def test_repeated_snapshots_match_and_reject_bad_payloads():
    arrivals = np.array([[[0.0, np.inf], [4.0, 8.0]]], np.float32)
    weights = np.array([1.0], np.float64)
    model, forecast = _model(arrivals, weights)
    first = snapshot_prediction(model, forecast)
    second = snapshot_prediction(model, forecast)
    np.testing.assert_array_equal(first.arrivals_abs_min, second.arrivals_abs_min)
    np.testing.assert_array_equal(first.weights, second.weights)
    assert first.t_issue_min == 10.0
    assert first.t_ref_s == 1_700_000_000
    assert first.cell_m == 90.0
    assert np.isposinf(first.arrivals_abs_min[0, 0, 1])
    model._arrivals[0, 0, 0] = np.float32(15)
    assert first.arrivals_abs_min[0, 0, 0] == 0
    assert not np.shares_memory(first.arrivals_abs_min, model._arrivals)
    assert not np.shares_memory(first.weights, model._weights)

    broken = np.array(arrivals, copy=True)
    broken[0, 0, 0] = np.nan
    model._arrivals = broken
    with pytest.raises(ConfigError, match="NaN"):
        snapshot_prediction(model, forecast)
    broken[0, 0, 0] = -np.inf
    with pytest.raises(ConfigError, match="negative infinity"):
        snapshot_prediction(model, forecast)
    model._arrivals = np.array(arrivals, dtype=np.float64)
    with pytest.raises(ConfigError, match="float32"):
        snapshot_prediction(model, forecast)
    model._arrivals = np.array(arrivals, copy=True)
    model._weights = np.array([0.25], np.float64)
    forecast.weights = np.array([0.25], np.float64)
    with pytest.raises(ConfigError, match="normalized"):
        snapshot_prediction(model, forecast)
    model._arrivals = np.zeros((2, 2, 2), np.float32)
    model._weights = np.array([1.2, -0.2], np.float64)
    forecast.weights = model._weights
    with pytest.raises(ConfigError, match="nonnegative"):
        snapshot_prediction(model, forecast)
    model._last_issue = 12.0
    model._weights = weights
    forecast.weights = weights
    model._arrivals = np.array(arrivals, copy=True)
    with pytest.raises(ConfigError, match="issue"):
        snapshot_prediction(model, forecast)
    model._last_issue = 10.0
    model._arrivals = np.zeros((1, 1, 2), np.float32)
    with pytest.raises(ConfigError, match="dimensions"):
        snapshot_prediction(model, forecast)
    metric = MetricResult(name="brier", value=None, reason="no valid cells")
    assert '"value":null' in canonical_json(metric.model_dump(mode="json"))
    with pytest.raises(ValidationError):
        MetricResult(name="brier", value=None, reason=None)
    with pytest.raises(ValidationError):
        MetricResult(name="brier", value=float("nan"), reason=None)


def test_synthetic_truth_override_is_explicit_and_member_local():
    class Twin:
        _initialized = True

        def __init__(self) -> None:
            self._theta = np.zeros((2, 5), np.float64)

    twin = Twin()
    described = apply_synthetic_truth_override(
        twin,
        ros_mult=1.6,
        wind_speed_mult=1.0,
        wind_dir_bias_deg=20.0,
        dfm_1h=0.04,
        urban_ros=2.0,
        member=0,
    )
    expected = np.array(
        [np.log(1.6), 0.0, np.deg2rad(20.0), np.log(0.04), np.log(2.0)], np.float64
    )
    np.testing.assert_array_equal(twin._theta[0], expected)
    assert np.all(twin._theta[1] == 0)
    assert described["kind"] == "synthetic_hidden_truth"
    assert described["parameters"]["ros_mult"] == 1.6
    bare = Twin()
    bare._initialized = False
    with pytest.raises(ConfigError, match="initialized"):
        apply_synthetic_truth_override(
            bare,
            ros_mult=1.6,
            wind_speed_mult=1.0,
            wind_dir_bias_deg=20.0,
            dfm_1h=0.04,
            urban_ros=2.0,
            member=0,
        )
    with pytest.raises(ConfigError, match="outside"):
        apply_synthetic_truth_override(
            twin,
            ros_mult=1.6,
            wind_speed_mult=1.0,
            wind_dir_bias_deg=0.0,
            dfm_1h=0.04,
            urban_ros=2.0,
            member=2,
        )
    with pytest.raises(ConfigError, match="positive"):
        apply_synthetic_truth_override(
            twin,
            ros_mult=0.0,
            wind_speed_mult=1.0,
            wind_dir_bias_deg=0.0,
            dfm_1h=0.04,
            urban_ros=2.0,
            member=0,
        )


def test_git_and_pinned_toolchain_are_recorded_without_env_dumps():
    try:
        commit, dirty = read_git_state()
    except ConfigError as exc:
        pytest.skip(str(exc))
    assert len(commit) == 40
    assert isinstance(dirty, bool)
    assert pinned_rust_channel() == "1.99.1"
    try:
        environment = collect_environment()
    except ConfigError as exc:
        pytest.skip(str(exc))
    dumped = environment.model_dump()
    assert set(dumped) == {
        "os",
        "architecture",
        "python",
        "numpy",
        "scipy",
        "pyproj",
        "pydantic",
        "rust",
    }
    assert dumped["rust"].startswith("rustc 1.99.1 ")
    assert "TOKEN" not in json.dumps(dumped)
    with pytest.raises(ValidationError):
        RunManifest.model_validate({"schema_version": 1})
