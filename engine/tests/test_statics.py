"""spec/01 §12 tests 2-3 plus slope/aspect conventions for data/statics.py (Abby's Landscape, hazard/api.py)."""

from __future__ import annotations

import numpy as np
import pytest
import rasterio

from emberline.data import grid, statics
from emberline.data.config import load_scenario
from emberline.hazard import api
from emberline.hazard.geometry import GeometryCache, centers
from emberline.hazard.validation import validate_landscape

SCN = "eaton"
SDIR = statics.static_dir(SCN)
BUILT = all((SDIR / f"{n}.tif").exists() for n in (*statics.FLOAT_STATICS, "fbfm40"))
needs_statics = pytest.mark.skipif(not BUILT, reason="run python -m emberline.data.statics --scenario eaton")


def small_grid(n: int = 21) -> api.Grid:
    mg = statics.model_grid(load_scenario(SCN))
    return api.Grid(crs=mg.crs, x0=mg.x0, y0=mg.y0, cell_m=mg.cell_m, H=n, W=n, t_ref_s=mg.t_ref_s)


def plane(mg: api.Grid, a: float, b: float) -> np.ndarray:
    x, y = centers(mg)
    return a * (x - mg.x0) + b * (y - mg.y0)


@pytest.mark.parametrize("a,b,want", [(0.0, 0.05, 180.0), (0.05, 0.0, 270.0), (0.03, -0.05, None)])
def test_slope_aspect_plane_round_trips_through_the_bridge(a, b, want):
    mg = small_grid()
    slope, aspect = statics.slope_aspect(plane(mg, a, b), mg)
    np.testing.assert_allclose(slope, np.hypot(a, b), rtol=1e-9)
    if want is not None:  # grid-vs-true north convergence at Eaton is ~0.6 deg
        assert np.abs(aspect - want).max() < 1.5
    geo = GeometryCache(mg)  # hazard/bridge.py solve(): downslope = sin(a) true_east + cos(a) true_north
    r = np.deg2rad(aspect)
    d = np.sin(r)[None] * geo.true_east + np.cos(r)[None] * geo.true_north
    np.testing.assert_allclose(-slope * d[0], a, atol=1e-9)
    np.testing.assert_allclose(-slope * d[1], b, atol=1e-9)


def test_block_mode_picks_the_majority_and_breaks_ties_by_centre():
    a = np.array([[5, 5, 7], [5, 7, 7], [9, 9, 7]] * 1, dtype=np.int16)  # 7 x4, 5 x3, 9 x2
    assert statics.block_mode(a).tolist() == [[7]]
    tie = np.array([[91, 122, 122], [91, 122, 91], [3, 3, 3]], dtype=np.int16)  # x3 each, centre 122
    assert statics.block_mode(tie).tolist() == [[122]]  # not 91 just because it is smaller
    off = np.array([[1, 2, 2], [1, 3, 2], [1, 4, 5]], dtype=np.int16)  # 1,2 x3, centre 3 not tied
    assert statics.block_mode(off).tolist() == [[1]]


@needs_statics
def test_static_rasters_share_the_model_grid():  # 01 §12 #2
    g = load_scenario(SCN)["grid"]
    for path in sorted(SDIR.glob("*.tif")):
        with rasterio.open(path) as ds:
            assert (ds.height, ds.width) == (340, 340), path.name
            assert ds.crs.to_epsg() == 32611, path.name
            assert ds.transform == grid.transform(g), path.name


@needs_statics
def test_fuel_codes_are_supported_and_prefire():  # 01 §12 #3
    land = statics.load_landscape(SCN)
    hist = statics.fuel_histogram(land.fuel)
    print("FBFM40", [(k, f"{pct:.2f}%") for k, _, pct in hist])
    validate_landscape(land, api.ModelConfig(spotting=False))  # raises on any unsupported code
    with rasterio.open(SDIR / "fbfm40.tif") as ds:
        version = ds.tags()["landfire_version"]
    assert version in ("LF2024", "LF2023"), version  # never LF2025: it includes the Eaton burn
    assert f"LANDFIRE {version}" in load_scenario(SCN)["honesty"]["real"]


@needs_statics
def test_load_landscape_passes_abbys_validation():
    land = statics.load_landscape(SCN)
    from emberline.runtime import model_host
    from emberline.runtime.loop import load_scenario_dir

    ctx = load_scenario_dir(statics.scenario_dir(SCN))
    assert land.grid == model_host.model_grid(ctx)
    checked = validate_landscape(land, model_host.model_config(ctx))
    assert checked.fuel.dtype == np.int16 and checked.slope.dtype == np.float32
    assert checked.canopy_cover is not None and 0 <= checked.canopy_cover.min() <= checked.canopy_cover.max() <= 1


@needs_statics
def test_san_gabriel_front_faces_south_and_rises_north():
    land = statics.load_landscape(SCN)
    geo = GeometryCache(land.grid)
    box = (geo.lat >= 34.20) & (geo.lat <= 34.23) & (geo.lon >= -118.16) & (geo.lon <= -118.10)
    r = np.deg2rad(land.aspect[box])
    w = land.slope[box]
    mean = np.degrees(np.arctan2((w * np.sin(r)).sum(), (w * np.cos(r)).sum())) % 360
    assert 135 <= mean <= 225, mean
    south = land.elev[box & (geo.lat < 34.21)].mean()
    north = land.elev[box & (geo.lat > 34.22)].mean()
    assert north > south + 100, (south, north)
