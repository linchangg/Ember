"""HRRR surface wind forcing (spec/01 §5).

Downloads HRRR `sfc` f00–f09 for each init 18Z Jan 7 .. 15Z Jan 8 2025, rotates u/v
from Lambert-grid-relative to earth-relative, regrids onto the model grid and writes
`forcing/hrrr_{YYYYMMDDHH}.nc`, stamped with per-fxx availability (S3 LastModified).

    uv run python -m ember.data.hrrr --scenario eaton [--force] [--only 2025010806]
"""

from __future__ import annotations

import argparse
import math
import warnings
from datetime import UTC, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
import numpy as np
import xarray as xr
from scipy.interpolate import griddata

from ember.data import grid
from ember.data.config import load_scenario, scenario_dir

INITS = [datetime(2025, 1, 7, 18, tzinfo=UTC) + timedelta(hours=h) for h in range(22)]
FXX = range(10)
SEARCH = r":(?:UGRD|VGRD):10 m above ground|:TMP:2 m above ground|:RH:2 m above ground"
S3_URL = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.{d:%Y%m%d}/conus/hrrr.t{d:%H}z.wrfsfcf{f:02d}.grib2"
SOURCE = "HRRR sfc via Herbie"
MARGIN_M = 15_000.0
HOUR_MS = 3_600_000
PST = timezone(timedelta(hours=-8))
RAW_VARS = ("u10", "v10", "t2m", "r2")


# ---------- pure helpers ----------


def rotation_deg(lon: np.ndarray) -> np.ndarray:
    """HRRR Lambert grid → earth rotation angle (degrees). lon in −180..180."""
    return math.sin(math.radians(38.5)) * (np.asarray(lon, dtype=np.float64) + 97.5)


def to_earth_relative(u, v, lon) -> tuple[np.ndarray, np.ndarray]:
    a = np.radians(rotation_deg(lon))
    u, v = np.asarray(u, dtype=np.float64), np.asarray(v, dtype=np.float64)
    return u * np.cos(a) + v * np.sin(a), -u * np.sin(a) + v * np.cos(a)


def wind_from_deg(u, v) -> float:
    """Meteorological 'from' direction (0..360) of the mean vector."""
    um, vm = float(np.mean(u)), float(np.mean(v))
    return math.degrees(math.atan2(-um, -vm)) % 360.0


def available_ms(init_ms: int, fxx: int, s3_last_modified_ms: int | None) -> int:
    if s3_last_modified_ms is not None and 0 <= s3_last_modified_ms - init_ms <= 3 * HOUR_MS:
        return s3_last_modified_ms
    return init_ms + 60 * 60_000 + 2 * 60_000 * fxx


def to_ms(dt: datetime) -> int:
    return int(round(dt.timestamp() * 1000))


def init_tag(init: datetime) -> str:
    return f"{init:%Y%m%d%H}"


def lon180(lon):
    return ((np.asarray(lon, dtype=np.float64) + 180.0) % 360.0) - 180.0


def crop_bounds(g: dict, margin_m: float = MARGIN_M) -> tuple[float, float, float, float]:
    w, s, e, n = grid.bbox_lonlat(g)
    dlat = margin_m / 111_320.0
    dlon = margin_m / (111_320.0 * math.cos(math.radians(n)))
    return w - dlon, s - dlat, e + dlon, n + dlat


# ---------- fetch ----------


def s3_last_modified_ms(url: str) -> int | None:
    try:
        r = httpx.head(url, timeout=20.0)
        r.raise_for_status()
        return to_ms(parsedate_to_datetime(r.headers["last-modified"]))
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return None


def crop(ds: xr.Dataset, bounds: tuple[float, float, float, float]) -> xr.Dataset:
    """Index-box crop by the 2-D lat/lon fields; lon converted to −180..180."""
    w, s, e, n = bounds
    lon = lon180(ds["longitude"].values)
    lat = ds["latitude"].values
    ys, xs = np.nonzero((lon >= w) & (lon <= e) & (lat >= s) & (lat <= n))
    assert ys.size, f"no HRRR points inside {bounds}"
    out = ds.isel(y=slice(ys.min(), ys.max() + 1), x=slice(xs.min(), xs.max() + 1))
    return out.assign_coords(longitude=(("y", "x"), lon180(out["longitude"].values)))


def fetch_one(init: datetime, fxx: int, raw_dir: Path, bounds) -> xr.Dataset:
    from herbie import Herbie  # heavy import; only needed when downloading

    H = Herbie(init.replace(tzinfo=None), model="hrrr", product="sfc", fxx=fxx,
               priority="aws", save_dir=raw_dir, verbose=False)
    for attempt in range(3):  # ponytail: plain retry for S3 read timeouts, no backoff
        try:
            got = H.xarray(SEARCH)
            break
        except (RuntimeError, OSError):  # Herbie wraps some; requests errors are OSError
            if attempt == 2:
                raise
    parts = got if isinstance(got, list) else [got]
    ds = xr.merge([p[[v for v in RAW_VARS if v in p]] for p in parts], compat="override",
                  join="override", combine_attrs="drop_conflicts")
    missing = set(RAW_VARS) - set(ds.data_vars)
    assert not missing, f"HRRR {init_tag(init)} f{fxx:02d} missing {missing}"
    ds = crop(ds[list(RAW_VARS)], bounds).load()
    keep = {"latitude", "longitude"}
    ds = ds.drop_vars([c for c in ds.coords if c not in keep])
    lm = s3_last_modified_ms(S3_URL.format(d=init, f=fxx))
    ds["available_ms"] = np.int64(available_ms(to_ms(init), fxx, lm))
    ds.attrs = {}
    return ds


def fetch_init(init: datetime, raw_dir: Path, bounds, force: bool = False) -> xr.Dataset:
    """Cropped raw HRRR for all fxx, cached as raw/hrrr/{init}.nc (idempotent)."""
    path = raw_dir / f"{init_tag(init)}.nc"
    if path.exists() and not force:
        return xr.load_dataset(path)
    raw_dir.mkdir(parents=True, exist_ok=True)
    per_fxx = [fetch_one(init, f, raw_dir, bounds) for f in FXX]
    ds = xr.concat(per_fxx, dim="fxx", coords="minimal", compat="override").assign_coords(fxx=list(FXX))
    ds.to_netcdf(path)
    return ds


# ---------- regrid + write ----------


def model_lonlat(g: dict) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = grid.cell_centers(g)
    X, Y = np.meshgrid(xs, ys)
    lon, lat = grid.xy_to_lonlat(X, Y, g)
    return np.asarray(lon), np.asarray(lat)


def interp(src_pts: np.ndarray, vals: np.ndarray, dst: tuple[np.ndarray, np.ndarray]) -> np.ndarray:
    out = griddata(src_pts, vals, dst, method="linear")
    bad = np.isnan(out)
    if bad.any():
        out[bad] = griddata(src_pts, vals, (dst[0][bad], dst[1][bad]), method="nearest")
    return out


def regrid(raw: xr.Dataset, g: dict, init: datetime, source: str = SOURCE) -> xr.Dataset:
    lon = lon180(raw["longitude"].values)
    lat = raw["latitude"].values
    pts = np.column_stack([lon.ravel(), lat.ravel()])
    dst = model_lonlat(g)
    xs, ys = grid.cell_centers(g)
    u_out, v_out = [], []
    for f in FXX:
        r = raw.sel(fxx=f)
        ue, ve = to_earth_relative(r["u10"].values, r["v10"].values, lon)
        u_out.append(interp(pts, ue.ravel(), dst))
        v_out.append(interp(pts, ve.ravel(), dst))
    temp_c = (raw["t2m"] - 273.15).mean(dim=("y", "x")).values
    rh = (raw["r2"] / 100.0).mean(dim=("y", "x")).values
    return forcing_dataset(np.stack(u_out), np.stack(v_out), temp_c, rh, xs, ys,
                           to_ms(init), [int(a) for a in raw["available_ms"].values], source)


def forcing_dataset(u, v, temp_c, rh, xs, ys, init_ms: int, avail: list[int], source: str) -> xr.Dataset:
    """The contract with T7's ForcingProvider (brief T6 'Output format')."""
    assert len(avail) == len(FXX), avail
    assert all(a >= init_ms for a in avail), "availability before init"
    dims = ("fxx", "y", "x")
    return xr.Dataset(
        {
            "u10": (dims, np.asarray(u, dtype=np.float32)),
            "v10": (dims, np.asarray(v, dtype=np.float32)),
            "temp_c": (("fxx",), np.asarray(temp_c, dtype=np.float32)),
            "rh": (("fxx",), np.asarray(rh, dtype=np.float32)),
        },
        coords={"fxx": np.arange(len(FXX), dtype=np.int32), "y": ys, "x": xs},
        attrs={
            "init_ms": np.int64(init_ms),
            "available_ms_by_fxx": np.asarray(avail, dtype=np.int64),
            "source": source,
            "rotation": "earth-relative",
            "crs": "EPSG:32611",
        },
    )


def write_forcing(ds: xr.Dataset, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # ponytail: 0.01 m/s quantisation (max err ~0.004 m/s) keeps files ~1.3 MB instead of ~5 MB
    enc = {v: {"zlib": True, "complevel": 4, "shuffle": True, "least_significant_digit": 2} for v in ("u10", "v10")}
    ds.to_netcdf(path, engine="netcdf4", format="NETCDF4", encoding=enc)
    return path


# ---------- sign check (01 §12 test 5) ----------


def sign_check(forcing_dir: Path, at: datetime = datetime(2025, 1, 8, 6, tzinfo=UTC)) -> tuple[str, float, float]:
    """Newest init available by `at`, band valid at `at`: (init tag, from-deg, mean speed)."""
    at_ms = to_ms(at)
    best = None
    for p in sorted(forcing_dir.glob("hrrr_*.nc")):
        with xr.open_dataset(p) as ds:
            init_ms = int(ds.attrs["init_ms"])
            if int(np.atleast_1d(ds.attrs["available_ms_by_fxx"])[0]) <= at_ms and (best is None or init_ms > best[1]):
                best = (p, init_ms)
    assert best is not None, f"no init available by {at:%Y-%m-%dT%HZ} in {forcing_dir}"
    p, init_ms = best
    fxx = (at_ms - init_ms) // HOUR_MS
    with xr.open_dataset(p) as ds:
        u, v = ds["u10"].sel(fxx=fxx).values, ds["v10"].sel(fxx=fxx).values
    speed = float(math.hypot(u.mean(), v.mean()))
    return p.stem.removeprefix("hrrr_") + f" f{fxx:02d}", wind_from_deg(u, v), speed


def pst(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, PST).strftime("%m-%d %H:%M PST")


def build(scn: str, only: list[str] | None, force: bool) -> None:
    g = load_scenario(scn)["grid"]
    root = scenario_dir(scn)
    raw_dir, out_dir = root / "raw" / "hrrr", root / "forcing"
    bounds = crop_bounds(g)
    for init in INITS:
        if only and init_tag(init) not in only:
            continue
        out = out_dir / f"hrrr_{init_tag(init)}.nc"
        if out.exists() and not force:
            ds = xr.load_dataset(out)
        else:
            ds = regrid(fetch_init(init, raw_dir, bounds, force), g, init)
            write_forcing(ds, out)
        av = np.atleast_1d(ds.attrs["available_ms_by_fxx"])
        u0, v0 = ds["u10"].sel(fxx=0).values, ds["v10"].sel(fxx=0).values
        print(f"{init_tag(init)}  avail f00 {pst(int(av[0]))}  f09 {pst(int(av[-1]))}  "
              f"f00 mean {math.hypot(u0.mean(), v0.mean()):4.1f} m/s from {wind_from_deg(u0, v0):5.1f}°  "
              f"{out.stat().st_size / 1e6:.2f} MB", flush=True)
    tag, deg, spd = sign_check(out_dir)
    ok = 0.0 <= deg <= 90.0
    print(f"SIGN CHECK 06Z Jan 8: init {tag}  mean wind {spd:.1f} m/s from {deg:.1f}°  "
          f"{'PASS (NE, Santa Ana)' if ok else 'FAIL (not NE: check rotation sign)'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="eaton")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", nargs="*", help="init tags YYYYMMDDHH")
    a = ap.parse_args()
    warnings.filterwarnings("ignore", category=FutureWarning, module="cfgrib")
    build(a.scenario, a.only, a.force)
