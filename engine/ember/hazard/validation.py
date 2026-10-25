"""Strict boundary checks so future runtime/frontend changes fail visibly."""

from __future__ import annotations
from dataclasses import replace
import numpy as np
from ember.contracts import QUANTILES
from .api import Weather
from .bridge import CATALOG, LEGACY_CATALOG
import csv


def validate_config(cfg):
    if not isinstance(cfg.n_members, int) or not 1 <= cfg.n_members <= 512:
        raise ValueError("n_members must be 1..512")
    for name in ["horizon_min", "out_step_min", "cycle_min", "history_min"]:
        value = getattr(cfg, name)
        if not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if (
        cfg.horizon_min % 2
        or cfg.horizon_min > 508
        or cfg.horizon_min % cfg.out_step_min
    ):
        raise ValueError(
            "horizon must be even, <=508 minutes, and divisible by out_step_min"
        )
    if tuple(cfg.quantiles) != QUANTILES:
        raise ValueError("quantiles differ from frozen frontend contract")
    if cfg.filter not in {"none", "pf", "reinit"}:
        raise ValueError("unsupported filter")
    if (
        not 0 < cfg.ess_target <= 1
        or not 0 < cfg.jitter_a <= 1
        or not 0 <= cfg.reseed_frac <= 1
    ):
        raise ValueError("invalid filter settings")
    if not isinstance(cfg.seed, int) or cfg.seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    if cfg.spotting:
        raise ValueError(
            "Rust surface backend does not implement spotting; explicitly set spotting=False"
        )


def validate_landscape(land, cfg):
    g = land.grid
    if (
        min(g.H, g.W) < 3
        or not isinstance(g.H, int)
        or not isinstance(g.W, int)
        or not np.isfinite([g.x0, g.y0, g.cell_m, g.t_ref_s]).all()
        or g.cell_m <= 0
        or not isinstance(g.t_ref_s, int)
    ):
        raise ValueError("invalid grid")
    shape = (g.H, g.W)
    for name in [
        "fuel",
        "slope",
        "aspect",
        "elev",
        "canopy_cover",
        "canopy_height",
        "building_frac",
    ]:
        a = getattr(land, name)
        if a is not None and (np.shape(a) != shape or not np.isfinite(a).all()):
            raise ValueError(f"invalid landscape {name}")
    if np.asarray(land.fuel).dtype.kind not in "iu" or (land.slope < 0).any():
        raise ValueError("fuel must be integer; slope must be nonnegative rise/run")
    for name in ["canopy_cover", "building_frac"]:
        a = getattr(land, name)
        if a is not None and ((a < 0) | (a > 1)).any():
            raise ValueError(f"{name} must be a fraction")
    if land.canopy_height is not None and (land.canopy_height < 0).any():
        raise ValueError("negative canopy height")
    supported = {
        int(row["fuel_id"])
        for path in [CATALOG, LEGACY_CATALOG]
        for row in csv.DictReader(path.open())
    } | {0, 91, 92, 93, 98, 99}
    unknown = set(np.unique(land.fuel).tolist()) - supported
    if unknown:
        raise ValueError(f"unsupported fuel codes: {sorted(unknown)}")
    return replace(
        land,
        **{
            name: None
            if getattr(land, name) is None
            else np.array(getattr(land, name), copy=True)
            for name in [
                "fuel",
                "slope",
                "aspect",
                "elev",
                "canopy_cover",
                "canopy_height",
                "building_frac",
            ]
        },
    )


def validate_weather(weather, shape):
    times = np.asarray(weather.t_min, dtype=np.float32)
    if (
        times.ndim != 1
        or not len(times)
        or not np.isfinite(times).all()
        or (np.diff(times) <= 0).any()
        or np.max(np.abs(times * 60 - np.round(times * 60))) > 1e-3
    ):
        raise ValueError(
            "weather band times must be ascending finite whole-second times"
        )
    expected = (len(times), *shape)
    if np.shape(weather.u10) != expected or np.shape(weather.v10) != expected:
        raise ValueError("weather grid mismatch")
    if any(
        not np.isfinite(a).all()
        for a in [weather.u10, weather.v10, weather.temp_c, weather.rh]
    ):
        raise ValueError("nonfinite weather")
    if (
        np.shape(weather.temp_c) != (len(times),)
        or np.shape(weather.rh) != (len(times),)
        or ((weather.rh < 0) | (weather.rh > 1)).any()
    ):
        raise ValueError("invalid temperature/RH bands")
    return Weather(
        *(
            np.array(getattr(weather, n), dtype=np.float32, copy=True)
            for n in ["t_min", "u10", "v10", "temp_c", "rh"]
        )
    )
