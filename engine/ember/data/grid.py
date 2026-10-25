"""Shared model-grid helpers (01 §1). Every function takes `g`, the `grid` dict from scenario.yaml."""

from __future__ import annotations

import argparse
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr
from affine import Affine
from pyproj import Transformer

from ember.data.config import load_scenario, scenario_dir


def transform(g: dict) -> Affine:
    return Affine(g["cell_m"], 0, g["x0"], 0, -g["cell_m"], g["y0"])


def cell_centers(g: dict) -> tuple[np.ndarray, np.ndarray]:
    c = g["cell_m"]
    xs = g["x0"] + c / 2 + c * np.arange(g["nx"], dtype=np.float64)
    ys = g["y0"] - c / 2 - c * np.arange(g["ny"], dtype=np.float64)
    return xs, ys


@lru_cache(maxsize=8)
def _to_lonlat(crs: str) -> Transformer:
    return Transformer.from_crs(crs, "EPSG:4326", always_xy=True)


@lru_cache(maxsize=8)
def _from_lonlat(crs: str) -> Transformer:
    return Transformer.from_crs("EPSG:4326", crs, always_xy=True)


def xy_to_lonlat(x, y, g: dict):
    return _to_lonlat(g["crs"]).transform(x, y)


def lonlat_to_xy(lon, lat, g: dict):
    return _from_lonlat(g["crs"]).transform(lon, lat)


def xy_to_rowcol(x, y, g: dict):
    """Integer floor of the cell index; -1 (both row and col) for points outside the grid."""
    c = g["cell_m"]
    col = np.floor((np.asarray(x, dtype=np.float64) - g["x0"]) / c).astype(np.int64)
    row = np.floor((g["y0"] - np.asarray(y, dtype=np.float64)) / c).astype(np.int64)
    outside = (col < 0) | (col >= g["nx"]) | (row < 0) | (row >= g["ny"])
    row = np.where(outside, -1, row)
    col = np.where(outside, -1, col)
    if row.ndim == 0:
        return int(row), int(col)
    return row, col


def corners_lonlat(g: dict) -> dict:
    x0, y0 = g["x0"], g["y0"]
    x1, y1 = x0 + g["nx"] * g["cell_m"], y0 - g["ny"] * g["cell_m"]
    pts = {"tl": (x0, y0), "tr": (x1, y0), "br": (x1, y1), "bl": (x0, y1)}
    return {k: [float(v) for v in xy_to_lonlat(x, y, g)] for k, (x, y) in pts.items()}


def bbox_lonlat(g: dict) -> tuple[float, float, float, float]:
    c = corners_lonlat(g).values()
    lons, lats = [p[0] for p in c], [p[1] for p in c]
    return min(lons), min(lats), max(lons), max(lats)


def template(g: dict) -> xr.DataArray:
    xs, ys = cell_centers(g)
    da = xr.DataArray(
        np.zeros((g["ny"], g["nx"]), dtype=np.float32),
        dims=("y", "x"),
        coords={"y": ys, "x": xs},
    )
    return da.rio.write_crs(g["crs"]).rio.write_transform(transform(g))


def grid_json(g: dict) -> dict:
    t = transform(g)
    return {
        "crs": g["crs"],
        "cell_m": g["cell_m"],
        "nx": g["nx"],
        "ny": g["ny"],
        "x0": g["x0"],
        "y0": g["y0"],
        "transform": [t.a, t.b, t.c, t.d, t.e, t.f],
        "corners": corners_lonlat(g),
        "bbox": list(bbox_lonlat(g)),
    }


def write_grid_json(scn: str) -> Path:
    g = load_scenario(scn)["grid"]
    path = scenario_dir(scn) / "grid.json"
    path.write_text(json.dumps(grid_json(g), indent=2) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="eaton")
    args = ap.parse_args()
    p = write_grid_json(args.scenario)
    print(p)
    for k, v in json.loads(p.read_text(encoding="utf-8"))["corners"].items():
        print(f"{k}: {v}")
