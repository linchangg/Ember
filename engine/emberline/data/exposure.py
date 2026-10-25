"""Exposure per zone (spec/01 §6 "Exposure"; 00 §7 tiers): Census 2020 blocks, OSM assets, egress, tiers.

Pure-ish: the fetchers cache raw downloads under `data/scenarios/{scn}/raw/` (git-ignored);
everything else takes and returns GeoDataFrames in EPSG:32611. `data/vectors.py` drives it.
"""

from __future__ import annotations

from collections import Counter
import re
from pathlib import Path

import geopandas as gpd
import httpx
import numpy as np
import pandas as pd
import shapely

from emberline.risk.alerts import load_rules

UTM = "EPSG:32611"
TIGER_BLOCKS = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Tracts_Blocks/MapServer/2/query"
BLOCKS_SOURCE = "Census 2020 blocks (POP100, HU100) via TIGERweb REST Tracts_Blocks/MapServer/2"
OSM_SOURCE = "OpenStreetMap, Geofabrik socal-261023.osm.pbf (state 2026-10-23, not a 2025-01-07 snapshot)"
PAGE = 2000

# 01 §6 tags, mapped by asset_type(). ponytail: amenity=clinic is dropped; 00 §4.3 has no clinic type.
CRITICAL = ("hospital", "nursing_home", "school")


# ---------- Census blocks ----------


def fetch_blocks(bbox: tuple[float, float, float, float], path: Path, force: bool = False) -> gpd.GeoDataFrame:
    """2020 blocks intersecting `bbox` (lon/lat), cached at `path`. Paged TIGERweb query."""
    if path.exists() and not force:
        return gpd.read_file(path)
    feats: list[dict] = []
    offset = 0
    with httpx.Client(timeout=180.0) as client:
        while True:
            params = {
                "where": "1=1",
                "geometry": ",".join(f"{v:.6f}" for v in bbox),
                "geometryType": "esriGeometryEnvelope",
                "inSR": 4326,
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "GEOID,POP100,HU100",
                "outSR": 4326,
                "orderByFields": "OBJECTID",
                "resultOffset": offset,
                "resultRecordCount": PAGE,
                "f": "geojson",
            }
            r = client.get(TIGER_BLOCKS, params=params)
            r.raise_for_status()
            page = r.json().get("features", [])
            feats += page
            if len(page) < PAGE:
                break
            offset += PAGE
    gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")
    gdf = gdf.drop_duplicates("GEOID").reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(path, driver="GeoJSON")
    return gdf


def area_weight(blocks: gpd.GeoDataFrame, zones: gpd.GeoDataFrame) -> pd.DataFrame:
    """Area-weighted POP100/HU100 per zone. Both in UTM; `zones` has `zone_id`."""
    b = blocks[["GEOID", "POP100", "HU100", "geometry"]].copy()
    b["block_m2"] = b.geometry.area
    inter = gpd.overlay(b, zones[["zone_id", "geometry"]], how="intersection", keep_geom_type=True)
    w = inter.geometry.area / inter["block_m2"].where(inter["block_m2"] > 0, np.nan)
    inter["pop"] = inter["POP100"].astype(float) * w.fillna(0.0)
    inter["hu"] = inter["HU100"].astype(float) * w.fillna(0.0)
    out = inter.groupby("zone_id")[["pop", "hu"]].sum()
    return out.reindex(zones["zone_id"]).fillna(0.0)


def point_in_zone_total(blocks: gpd.GeoDataFrame, zones: gpd.GeoDataFrame) -> int:
    """Independent check (01 §12 test 6): POP100 of blocks whose interior point is in any zone."""
    pts = blocks.copy()
    pts["geometry"] = blocks.geometry.representative_point()
    hit = gpd.sjoin(pts, zones[["zone_id", "geometry"]], how="inner", predicate="within")
    return int(hit.drop_duplicates("GEOID")["POP100"].astype(int).sum())


# ---------- OSM assets ----------


def asset_type(row: pd.Series) -> str | None:
    """00 §4.3 `type` from OSM tags, or None (dropped)."""
    amenity = row.get("amenity")
    if row.get("social_facility") in ("nursing_home", "assisted_living") or amenity == "nursing_home":
        return "nursing_home"
    if amenity == "hospital":
        return "hospital"
    if amenity in ("school", "kindergarten"):
        return "school"  # ponytail: kindergarten counts as a school for tier 2; clinic has no 00 §4.3 type
    if amenity == "fire_station":
        return "fire_station"
    if row.get("power") == "substation":
        return "substation"
    if row.get("man_made") in ("water_works", "wastewater_plant"):
        return "water_plant"
    return None


def assets_from_osm(raw: gpd.GeoDataFrame, zones: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """OSM features (columns element, oid, tags..., any CRS) -> UTM points: asset_id, type, name, zone_id.

    A node, or a smaller area, whose point lies inside a mapped area of the same type is the same
    facility twice: keep the (larger) area. ponytail: names are ignored; every nested pair seen was
    unnamed or same-named (school buildings inside a campus, a way inside its relation).
    """
    df = raw.to_crs(UTM).copy()
    df["type"] = df.apply(asset_type, axis=1)
    df = df[df["type"].notna()].copy()
    df["asset_id"] = [f"osm_{el}_{oid}" for el, oid in zip(df["element"], df["oid"])]
    df = df.drop_duplicates("asset_id")
    df["name"] = df["name"].where(df["name"].notna(), "")
    areas = df[df["element"] != "node"]
    pts = df.assign(geometry=df.geometry.representative_point())
    hit = gpd.sjoin(pts[["asset_id", "type", "geometry"]], areas[["asset_id", "type", "geometry"]],
                    how="inner", predicate="within")
    hit = hit[(hit["type_left"] == hit["type_right"]) & (hit["asset_id_left"] != hit["asset_id_right"])]
    m2 = df.geometry.area
    own, other = m2.loc[hit.index].values, m2.loc[hit["index_right"]].values
    # nodes have area 0, so they always lose; equal areas: the lower asset_id survives
    lose = (own < other) | ((own == other) & (hit["asset_id_left"].values > hit["asset_id_right"].values))
    df = df.drop(index=hit.index[lose].unique())
    df["geometry"] = df.geometry.representative_point()
    pts = gpd.GeoDataFrame(df[["asset_id", "type", "name"]], geometry=df.geometry, crs=UTM)
    joined = gpd.sjoin(pts, zones[["zone_id", "geometry"]], how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]
    joined["zone_id"] = joined["zone_id"].astype(object).where(joined["zone_id"].notna(), None)
    out = joined[["asset_id", "type", "name", "zone_id", "geometry"]].sort_values("asset_id")
    return gpd.GeoDataFrame(out.reset_index(drop=True), crs=UTM)


# ---------- OSM from a Geofabrik extract ----------
# ponytail: every public Overpass mirror timed out from this network (2026-10-24), so osmnx can't
# fetch. We read the Geofabrik SoCal .osm.pbf with GDAL's OSM driver instead (whole ways, not
# osmnx's intersection-split edges; egress counts distinct roads, so that is equivalent).

PBF_URL = "https://download.geofabrik.de/north-america/us/california/socal-261023.osm.pbf"
DRIVE = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street",
    "road", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
}  # ponytail: osmnx "drive" minus service roads (driveways/parking aisles dominate them)
_TAG = re.compile(r'"((?:[^"\\]|\\.)*)"=>"((?:[^"\\]|\\.)*)"')
ASSET_KEYS = ("amenity", "social_facility", "power", "man_made")


def hstore(s) -> dict[str, str]:
    return dict(_TAG.findall(s)) if isinstance(s, str) else {}


def _tags_frame(gdf: gpd.GeoDataFrame, element) -> gpd.GeoDataFrame:
    tags = [hstore(t) for t in gdf.get("other_tags", [None] * len(gdf))]
    out = gpd.GeoDataFrame({"element": element, "name": gdf["name"].values}, geometry=gdf.geometry.values, crs=gdf.crs)
    for k in ASSET_KEYS:
        col = gdf[k].values if k in gdf else [None] * len(gdf)
        out[k] = [c if isinstance(c, str) else t.get(k) for c, t in zip(col, tags)]
    return out


def osm_assets_from_pbf(pbf: Path, bbox: tuple[float, float, float, float], path: Path,
                        force: bool = False) -> gpd.GeoDataFrame:
    """Asset candidates (nodes + areas) inside bbox, cached as GeoJSON at `path`."""
    if path.exists() and not force:
        return gpd.read_file(path)
    import pyogrio

    like = " OR ".join(f"other_tags LIKE '%\"{k}\"=>%'" for k in ASSET_KEYS)
    pts = pyogrio.read_dataframe(pbf, layer="points", bbox=bbox, where=f"({like}) OR man_made IS NOT NULL")
    pf = _tags_frame(pts, "node")
    pf["oid"] = pts["osm_id"].values
    polys = pyogrio.read_dataframe(pbf, layer="multipolygons", bbox=bbox,
                                   where=f"({like}) OR amenity IS NOT NULL OR man_made IS NOT NULL")
    is_way = polys["osm_way_id"].notna()
    af = _tags_frame(polys, np.where(is_way, "way", "relation"))
    af["oid"] = np.where(is_way, polys["osm_way_id"], polys["osm_id"])
    # GDAL's osmconf.ini doesn't list power/man_made in closed_ways_are_polygons, so closed
    # substation/water-plant ways land in `lines`. Read them there and close them into polygons.
    lines = pyogrio.read_dataframe(pbf, layer="lines", bbox=bbox, where=(
        "other_tags LIKE '%\"power\"=>\"substation\"%' OR man_made IN ('water_works', 'wastewater_plant')"))
    lines = lines[lines.geometry.is_closed & (lines.geometry.count_coordinates() >= 4)]
    lines["geometry"] = [shapely.make_valid(shapely.Polygon(g.coords)) for g in lines.geometry]
    lf = _tags_frame(lines, "way")
    lf["oid"] = lines["osm_id"].values
    df = pd.concat([pf, af, lf], ignore_index=True)
    df = gpd.GeoDataFrame(df[df.apply(asset_type, axis=1).notna()], crs="EPSG:4326")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_file(path, driver="GeoJSON")
    return df


def drive_roads_from_pbf(pbf: Path, bbox: tuple[float, float, float, float], path: Path,
                         force: bool = False) -> gpd.GeoDataFrame:
    """Drivable OSM ways inside bbox (UTM) with a stable `road` key: name, else `osm{way id}`."""
    if not path.exists() or force:
        import pyogrio

        hw = ",".join(f"'{h}'" for h in sorted(DRIVE))
        lines = pyogrio.read_dataframe(pbf, layer="lines", bbox=bbox, where=f"highway IN ({hw})",
                                       columns=["osm_id", "name", "highway"])
        lines["road"] = [road_key(n, o) for n, o in zip(lines["name"], lines["osm_id"])]
        path.parent.mkdir(parents=True, exist_ok=True)
        lines[["osm_id", "road", "name", "highway", "geometry"]].to_file(path, driver="GeoJSON")
    return gpd.read_file(path).to_crs(UTM)


def fetch_pbf(path: Path) -> Path:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        with httpx.stream("GET", PBF_URL, timeout=1800.0, follow_redirects=True) as r, open(tmp, "wb") as f:
            r.raise_for_status()
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
        tmp.rename(path)
    return path


def road_key(name, osm_id) -> str:
    if isinstance(name, str) and name:
        return name
    return f"osm{int(osm_id)}"


# ---------- egress ----------

TOUCH_M = 10.0  # a road within 10 m of the zone is reachable from it (zone edges are often road centrelines)
OUT_M = 20.0  # ...and crosses if it continues > 20 m outside the zone
BAND_M = 60.0  # only look at road pieces near the boundary, so same-named roads elsewhere don't count


def egress_roads(zone_geom, edges: gpd.GeoDataFrame) -> int:
    """Distinct drivable roads crossing the zone boundary (01 §6).

    A road crosses if, within BAND_M of the boundary, its edges touch the zone (≤ TOUCH_M) and also
    reach > OUT_M outside it. Boundary-following roads count (they continue past corners);
    interior dead ends that stop at a boundary road don't.
    """
    band = zone_geom.boundary.buffer(BAND_M)
    near = edges.iloc[edges.sindex.query(band, predicate="intersects")]
    if near.empty:
        return 0
    touch = zone_geom.buffer(TOUCH_M)
    outside = band.difference(zone_geom.buffer(OUT_M))
    t = near.intersects(touch)
    o = near.intersects(outside)
    touching = set(near.loc[t, "road"])
    leaving = set(near.loc[o, "road"])
    return len(touching & leaving)


# ---------- tiers ----------


def tier_props(population: int, counts: Counter, egress: int, rules: dict | None = None) -> dict:
    """00 §7: tier, n_critical, evac_min_default."""
    rules = rules or load_rules()
    if counts["hospital"] or counts["nursing_home"] or egress <= 1:
        tier = 1
    elif population > 500 or counts["school"]:
        tier = 2
    else:
        tier = 3
    evac = rules["evac_min_tier1"] if tier == 1 else rules["evac_min_default"]
    return {"tier": tier, "n_critical": sum(counts[k] for k in CRITICAL), "evac_min_default": float(evac)}


if __name__ == "__main__":
    c = Counter({"school": 1})
    assert tier_props(100, c, 3, {"evac_min_tier1": 75, "evac_min_default": 45}) == {
        "tier": 2, "n_critical": 1, "evac_min_default": 45.0}
    assert tier_props(900, Counter(), 1, {"evac_min_tier1": 75, "evac_min_default": 45})["tier"] == 1
    assert road_key("A St", 3) == "A St" and road_key(None, 4) == "osm4"
    assert hstore('"amenity"=>"school","name:es"=>"Escuela"') == {"amenity": "school", "name:es": "Escuela"}
    print("ok")
