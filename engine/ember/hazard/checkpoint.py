"""Self-contained, versioned NPZ checkpoints; no pickle or machine-local file paths."""

from __future__ import annotations
from dataclasses import asdict
import hashlib
import io
import json
import zipfile
import numpy as np
from ember.contracts import ObservationRecord
from .api import Grid, Landscape, Weather, ModelConfig, Ignition
from .bridge import (
    MODEL_VERSION,
    PROTOCOL_VERSION,
    catalog_text,
    sha256_file,
    verify_backend,
    python_fingerprint,
)
import importlib.metadata
import platform

FORMAT = "ember-rust-hazard-state-v1"


def environment():
    return {
        "platform": platform.system() + "/" + platform.machine(),
        **{
            name: importlib.metadata.version(name)
            for name in ["numpy", "scipy", "pyproj", "pydantic"]
        },
    }


def save(model):
    arrays = {
        "theta": model._theta,
        "weights": model._weights,
        "ignition_xy": model._ignition_xy,
        "arrivals": model._arrivals,
        "mask": model._mask.astype(np.uint8),
        "ignition_points": model.ign.points_xy,
    }
    landscape = []
    for name in [
        "fuel",
        "slope",
        "aspect",
        "elev",
        "canopy_cover",
        "canopy_height",
        "building_frac",
    ]:
        value = getattr(model.land, name)
        if value is not None:
            arrays[f"land_{name}"] = value
            landscape.append(name)
    changes = []
    all_weather = model._weather_changes + (
        [(0, model._pending_weather)] if model._pending_weather is not None else []
    )
    for k, (effective, weather) in enumerate(all_weather):
        for name in ["t_min", "u10", "v10", "temp_c", "rh"]:
            arrays[f"weather_{k}_{name}"] = getattr(weather, name)
        changes.append(effective)
    seed_times = []
    for k, seed in enumerate(model._seeds):
        seed_times.append(None if seed is None else seed["time_min"])
        if seed is not None:
            arrays[f"seed_phi_{k}"] = seed["phi"]
            arrays[f"seed_arrival_{k}"] = seed["arrival"]
    metadata = {
        "format": FORMAT,
        "model_version": MODEL_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "native_binary_sha256": sha256_file(model.options.executable()),
        "physics_sha256": model.backend_identity["physics_sha256"],
        "python_sha256": model._python_fingerprint,
        "environment": environment(),
        "catalog_sha256": hashlib.sha256(catalog_text().encode()).hexdigest(),
        "grid": asdict(model.land.grid),
        "config": asdict(model.cfg),
        "ignition": {"t_min": model.ign.t_min, "radius_m": model.ign.radius_m},
        "landscape_fields": landscape,
        "weather_effective": changes,
        "pending_weather": model._pending_weather is not None,
        "seed_times": seed_times,
        "anchors": model._anchors,
        "history": [o.model_dump(mode="json") for o in model._history],
        "seen": model._seen,
        "last_issue": model._last_issue,
        "array_hashes": {
            k: hashlib.sha256(np.ascontiguousarray(v).tobytes()).hexdigest()
            for k, v in arrays.items()
        },
    }
    arrays["metadata"] = np.frombuffer(
        json.dumps(metadata, sort_keys=True, allow_nan=False).encode(), dtype=np.uint8
    )
    stream = io.BytesIO()
    np.savez_compressed(stream, **arrays)
    return stream.getvalue()


def load(blob, options):
    from .model import RustHazardModel

    if not isinstance(blob, bytes):
        raise ValueError("checkpoint must be bytes")
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            if (
                sum(info.file_size for info in archive.infolist())
                > options.memory_budget_bytes * 2
            ):
                raise ValueError("checkpoint exceeds decompressed memory budget")
        with np.load(io.BytesIO(blob), allow_pickle=False) as archive:
            metadata = json.loads(archive["metadata"].tobytes())
            arrays = {
                name: archive[name].copy()
                for name in archive.files
                if name != "metadata"
            }
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise ValueError("invalid HazardModel checkpoint") from exc
    if (
        metadata.get("format") != FORMAT
        or metadata.get("model_version") != MODEL_VERSION
        or metadata.get("protocol_version") != PROTOCOL_VERSION
    ):
        raise ValueError("checkpoint version mismatch; explicit migration is required")
    identity = verify_backend(options)
    if (
        metadata.get("physics_sha256") != identity["physics_sha256"]
        or metadata.get("python_sha256") != python_fingerprint()
        or metadata.get("environment") != environment()
        or metadata.get("catalog_sha256")
        != hashlib.sha256(catalog_text().encode()).hexdigest()
    ):
        raise ValueError(
            "checkpoint backend/catalog differs; use the original build or restart"
        )
    expected = metadata.get("array_hashes", {})
    if set(arrays) != set(expected) or any(
        hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest() != expected[k]
        for k, a in arrays.items()
    ):
        raise ValueError("checkpoint array hash mismatch")
    cfgdata = metadata["config"]
    cfgdata["quantiles"] = tuple(cfgdata["quantiles"])
    cfg = ModelConfig(**cfgdata)
    grid = Grid(**metadata["grid"])
    land = Landscape(
        grid=grid,
        **{name: arrays[f"land_{name}"] for name in metadata["landscape_fields"]},
    )
    changes = []
    for k, effective in enumerate(metadata["weather_effective"]):
        changes.append(
            (
                effective,
                Weather(
                    **{
                        name: arrays[f"weather_{k}_{name}"]
                        for name in ["t_min", "u10", "v10", "temp_c", "rh"]
                    }
                ),
            )
        )
    if not changes:
        raise ValueError("checkpoint has no weather")
    pending = changes.pop()[1] if metadata["pending_weather"] else None
    model = RustHazardModel(options)
    model.init(
        land,
        changes[0][1],
        cfg,
        Ignition(points_xy=arrays["ignition_points"], **metadata["ignition"]),
    )
    shape = (cfg.n_members, grid.H, grid.W)
    theta, weights, arrivals, xy = (
        arrays["theta"],
        arrays["weights"],
        arrays["arrivals"],
        arrays["ignition_xy"],
    )
    if (
        theta.shape != (cfg.n_members, 5)
        or not np.isfinite(theta).all()
        or weights.shape != (cfg.n_members,)
        or not np.isfinite(weights).all()
        or (weights < 0).any()
        or abs(weights.sum() - 1) > 1e-10
        or arrivals.shape != shape
        or np.isnan(arrivals).any()
        or np.isneginf(arrivals).any()
        or xy.shape != (cfg.n_members, 2)
        or not np.isfinite(xy).all()
    ):
        raise ValueError("invalid checkpoint posterior shapes or values")
    if (
        len(metadata["anchors"]) != cfg.n_members
        or len(metadata["seed_times"]) != cfg.n_members
        or arrays["mask"].shape != shape[1:]
    ):
        raise ValueError("invalid checkpoint member state")
    model._theta = theta
    model._weights = weights
    model._arrivals = arrivals
    model._ignition_xy = xy
    model._anchors = metadata["anchors"]
    model._seeds = []
    for k, t in enumerate(metadata["seed_times"]):
        model._seeds.append(
            None
            if t is None
            else {
                "time_min": t,
                "phi": arrays[f"seed_phi_{k}"],
                "arrival": arrays[f"seed_arrival_{k}"],
            }
        )
    model._history = [ObservationRecord.model_validate(o) for o in metadata["history"]]
    model._seen = metadata["seen"]
    model._last_issue = metadata["last_issue"]
    model._mask = arrays["mask"].astype(bool)
    model._weather_changes = changes
    model._pending_weather = pending
    if model._last_issue is not None:
        limit = round((grid.t_ref_s + model._last_issue * 60) * 1000)
        if any(o.available_sim_ms > limit for o in model._history):
            raise ValueError("checkpoint includes future observations")
    return model
