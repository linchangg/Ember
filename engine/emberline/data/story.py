"""Story geometry (01 §6): Lake Ave centreline from OSM, area X polygon + area_x_index.tif.

Also the OSM named-street lookup `truth.py` uses to geocode timeline intersections.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import httpx
import numpy as np
from rasterio.features import rasterize
from shapely.geometry import LineString, MultiLineString, box, mapping
from shapely.geometry.polygon import orient
from shapely.ops import linemerge

from emberline.data.area_x import _utm, area_x_polygon
from emberline.data.config import load_scenario, scenario_dir
from emberline.data.grid import template, transform
from emberline.data.terrain import aoi_bbox

LAKE_AVE_NAME = "North Lake Avenue"
LAKE_AVE_LON = -118.1316
LAKE_AVE_TOL = 0.003
OVERPASS = (
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
AREA_X_NAME = "west Altadena foothills (area X)"


# ---------- OSM ----------


def _cache_path(scn: str, query: str) -> Path:
    # sha256, not hash(): persisted names must be stable across runs
    return scenario_dir(scn) / "raw" / "osm" / f"{hashlib.sha256(query.encode()).hexdigest()[:16]}.json"


def overpass(scn: str, query: str) -> dict:
    """Run an Overpass query against the first mirror that answers; cache the JSON under raw/osm/."""
    path = _cache_path(scn, query)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    errors = []
    for url in OVERPASS * 2:  # mirrors are flaky; two rounds
        try:
            r = httpx.post(url, data={"data": query}, timeout=httpx.Timeout(180, connect=20),
                           headers={"User-Agent": "emberline-calhacks/0.1"})
            r.raise_for_status()
            out = r.json()
            if "remark" in out:  # Overpass reports timeouts/OOM as HTTP 200 + partial/empty elements + remark
                raise ValueError(out["remark"])
            break
        except (httpx.HTTPError, ValueError) as ex:
            errors.append(f"{url}: {type(ex).__name__}: {ex}"[:300])
    else:
        raise RuntimeError("all Overpass mirrors failed: " + "; ".join(errors))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out), encoding="utf-8")
    return out


def named_ways(scn: str, name: str, bbox: tuple[float, float, float, float]) -> list[LineString]:
    """Highway ways with exactly this OSM `name` inside bbox (w, s, e, n), as lon/lat LineStrings."""
    w, s, e, n = bbox
    q = f'[out:json][timeout:60];way["highway"]["name"="{name}"]({s},{w},{n},{e});out geom;'
    ways = [el for el in overpass(scn, q)["elements"] if el["type"] == "way"]
    return [LineString([(p["lon"], p["lat"]) for p in el["geometry"]]) for el in ways]


def street_intersection(scn: str, a: str, b: str, bbox: tuple[float, float, float, float]) -> tuple[float, float] | None:
    """Lon/lat where OSM streets `a` and `b` meet (centroid of all crossings), or None."""
    la, lb = MultiLineString(named_ways(scn, a, bbox)), MultiLineString(named_ways(scn, b, bbox))
    if la.is_empty or lb.is_empty:
        return None
    hit = la.intersection(lb)
    if hit.is_empty:
        return None
    c = hit.centroid
    return round(c.x, 6), round(c.y, 6)


# ---------- Lake Ave ----------


def lake_ave_line(lines: list[LineString], aoi: tuple[float, float, float, float]) -> list[list[float]]:
    """One south-to-north polyline: merge the ways, clip to the AOI, then grow the longest piece
    end-to-end through shared nodes. Dual-carriageway stretches give two parallel pieces between the
    same junctions; whichever is found first is used.
    """
    merged = linemerge(MultiLineString(lines)).intersection(box(*aoi))
    pieces = [_south_north(p) for p in getattr(merged, "geoms", [merged]) if isinstance(p, LineString)]
    line = max(pieces, key=lambda c: LineString(c).length)
    pieces.remove(line)
    grown = True
    while grown:
        grown = False
        for p in pieces:
            if p[-1] == line[0]:
                line = p[:-1] + line
            elif p[0] == line[-1]:
                line = line + p[1:]
            else:
                continue
            pieces.remove(p)
            grown = True
            break
    return [[round(x, 6), round(y, 6)] for x, y in line]


def _south_north(p: LineString) -> list[tuple[float, float]]:
    c = list(p.coords)
    return c if c[0][1] <= c[-1][1] else c[::-1]


def check_lake_ave(coords: list[list[float]]) -> float:
    lon = float(np.mean([c[0] for c in coords]))
    assert abs(lon - LAKE_AVE_LON) <= LAKE_AVE_TOL, f"Lake Ave mean lon {lon} not near {LAKE_AVE_LON}"
    return lon


def write_lake_ave(scn: str, force: bool = False) -> list[list[float]]:
    out = scenario_dir(scn) / "vectors" / "lake_ave.geojson"
    if out.exists() and not force:
        return json.loads(out.read_text(encoding="utf-8"))["geometry"]["coordinates"]
    aoi = aoi_bbox(scn)
    coords = lake_ave_line(named_ways(scn, LAKE_AVE_NAME, aoi), aoi)
    lon = check_lake_ave(coords)
    feat = {
        "type": "Feature",
        "properties": {"name": LAKE_AVE_NAME, "source": "OpenStreetMap (ODbL)", "mean_lon": round(lon, 6)},
        "geometry": {"type": "LineString", "coordinates": coords},
    }
    _write_json(out, feat)
    print(f"lake_ave: {len(coords)} pts, mean lon {lon:.5f}, lat {coords[0][1]:.4f}..{coords[-1][1]:.4f}")
    return coords


# ---------- area X ----------


def area_x_feature(cfg: dict) -> dict:
    poly = orient(area_x_polygon(cfg), sign=1.0)  # CCW exterior (RFC 7946)
    geom = mapping(poly)
    geom = {"type": "Polygon", "coordinates": [[[round(x, 6), round(y, 6)] for x, y in geom["coordinates"][0]]]}
    return {"type": "Feature", "properties": {"name": AREA_X_NAME}, "geometry": geom}


def area_x_index(cfg: dict) -> np.ndarray:
    """int8 on the model grid, 1 = cell centre inside area X (all cells, any fuel)."""
    g = cfg["grid"]
    return rasterize(
        [(_utm(area_x_polygon(cfg)), 1)],
        out_shape=(g["ny"], g["nx"]),
        transform=transform(g),
        fill=0,
        dtype="int8",
    )


def write_area_x(scn: str) -> tuple[Path, Path]:
    cfg = load_scenario(scn)
    feat = area_x_feature(cfg)
    vec = scenario_dir(scn) / "vectors" / "area_x.geojson"
    web = scenario_dir(scn) / "web" / "area_x.geojson"
    for p in (vec, web):
        _write_json(p, feat)
    idx = area_x_index(cfg)
    da = template(cfg["grid"]).astype(np.int8).copy(data=idx)
    tif = scenario_dir(scn) / "static" / "area_x_index.tif"
    tif.parent.mkdir(parents=True, exist_ok=True)
    da.rio.to_raster(tif, dtype="int8", compress="deflate")
    print(f"area_x_index.tif: {int(idx.sum())} cells in area X")
    return web, tif


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def build(scn: str, force: bool = False) -> list[list[float]]:
    write_area_x(scn)
    return write_lake_ave(scn, force)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="eaton")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    build(a.scenario, a.force)
