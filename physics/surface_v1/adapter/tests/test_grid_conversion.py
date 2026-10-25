"""Grid orientation, cell centers, and CRS checks."""

from __future__ import annotations

import numpy as np
import pytest

from adapter.conversions import (
    AppGrid,
    RustGrid,
    app_cell_centers,
    app_grid_to_rust,
    assert_grids_correspond,
    convert_elevation,
    convert_fuel_ids,
    convert_mask,
    convert_moisture_fractions,
    convert_wind_components,
    flip_rows_app_to_rust,
    flip_rows_rust_to_app,
    reject_humidity_as_moisture,
    rust_cell_centers,
    rust_grid_to_app,
    validate_crs,
    wind_components_from_direction,
)
from adapter.errors import UnsupportedMapping, ValidationError

EATON = AppGrid(
    crs="EPSG:32611",
    x0_m=381480.0,
    y0_m=3800280.0,
    cell_m=90.0,
    height=340,
    width=340,
)


def _small(**overrides: object) -> AppGrid:
    values = dict(
        crs="EPSG:32611",
        x0_m=500_000.0,
        y0_m=3_800_000.0,
        cell_m=90.0,
        height=4,
        width=5,
    )
    values.update(overrides)
    return AppGrid(**values)  # type: ignore[arg-type]


def test_asymmetric_raster_corners_reverse_rows_only() -> None:
    grid = _small()
    raster = np.arange(grid.height * grid.width, dtype=np.int32).reshape(grid.height, grid.width)
    raster[0, 0] = 101
    raster[0, -1] = 102
    raster[-1, 0] = 103
    raster[-1, -1] = 104
    flipped = flip_rows_app_to_rust(raster)
    assert flipped.flags.c_contiguous
    assert flipped.dtype == raster.dtype
    assert flipped[0, 0] == 103
    assert flipped[0, -1] == 104
    assert flipped[-1, 0] == 101
    assert flipped[-1, -1] == 102
    # Columns are not reversed or transposed.
    assert np.array_equal(flipped[-1, :], raster[0, :])
    assert np.array_equal(flipped[0, :], raster[-1, :])


def test_forward_and_inverse_conversion() -> None:
    grid = _small()
    raster = np.array(
        [[1.0, 2.0, 3.0, 4.0, 5.0], [6.0, 7.0, 8.0, 9.0, 10.0], [11.0, 12.0, 13.0, 14.0, 15.0], [16.0, 17.0, 18.0, 19.0, 20.0]],
        dtype=np.float32,
    )
    assert raster.shape == (grid.height, grid.width)
    rust = flip_rows_app_to_rust(raster)
    back = flip_rows_rust_to_app(rust)
    assert back.flags.c_contiguous
    assert np.array_equal(back, raster)
    restored = rust_grid_to_app(app_grid_to_rust(grid))
    assert restored == grid


def test_noncontiguous_input_keeps_values_and_becomes_c_contiguous() -> None:
    wide = np.arange(40, dtype=np.int16).reshape(4, 10)
    view = wide[:, :5]
    assert not view.flags.c_contiguous
    flipped = flip_rows_app_to_rust(view)
    assert flipped.flags.c_contiguous
    assert np.array_equal(flipped, np.flipud(np.ascontiguousarray(view)))


def test_cell_centers_agree_after_row_reversal() -> None:
    grid = _small(height=5, width=4, cell_m=90.0)
    rust = app_grid_to_rust(grid)
    app_x, app_y = app_cell_centers(grid)
    rust_x, rust_y = rust_cell_centers(rust)
    assert np.array_equal(app_x, rust_x)
    for row in range(grid.height):
        assert rust_y[grid.height - 1 - row] == pytest.approx(app_y[row])
    # Spec/01 cell-center formula for a 90 m outer-edge origin.
    assert app_x[0] == pytest.approx(grid.x0_m + 45.0)
    assert app_y[0] == pytest.approx(grid.y0_m - 45.0)
    assert app_y[2] == pytest.approx(grid.y0_m - 45.0 - 180.0)


def test_90m_eaton_outer_edge_and_small_raster() -> None:
    rust = app_grid_to_rust(EATON)
    assert rust.cell_size_m == 90.0
    assert rust.x0_m == 381480.0
    assert rust.y0_m == pytest.approx(3800280.0 - 340 * 90.0)
    app_x, app_y = app_cell_centers(EATON)
    rust_x, rust_y = rust_cell_centers(rust)
    assert app_x[0] == pytest.approx(381480.0 + 45.0)
    assert app_y[0] == pytest.approx(3800280.0 - 45.0)
    assert rust_x[10] == pytest.approx(app_x[10])
    assert rust_y[339] == pytest.approx(app_y[0])
    assert rust_y[0] == pytest.approx(app_y[339])
    small = _small(height=3, width=3, cell_m=90.0)
    values = np.arange(9, dtype=np.float64).reshape(3, 3)
    flipped = flip_rows_app_to_rust(values)
    assert flipped[2, 1] == values[0, 1]
    assert app_grid_to_rust(small).cell_size_m == 90.0


def test_masks_and_elevation_preserve_values_through_reversal() -> None:
    grid = _small()
    mask = np.zeros((grid.height, grid.width), dtype=np.uint8)
    mask[0, 0] = 1
    mask[-1, -1] = 1
    flipped = convert_mask(mask, grid, "valid")
    assert flipped.dtype == np.uint8
    assert flipped.flags.c_contiguous
    assert flipped[-1, 0] == 1
    assert flipped[0, -1] == 1
    assert int(flipped.sum()) == 2
    elevation = np.array(
        [[np.float32(0.1), 1, 2, 3, 4], [5, 6, 7, 8, 9], [10, 11, 12, 13, 14], [15, 16, 17, 18, 19]],
        dtype=np.float32,
    )
    converted = convert_elevation(elevation, grid)
    assert converted.dtype == np.dtype("<f8")
    assert converted[-1, 0] == np.float64(elevation[0, 0])
    assert np.array_equal(flip_rows_rust_to_app(converted).astype(np.float32), elevation)


def test_wind_components_are_not_negated_when_rows_reverse() -> None:
    grid = _small()
    east = np.full((grid.height, grid.width), 3.5, dtype=np.float64)
    north = np.full((grid.height, grid.width), -1.25, dtype=np.float64)
    east[0, 0] = 9.0
    north[0, 4] = 4.0
    out_east, out_north = convert_wind_components(
        east, north, grid, components_are_midflame=True
    )
    assert out_east[-1, 0] == 9.0
    assert out_north[-1, 4] == 4.0
    assert np.all(out_east[-1, 1:] == 3.5)
    assert np.all(out_north[0, :] == -1.25)
    with pytest.raises(UnsupportedMapping):
        convert_wind_components(east, north, grid, components_are_midflame=False)


def test_invalid_dimensions_and_mismatched_shapes() -> None:
    with pytest.raises(ValidationError):
        app_grid_to_rust(_small(height=2, width=5))
    with pytest.raises(ValidationError):
        app_grid_to_rust(_small(cell_m=0.0))
    with pytest.raises(ValidationError):
        app_grid_to_rust(_small(cell_m=float("nan")))
    grid = _small()
    with pytest.raises(ValidationError, match="shape"):
        convert_elevation(np.zeros((grid.height, grid.width + 1)), grid)
    with pytest.raises(ValidationError):
        flip_rows_app_to_rust(np.arange(12))
    other = RustGrid(
        crs=grid.crs,
        x0_m=grid.x0_m,
        y0_m=grid.y0_m,
        cell_size_m=30.0,
        height=grid.height,
        width=grid.width,
    )
    with pytest.raises(ValidationError) as caught:
        assert_grids_correspond(grid, other)
    assert caught.value.code == "grid_misaligned"


def test_unsupported_and_mismatched_crs() -> None:
    for crs in ("EPSG:4326", "WGS84", "latlon", "EPSG:4269"):
        with pytest.raises(ValidationError, match="geographic|unsupported"):
            validate_crs(crs)
    with pytest.raises(ValidationError):
        app_grid_to_rust(_small(crs="EPSG:32610"))
    app = _small()
    rust = app_grid_to_rust(app)
    mismatched = RustGrid(
        crs="EPSG:32610",
        x0_m=rust.x0_m,
        y0_m=rust.y0_m,
        cell_size_m=rust.cell_size_m,
        height=rust.height,
        width=rust.width,
    )
    with pytest.raises(ValidationError) as caught:
        assert_grids_correspond(app, mismatched)
    assert caught.value.code == "grid_misaligned"
    with pytest.raises(ValidationError, match="outer edge"):
        app_grid_to_rust(_small(origin="cell_center"))


def test_fuel_passthrough_and_moisture_do_not_invent_mappings() -> None:
    grid = _small(height=3, width=3)
    fuel = np.ones((3, 3), dtype=np.int16)
    valid = np.ones((3, 3), dtype=np.uint8)
    burnable = np.ones((3, 3), dtype=np.uint8)
    fuel[0, 0] = 91
    burnable[0, 0] = 0
    converted = convert_fuel_ids(
        fuel, valid, burnable, grid, policy="passthrough_catalog_id", catalog_ids={1}
    )
    assert converted[-1, 0] == 91
    assert converted[0, 0] == 1
    burnable_urban = burnable.copy()
    burnable_urban[0, 0] = 1
    with pytest.raises(ValidationError, match="FBFM40"):
        convert_fuel_ids(
            fuel, valid, burnable_urban, grid, policy="passthrough_catalog_id", catalog_ids={1}
        )
    with pytest.raises(UnsupportedMapping):
        convert_fuel_ids(fuel, valid, burnable, grid, policy="fbfm40_to_catalog", catalog_ids={1})
    moist = convert_moisture_fractions(0.06, 0.07, 0.08, 0.6, 0.9, grid)
    assert moist["m1"].shape == (3, 3)
    assert np.all(moist["mlw"] == 0.9)
    with pytest.raises(ValidationError):
        convert_moisture_fractions(-0.01, 0.07, 0.08, 0.6, 0.9, grid)
    with pytest.raises(UnsupportedMapping):
        reject_humidity_as_moisture(0.15)
    east, north = wind_components_from_direction(2.0, 90.0, speed_is_midflame=True)
    assert east == pytest.approx(2.0)
    assert north == pytest.approx(0.0, abs=1e-12)
    with pytest.raises(UnsupportedMapping):
        wind_components_from_direction(2.0, 90.0, speed_is_midflame=False)


def test_research_metric_crs_is_opt_in_and_not_reprojected() -> None:
    grid = _small(crs="LOCAL_METRIC_TEST")
    with pytest.raises(ValidationError):
        app_grid_to_rust(grid)
    rust = app_grid_to_rust(grid, allow_research_metric_crs=True)
    assert rust.crs == "LOCAL_METRIC_TEST"
    assert rust.x0_m == grid.x0_m
