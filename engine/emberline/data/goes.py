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


# --- T8: pixel table + scan events (01 §3, §8) ------------------------------------------------

import json  # noqa: E402

import pandas as pd  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402

from emberline.contracts import GOES_FIRE  # noqa: E402

SENSOR_ID = "goes18_fdcc"
PRODUCT = "ABI-L2-FDCC"
GOES_LOW = frozenset({15, 35})  # low-probability fire: hot, trust 0.3 (01 §3)
DQF_NO_INFO = frozenset({2, 3, 4, 5})
TRUST = {**dict.fromkeys((10, 11, 30, 31), 0.95), **dict.fromkeys((12, 13, 32, 33), 0.85),
         **dict.fromkeys((14, 34), 0.6), **dict.fromkeys(GOES_LOW, 0.3)}
CLEAR_TRUST = 1.0  # ponytail: the §3 table only covers fire classes; clear pixels use the contract default
STALE_LM_MS = 2 * 3_600_000  # §8: LastModified more than 2 h after scan end = re-upload, not first arrival
ASSUMED_LATENCY_MS = 3 * 60_000
# Full-disk 2 km fixed grid (5424 x 5424): x = -0.151844 + 56e-6·col, y = 0.151844 - 56e-6·row.
FD_OFFSET_RAD = 0.151844
FD_STEP_RAD = 56e-6


def fd_index(x_rad, y_rad) -> tuple[np.ndarray, np.ndarray]:
    """Scan angles -> full-disk fixed-grid (col, row). Stable across scans and sectors."""
    col = (np.asarray(x_rad) + FD_OFFSET_RAD) / FD_STEP_RAD
    row = (FD_OFFSET_RAD - np.asarray(y_rad)) / FD_STEP_RAD
    assert np.allclose(col, np.round(col), atol=0.01) and np.allclose(row, np.round(row), atol=0.01), "off-grid"
    return np.round(col).astype(np.int32), np.round(row).astype(np.int32)


def scan_id(scan_start_ms: int) -> str:
    return f"g18c_{datetime.fromtimestamp(scan_start_ms / 1000, timezone.utc):%Y%m%dT%H%M%S}"


def s3_key(name: str) -> str:
    """'OR_ABI-L2-FDCC-..._s2025008HHMMSSt_...nc' -> bucket key."""
    tok = re.search(r"_s(\d{14})", name).group(1)
    return f"{BUCKET}/{PRODUCT}/{tok[:4]}/{tok[4:7]}/{tok[7:9]}/{name}"


def availability(created_ms: int, scan_end_ms: int, lm_ms: int | None) -> tuple[int, str]:
    """§8: max(created, LastModified) if LastModified is within 2 h of scan end, else created + 3 min."""
    if lm_ms is not None and lm_ms - scan_end_ms < STALE_LM_MS:
        return max(created_ms, lm_ms), "s3_last_modified"
    return created_ms + ASSUMED_LATENCY_MS, "product_created"


def last_modified(keys: list[str], cache: Path) -> dict[str, int | None]:
    """S3 LastModified (epoch ms) per key, from one detailed listing per hour prefix; cached as JSON."""
    known: dict[str, int | None] = json.loads(cache.read_text()) if cache.exists() else {}
    for prefix in sorted({k.rsplit("/", 1)[0] for k in keys if known.get(k) is None}):  # retry keys a past listing missed
        lm = {d["name"]: int(d["LastModified"].timestamp() * 1000) for d in _fs().ls(prefix, detail=True)}
        known.update({k: lm.get(k) for k in keys if k.startswith(prefix + "/")})
    cache.write_text(json.dumps(known, indent=1, sort_keys=True))
    return {k: known.get(k) for k in keys}


def classify(mask: int, dqf: int) -> tuple[bool, bool, float] | None:
    """(hot, clear, trust), or None to drop (DQF 2-5 = no information; 255 = fill)."""
    if dqf in DQF_NO_INFO or dqf == FILL:
        return None
    hot = mask in GOES_FIRE or mask in GOES_LOW
    return hot, dqf == 1, TRUST[mask] if hot else CLEAR_TRUST


def pixel_geometry(crop: dict, aoi: Polygon, dem_lookup, geo: dict) -> None:
    """Parallax-corrected centre + closed ring per pixel, computed once per fixed-grid pixel."""
    cols, rows = fd_index(crop["x"], crop["y"])
    for i, r in enumerate(rows):
        for j, c in enumerate(cols):
            px = (int(c), int(r))
            if px in geo:
                continue
            ring = footprint_ring(crop["x"][j], crop["y"][i], crop["geos"], crop["h"])
            lon, lat, ring_c = correct_pixel(float(crop["lon"][i, j]), float(crop["lat"][i, j]), ring, dem_lookup)
            ring_c = ring_c + [ring_c[0]]
            geo[px] = {"lon": lon, "lat": lat, "ring": ring_c, "in_aoi": Polygon(ring_c).intersects(aoi)}


def pixel_rows(crop: dict, sid: str, avail: int, lm_ms: int | None, geo: dict) -> list[dict]:
    """Rows for every kept pixel whose corrected footprint intersects the AOI (`geo` from pixel_geometry)."""
    cols, rows = fd_index(crop["x"], crop["y"])
    out = []
    for i, r in enumerate(rows):
        for j, c in enumerate(cols):
            mask, dqf = int(crop["mask"][i, j]), int(crop["dqf"][i, j])
            cls, px = classify(mask, dqf), (int(c), int(r))
            if cls is None or not geo[px]["in_aoi"]:
                continue
            hot, clear, trust = cls
            power = float(crop["power"][i, j])
            out.append({
                "scan_id": sid, "sensor_id": SENSOR_ID,
                "scan_start_ms": crop["scan_start_ms"], "scan_end_ms": crop["scan_end_ms"],
                "created_ms": crop["created_ms"], "s3_last_modified_ms": lm_ms, "available_ms": avail,
                "px_x": px[0], "px_y": px[1], "lon": geo[px]["lon"], "lat": geo[px]["lat"], "ring": geo[px]["ring"],
                "mask": mask, "dqf": dqf, "hot": hot, "clear": clear,
                "frp_mw": power if hot and math.isfinite(power) else None, "trust": trust,
            })
    return out


def scan_event(sid: str, crop: dict, avail: int, rows: list[dict]) -> dict:
    """ScanEvent fields minus scenario_id/branch_id (runtime/sources.py docstring)."""
    return {
        "scan_id": sid, "sensor_id": SENSOR_ID, "mode": "CONUS",
        "t_start_sim_ms": crop["scan_start_ms"], "t_end_sim_ms": crop["scan_end_ms"], "available_sim_ms": avail,
        "n_pixels_aoi": len(rows), "n_hot": sum(r["hot"] for r in rows), "n_clear": sum(r["clear"] for r in rows),
        "assimilated": True, "pixels_path": None, "provenance": "real",
    }


def scan_index(scn: str = "eaton") -> list[dict]:
    """Per cached scan: scan_id, times, LastModified, available_ms and latency_basis (no crop read)."""
    raw = scenario_dir(scn) / "raw" / "goes"
    names = sorted(p.name.removesuffix(".npz") for p in raw.glob("*.nc.npz"))
    assert names, f"no cached crops in {raw}"
    keys = [s3_key(n) for n in names]
    lms = last_modified(keys, raw / "s3_last_modified.json")
    out = []
    for key, name in zip(keys, names):
        t = parse_times(key)
        avail, basis = availability(t["created_ms"], t["scan_end_ms"], lms[key])
        assert avail >= t["scan_end_ms"], f"{name}: available before scan end"
        out.append({"scan_id": scan_id(t["scan_start_ms"]), "name": name, "key": key, **t,
                    "s3_last_modified_ms": lms[key], "available_ms": avail, "latency_basis": basis})
    return out


PIXEL_DTYPES = {"scan_start_ms": "int64", "scan_end_ms": "int64", "created_ms": "int64",
                "s3_last_modified_ms": "Int64", "available_ms": "int64", "px_x": "int32", "px_y": "int32",
                "mask": "int16", "dqf": "int8", "hot": "bool", "clear": "bool", "frp_mw": "float32", "trust": "float32"}


def build_pixel_table(scn: str = "eaton") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Cached crops (raw/goes/*.npz) -> obs/goes18_fdcc.parquet + obs/scan_events.parquet."""
    from emberline.data.config import load_scenario
    from emberline.data.grid import bbox_lonlat, corners_lonlat
    from emberline.scripts.check_goes import dem_lookup_fn, ensure_dem  # lazy: check_goes imports this module

    g = load_scenario(scn)["grid"]
    c = corners_lonlat(g)
    aoi = Polygon([c["tl"], c["tr"], c["br"], c["bl"]])
    bbox = bbox_lonlat(g)
    # Always the T1 90 m pull, never static/dem.tif: that covers only the model grid and edge-clamps the
    # NE-shifted apparent positions, and would change rings/centres once A1 lands.
    dem_path = scenario_dir(scn) / "raw" / "dem_parallax.tif"
    if not dem_path.exists():
        ensure_dem(scn, bbox)  # writes raw/dem_parallax.tif (only while static/dem.tif is absent)
    assert dem_path.exists(), f"parallax DEM missing: {dem_path}; refusing constant-elevation rows labelled real"
    print(f"DEM for parallax: USGS 3DEP 90 m {dem_path}")
    dem_lookup = dem_lookup_fn(dem_path)
    raw = scenario_dir(scn) / "raw" / "goes"
    geo: dict = {}
    pixels: list[dict] = []
    events: list[dict] = []
    for s in scan_index(scn):
        crop = read_crop(s["key"], bbox, cache=raw / f"{s['name']}.npz")
        pixel_geometry(crop, aoi, dem_lookup, geo)
        rows = pixel_rows(crop, s["scan_id"], s["available_ms"], s["s3_last_modified_ms"], geo)
        pixels += rows
        events.append(scan_event(s["scan_id"], crop, s["available_ms"], rows))
    px_df, ev_df = pd.DataFrame(pixels).astype(PIXEL_DTYPES), pd.DataFrame(events)
    assert ev_df["scan_id"].is_unique, "duplicate scan_id"
    obs = scenario_dir(scn) / "obs"
    obs.mkdir(parents=True, exist_ok=True)
    px_df.to_parquet(obs / "goes18_fdcc.parquet", index=False)
    ev_df.to_parquet(obs / "scan_events.parquet", index=False)
    print(f"{len(ev_df)} scans, {len(px_df)} pixel rows ({int(px_df['hot'].sum())} hot, "
          f"{int(px_df['clear'].sum())} clear) -> {obs}")
    return px_df, ev_df


if __name__ == "__main__":
    build_pixel_table()
