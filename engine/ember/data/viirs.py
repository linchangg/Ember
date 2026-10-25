"""VIIRS 375 m active fire (NOAA Enterprise EFIRE I-band, open S3) -> obs/viirs.parquet (01 §4).

    uv run python -m ember.data.viirs --scenario eaton

Columns: runtime/sources.py docstring. available_ms = the NOAA file creation time (`_c` in the name),
never a FIRMS URT assumption. Raw granules that overlap the AOI are cached under raw/viirs/ (git-ignored);
every granule checked is remembered in raw/viirs/index.json so a rerun downloads nothing.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import netCDF4
import pandas as pd
from shapely import wkt
from shapely.geometry import box

from ember.data.config import load_scenario, scenario_dir, utc_ms
from ember.data.goes import _fs
from ember.data.grid import bbox_lonlat

PREFIX = "VIIRS_EFIRE_VIIRSI_EDR"
SENSORS = {"npp": ("noaa-nesdis-snpp-pds", "viirs_snpp"), "n20": ("noaa-nesdis-n20-pds", "viirs_n20"),
           "n21": ("noaa-nesdis-n21-pds", "viirs_n21")}
LATENCY_BASIS = "product_created"  # NOAA EFIRE file `_c` creation time (01 §8)
COLUMNS = ["pass_id", "sensor_id", "row", "t_obs_ms", "available_ms", "lon", "lat", "ring", "trust", "confidence",
           "frp_mw"]
# FP_FireMask 7/8/9 = low/nominal/high (01 §4). Only n/h are kept; l stays here for the mapping's record.
TRUST = {7: 0.5, 8: 0.8, 9: 0.95}
CONFIDENCE = {7: 0.3, 8: 0.6, 9: 0.9}
KEEP = (8, 9)
NADIR_KM2 = 0.375 ** 2
OVERLAP_MARGIN_DEG = 0.5  # geospatial_bounds is an 8-vertex outline of a curved swath


def _times(name: str) -> dict:
    """'EFIRE-VIIRSI_v1r3_npp_s202501080920401_e..._c202501081059404.nc' -> start/end/created ms (YYYYMMDDHHMMSSt)."""
    def ms(f: str) -> int:
        tok = re.search(rf"_{f}(\d{{15}})", name).group(1)
        dt = datetime.strptime(tok[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        return int(dt.timestamp()) * 1000 + int(tok[14]) * 100
    return {"start_ms": ms("s"), "end_ms": ms("e"), "created_ms": ms("c")}


def list_granules(start_ms: int, end_ms: int) -> list[str]:
    """Keys whose scan start lies in [start, end], all three satellites."""
    day0 = datetime.fromtimestamp(start_ms / 1000, timezone.utc).date()
    day1 = datetime.fromtimestamp(end_ms / 1000, timezone.utc).date()
    keys = []
    for bucket, _ in SENSORS.values():
        d = day0
        while d <= day1:
            keys += _fs().ls(f"{bucket}/{PREFIX}/{d:%Y/%m/%d}/")
            d += timedelta(days=1)
    return sorted(k for k in keys if k.endswith(".nc") and start_ms <= _times(k)["start_ms"] <= end_ms)


def cache_overlapping(keys: list[str], bbox: tuple, scn: str) -> list[str]:
    """Download each unchecked granule once; keep the file only if its outline touches the AOI."""
    raw = scenario_dir(scn) / "raw" / "viirs"
    raw.mkdir(parents=True, exist_ok=True)
    idx_path = raw / "index.json"
    index: dict[str, bool] = json.loads(idx_path.read_text()) if idx_path.exists() else {}
    aoi = box(*bbox).buffer(OVERLAP_MARGIN_DEG)

    todo = [k for k in keys if k not in index]
    print(f"VIIRS: {len(keys)} granules in window, {len(todo)} to check")
    with ThreadPoolExecutor(16) as ex:  # threads only download: HDF5 is not thread-safe, so parse here
        for i, (k, data) in enumerate(zip(todo, ex.map(_fs().cat, todo))):
            with netCDF4.Dataset("mem.nc", memory=data) as nc:
                index[k] = wkt.loads(nc.getncattr("geospatial_bounds")).intersects(aoi)
            if index[k]:
                (raw / k.rsplit("/", 1)[1]).write_bytes(data)
            if i % 100 == 99:
                idx_path.write_text(json.dumps(index, indent=1, sort_keys=True))
    idx_path.write_text(json.dumps(index, indent=1, sort_keys=True))
    return [k for k in keys if index[k]]


def _square(lon: float, lat: float, area_km2: float) -> list[list[float]]:
    """Closed lon/lat square of the pixel's own area (FP_PixelArea); 375 m if missing.

    ponytail: axis-aligned square; the real footprint is a scan x track rectangle rotated with the orbit.
    """
    half_m = 500.0 * math.sqrt(area_km2 if area_km2 > 0 else NADIR_KM2)
    dlat = half_m / 111_320.0
    dlon = half_m / (111_320.0 * math.cos(math.radians(lat)))
    ring = [[lon - dlon, lat - dlat], [lon + dlon, lat - dlat], [lon + dlon, lat + dlat], [lon - dlon, lat + dlat]]
    return ring + [ring[0]]


def granule_rows(path, bbox: tuple) -> tuple[int, list[dict]]:
    """(orbit number, kept detections inside bbox) for one cached granule."""
    t = _times(path.name)
    sensor_id = SENSORS[path.name.split("_")[2]][1]
    w, s, e, n = bbox
    with netCDF4.Dataset(path) as nc:
        nc.set_auto_mask(False)  # FP_PersistentAnomalyCategory's _FillValue is 0, which masking would hide
        v = {k: nc.variables[k][:] for k in ("FP_Latitude", "FP_Longitude", "FP_FireMask", "FP_Line",
                                             "FP_PersistentAnomalyCategory", "FP_FireRadiativePower", "FP_PixelArea")}
        orbit, n_lines = int(nc.getncattr("start_orbit_number")), nc.dimensions["y"].size
    out = []
    for lat, lon, cls, line, pa, frp, area in zip(*v.values()):
        lat, lon, cls = float(lat), float(lon), int(cls)
        if not (w <= lon <= e and s <= lat <= n) or pa > 0 or cls not in KEEP:
            continue
        # Scan time of the pixel's line, interpolated across the granule.
        t_obs = t["start_ms"] + round((t["end_ms"] - t["start_ms"]) * int(line) / n_lines)
        assert t["created_ms"] >= t_obs, f"{path.name}: created before observed"
        out.append({"sensor_id": sensor_id, "t_obs_ms": t_obs, "available_ms": t["created_ms"], "lon": lon,
                    "lat": lat, "ring": _square(lon, lat, float(area)), "trust": TRUST[cls],
                    "confidence": CONFIDENCE[cls], "frp_mw": float(frp) if frp >= 0 else None,
                    "_gran_start": t["start_ms"], "_line": int(line)})
    return orbit, out


def build(scn: str = "eaton") -> pd.DataFrame:
    cfg = load_scenario(scn)
    start, end = utc_ms(cfg["replay_start_utc"]), utc_ms(cfg["replay_end_utc"])
    bbox = bbox_lonlat(cfg["grid"])
    cache_overlapping(list_granules(start, end), bbox, scn)
    raw = scenario_dir(scn) / "raw" / "viirs"
    passes: dict[tuple, list[dict]] = {}
    for p in sorted(raw.glob("EFIRE-*.nc")):
        if not start <= _times(p.name)["start_ms"] <= end:
            continue
        orbit, rows = granule_rows(p, bbox)
        passes.setdefault((SENSORS[p.name.split("_")[2]][1], orbit), []).extend(rows)
    out = []
    for (sensor_id, orbit), rows in sorted(passes.items()):
        if not rows:
            print(f"  {sensor_id} orbit {orbit}: AOI in swath, no n/h detections")
            continue
        rows.sort(key=lambda r: (r["t_obs_ms"], r["_line"], r["lon"], r["lat"]))
        g0 = min(r["_gran_start"] for r in rows)
        pid = f"{sensor_id}_{datetime.fromtimestamp(g0 / 1000, timezone.utc):%Y%m%dT%H%M%S}"
        out += [{"pass_id": pid, "row": i, **r} for i, r in enumerate(rows)]
    df = pd.DataFrame(out, columns=COLUMNS).astype(
        {"row": "int32", "t_obs_ms": "int64", "available_ms": "int64", "trust": "float32", "confidence": "float32",
         "frp_mw": "float32"})
    assert (df["available_ms"] >= df["t_obs_ms"]).all(), "available before observed"
    obs = scenario_dir(scn) / "obs"
    obs.mkdir(parents=True, exist_ok=True)
    df.to_parquet(obs / "viirs.parquet", index=False)
    for pid, g in df.groupby("pass_id", sort=False):
        print(f"  {pid}: {len(g)} px, observed {pd.Timestamp(g['t_obs_ms'].min(), unit='ms')}Z, "
              f"available {pd.Timestamp(g['available_ms'].max(), unit='ms')}Z")
    print(f"{len(df)} VIIRS rows, {df['pass_id'].nunique()} passes -> {obs / 'viirs.parquet'}")
    return df


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="eaton")
    build(ap.parse_args().scenario)
