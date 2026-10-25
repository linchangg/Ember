"""Compatibility helpers for Cody's current HRRR files, without editing runtime ownership."""

from __future__ import annotations
import numpy as np
from .api import Grid, Weather
from .validation import validate_weather


def weather_from_hrrr(dataset, grid: Grid, issue_ms: int) -> tuple[Weather, dict]:
    """Select only already released fxx bands; valid times may be in the future.

    Caller chooses the latest eligible initialization, opens its NetCDF, calls this
    function, and passes the Weather to update_weather() immediately before step().
    The returned audit record must accompany the runtime's cache provenance.
    """
    if not isinstance(issue_ms, (int, np.integer)):
        raise ValueError("issue_ms must be integer UTC milliseconds")
    if (
        dataset.attrs.get("rotation") != "earth-relative"
        or dataset.attrs.get("crs") != grid.crs
    ):
        raise ValueError("HRRR wind reference or CRS differs from HazardModel grid")
    for name in ("u10", "v10"):
        if dataset[name].dims != ("fxx", "y", "x"):
            raise ValueError("HRRR fields must be fxx,y,x with north-first rows")
    expected_x = grid.x0 + (np.arange(grid.W) + 0.5) * grid.cell_m
    expected_y = grid.y0 - (np.arange(grid.H) + 0.5) * grid.cell_m
    if (
        dataset.sizes.get("x") != grid.W
        or dataset.sizes.get("y") != grid.H
        or not np.allclose(dataset.x.values, expected_x, atol=0.01, rtol=0)
        or not np.allclose(dataset.y.values, expected_y, atol=0.01, rtol=0)
    ):
        raise ValueError("HRRR coordinates do not align with the model cell centers")
    fxx = np.asarray(dataset.fxx.values, dtype=np.float64)
    available = np.atleast_1d(dataset.attrs["available_ms_by_fxx"])
    init_ms = int(dataset.attrs["init_ms"])
    if (
        available.shape != fxx.shape
        or not np.isfinite(available).all()
        or not np.isfinite(fxx).all()
        or (np.diff(fxx) <= 0).any()
        or (fxx < 0).any()
        or init_ms > issue_ms
        or (available < init_ms).any()
    ):
        raise ValueError("invalid HRRR availability/hour metadata")
    keep = np.flatnonzero(available <= issue_ms)
    if not len(keep):
        raise ValueError(
            "no HRRR band was available at this issue; try an older initialization"
        )
    times = (init_ms + fxx[keep] * 3_600_000 - grid.t_ref_s * 1000) / 60_000
    selected = dataset.isel(fxx=keep)
    weather = validate_weather(
        Weather(
            times,
            selected.u10.values,
            selected.v10.values,
            selected.temp_c.values,
            selected.rh.values,
        ),
        (grid.H, grid.W),
    )
    provenance = {
        "source": str(dataset.attrs.get("source", "HRRR")),
        "init_ms": init_ms,
        "issue_ms": int(issue_ms),
        "selected_fxx": fxx[keep].tolist(),
        "available_ms_by_fxx": available[keep].astype(np.int64).tolist(),
        "valid_min_range": [float(times[0]), float(times[-1])],
        "outside_selected_valid_range": "hold nearest available band",
    }
    return weather, provenance
