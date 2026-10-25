"""Versioned subprocess/file adapter. Never builds code or downloads data implicitly."""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import numpy as np
from .geometry import north_to_south

PROTOCOL_VERSION = 1
MODEL_VERSION = "rust-levelset-pf-1"
REPO = Path(__file__).resolve().parents[3]
WORKSPACE = REPO / "physics/surface_v1"
CATALOG = WORKSPACE / "docs/spec/sb40.csv"
LEGACY_CATALOG = WORKSPACE / "docs/spec/fuel_models.csv"


@dataclass(frozen=True)
class BackendOptions:
    binary: Path | None = None
    workers: int = 2
    memory_budget_bytes: int = 1_073_741_824
    timeout_s: float = 120.0

    def executable(self):
        path = Path(
            self.binary
            or os.environ.get(
                "EMBERLINE_RUST_BRIDGE",
                WORKSPACE
                / "target/release"
                / ("fire-bridge.exe" if os.name == "nt" else "fire-bridge"),
            )
        )
        if not path.is_file():
            raise RuntimeError(
                f"Rust backend missing at {path}; run cargo build --manifest-path physics/surface_v1/Cargo.toml -p fire-bridge --release"
            )
        if (
            self.workers < 1
            or self.memory_budget_bytes < 1
            or not np.isfinite(self.timeout_s)
            or self.timeout_s <= 0
        ):
            raise ValueError("invalid backend execution limits")
        return path.resolve()


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def physics_fingerprint():
    paths = [
        WORKSPACE / name for name in ["Cargo.toml", "Cargo.lock", "rust-toolchain.toml"]
    ]
    for name in [
        "fire-types",
        "fire-behavior",
        "fire-propagation",
        "fire-io",
        "fire-bridge",
    ]:
        root = WORKSPACE / "crates" / name
        paths.append(root / "Cargo.toml")
        if (root / "build.rs").is_file():
            paths.append(root / "build.rs")
        paths.extend((root / "src").rglob("*.rs"))
    h = hashlib.sha256()
    for path in sorted(paths):
        h.update(path.relative_to(WORKSPACE).as_posix().encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def python_fingerprint():
    root = Path(__file__).parent
    h = hashlib.sha256()
    for name in sorted(
        [
            "api.py",
            "bridge.py",
            "cdf.py",
            "checkpoint.py",
            "filter.py",
            "geometry.py",
            "likelihoods.py",
            "model.py",
            "validation.py",
        ]
    ):
        h.update(name.encode())
        h.update(b"\0")
        h.update((root / name).read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def verify_backend(options):
    binary = options.executable()
    try:
        response = subprocess.run(
            [str(binary), "--describe"],
            capture_output=True,
            text=True,
            timeout=min(options.timeout_s, 15),
            check=True,
        )
        identity = json.loads(response.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "Rust bridge handshake failed; rebuild the versioned fire-bridge executable"
        ) from exc
    if (
        identity.get("protocol_version") != PROTOCOL_VERSION
        or identity.get("model_version") != MODEL_VERSION
        or identity.get("physics_sha256") != physics_fingerprint()
    ):
        raise RuntimeError(
            "Rust bridge/source version mismatch; rebuild fire-bridge before running forecasts"
        )
    return identity


def utc(grid, minutes):
    seconds = grid.t_ref_s + minutes * 60
    if not np.isfinite(seconds) or abs(seconds - round(seconds)) > 1e-4:
        raise ValueError("native environment times require whole UTC seconds")
    return datetime.fromtimestamp(round(seconds), tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def catalog_text():
    # Preserve the independent legacy reference catalog, then add all forty SB40 records.
    legacy = LEGACY_CATALOG.read_text().splitlines()
    return "\n".join(legacy[:14] + CATALOG.read_text().splitlines()[1:]) + "\n"


def solve(
    land,
    cfg,
    weather_changes,
    parameters,
    ignition_xy,
    ignition_min,
    anchors,
    seeds,
    issue_min,
    options,
    geometry,
):
    binary = options.executable()
    shape = (land.grid.H, land.grid.W)
    burnable = ~np.isin(land.fuel, [0, 92, 93, 98, 99])
    if not cfg.urban:
        burnable &= land.fuel != 91
    end_min = issue_min + cfg.horizon_min
    with tempfile.TemporaryDirectory(prefix="emberline-rust-") as temporary:
        root = Path(temporary)

        def write(name, values, dtype="<f8"):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            north_to_south(np.asarray(values), dtype).tofile(path)

        write("elevation.f64le", land.elev)
        write("fuel.u16le", np.where(burnable, land.fuel, 1), "<u2")
        write("valid.u8", np.ones(shape), "u1")
        write("burnable.u8", burnable, "u1")
        write(
            "initial.f64le", np.ones(shape)
        )  # Each member replaces this placeholder before prepare().
        bearing = np.deg2rad(land.aspect)
        downslope = (
            np.sin(bearing)[None] * geometry.true_east
            + np.cos(bearing)[None] * geometry.true_north
        )
        write("gx.f64le", -land.slope * downslope[0])
        write("gy.f64le", -land.slope * downslope[1])
        (root / "catalog.csv").write_text(catalog_text())
        times = {0.0}
        for effective, weather in weather_changes:
            if effective <= end_min:
                times.add(max(float(effective), 0.0))
            times.update(float(t) for t in weather.t_min if 0 < t <= end_min)
        frames = []
        waf = (
            np.where(land.canopy_cover > 0.5, 0.2, 0.4)
            if land.canopy_cover is not None
            else np.full(shape, 0.4)
        )
        for k, time_min in enumerate(sorted(times)):
            selected = max(
                (
                    i
                    for i, (effective, _) in enumerate(weather_changes)
                    if effective <= time_min
                ),
                default=0,
            )
            available, weather = weather_changes[selected]
            band = int(
                np.clip(
                    np.searchsorted(weather.t_min, time_min, side="right") - 1,
                    0,
                    len(weather.t_min) - 1,
                )
            )
            directory = f"weather/{k}"
            for name, values in [
                (
                    "wind_east",
                    (
                        weather.u10[band] * geometry.true_east[0]
                        + weather.v10[band] * geometry.true_north[0]
                    )
                    * waf,
                ),
                (
                    "wind_north",
                    (
                        weather.u10[band] * geometry.true_east[1]
                        + weather.v10[band] * geometry.true_north[1]
                    )
                    * waf,
                ),
                ("m1", np.full(shape, 0.04)),
                ("m10", np.full(shape, 0.05)),
                ("m100", np.full(shape, 0.06)),
                ("mlh", np.full(shape, 0.30)),
                ("mlw", np.full(shape, 0.60)),
            ]:
                write(f"{directory}/{name}.f64le", values)
            frames.append(
                {
                    "time_s": time_min * 60,
                    "observed_or_valid_utc": utc(land.grid, time_min),
                    "available_utc": utc(land.grid, available),
                    "directory": directory,
                }
            )
        grid = land.grid
        manifest = {
            "schema_version": 1,
            "incident_id": "hazardmodel",
            "start_utc": utc(grid, 0),
            "grid": {
                "width": grid.W,
                "height": grid.H,
                "cell_size_m": grid.cell_m,
                "x0_m": grid.x0,
                "y0_m": grid.y0 - grid.H * grid.cell_m,
                "crs": grid.crs,
            },
            "landscape": {
                "elevation": "elevation.f64le",
                "fuel_id": "fuel.u16le",
                "valid": "valid.u8",
                "burnable": "burnable.u8",
                "fuel_catalog": "catalog.csv",
            },
            "initial": {
                "phi": "initial.f64le",
                "perimeter_activity": "assume_entire_boundary_active",
            },
            "environment": {
                "wind_reference": "midflame_horizontal_heading_surface_speed",
                "interpolation": "hold_right_continuous",
                "frames": frames,
                "coverage_end_s": end_min * 60,
            },
            "simulation": {
                "horizons_s": [end_min * 60],
                "cfl": 0.4,
                "max_step_s": 30.0,
                "wind_limit": "effective_0p9_ir",
                "ellipse_max_lb": 8.0,
                "reinitialize": False,
                "solver": "level_set_lf_euler_v1",
            },
        }
        (root / "manifest.json").write_text(json.dumps(manifest, allow_nan=False))
        members = []
        for i, p in enumerate(parameters):
            member = {
                "ros_mult": float(np.exp(p[0])),
                "wind_speed_mult": float(np.exp(p[1])),
                "wind_dir_bias_deg": float(np.rad2deg(p[2])),
                "dfm_1h": float(np.exp(p[3])),
                "urban_ros": float(np.exp(p[4])),
                "ignition_xy": ignition_xy[i].tolist(),
                "ignition_time_s": ignition_min * 60,
                "anchors": [
                    {
                        "time_s": float(a["time_min"] * 60),
                        "index": int((grid.H - 1 - a["row"]) * grid.W + a["col"]),
                    }
                    for a in anchors[i]
                ],
            }
            seed = seeds[i]
            if seed is not None:
                write(f"seed-{i}-phi.f64le", seed["phi"])
                write(f"seed-{i}-arrival.f64le", seed["arrival"] * 60)
                member.update(
                    seed_phi=str(root / f"seed-{i}-phi.f64le"),
                    seed_arrival=str(root / f"seed-{i}-arrival.f64le"),
                    seed_time_s=seed["time_min"] * 60,
                )
            members.append(member)
        request = {
            "protocol_version": PROTOCOL_VERSION,
            "manifest": str(root / "manifest.json"),
            "gx": str(root / "gx.f64le"),
            "gy": str(root / "gy.f64le"),
            "issue_s": issue_min * 60,
            "until_s": end_min * 60,
            "out": str(root / "arrivals.f64le"),
            "workers": options.workers,
            "memory_budget_bytes": options.memory_budget_bytes,
            "urban": cfg.urban,
            "members": members,
        }
        (root / "request.json").write_text(json.dumps(request, allow_nan=False))
        try:
            completed = subprocess.run(
                [str(binary), str(root / "request.json")],
                capture_output=True,
                text=True,
                timeout=options.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"Rust forecast exceeded {options.timeout_s} seconds; model state was not committed"
            ) from exc
        if completed.returncode:
            raise RuntimeError(
                f"Rust propagation failed ({completed.returncode}): {completed.stderr.strip()}"
            )
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("malformed native bridge response") from exc
        output = root / "arrivals.f64le"
        if (
            response.get("protocol_version") != PROTOCOL_VERSION
            or response.get("model_version") != MODEL_VERSION
            or response.get("physics_sha256") != physics_fingerprint()
        ):
            raise RuntimeError(
                "native bridge version mismatch; upgrade adapter/backend together"
            )
        expected = len(members) * grid.H * grid.W
        if (
            response.get("members") != len(members)
            or response.get("cells") != grid.H * grid.W
            or response.get("arrival_path") != str(output)
            or not output.is_file()
            or output.stat().st_size != 8 * expected
        ):
            raise RuntimeError("native output shape/path mismatch")
        if sha256_file(output) != response.get("sha256"):
            raise RuntimeError("native output checksum mismatch")
        raw = np.fromfile(output, dtype="<f8").reshape(len(members), grid.H, grid.W)
        arrivals = np.ascontiguousarray(raw[:, ::-1, :] / 60, dtype=np.float32)
        if (
            np.isnan(arrivals).any()
            or np.isneginf(arrivals).any()
            or ((arrivals < ignition_min) & np.isfinite(arrivals)).any()
        ):
            raise RuntimeError("native backend returned invalid arrival times")
        return arrivals, response
