"""Local experiment contracts and prediction snapshots. Not a shared wire contract."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import platform
import subprocess
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from emberline.contracts import Prov

from .experiment_io import (
    SCHEMA_VERSION,
    ConfigError,
    RunSession,
    is_sha256,
    pinned_rust_channel,
    read_strict_json,
    repo_root,
    resolve_input_path,
    sha256_canonical,
    sha256_file,
    validate_run_id,
    write_strict_json,
)


FORECAST_CONSUMING_ROLES = frozenset(
    {
        "forecast",
        "prediction",
        "observation",
        "weather",
        "truth",
        "perimeter",
        "forcing",
    }
)
_SYNTHETIC_KINDS = frozenset({"benchmark", "synthetic_twin", "report_effect"})
_STRICT = ConfigDict(extra="forbid", frozen=True)


def _sha256_field(value: str) -> str:
    if not is_sha256(value):
        raise ValueError("sha256 must be 64 lowercase hex characters")
    return value


class ExperimentLimits(BaseModel):
    model_config = _STRICT
    timeout_s: float | None = None
    memory_budget_bytes: int | None = None
    horizon_min: int | None = None

    @model_validator(mode="after")
    def _finite_limits(self) -> ExperimentLimits:
        if self.timeout_s is not None and (
            not np.isfinite(self.timeout_s) or self.timeout_s <= 0
        ):
            raise ValueError("timeout_s must be finite and positive")
        if self.memory_budget_bytes is not None and self.memory_budget_bytes < 1:
            raise ValueError("memory_budget_bytes must be positive")
        if self.horizon_min is not None and self.horizon_min < 1:
            raise ValueError("horizon_min must be positive")
        return self


class ExperimentConfig(BaseModel):
    """Resolved scientific configuration. Run id and wall time stay on the manifest."""

    model_config = _STRICT
    schema_version: Literal[1] = SCHEMA_VERSION
    experiment_kind: Literal[
        "benchmark", "synthetic_twin", "report_effect", "historical", "demo_package"
    ]
    dataset_kind: Literal["synthetic", "historical"]
    seed: int
    profile: str = Field(min_length=1)
    filter: Literal["pf", "reinit", "none"]
    spotting: Literal[False]
    limits: ExperimentLimits = Field(default_factory=ExperimentLimits)

    @model_validator(mode="after")
    def _kind_matches_data(self) -> ExperimentConfig:
        if self.experiment_kind == "historical" and self.dataset_kind != "historical":
            raise ValueError("historical experiments require historical data")
        if self.experiment_kind in _SYNTHETIC_KINDS and self.dataset_kind != "synthetic":
            raise ValueError("this experiment kind requires synthetic data")
        return self


class InputDeclaration(BaseModel):
    model_config = _STRICT
    relative_path: str = Field(min_length=1)
    role: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    provenance: Prov
    available_sim_ms: int | None = None
    availability_reason: str | None = None
    expected_sha256: str | None = None

    @field_validator("expected_sha256")
    @classmethod
    def _expected_hash(cls, value: str | None) -> str | None:
        if value is not None:
            _sha256_field(value)
        return value

    @field_validator("available_sim_ms", mode="before")
    @classmethod
    def _available(cls, value: object) -> int | None:
        if value is None:
            return None
        return _epoch_ms(value)

    @model_validator(mode="after")
    def _availability(self) -> InputDeclaration:
        _check_availability(
            self.role, self.available_sim_ms, self.availability_reason, self.relative_path
        )
        return self


class InputRecord(BaseModel):
    model_config = _STRICT
    relative_path: str = Field(min_length=1)
    sha256: str
    role: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    provenance: Prov
    available_sim_ms: int | None
    availability_reason: str | None = None

    @field_validator("sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _sha256_field(value)

    @field_validator("available_sim_ms", mode="before")
    @classmethod
    def _available(cls, value: object) -> int | None:
        if value is None:
            return None
        return _epoch_ms(value)

    @model_validator(mode="after")
    def _availability(self) -> InputRecord:
        _check_availability(
            self.role, self.available_sim_ms, self.availability_reason, self.relative_path
        )
        return self


class EnvironmentInfo(BaseModel):
    model_config = _STRICT
    os: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    python: str = Field(min_length=1)
    numpy: str = Field(min_length=1)
    scipy: str = Field(min_length=1)
    pyproj: str = Field(min_length=1)
    pydantic: str = Field(min_length=1)
    rust: str = Field(min_length=1)


class RunManifest(BaseModel):
    model_config = _STRICT
    schema_version: Literal[1] = SCHEMA_VERSION
    run_id: str = Field(min_length=1)
    experiment_kind: Literal[
        "benchmark", "synthetic_twin", "report_effect", "historical", "demo_package"
    ]
    dataset_kind: Literal["synthetic", "historical"]
    created_wall_ms: int = Field(ge=0)
    git_commit: str
    git_dirty: bool
    physics_sha256: str
    python_sha256: str
    binary_sha256: str
    config_sha256: str
    scientific_identity: str
    environment: EnvironmentInfo
    config: ExperimentConfig
    inputs: list[InputRecord]
    status: Literal["complete", "failed"]
    limitations: list[str]

    @field_validator(
        "physics_sha256",
        "python_sha256",
        "binary_sha256",
        "config_sha256",
        "scientific_identity",
    )
    @classmethod
    def _hashes(cls, value: str) -> str:
        return _sha256_field(value)

    @field_validator("git_commit")
    @classmethod
    def _commit(cls, value: str) -> str:
        if len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("git_commit must be a full lowercase SHA-1")
        return value

    @model_validator(mode="after")
    def _identity_agrees(self) -> RunManifest:
        try:
            validate_run_id(self.run_id)
        except ConfigError as exc:
            raise ValueError(str(exc)) from exc
        if self.experiment_kind != self.config.experiment_kind:
            raise ValueError("manifest kind does not match the config")
        if self.dataset_kind != self.config.dataset_kind:
            raise ValueError("manifest dataset does not match the config")
        if self.config_sha256 != config_sha256(self.config):
            raise ValueError("config hash does not match the stored config")
        if self.scientific_identity != scientific_identity(
            self.config,
            self.inputs,
            physics_sha256=self.physics_sha256,
            python_sha256=self.python_sha256,
            binary_sha256=self.binary_sha256,
        ):
            raise ValueError("scientific identity does not match the stored inputs")
        return self

    @field_validator("created_wall_ms", mode="before")
    @classmethod
    def _created_wall_ms(cls, value: object) -> int:
        return _epoch_ms(value)


class MetricResult(BaseModel):
    """Undefined metrics are null with a reason. They are never stored as zero."""

    model_config = _STRICT
    name: str = Field(min_length=1)
    value: float | None
    reason: str | None = None

    @model_validator(mode="after")
    def _undefined_needs_reason(self) -> MetricResult:
        if self.value is None:
            if self.reason is None or not self.reason.strip():
                raise ValueError("undefined metrics require a reason")
            return self
        if not np.isfinite(self.value):
            raise ValueError("non-finite metrics must be null with a reason")
        return self


def _epoch_ms(value: object) -> int:
    """Integer epoch milliseconds. Booleans and fractional numbers are rejected."""
    if isinstance(value, bool) or type(value) is not int or value < 0:
        raise ValueError("timestamp must be a nonnegative integer epoch millisecond")
    return value


def _check_availability(
    role: str, available_sim_ms: int | None, reason: str | None, relative_path: str
) -> None:
    raw = Path(relative_path)
    if raw.is_absolute() or ".." in raw.parts or "\x00" in relative_path:
        raise ValueError("input path escapes the input root")
    if available_sim_ms is None:
        if reason is None or not reason.strip():
            raise ValueError("null availability requires a reason")
        if role in FORECAST_CONSUMING_ROLES:
            raise ValueError("forecast-consuming inputs require available_sim_ms")
        return
    if available_sim_ms < 0:
        raise ValueError("available_sim_ms must be an integer epoch millisecond")


def parse_experiment_config(payload: object) -> ExperimentConfig:
    try:
        if isinstance(payload, ExperimentConfig):
            return payload
        return ExperimentConfig.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError("malformed experiment config") from exc


def config_sha256(config: ExperimentConfig) -> str:
    return sha256_canonical(config.model_dump(mode="json"))


def scientific_identity(
    config: ExperimentConfig,
    inputs: Sequence[InputRecord],
    *,
    physics_sha256: str,
    python_sha256: str,
    binary_sha256: str,
) -> str:
    """Identity of the experiment and the engine that produced it.

    Run id, wall time, git metadata, and limitations are excluded. Physics,
    Python, and binary fingerprints are included, so a different implementation
    is a different experiment even when the config and inputs match.
    """
    for name, value in (
        ("physics_sha256", physics_sha256),
        ("python_sha256", python_sha256),
        ("binary_sha256", binary_sha256),
    ):
        if not is_sha256(value):
            raise ConfigError(f"{name} must be a lowercase sha256")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "implementation": {
            "binary_sha256": binary_sha256,
            "physics_sha256": physics_sha256,
            "python_sha256": python_sha256,
        },
        "config": config.model_dump(mode="json"),
        "inputs": [
            {
                "available_sim_ms": item.available_sim_ms,
                "provenance": item.provenance,
                "relative_path": item.relative_path,
                "role": item.role,
                "sha256": item.sha256,
                "source_id": item.source_id,
            }
            for item in sorted(
                inputs, key=lambda item: (item.role, item.relative_path, item.sha256)
            )
        ],
    }
    return sha256_canonical(payload)


def bind_inputs(
    root: Path, declarations: Sequence[object], *, issue_sim_ms: int | None
) -> list[InputRecord]:
    if issue_sim_ms is not None:
        try:
            _epoch_ms(issue_sim_ms)
        except ValueError as exc:
            raise ConfigError("issue_sim_ms must be an integer epoch millisecond") from exc
    records: list[InputRecord] = []
    for item in declarations:
        try:
            declaration = (
                item
                if isinstance(item, InputDeclaration)
                else InputDeclaration.model_validate(item)
            )
        except ValidationError as exc:
            errors = exc.errors()
            if errors and errors[0].get("type") == "value_error":
                raise ConfigError(str(errors[0].get("msg", "malformed input declaration"))) from exc
            raise ConfigError("malformed input declaration") from exc
        if declaration.available_sim_ms is not None:
            if issue_sim_ms is None:
                raise ConfigError("issue time is required to admit timed inputs")
            if declaration.available_sim_ms > issue_sim_ms:
                raise ConfigError("future input is not available at issue")
        path = resolve_input_path(root, declaration.relative_path)
        digest = sha256_file(path)
        if declaration.expected_sha256 is not None and digest != declaration.expected_sha256:
            raise ConfigError("input hash does not match the declared sha256")
        records.append(
            InputRecord(
                relative_path=declaration.relative_path,
                sha256=digest,
                role=declaration.role,
                source_id=declaration.source_id,
                provenance=declaration.provenance,
                available_sim_ms=declaration.available_sim_ms,
                availability_reason=declaration.availability_reason,
            )
        )
    return records


def verify_bound_inputs(root: Path, records: Sequence[InputRecord]) -> None:
    for record in records:
        path = resolve_input_path(root, record.relative_path)
        if sha256_file(path) != record.sha256:
            raise ConfigError("input hash does not match the declared sha256")


def build_manifest(
    *,
    run_id: str,
    config: ExperimentConfig,
    inputs: Sequence[InputRecord],
    created_wall_ms: int,
    git_commit: str,
    git_dirty: bool,
    physics_sha256: str,
    python_sha256: str,
    binary_sha256: str,
    environment: EnvironmentInfo,
    status: Literal["complete", "failed"],
    limitations: Sequence[str],
) -> RunManifest:
    try:
        return RunManifest(
            run_id=run_id,
            experiment_kind=config.experiment_kind,
            dataset_kind=config.dataset_kind,
            created_wall_ms=created_wall_ms,
            git_commit=git_commit,
            git_dirty=git_dirty,
            physics_sha256=physics_sha256,
            python_sha256=python_sha256,
            binary_sha256=binary_sha256,
            config_sha256=config_sha256(config),
            scientific_identity=scientific_identity(
                config,
                inputs,
                physics_sha256=physics_sha256,
                python_sha256=python_sha256,
                binary_sha256=binary_sha256,
            ),
            environment=environment,
            config=config,
            inputs=list(inputs),
            status=status,
            limitations=list(limitations),
        )
    except ValidationError as exc:
        raise ConfigError("malformed run manifest") from exc


def load_manifest(payload: object) -> RunManifest:
    try:
        return RunManifest.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError("malformed run manifest") from exc


def completion_exit_code(*, gates_passed: bool | None, require_gates: bool) -> int:
    """Exit 0 keeps a failed scientific gate. Exit 4 is only an explicit request."""
    if require_gates and gates_passed is False:
        return 4
    return 0


def execute_run(
    parent: Path,
    *,
    run_id: str,
    config: ExperimentConfig,
    inputs: Sequence[InputRecord],
    created_wall_ms: int,
    git_commit: str,
    git_dirty: bool,
    physics_sha256: str,
    python_sha256: str,
    binary_sha256: str,
    environment: EnvironmentInfo,
    limitations: Sequence[str],
    work: Callable[[Path], None],
) -> RunManifest:
    """Run ``work`` in a staging directory and publish it only after a complete manifest."""
    records = list(inputs)
    common = dict(
        run_id=run_id,
        config=config,
        inputs=records,
        created_wall_ms=created_wall_ms,
        git_commit=git_commit,
        git_dirty=git_dirty,
        physics_sha256=physics_sha256,
        python_sha256=python_sha256,
        binary_sha256=binary_sha256,
        environment=environment,
        limitations=list(limitations),
    )
    build_manifest(status="complete", **common)
    session = RunSession(parent, run_id)
    session.open()
    try:
        work(session.staging)
        manifest = build_manifest(status="complete", **common)
        write_strict_json(session.staging / "config.json", config.model_dump(mode="json"))
        write_strict_json(session.staging / "manifest.json", manifest.model_dump(mode="json"))
        session.commit()
        return manifest
    except Exception as exc:
        failed = _failed_manifest(**common)
        session.fail(exc, failed)
        raise


def _failed_manifest(**kwargs: object) -> RunManifest | None:
    try:
        notes = list(kwargs["limitations"])  # type: ignore[arg-type]
        notes.append("run failed before completion and is not a result")
        payload = dict(kwargs)
        payload["limitations"] = notes
        payload["status"] = "failed"
        return build_manifest(**payload)  # type: ignore[arg-type]
    except Exception:
        return None


def read_git_state(repo: Path | None = None) -> tuple[str, bool]:
    root = Path(repo or repo_root())
    commit = _git(root, ["rev-parse", "HEAD"])
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise ConfigError("git commit is not a full SHA")
    dirty = _git(root, ["status", "--porcelain", "--untracked-files=normal"]) != ""
    return commit, dirty


def collect_environment(repo: Path | None = None) -> EnvironmentInfo:
    """Record library versions only. Environment variables are not copied."""
    import pydantic
    import pyproj
    import scipy

    channel = pinned_rust_channel(repo)
    return EnvironmentInfo(
        os=platform.system(),
        architecture=platform.machine(),
        python=platform.python_version(),
        numpy=np.__version__,
        scipy=scipy.__version__,
        pyproj=pyproj.__version__,
        pydantic=pydantic.__version__,
        rust=_rustc_version(channel),
    )


def _git(repo: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ConfigError("git identity could not be read") from exc
    return result.stdout.strip()


def _rustc_version(channel: str) -> str:
    try:
        listed = subprocess.run(
            ["rustup", "toolchain", "list"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ConfigError("rustup is required to identify the pinned Rust toolchain") from exc
    installed = False
    for line in listed.stdout.splitlines():
        name = line.split()[0] if line.split() else ""
        if name == channel or name.startswith(channel + "-"):
            installed = True
            break
    if not installed:
        raise ConfigError(f"Rust toolchain {channel} is not installed")
    try:
        result = subprocess.run(
            ["rustup", "run", channel, "rustc", "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ConfigError(f"could not run rustc for toolchain {channel}") from exc
    version = result.stdout.strip()
    prefix = f"rustc {channel}"
    if "\n" in version or not (version == prefix or version.startswith(prefix + " ")):
        raise ConfigError(
            f"rustc for toolchain {channel} reported {version!r}; refusing a different toolchain"
        )
    return version


@dataclass(frozen=True)
class PredictionSnapshot:
    arrivals_abs_min: np.ndarray
    weights: np.ndarray
    t_issue_min: float
    t_ref_s: int
    crs: str
    x0: float
    y0: float
    cell_m: float
    height: int
    width: int


def snapshot_prediction(model: object, forecast: object) -> PredictionSnapshot:
    """Copy raw arrivals and weights. This is the experiment adapter for ``_arrivals``."""
    grid = model.land.grid
    raw = model._arrivals
    if not isinstance(raw, np.ndarray) or raw.dtype != np.float32 or raw.ndim != 3:
        raise ConfigError("raw arrivals must be a float32 array shaped (N, H, W)")
    if np.isnan(raw).any() or np.isneginf(raw).any():
        raise ConfigError("NaN and negative infinity are invalid raw arrivals")
    model_weights = np.asarray(model._weights)
    forecast_weights = np.asarray(forecast.weights)
    if model_weights.dtype != np.float64 or forecast_weights.dtype != np.float64:
        raise ConfigError("weights must be float64")
    if model_weights.shape != forecast_weights.shape or not np.array_equal(
        model_weights, forecast_weights
    ):
        raise ConfigError("forecast weights disagree with model weights")
    members, height, width = raw.shape
    if model_weights.shape != (members,):
        raise ConfigError("arrival and weight dimensions disagree")
    if not np.isfinite(model_weights).all() or np.any(model_weights < 0):
        raise ConfigError("weights must be finite and nonnegative")
    if not np.isclose(float(model_weights.sum()), 1.0, rtol=0.0, atol=1e-8):
        raise ConfigError("weights must be normalized")
    grid_h = _plain_int(grid.H, "grid height")
    grid_w = _plain_int(grid.W, "grid width")
    if (grid_h, grid_w) != (height, width) or grid_h < 1 or grid_w < 1:
        raise ConfigError("arrival and grid dimensions disagree")
    issue = _issue_minutes(forecast.t_issue_min)
    last = _issue_minutes(model._last_issue)
    if issue != last:
        raise ConfigError("forecast issue does not match model issue")
    crs = grid.crs
    if not isinstance(crs, str) or not crs:
        raise ConfigError("grid crs is missing")
    x0 = float(grid.x0)
    y0 = float(grid.y0)
    cell_m = float(grid.cell_m)
    if not np.isfinite([x0, y0, cell_m]).all() or cell_m <= 0:
        raise ConfigError("grid coordinates are invalid")
    t_ref_s = _plain_int(grid.t_ref_s, "t_ref_s")
    return PredictionSnapshot(
        arrivals_abs_min=np.copy(raw),
        weights=np.copy(model_weights),
        t_issue_min=issue,
        t_ref_s=t_ref_s,
        crs=crs,
        x0=x0,
        y0=y0,
        cell_m=cell_m,
        height=height,
        width=width,
    )


def _plain_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ConfigError(f"{name} must be an integer")
    return int(value)


def _issue_minutes(value: object) -> float:
    if isinstance(value, bool) or isinstance(value, (str, bytes)) or value is None:
        raise ConfigError("forecast issue does not match model issue")
    try:
        issue = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError("forecast issue does not match model issue") from exc
    if not np.isfinite(issue):
        raise ConfigError("forecast issue does not match model issue")
    return issue


def apply_synthetic_truth_override(
    model: object,
    *,
    ros_mult: float,
    wind_speed_mult: float,
    wind_dir_bias_deg: float,
    dfm_1h: float,
    urban_ros: float,
    member: int,
) -> dict[str, object]:
    """Write one hidden-truth parameter vector for a synthetic twin.

    Production priors are unchanged. The twin shares this solver's parameter
    layout and is not historical validation.
    """
    if not getattr(model, "_initialized", False):
        raise ConfigError("synthetic truth override requires an initialized model")
    theta = getattr(model, "_theta", None)
    if not isinstance(theta, np.ndarray) or theta.ndim != 2 or theta.shape[1] != 5:
        raise ConfigError("synthetic truth override does not recognize the parameter layout")
    if isinstance(member, bool) or not isinstance(member, int) or not 0 <= member < theta.shape[0]:
        raise ConfigError("synthetic truth member is outside the ensemble")
    values = np.array(
        [ros_mult, wind_speed_mult, wind_dir_bias_deg, dfm_1h, urban_ros], dtype=np.float64
    )
    if (
        not np.isfinite(values).all()
        or ros_mult <= 0
        or wind_speed_mult <= 0
        or dfm_1h <= 0
        or urban_ros <= 0
    ):
        raise ConfigError("synthetic truth multipliers and moisture must be finite and positive")
    theta[member] = np.array(
        [
            np.log(ros_mult),
            np.log(wind_speed_mult),
            np.deg2rad(wind_dir_bias_deg),
            np.log(dfm_1h),
            np.log(urban_ros),
        ],
        dtype=np.float64,
    )
    return {
        "kind": "synthetic_hidden_truth",
        "assumption": "shared solver and likelihood; not historical validation",
        "member": member,
        "parameters": {
            "ros_mult": float(ros_mult),
            "wind_speed_mult": float(wind_speed_mult),
            "wind_dir_bias_deg": float(wind_dir_bias_deg),
            "dfm_1h": float(dfm_1h),
            "urban_ros": float(urban_ros),
        },
    }


def manifest_from_file(path: Path) -> RunManifest:
    return load_manifest(read_strict_json(path))
