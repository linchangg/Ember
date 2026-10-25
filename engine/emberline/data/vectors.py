"""Zones, assets, zone raster and spawn points (spec/01 §6; 00 §3.3 ZoneFeatureProps, §4.3).

Zone source (01 §6 order): (1) the Cal OES aggregation layer holds only currently active zones
(none in LA County when checked), so it can't serve; (2) the LA County ArcGIS layer
"Evacuation Polygon" (LA County DPH, Zonehaven/Genasys schema, Jan 2025 Eaton/Palisades
snapshot) works and is used. Zones are real county codes, so `approximate` is false.

    uv run --no-sync python -m emberline.data.vectors --scenario eaton [--force]

Writes vectors/{zones,assets}.geojson, web/{zones,assets}.geojson (same content),
static/zone_index.tif (int16, -1 none, band value = index into zones.geojson features)
and web/spawn_points.json.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import geopandas as gpd
import httpx
import numpy as np
import shapely
from rasterio.features import rasterize
from shapely.geometry import box, mapping

from emberline.data import exposure, grid
from emberline.data.area_x import area_x_polygon
from emberline.data.config import load_scenario, scenario_dir

UTM = exposure.UTM
ZONES_URL = (
    "https://services.arcgis.com/RmCCgQtiZLDCtblq/arcgis/rest/services/Evacuation_Polygon/FeatureServer/0/query"
)
ZONES_SOURCE = (
    "LA County ArcGIS 'Evacuation Polygon' (owner LA County DPH; Data_Source_Name Zonehaven; "
    "Eaton/Palisades snapshot, item created 2025-01-10)"
)
# research/06 §2: the 13 zones ordered at 03:25 PST.
WEST_ALTADENA = (
    "ALD-ARROYOSECO", "ALD-CANON", "ALD-CASITAS", "ALD-CHANEY", "ALD-FARNSWORTH", "ALD-GARDEN",
    "ALD-LAUREL", "ALD-MEADOWS", "ALD-MILLARD", "ALD-PALM", "ALD-WAPELLO", "ALD-WHITEPARK", "LCF-JPL",
)
CITY = {
    "ALD": "Altadena", "LCF": "La Cañada Flintridge", "PAS": "Pasadena", "ARC": "Arcadia",
    "SMD": "Sierra Madre", "MRV": "Monrovia", "BRA": "Bradbury", "DUA": "Duarte", "GLN": "Glendale",
    "CRV": "Crescenta Valley", "KIN": "Kinneloa", "LAC": "LA County",
}
N_SPAWN = 6
SPAWN_MIN_DENSITY = 1500.0  # people/km²
PROPS = ("zone_id", "name", "population", "housing", "n_critical", "tier", "evac_min_default",
         "egress_roads", "approximate")  # 00 §3.3, exactly


def raw_dir(scn: str) -> Path:
    return scenario_dir(scn) / "raw"


# ---------- zones ----------


def fetch_zones(bbox: tuple[float, float, float, float], path: Path, force: bool = False) -> dict:
    if path.exists() and not force:
        return json.loads(path.read_text(encoding="utf-8"))
    params = {
        "where": "1=1",
        "geometry": ",".join(f"{v:.6f}" for v in bbox),
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "outSR": 4326,
        "f": "geojson",
    }
    r = httpx.get(ZONES_URL, params=params, timeout=180.0)
    r.raise_for_status()
    data = r.json()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def zone_name(zone_id: str, known_as: str | None) -> str:
    if isinstance(known_as, str) and known_as:
        return known_as
    prefix, _, rest = zone_id.partition("-")
    city = CITY.get(prefix, prefix)
    if not rest or rest.title() == city:  # KIN-KINNELOA is "Kinneloa", not "Kinneloa Kinneloa"
        return city if rest else zone_id
    return f"{city} {rest.title()}"


def aoi_utm(g: dict):
    x1, y1 = g["x0"] + g["nx"] * g["cell_m"], g["y0"] - g["ny"] * g["cell_m"]
    return box(g["x0"], y1, x1, g["y0"])


def select_zones(raw: dict, g: dict) -> gpd.GeoDataFrame:
    """Zones whose representative point is inside the model grid, sorted by zone_id. UTM.

    Only geometry, zone_id and known_as are kept: status/time fields describe what happened
    later that night and must not leak into the replay.
    """
    gdf = gpd.GeoDataFrame.from_features(raw["features"], crs="EPSG:4326").to_crs(UTM)
    gdf["geometry"] = gdf.geometry.make_valid()
    gdf = gdf[gdf.geometry.representative_point().within(aoi_utm(g))]
    gdf = gdf.dissolve("zone_id", aggfunc={"known_as": "first"}).reset_index()
    return gdf[["zone_id", "known_as", "geometry"]].sort_values("zone_id").reset_index(drop=True)


# ---------- spawn points ----------


def spawn_zones(zones: gpd.GeoDataFrame, cfg: dict) -> list[str]:
    """Built-up west-Altadena ALD zones ranked by overlap with area X (largest first).

    ponytail: "not in canyons" = zone density >= SPAWN_MIN_DENSITY people/km² (drops the
    Chaney/Millard/Meadows/Canon/Arroyo Seco foothill-canyon zones); a DEM relief check is the upgrade.
    """
    ax = gpd.GeoSeries([area_x_polygon(cfg)], crs="EPSG:4326").to_crs(UTM).iloc[0]
    dens = zones["population"] / (zones.geometry.area / 1e6)
    west = zones[zones["zone_id"].isin(WEST_ALTADENA) & zones["zone_id"].str.startswith("ALD-")
                 & (dens >= SPAWN_MIN_DENSITY)]
    overlap = west.geometry.intersection(ax).area
    ranked = west.assign(ov=overlap.values).sort_values(["ov", "zone_id"], ascending=[False, True])
    return list(ranked["zone_id"])


def pick_spawn(zone_geom, edges: gpd.GeoDataFrame) -> tuple[float, float, str] | None:
    """Midpoint of the named residential street piece closest to the zone's residential-street median.

    Streets are clipped to the zone (30 m inset) first. The median of the piece midpoints sits in
    the built-up part of the zone, which keeps the point off canyon roads. Returns (x, y, street), UTM.
    """
    inner = zone_geom.buffer(-30.0)
    res = edges[(edges["highway"] == "residential") & edges["name"].notna()]
    res = res.iloc[res.sindex.query(inner, predicate="intersects")]
    pieces = res.assign(geometry=res.geometry.intersection(inner)).explode(index_parts=False)
    pieces = pieces[(pieces.geom_type == "LineString") & (pieces.length > 40.0)].reset_index(drop=True)
    if pieces.empty:
        return None
    mids = pieces.geometry.interpolate(0.5, normalized=True)
    cx, cy = float(np.median(mids.x)), float(np.median(mids.y))
    i = int(((mids.x - cx) ** 2 + (mids.y - cy) ** 2).idxmin())
    return float(mids[i].x), float(mids[i].y), str(pieces.loc[i, "name"])


def spawn_points(zones: gpd.GeoDataFrame, edges: gpd.GeoDataFrame, cfg: dict, g: dict) -> list[dict]:
    out = []
    by_id = zones.set_index("zone_id").geometry
    for zid in spawn_zones(zones, cfg):
        got = pick_spawn(by_id[zid], edges)
        if got is None:
            continue
        x, y, street = got
        lon, lat = grid.xy_to_lonlat(x, y, g)
        out.append({"spawn_id": f"spawn_{zid}", "lon": round(lon, 6), "lat": round(lat, 6), "zone_id": zid,
                    "street": street})
        if len(out) == N_SPAWN:
            break
    assert len(out) == N_SPAWN, f"only {len(out)} spawn points"
    return out


# ---------- writers ----------


def lonlat_geom(geom, g: dict) -> dict:
    ll = shapely.transform(geom, lambda xy: np.round(np.column_stack(grid.xy_to_lonlat(xy[:, 0], xy[:, 1], g)), 6))
    return mapping(ll)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def zone_features(zones: gpd.GeoDataFrame, g: dict) -> list[dict]:
    feats = []
    for z in zones.itertuples():
        props = {
            "zone_id": z.zone_id, "name": z.name, "population": int(z.population), "housing": int(z.housing),
            "n_critical": int(z.n_critical), "tier": int(z.tier), "evac_min_default": float(z.evac_min_default),
            "egress_roads": int(z.egress_roads), "approximate": bool(z.approximate),
        }
        assert tuple(props) == PROPS
        feats.append({"type": "Feature", "id": z.zone_id, "properties": props, "geometry": lonlat_geom(z.geometry, g)})
    return feats


def write_zone_index(zones: gpd.GeoDataFrame, g: dict, path: Path) -> np.ndarray:
    tmpl = grid.template(g)
    shapes = [(geom, i) for i, geom in enumerate(zones.geometry)]
    arr = rasterize(shapes, out_shape=tmpl.shape, transform=grid.transform(g), fill=-1, dtype="int16")
    da = tmpl.copy(data=arr).rio.write_nodata(-1)
    da.attrs["zone_ids"] = json.dumps(list(zones["zone_id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    da.rio.to_raster(path, dtype="int16", compress="deflate")
    return arr


def build(scn: str, force: bool = False) -> dict:
    cfg = load_scenario(scn)
    g = cfg["grid"]
    raw = raw_dir(scn)
    sdir = scenario_dir(scn)

    zones = select_zones(fetch_zones(grid.bbox_lonlat(g), raw / "zones" / "la_evacuation_polygon.geojson", force), g)
    bounds_ll = tuple(gpd.GeoSeries(zones.geometry, crs=UTM).to_crs("EPSG:4326").total_bounds)
    pad = 0.01  # ~1 km so egress sees roads leaving edge zones
    bbox = (bounds_ll[0] - pad, bounds_ll[1] - pad, bounds_ll[2] + pad, bounds_ll[3] + pad)

    blocks = exposure.fetch_blocks(bbox, raw / "census" / "blocks2020.geojson", force).to_crs(UTM)
    pop = exposure.area_weight(blocks, zones)
    zones["population"] = pop["pop"].round().astype(int).values
    zones["housing"] = pop["hu"].round().astype(int).values
    census_check = exposure.point_in_zone_total(blocks, zones)

    osm = raw / "osm"
    pbf = exposure.fetch_pbf(osm / exposure.PBF_URL.rsplit("/", 1)[1])
    assets = exposure.assets_from_osm(exposure.osm_assets_from_pbf(pbf, bbox, osm / "assets_osm.geojson", force), zones)
    edges = exposure.drive_roads_from_pbf(pbf, bbox, osm / "roads_drive.geojson", force)
    counts = {zid: Counter(assets.loc[assets["zone_id"] == zid, "type"]) for zid in zones["zone_id"]}
    zones["egress_roads"] = [exposure.egress_roads(geom, edges) for geom in zones.geometry]
    rules = exposure.load_rules()
    tp = [exposure.tier_props(int(p), counts[z], int(e), rules)
          for z, p, e in zip(zones["zone_id"], zones["population"], zones["egress_roads"])]
    for k in ("tier", "n_critical", "evac_min_default"):
        zones[k] = [t[k] for t in tp]
    zones["name"] = [zone_name(z, k) for z, k in zip(zones["zone_id"], zones["known_as"])]
    zones["approximate"] = False

    meta = {
        "source": ZONES_SOURCE,
        "source_url": ZONES_URL.removesuffix("/query"),
        "provenance": "real",
        "note": "Zone set = LA County zones with a Jan 2025 warning/order (snapshot), centre inside the model grid. "
        "Geometry may include post-fire edits at worst. population/housing are area-weighted Census 2020 blocks.",
        "population_source": exposure.BLOCKS_SOURCE,
        "assets_source": exposure.OSM_SOURCE,
        "census_point_in_zone_pop": census_check,
        "egress_rule": f"distinct drivable OSM roads touching the zone (<= {exposure.TOUCH_M:g} m) and continuing "
        f"> {exposure.OUT_M:g} m outside it, within {exposure.BAND_M:g} m of the boundary",
    }
    zfc = {"type": "FeatureCollection", "metadata": meta, "features": zone_features(zones, g)}
    afeats = [
        {"type": "Feature", "properties": {k: a[k] for k in ("asset_id", "type", "name", "zone_id")},
         "geometry": lonlat_geom(a["geometry"], g)}
        for _, a in assets.iterrows()
    ]
    afc = {"type": "FeatureCollection", "metadata": {"source": exposure.OSM_SOURCE, "provenance": "real"},
           "features": afeats}
    for d in ("vectors", "web"):
        write_json(sdir / d / "zones.geojson", zfc)
        write_json(sdir / d / "assets.geojson", afc)
    arr = write_zone_index(zones, g, sdir / "static" / "zone_index.tif")
    spawns = spawn_points(zones, edges, cfg, g)
    write_json(sdir / "web" / "spawn_points.json", {"demo": spawns})

    hist = Counter(zones["tier"])
    return {
        "n_zones": len(zones),
        "west_altadena_present": sorted(set(WEST_ALTADENA) & set(zones["zone_id"])),
        "population": int(zones["population"].sum()),
        "census_point_in_zone_pop": census_check,
        "tiers": dict(sorted(hist.items())),
        "assets": dict(Counter(assets["type"])),
        "assets_in_zones": int(assets["zone_id"].notna().sum()),
        "raster_cells": int((arr >= 0).sum()),
        "spawns": spawns,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="eaton")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    rep = build(args.scenario, args.force)
    spawns = rep.pop("spawns")
    for k, v in rep.items():
        print(f"{k}: {v}")
    print("tier histogram:", " ".join(f"T{t}={n}" for t, n in rep["tiers"].items()))
    if rep["tiers"].get(1, 0) > rep["n_zones"] / 2:
        print("WARNING: more than half the zones are tier 1; recheck egress (01 §6)")
    for s in spawns:
        print("spawn:", s)


if __name__ == "__main__":
    main()
