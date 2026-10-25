"""Model-grid statics for physics (spec/01 §6): DEM, slope, aspect, LANDFIRE fuels and canopy cover.

    uv run --no-sync python -m ember.data.statics --scenario eaton [--force]

Writes static/{dem,slope,aspect,cc}.tif (float32) and static/fbfm40.tif (int16), all on the scenario grid
(same CRS/transform/shape as static/zone_index.tif), and `load_landscape(scn)` reads them into
hazard.api.Landscape.

Sources:
- DEM: USGS 3DEP 1/3 arc-second, the raw/dem_10m.tif terrain.py already fetches (py3dep), averaged to 90 m.
  Slope/aspect come from np.gradient on that 90 m DEM (01 §6), never from averaging finer slopes.
- Fuels: LANDFIRE LF2024 (pre-fire: LF2025 shows the Eaton burn, LF2024 does not) from the LFPS ArcGIS
  ImageServer `exportImage`, which needs no key or email. Spec deviation: 01 §6 names the LFPS GP job
  (needs an email); this endpoint serves the same layers. Fetched at 30 m on the grid's own UTM frame,
  so each 90 m cell is exactly 3x3 source cells: FBFM40 takes the block mode, CC the block mean.
  Never use the LF2025 folders: they already include the Eaton burn.
- Canopy height is skipped: the Rust backend never reads it (only validation/checkpoints do).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import rasterio
import rioxarray  # noqa: F401  (registers the .rio accessor)
from affine import Affine
from rasterio.enums import Resampling

from ember.data import grid, terrain
from ember.data.config import load_scenario, scenario_dir, utc_ms
from ember.hazard import api
from ember.hazard.geometry import GeometryCache
from ember.hazard.validation import validate_landscape

LF_VERSION = "LF2024"  # pre-fire; must match scenario.yaml honesty label "LANDFIRE LF2024"
LF_URL = "https://lfps.usgs.gov/arcgis/rest/services/Landfire_{v}/{v}_{layer}_CONUS/ImageServer/exportImage"
LF_VINTAGE = ("LANDFIRE 2024 Update (LF2024), pre-fire: over the Eaton scar 6.1% of 30 m FBFM40 cells differ "
              "LF2023->LF2024 vs 59% LF2024->LF2025, so LF2025 shows the burn and LF2024 does not")
LF_NODATA = -9999
LF_SERVER_RESAMPLE = "server nearest-neighbour EPSG:5070 30 m -> EPSG:32611 30 m"
FINE = 3  # LANDFIRE 30 m cells per 90 m model cell
DEM_SOURCE = "USGS 3DEP 1/3 arc-second (10 m) via py3dep, raw/dem_10m.tif, NAVD88 m"
NON_BURNABLE = (0, 92, 93, 98, 99)
FLOAT_STATICS = ("dem", "slope", "aspect", "cc")


def static_dir(scn: str) -> Path:
    return scenario_dir(scn) / "static"


def model_grid(cfg: dict) -> api.Grid:
    g = cfg["grid"]
    return api.Grid(crs=g["crs"], x0=float(g["x0"]), y0=float(g["y0"]), cell_m=float(g["cell_m"]),
                    H=int(g["ny"]), W=int(g["nx"]), t_ref_s=utc_ms(cfg["t_ref_utc"]) // 1000)


# ---------- terrain ----------


def dem_on_grid(dem_path: Path, g: dict) -> np.ndarray:
    da = rioxarray.open_rasterio(dem_path, masked=True).squeeze(drop=True)
    out = np.asarray(da.rio.reproject_match(grid.template(g), resampling=Resampling.average).values, np.float64)
    assert np.isfinite(out).all(), f"DEM has {(~np.isfinite(out)).sum()} empty cells on the model grid"
    return out


def slope_aspect(dem: np.ndarray, mg: api.Grid) -> tuple[np.ndarray, np.ndarray]:
    """Rise/run slope and DOWNSLOPE aspect (deg cw from TRUE north), the inverse of hazard/bridge.py's gx/gy."""
    dz_drow, dz_dcol = np.gradient(np.asarray(dem, np.float64), mg.cell_m)  # row 0 = north, so rows run south
    gx, gy = dz_dcol, -dz_drow  # upslope gradient, grid east / grid north
    geo = GeometryCache(mg)
    e = -gx * geo.true_east[0] - gy * geo.true_east[1]  # downslope vector in true east/north
    n = -gx * geo.true_north[0] - gy * geo.true_north[1]
    slope = np.hypot(gx, gy)
    aspect = np.where(slope > 0, np.degrees(np.arctan2(e, n)) % 360.0, 0.0)  # flat: any value, bridge x slope
    return slope, aspect


# ---------- LANDFIRE ----------


def lf_params(g: dict) -> dict:
    c = g["cell_m"]
    x1, y1 = g["x0"] + g["nx"] * c, g["y0"] - g["ny"] * c
    return {
        "bbox": f"{g['x0']},{y1},{x1},{g['y0']}", "bboxSR": 32611, "imageSR": 32611,
        "size": f"{g['nx'] * FINE},{g['ny'] * FINE}", "format": "tiff", "pixelType": "S16",
        "noData": LF_NODATA, "interpolation": "RSP_NearestNeighbor", "f": "image",
        "renderingRule": json.dumps({"rasterFunction": "None"}),  # raw codes, never the symbolized default
    }


def fetch_lf(scn: str, layer: str, g: dict, force: bool = False) -> Path:
    """30 m LANDFIRE layer on the grid's UTM frame, cached under raw/landfire/ (git-ignored)."""
    assert g["crs"] == "EPSG:32611", g["crs"]
    path = scenario_dir(scn) / "raw" / "landfire" / f"{LF_VERSION}_{layer}_30m.tif"
    if path.exists() and not force:
        return path
    url = LF_URL.format(v=LF_VERSION, layer=layer)
    r = httpx.get(url, params=lf_params(g), timeout=180.0, follow_redirects=True).raise_for_status()
    assert r.content[:2] in (b"II", b"MM"), f"{layer}: not a TIFF: {r.content[:200]!r}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    (path.with_suffix(".json")).write_text(json.dumps({
        "url": str(r.url), "layer": f"{LF_VERSION}_{layer}_CONUS", "vintage": LF_VINTAGE,
        "retrieved_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, indent=2) + "\n", encoding="utf-8")
    return path


def read_fine(path: Path, g: dict) -> np.ndarray:
    """The 30 m layer, checked to be exactly the model grid subdivided 3x3; nodata raises."""
    c = g["cell_m"] / FINE
    with rasterio.open(path) as ds:
        assert ds.crs.to_epsg() == 32611, ds.crs
        assert ds.transform.almost_equals(Affine(c, 0, g["x0"], 0, -c, g["y0"]), precision=1e-6), ds.transform
        assert ds.count == 1 and ds.dtypes[0] == "int16", (path.name, ds.count, ds.dtypes)
        a = ds.read(1)
    assert a.shape == (g["ny"] * FINE, g["nx"] * FINE), a.shape
    assert not (a == LF_NODATA).any(), f"{path.name}: {(a == LF_NODATA).sum()} nodata cells"
    return a


def blocks(a: np.ndarray) -> np.ndarray:
    """(3H, 3W) -> (H, W, 9) source cells of each model cell."""
    h, w = a.shape[0] // FINE, a.shape[1] // FINE
    return a.reshape(h, FINE, w, FINE).transpose(0, 2, 1, 3).reshape(h, w, FINE * FINE)


def block_mode(a: np.ndarray) -> np.ndarray:
    """Most common code per 3x3 block. Ties go to the centre 30 m cell when it is one of the tied codes,
    else to the smaller code: plain smallest-wins would always favour NB91/NB9x over SB40 (101+)."""
    raw = blocks(a)
    b = np.sort(raw, axis=-1)
    counts = (b[..., :, None] == b[..., None, :]).sum(-1)
    lowest = np.take_along_axis(b, counts.argmax(-1)[..., None], -1)[..., 0]
    centre = raw[..., FINE * FINE // 2]
    centre_count = (raw == centre[..., None]).sum(-1)
    return np.where(centre_count == counts.max(-1), centre, lowest)


def lf_tags(scn: str, layer: str, resampling: str) -> dict:
    meta = json.loads((scenario_dir(scn) / "raw" / "landfire" / f"{LF_VERSION}_{layer}_30m.json").read_text("utf-8"))
    return {"source": "LANDFIRE LFPS ImageServer exportImage", "landfire_version": LF_VERSION,
            "coverage": meta["layer"], "vintage": LF_VINTAGE, "source_url": meta["url"],
            "retrieved_utc": meta["retrieved_utc"], "resampling": resampling}


# ---------- write / read ----------


def write_static(arr: np.ndarray, g: dict, path: Path, dtype: str, tags: dict) -> None:
    da = grid.template(g).copy(data=arr.astype(dtype))
    da.attrs.update(tags)
    path.parent.mkdir(parents=True, exist_ok=True)
    da.rio.to_raster(path, dtype=dtype, compress="deflate")


def fuel_histogram(fuel: np.ndarray) -> list[tuple[int, int, float]]:
    n = fuel.size
    return [(int(k), int(v), 100.0 * v / n) for k, v in sorted(Counter(fuel.ravel().tolist()).items())]


def print_fuel_report(scn: str, fuel: np.ndarray) -> None:
    print(f"FBFM40 {LF_VERSION} histogram on the 90 m grid ({fuel.size} cells):")
    for k, v, pct in fuel_histogram(fuel):
        print(f"  {k:4d} {v:7d} {pct:6.2f}%")
    with rasterio.open(static_dir(scn) / "zone_index.tif") as ds:
        zi, ids = ds.read(1), json.loads(ds.tags()["zone_ids"])
    ald = np.isin(zi, [i for i, z in enumerate(ids) if z.startswith("ALD-")])
    with rasterio.open(static_dir(scn) / "area_x_index.tif") as ds:
        ax = ds.read(1) == 1
    for name, m in [("grid", np.ones_like(ald)), ("Altadena zones (ALD-*)", ald), ("Area X", ax)]:
        print(f"NB91 urban in {name}: {(fuel[m] == 91).mean():.1%} of {int(m.sum())} cells")


def build(scn: str, force: bool = False) -> dict:
    cfg = load_scenario(scn)
    g, mg, out = cfg["grid"], model_grid(cfg), static_dir(scn)
    dem = dem_on_grid(terrain.fetch_dem10(scn, grid.bbox_lonlat(g)), g)
    slope, aspect = slope_aspect(dem, mg)
    write_static(dem, g, out / "dem.tif", "float32", {"source": DEM_SOURCE, "resampling": "average 10 m -> 90 m"})
    grad = {"source": f"np.gradient of static/dem.tif at {g['cell_m']} m", "dem_source": DEM_SOURCE}
    write_static(slope, g, out / "slope.tif", "float32", {**grad, "units": "rise/run"})
    write_static(aspect, g, out / "aspect.tif", "float32",
                 {**grad, "units": "deg clockwise from true north, downslope direction; 0 where flat"})

    fuel = block_mode(read_fine(fetch_lf(scn, "FBFM40", g, force), g)).astype(np.int16)
    write_static(fuel, g, out / "fbfm40.tif", "int16",
                 {**lf_tags(scn, "FBFM40", f"{LF_SERVER_RESAMPLE}, then mode of 3x3 (ties: centre cell)"), "units": "Scott & Burgan FBFM40 code"})
    cc = blocks(read_fine(fetch_lf(scn, "CC", g, force), g)).mean(-1) / 100.0
    write_static(cc, g, out / "cc.tif", "float32",
                 {**lf_tags(scn, "CC", f"{LF_SERVER_RESAMPLE}, then mean of 3x3"), "units": "fraction 0-1 (LANDFIRE percent / 100)"})

    land = validate_landscape(load_landscape(scn), api.ModelConfig(spotting=False))
    print_fuel_report(scn, land.fuel)
    print(f"dem {dem.min():.1f}..{dem.max():.1f} m; slope p50 {np.median(slope):.3f} max {slope.max():.3f}; "
          f"cc>0.5 in {(cc > 0.5).mean():.1%}; burnable {(~np.isin(fuel, NON_BURNABLE)).mean():.1%}")
    return {n: str(out / f"{n}.tif") for n in (*FLOAT_STATICS, "fbfm40")}


def read_static(path: Path, g: dict) -> np.ndarray:
    with rasterio.open(path) as ds:
        assert ds.crs.to_epsg() == 32611 and ds.transform == grid.transform(g), (path, ds.crs, ds.transform)
        a = ds.read(1)
    assert a.shape == (g["ny"], g["nx"]), (path, a.shape)
    return a


def load_landscape(scn: str) -> api.Landscape:
    cfg = load_scenario(scn)
    g, d = cfg["grid"], static_dir(scn)
    f32 = {n: read_static(d / f"{n}.tif", g).astype(np.float32) for n in ("dem", "slope", "aspect")}
    cc = read_static(d / "cc.tif", g).astype(np.float32) if (d / "cc.tif").exists() else None
    return api.Landscape(grid=model_grid(cfg), fuel=read_static(d / "fbfm40.tif", g).astype(np.int16),
                         slope=f32["slope"], aspect=f32["aspect"], elev=f32["dem"], canopy_cover=cc)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="eaton")
    ap.add_argument("--force", action="store_true", help="re-download the LANDFIRE layers")
    a = ap.parse_args()
    print(json.dumps(build(a.scenario, a.force), indent=2))
