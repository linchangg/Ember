#!/usr/bin/env python3
"""Generate the synthetic 64x64 FM1 circle bundle from the specification."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "docs" / "spec" / "fuel_models.csv"


def write_f64le(path: Path, values: list[float]) -> None:
    path.write_bytes(struct.pack("<" + "d" * len(values), *values))


def write_u16le(path: Path, values: list[int]) -> None:
    path.write_bytes(struct.pack("<" + "H" * len(values), *values))


def write_u8(path: Path, values: list[int]) -> None:
    path.write_bytes(bytes(values))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=ROOT / "fixtures" / "generated" / "fm1")
    args = p.parse_args()
    out: Path = args.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    width = height = 64
    dx = 10.0
    n = width * height
    elev = [0.0] * n
    fuel = [1] * n
    valid = [1] * n
    burnable = [1] * n
    xc, yc, r0 = 320.0, 320.0, 45.0
    phi = []
    for row in range(height):
        for col in range(width):
            x = 0.0 + (col + 0.5) * dx
            y = 0.0 + (row + 0.5) * dx
            phi.append(math.hypot(x - xc, y - yc) - r0)
    write_f64le(out / "elevation.f64le", elev)
    write_u16le(out / "fuel_id.u16le", fuel)
    write_u8(out / "valid.u8", valid)
    write_u8(out / "burnable.u8", burnable)
    write_f64le(out / "initial_phi.f64le", phi)
    shutil.copy(CATALOG, out / "fuel_models.csv")
    moist = {
        "m1": 0.05, "m10": 0.07, "m100": 0.09, "mlh": 0.60, "mlw": 0.90,
        "wind_east": 0.0, "wind_north": 0.0,
    }
    for dirname, time_s in (("000000", 0.0), ("000600", 600.0)):
        d = out / "weather" / dirname
        d.mkdir(parents=True)
        for name in ("wind_east", "wind_north", "m1", "m10", "m100", "mlh", "mlw"):
            write_f64le(d / f"{name}.f64le", [moist[name]] * n)
    manifest = {
        "schema_version": 1,
        "incident_id": "synthetic-fm1",
        "start_utc": "2026-01-01T00:00:00Z",
        "grid": {
            "width": 64, "height": 64, "cell_size_m": 10.0,
            "x0_m": 0.0, "y0_m": 0.0, "crs": "LOCAL_METRIC_TEST",
        },
        "landscape": {
            "elevation": "elevation.f64le",
            "fuel_id": "fuel_id.u16le",
            "valid": "valid.u8",
            "burnable": "burnable.u8",
            "fuel_catalog": "fuel_models.csv",
        },
        "initial": {
            "phi": "initial_phi.f64le",
            "perimeter_activity": "assume_entire_boundary_active",
        },
        "environment": {
            "wind_reference": "midflame_horizontal_heading_surface_speed",
            "interpolation": "hold_right_continuous",
            "frames": [
                {
                    "time_s": 0.0,
                    "observed_or_valid_utc": "2026-01-01T00:00:00Z",
                    "available_utc": "2026-01-01T00:00:00Z",
                    "directory": "weather/000000",
                },
                {
                    "time_s": 600.0,
                    "observed_or_valid_utc": "2026-01-01T00:10:00Z",
                    "available_utc": "2026-01-01T00:00:00Z",
                    "directory": "weather/000600",
                },
            ],
            "coverage_end_s": 3600.0,
        },
        "simulation": {
            "horizons_s": [300.0, 600.0, 1800.0, 3600.0],
            "cfl": 0.4,
            "max_step_s": 30.0,
            "wind_limit": "effective_0p9_ir",
            "ellipse_max_lb": 8.0,
            "reinitialize": False,
            "solver": "level_set_lf_euler_v1",
        },
    }
    man_path = out / "manifest.json"
    man_path.write_text(json.dumps(manifest, indent=2) + "\n")
    inventory = []
    for path in sorted(out.rglob("*")):
        if path.is_file():
            rel = path.relative_to(out).as_posix()
            inventory.append({"path": rel, "sha256": sha256_file(path), "bytes": path.stat().st_size})
    (out / "checksums.json").write_text(json.dumps(inventory, indent=2) + "\n")
    print(f"wrote fixture to {out}")


if __name__ == "__main__":
    main()
