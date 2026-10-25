"""PhysicsCache: per-cycle physics outputs, checkpoints and briefings. spec/03 §4–5, spec/00 §4."""

from __future__ import annotations

import hashlib
import io
import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = REPO / "cache"


@dataclass
class PhysicsOutput:
    cycle: dict  # Cycle fields except "source" (set at publish)
    zone_arr: np.ndarray  # (N, Z) float32
    area_arr: np.ndarray  # (N, 1) float32
    weights: np.ndarray  # (N,) float64
    obs_status: list[dict]  # [{obs_id, status: "assimilated"|"rejected", influence}]
    scans: list[dict]  # ScanEvent dicts
    events: list[dict]  # ActivityEvent dicts
    key: str
    cached: bool = False


def cycle_key(
    prev_key: str | None,
    new_obs_ids: list[str],
    model_version: str,
    config_hash: str | None,
    seed: int,
    filter_mode: str,
) -> str:
    payload = [prev_key, sorted(new_obs_ids), model_version, config_hash, seed, filter_mode]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode("utf-8")).hexdigest()


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _write_json(path: Path, obj: object) -> None:
    _write_atomic(path, json.dumps(obj, separators=(",", ":")).encode("utf-8"))


def cycle_dir(root: Path, scn: str, branch: str, k_ms: int) -> Path:
    return Path(root) / scn / branch / str(k_ms)


def save_cycle(root: Path, scn: str, branch: str, k_ms: int, out: PhysicsOutput) -> None:
    d = cycle_dir(root, scn, branch, k_ms)
    buf = io.BytesIO()
    np.savez_compressed(buf, zone_arr=out.zone_arr, area_arr=out.area_arr, weights=out.weights)
    _write_atomic(d / "zone_arrivals.npz", buf.getvalue())
    # physics.json last: its key is what marks the cycle as complete.
    _write_json(
        d / "physics.json",
        {"cycle": out.cycle, "obs_status": out.obs_status, "scans": out.scans, "events": out.events, "key": out.key},
    )


def load_cycle(root: Path, scn: str, branch: str, k_ms: int, expect_key: str) -> PhysicsOutput | None:
    """None if missing or stale (physics.json key != expect_key)."""
    d = cycle_dir(root, scn, branch, k_ms)
    phys_path, npz_path = d / "physics.json", d / "zone_arrivals.npz"
    if not phys_path.exists() or not npz_path.exists():
        return None
    phys = json.loads(phys_path.read_text(encoding="utf-8"))
    if phys.get("key") != expect_key:
        return None
    with np.load(npz_path) as z:
        zone_arr, area_arr, weights = z["zone_arr"], z["area_arr"], z["weights"]
    return PhysicsOutput(
        cycle=phys["cycle"],
        zone_arr=zone_arr,
        area_arr=area_arr,
        weights=weights,
        obs_status=phys["obs_status"],
        scans=phys["scans"],
        events=phys["events"],
        key=phys["key"],
        cached=True,
    )


def _checkpoint_dir(root: Path, scn: str, branch: str) -> Path:
    return Path(root) / scn / branch / "checkpoints"


def save_checkpoint(root: Path, scn: str, branch: str, k_ms: int, blob: bytes) -> None:
    _write_atomic(_checkpoint_dir(root, scn, branch) / f"{k_ms}.npz", blob)


def latest_checkpoint(root: Path, scn: str, branch: str, at_or_before_ms: int) -> tuple[int, bytes] | None:
    d = _checkpoint_dir(root, scn, branch)
    if not d.is_dir():
        return None
    ks = [int(p.stem) for p in d.glob("*.npz") if p.stem.isdigit() and int(p.stem) <= at_or_before_ms]
    if not ks:
        return None
    k = max(ks)
    return k, (d / f"{k}.npz").read_bytes()


def write_index(root: Path, scn: str, branch: str) -> Path:
    """00 §4 index.json; one entry per numeric k dir that has bundle.json, sorted by k."""
    d = Path(root) / scn / branch
    ks = sorted(int(p.name) for p in d.iterdir() if p.is_dir() and p.name.isdigit() and (p / "bundle.json").exists())
    index = {
        "scenario_id": scn,
        "branch_id": branch,
        "cycles": [{"knowledge_sim_ms": k, "bundle": f"cache/{scn}/{branch}/{k}/bundle.json"} for k in ks],
    }
    path = d / "index.json"
    _write_json(path, index)
    return path


def briefing_path(root: Path, scn: str, input_hash: str) -> Path:
    return Path(root) / scn / "briefings" / f"{input_hash}.json"


def save_briefing(root: Path, scn: str, input_hash: str, briefing: dict) -> None:
    _write_json(briefing_path(root, scn, input_hash), briefing)


def load_briefing(root: Path, scn: str, input_hash: str) -> dict | None:
    path = briefing_path(root, scn, input_hash)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
