"""Atomic run directories, strict JSON, and confined input paths."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import re


SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ConfigError(ValueError):
    """Invalid experiment input or configuration. CLI exit 2."""


class ExecutionError(RuntimeError):
    """The run could not finish or could not store its artifacts. CLI exit 3."""


class GateError(RuntimeError):
    """A completed run failed a requested scientific gate. CLI exit 4."""


def exit_code_for(exc: BaseException) -> int:
    from pydantic import ValidationError

    if isinstance(exc, GateError):
        return 4
    if isinstance(exc, (ConfigError, ValidationError)):
        return 2
    return 3


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def pinned_rust_channel(repo: Path | None = None) -> str:
    text = (Path(repo or repo_root()) / "physics/surface_v1/rust-toolchain.toml").read_text(
        encoding="utf-8"
    )
    match = re.search(r'(?m)^channel\s*=\s*"([^"]+)"\s*$', text)
    if match is None:
        raise ConfigError("rust-toolchain.toml does not pin a channel")
    return match.group(1)


def canonical_json(value: object) -> str:
    """Sorted keys, compact separators, UTF-8, and no NaN or infinity."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_canonical(value: object) -> str:
    return sha256_hex(canonical_json(value).encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_sha256(value: str) -> bool:
    return bool(_SHA256.fullmatch(value))


def write_strict_json(path: Path, value: object) -> None:
    text = canonical_json(value) + "\n"
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")


def read_strict_json(path: Path) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def resolve_input_path(root: Path, relative_path: str) -> Path:
    """Resolve one file inside ``root``. Reject escape by path or symlink."""
    if not isinstance(relative_path, str) or relative_path == "" or "\x00" in relative_path:
        raise ConfigError("input path must be a nonempty relative path")
    raw = Path(relative_path)
    if raw.is_absolute() or any(part == ".." for part in raw.parts):
        raise ConfigError("input path escapes the input root")
    base = Path(root).resolve()
    if not base.is_dir():
        raise ConfigError("input root is not a directory")
    current = base
    for part in raw.parts:
        if part in ("", "."):
            continue
        current = current / part
        if current.is_symlink():
            target = current.resolve()
            if not _within(target, base):
                raise ConfigError("symlink escapes the input root")
            current = target
    if not current.exists():
        raise ConfigError("input path does not exist")
    resolved = current.resolve()
    if not _within(resolved, base):
        raise ConfigError("input path escapes the input root")
    if not resolved.is_file():
        raise ConfigError("input path is not a file")
    return resolved


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise ConfigError("run_id must be a nonempty identifier without a path")
    return run_id


class RunSession:
    """One new run directory. Success renames staging; failure stays marked failed."""

    def __init__(self, parent: Path, run_id: str) -> None:
        self.parent = Path(parent)
        self.run_id = validate_run_id(run_id)
        self.final = self.parent / self.run_id
        self.staging = self.parent / f".{self.run_id}.staging"
        self.failed = self.parent / f"{self.run_id}.failed"
        self._closed = False

    def open(self) -> Path:
        self.parent.mkdir(parents=True, exist_ok=True)
        if self.final.exists() or self.staging.exists() or self.failed.exists():
            raise ConfigError(f"run directory already exists: {self.run_id}")
        try:
            self.staging.mkdir()
        except FileExistsError as exc:
            raise ConfigError(f"run directory already exists: {self.run_id}") from exc
        return self.staging

    def commit(self) -> Path:
        if self._closed:
            raise ExecutionError("run directory is already closed")
        if not self.staging.is_dir():
            raise ExecutionError("staging directory disappeared before commit")
        try:
            os.rename(self.staging, self.final)
        except OSError as exc:
            raise ExecutionError(
                "could not publish the run directory on the same filesystem"
            ) from exc
        self._closed = True
        return self.final

    def fail(self, exc: BaseException, manifest: object | None = None) -> Path | None:
        if self._closed or not self.staging.exists():
            return None
        info = {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "run_id": self.run_id,
            "error_type": type(exc).__name__,
            "message": str(exc)[:2000],
        }
        try:
            write_strict_json(self.staging / "failure.json", info)
            if manifest is not None:
                payload = (
                    manifest.model_dump(mode="json")
                    if hasattr(manifest, "model_dump")
                    else manifest
                )
                write_strict_json(self.staging / "manifest.json", payload)
        except Exception as write_exc:
            (self.staging / "failure.txt").write_text(
                f"{type(exc).__name__}: {exc}\n"
                f"while recording failure: {type(write_exc).__name__}: {write_exc}\n",
                encoding="utf-8",
            )
        try:
            os.rename(self.staging, self.failed)
        except OSError as rename_exc:
            raise ExecutionError("could not preserve the failed run directory") from rename_exc
        self._closed = True
        return self.failed
