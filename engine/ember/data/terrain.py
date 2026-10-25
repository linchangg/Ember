"""Terrain and imagery for the web (01 §7): Terrarium PNG from 3DEP 10 m, night-graded USGS imagery JPEG.

Both images cover `aoi_bbox` (= grid.json `bbox`, plate carree EPSG:4326, row 0 = north).
"""

from __future__ import annotations

import argparse
import json
from io import BytesIO
from pathlib import Path

import httpx
import numpy as np
import rioxarray  # noqa: F401  (registers the .rio accessor)
from PIL import Image, ImageEnhance
from rasterio.enums import Resampling
from rasterio.transform import from_bounds

from ember.data.config import scenario_dir

ELEV_PX = 2048
TEX_W = 4096  # height follows the bbox aspect so the server does not pad the extent
EXPORT = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/export"
ITEMINFO = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/info/iteminfo?f=json"
ATTRIBUTION = ["USGS The National Map: Imagery", "USGS 3DEP"]
UNVERIFIED = "unverified (check pre-fire)"


def aoi_bbox(scn: str) -> tuple[float, float, float, float]:
    g = json.loads((scenario_dir(scn) / "grid.json").read_text(encoding="utf-8"))
    return tuple(round(v, 6) for v in g["bbox"])  # type: ignore[return-value]


# ---------- elevation ----------


def dem10_path(scn: str) -> Path:
    return scenario_dir(scn) / "raw" / "dem_10m.tif"


def fetch_dem10(scn: str, bbox: tuple[float, float, float, float]) -> Path:
    """3DEP 10 m via py3dep (EPSG:5070), cached under raw/ (delete the file to re-download)."""
    path = dem10_path(scn)
    if path.exists():
        return path
    import py3dep

    w, s, e, n = bbox
    pad = 0.01
    dem = py3dep.get_dem((w - pad, s - pad, e + pad, n + pad), resolution=10)
    path.parent.mkdir(parents=True, exist_ok=True)
    dem.load().rio.to_raster(path)
    return path


def dem_lonlat(dem_path: Path, bbox: tuple[float, float, float, float], px: int = ELEV_PX) -> np.ndarray:
    """10 m DEM reprojected onto a px x px EPSG:4326 grid over bbox; float64 metres, NaN-free."""
    da = rioxarray.open_rasterio(dem_path, masked=True).squeeze(drop=True)
    out = da.rio.reproject(
        "EPSG:4326",
        shape=(px, px),
        transform=from_bounds(*bbox, px, px),
        resampling=Resampling.bilinear,
    )
    arr = np.asarray(out.values, dtype=np.float64)
    bad = ~np.isfinite(arr)
    assert bad.mean() < 0.001, f"DEM has {bad.sum()} empty cells over the AOI"
    arr[bad] = np.nanmedian(arr)
    return arr


def terrarium_encode(elev: np.ndarray) -> np.ndarray:
    """01 §7: v = elev + 32768; R = floor(v/256), G = floor(v) % 256, B = floor((v - floor(v)) * 256)."""
    v = np.asarray(elev, dtype=np.float64) + 32768.0
    fv = np.floor(v)
    rgb = np.stack([np.floor(v / 256.0), fv % 256.0, np.floor((v - fv) * 256.0)], axis=-1)
    return np.clip(rgb, 0, 255).astype(np.uint8)


def terrarium_decode(rgb: np.ndarray) -> np.ndarray:
    rgb = np.asarray(rgb, dtype=np.float64)
    return rgb[..., 0] * 256.0 + rgb[..., 1] + rgb[..., 2] / 256.0 - 32768.0


def write_elevation(scn: str, force: bool = False) -> Path:
    out = scenario_dir(scn) / "web" / "terrain_elev.png"
    if out.exists() and not force:
        return out
    bbox = aoi_bbox(scn)
    elev = dem_lonlat(fetch_dem10(scn, bbox), bbox)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(terrarium_encode(elev), "RGB").save(out, optimize=True)
    print(f"terrain_elev.png {elev.shape} elev {elev.min():.1f}..{elev.max():.1f} m")
    return out


# ---------- texture ----------


def tex_height(bbox: tuple[float, float, float, float], width: int = TEX_W) -> int:
    w, s, e, n = bbox
    return round(width * (n - s) / (e - w))


def fetch_texture(bbox: tuple[float, float, float, float], width: int = TEX_W) -> Image.Image:
    """USGS Imagery Only export. Asks f=json first to check the server kept our extent exactly."""
    height = tex_height(bbox, width)
    params = {
        "bbox": ",".join(str(v) for v in bbox),
        "bboxSR": 4326,
        "imageSR": 4326,
        "size": f"{width},{height}",
        "format": "jpg",
        "f": "json",
    }
    with httpx.Client(timeout=180, follow_redirects=True) as c:
        meta = c.get(EXPORT, params=params).raise_for_status().json()
        ext = meta["extent"]
        got = (ext["xmin"], ext["ymin"], ext["xmax"], ext["ymax"])
        tol = 2 * (bbox[2] - bbox[0]) / width  # two pixels
        assert all(abs(a - b) <= tol for a, b in zip(got, bbox, strict=True)), f"export extent {got} != bbox {bbox}"
        img = Image.open(BytesIO(c.get(meta["href"]).raise_for_status().content)).convert("RGB")
    assert img.size == (width, height), img.size
    return img


def night_grade(img: Image.Image) -> Image.Image:
    """01 §7: brightness x0.35, saturation x0.4, slight blue tint."""
    img = ImageEnhance.Color(img).enhance(0.4)
    img = ImageEnhance.Brightness(img).enhance(0.35)
    a = np.asarray(img, dtype=np.float32) * np.array([0.92, 0.97, 1.10], dtype=np.float32)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


def imagery_evidence() -> str:
    """What the service item info says about the imagery's age. A sidecar note, never the manifest date:
    it describes the service, not these pixels, so it doesn't prove the texture is <= 2024.
    """
    try:
        info = httpx.get(ITEMINFO, timeout=60).raise_for_status().json()
    except httpx.HTTPError as ex:
        return f"no evidence: item info request failed ({type(ex).__name__})"
    desc, access = info.get("description", ""), info.get("accessInformation", "")
    if "NAIP Data is from 2017-2021 for CONUS" in desc and "refreshed June, 2024" in access:
        return "NAIP 2017-2021 (USGS service metadata; service refreshed June 2024); pre-fire not pixel-verified"
    return "no evidence: item info has no NAIP date statement"


def write_texture(scn: str, force: bool = False) -> tuple[Path, str]:
    """Returns (path, texture_date). texture_date stays UNVERIFIED: nothing here proves the pixels are
    <= 2024 (WAVE2 T9b). The service-metadata evidence is saved next to the raw download, when it is downloaded.
    """
    out = scenario_dir(scn) / "web" / "terrain_tex.jpg"
    raw = scenario_dir(scn) / "raw" / "usgs_imagery.jpg"
    evidence = scenario_dir(scn) / "raw" / "usgs_imagery_date.txt"
    if out.exists() and not force:
        return out, UNVERIFIED
    if raw.exists():  # raw download (and its evidence note) is reused even with force; delete both to re-fetch
        img = Image.open(raw).convert("RGB")
    else:
        bbox = aoi_bbox(scn)
        try:
            img = fetch_texture(bbox, TEX_W)
        except (httpx.HTTPError, AssertionError, OSError) as ex:
            print(f"4096 export failed ({type(ex).__name__}: {ex}); retrying at 2048")
            img = fetch_texture(bbox, 2048)
        raw.parent.mkdir(parents=True, exist_ok=True)
        img.save(raw, quality=92)
        evidence.write_text(imagery_evidence() + "\n", encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    night_grade(img).save(out, quality=85, optimize=True)
    note = evidence.read_text(encoding="utf-8").strip() if evidence.exists() else "no evidence recorded"
    print(f"terrain_tex.jpg {img.size} texture_date={UNVERIFIED!r} (evidence: {note})")
    return out, UNVERIFIED


def terrain_block(scn: str, date: str) -> dict:
    return {
        "elevation_url": f"static/{scn}/terrain_elev.png",
        "encoding": "terrarium",
        "bounds": list(aoi_bbox(scn)),
        "texture_url": f"static/{scn}/terrain_tex.jpg",
        "texture_date": date,
        "attribution": ATTRIBUTION,
    }


def build(scn: str, force: bool = False) -> dict:
    write_elevation(scn, force)
    _, date = write_texture(scn, force)
    return terrain_block(scn, date)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="eaton")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    print(json.dumps(build(a.scenario, a.force), indent=2))
