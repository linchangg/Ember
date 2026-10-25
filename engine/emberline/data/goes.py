"""GOES-18 ABI L2 FDC read + geometry helpers (01 §2-3; research 01 §2).

T1 owns the read/geometry helpers; wave-2 T8 adds the pixel-table writer here.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import netCDF4
import numpy as np
import pyproj
import s3fs
import xarray as xr

from emberline.data.config import scenario_dir

GOES18_LON = -137.2  # sub-satellite longitude (GOES-West operational slot)
GOES_H = 35_786_023.0  # satellite height above the equator, m
HALF_PIX_RAD = 28e-6  # half of the 56 µrad IR pixel
BUCKET = "noaa-goes18"
FILL = 255  # Mask/DQF fill after decoding NaN


@lru_cache(maxsize=1)
def _fs() -> s3fs.S3FileSystem:
    return s3fs.S3FileSystem(anon=True)


def list_fdc(product: str, day_of_year: int, hours: range, year: int = 2025) -> list[str]:
    keys: list[str] = []
    for hh in hours:
        keys += _fs().ls(f"{BUCKET}/{product}/{year}/{day_of_year:03d}/{hh:02d}/")
    return sorted(k for k in keys if k.endswith(".nc"))


def _tok_ms(tok: str) -> int:
    """'20250080200203' (YYYYJJJHHMMSSt) -> epoch ms UTC."""
    dt = datetime.strptime(tok[:13], "%Y%j%H%M%S").replace(tzinfo=timezone.utc)
    return int(dt.timestamp()) * 1000 + int(tok[13]) * 100


def parse_times(key: str) -> dict:
    name = key.rsplit("/", 1)[-1]
    s, e, c = (re.search(rf"_{f}(\d{{14}})", name).group(1) for f in "sec")
    return {"scan_start_ms": _tok_ms(s), "scan_end_ms": _tok_ms(e), "created_ms": _tok_ms(c)}


def geos_proj(p: dict) -> pyproj.Proj:
    """Fixed-grid projection from goes_imager_projection attrs (research 01 §2)."""
    return pyproj.Proj(
        proj="geos",
        h=float(p["perspective_point_height"]),
        lon_0=float(p["longitude_of_projection_origin"]),
        sweep=str(p["sweep_angle_axis"]),
        a=float(p["semi_major_axis"]),
        b=float(p["semi_minor_axis"]),
    )


def _as_u8(a: np.ndarray) -> np.ndarray:
    return np.where(np.isfinite(a), a, FILL).astype(np.uint8)


def _open(key: str) -> xr.Dataset:
    # ponytail: whole-file read (FDCC is ~230 KB); h5netcdf needs h5py, which isn't installed, so netCDF4 in memory.
    nc = netCDF4.Dataset("mem.nc", memory=_fs().cat(key))
    return xr.open_dataset(xr.backends.NetCDF4DataStore(nc))


def _window(ds: xr.Dataset, geos: pyproj.Proj, h: float, bbox: tuple, margin_km: float):
    w, s, e, n = bbox
    dlat = margin_km / 111.0
    dlon = margin_km / (111.0 * math.cos(math.radians((s + n) / 2)))
    lons = np.array([w - dlon, e + dlon, w - dlon, e + dlon, (w + e) / 2, (w + e) / 2])
    lats = np.array([s - dlat, s - dlat, n + dlat, n + dlat, s - dlat, n + dlat])
    gx, gy = geos(lons, lats)
    x, y = ds.x.values, ds.y.values
    xi = np.where((x >= gx.min() / h - HALF_PIX_RAD) & (x <= gx.max() / h + HALF_PIX_RAD))[0]
    yi = np.where((y >= gy.min() / h - HALF_PIX_RAD) & (y <= gy.max() / h + HALF_PIX_RAD))[0]
    assert xi.size and yi.size, f"bbox {bbox} not inside the scene"
    return slice(xi[0], xi[-1] + 1), slice(yi[0], yi[-1] + 1)


def _cache_path(key: str) -> Path:
    return scenario_dir("eaton") / "raw" / "goes" / (key.rsplit("/", 1)[-1] + ".npz")


def read_crop(key: str, bbox_lonlat: tuple, margin_km: float = 5, cache: Path | None = None) -> dict:
    """Crop Mask/DQF/Power to bbox + margin. Cached as .npz (default: eaton raw/goes/)."""
    path = cache or _cache_path(key)
    if path.exists():
        z = dict(np.load(path, allow_pickle=False))
        proj_attrs = {k[2:]: (str(v) if v.dtype.kind == "U" else float(v)) for k, v in z.items() if k.startswith("p_")}
    else:
        ds = _open(key)
        proj_attrs = dict(ds.goes_imager_projection.attrs)
        h = float(proj_attrs["perspective_point_height"])
        xs, ys = _window(ds, geos_proj(proj_attrs), h, bbox_lonlat, margin_km)
        sub = ds.isel(x=xs, y=ys)
        z = {
            "x": sub.x.values.astype(np.float64),
            "y": sub.y.values.astype(np.float64),
            "mask": _as_u8(sub.Mask.values),
            "dqf": _as_u8(sub.DQF.values),
            "power": sub.Power.values.astype(np.float32),
        }
        keep = ("perspective_point_height", "longitude_of_projection_origin", "sweep_angle_axis",
                "semi_major_axis", "semi_minor_axis")
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, **z, **{f"p_{k}": np.asarray(proj_attrs[k]) for k in keep})
    geos = geos_proj(proj_attrs)
    h = float(proj_attrs["perspective_point_height"])
    X, Y = np.meshgrid(z["x"] * h, z["y"] * h)
    lon, lat = geos(X, Y, inverse=True)
    return {
        "x": z["x"], "y": z["y"], "mask": z["mask"], "dqf": z["dqf"], "power": z["power"],
        "lon": lon, "lat": lat, "geos": geos, "h": h, **parse_times(key),
    }


def footprint_ring(x_rad: float, y_rad: float, geos: pyproj.Proj, h: float) -> list[list[float]]:
    d = HALF_PIX_RAD
    xs = np.array([x_rad - d, x_rad + d, x_rad + d, x_rad - d]) * h
    ys = np.array([y_rad - d, y_rad - d, y_rad + d, y_rad + d]) * h
    lon, lat = geos(xs, ys, inverse=True)
    return [[float(a), float(b)] for a, b in zip(lon, lat)]


@lru_cache(maxsize=1)
def _ecef() -> pyproj.Transformer:
    return pyproj.Transformer.from_crs("EPSG:4979", "EPSG:4978", always_xy=True)


_GEOD = pyproj.Geod(ellps="WGS84")


def parallax_shift(lon: float, lat: float, elev_m: float) -> tuple[float, float]:
    """Move an apparent position toward the sub-satellite point by elev · tan(VZA)."""
    if elev_m <= 0:
        return lon, lat
    sat = np.array(_ecef().transform(GOES18_LON, 0.0, GOES_H))
    gnd = np.array(_ecef().transform(lon, lat, elev_m))
    v = sat - gnd
    phi, lam = math.radians(lat), math.radians(lon)
    up = np.array([math.cos(phi) * math.cos(lam), math.cos(phi) * math.sin(lam), math.sin(phi)])
    vza = math.acos(float(v @ up) / float(np.linalg.norm(v)))
    az, _, _ = _GEOD.inv(lon, lat, GOES18_LON, 0.0)
    lon_c, lat_c, _ = _GEOD.fwd(lon, lat, az, elev_m * math.tan(vza))
    return float(lon_c), float(lat_c)


def correct_pixel(lon: float, lat: float, ring: list, dem_lookup) -> tuple[float, float, list]:
    """Two-pass parallax correction of the centre; the ring moves by the same Δlon/Δlat."""
    lon1, lat1 = parallax_shift(lon, lat, max(0.0, dem_lookup(lon, lat)))
    lon_c, lat_c = parallax_shift(lon, lat, max(0.0, dem_lookup(lon1, lat1)))
    dlon, dlat = lon_c - lon, lat_c - lat
    return lon_c, lat_c, [[p[0] + dlon, p[1] + dlat] for p in ring]
