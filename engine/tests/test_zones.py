"""T9a: zones, exposure, assets, zone raster, spawn points (01 §12 test 6). Offline, against written files."""

import json
from collections import Counter

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from shapely.geometry import Point, box, shape

from emberline.data import exposure, grid
from emberline.data.config import load_scenario, scenario_dir
from emberline.data.vectors import PROPS, WEST_ALTADENA

SCN = scenario_dir("eaton")
G = load_scenario("eaton")["grid"]
ASSET_TYPES = {"hospital", "nursing_home", "school", "fire_station", "substation", "water_plant"}

if not (SCN / "vectors" / "zones.geojson").exists():
    pytest.skip("run `python -m emberline.data.vectors --scenario eaton` first", allow_module_level=True)


def load(rel: str):
    return json.loads((SCN / rel).read_text(encoding="utf-8"))


ZFC = load("vectors/zones.geojson")
ZONES = ZFC["features"]
BY_ID = {f["properties"]["zone_id"]: shape(f["geometry"]) for f in ZONES}


def test_props_match_contract_exactly():
    for f in ZONES:
        p = f["properties"]
        assert tuple(p) == PROPS
        assert isinstance(p["population"], int) and isinstance(p["housing"], int)
        assert isinstance(p["name"], str) and p["name"] and p["name"] != "nan"
        assert f["geometry"]["type"] in ("Polygon", "MultiPolygon")


def test_tiers_and_evac_defaults():
    for f in ZONES:
        p = f["properties"]
        assert p["tier"] in (1, 2, 3)
        assert p["evac_min_default"] == (75 if p["tier"] == 1 else 45)
        assert p["egress_roads"] >= 0 and p["n_critical"] >= 0


def test_tier_and_n_critical_recomputed_from_assets():
    counts: dict[str, Counter] = {}
    for f in load("vectors/assets.geojson")["features"]:
        p = f["properties"]
        if p["zone_id"] is not None:
            counts.setdefault(p["zone_id"], Counter())[p["type"]] += 1
    for f in ZONES:
        p = f["properties"]
        c = counts.get(p["zone_id"], Counter())
        assert p["n_critical"] == c["hospital"] + c["nursing_home"] + c["school"], p["zone_id"]
        want = 1 if (c["hospital"] or c["nursing_home"] or p["egress_roads"] <= 1) else (
            2 if (p["population"] > 500 or c["school"]) else 3)
        assert p["tier"] == want, p["zone_id"]


def test_asset_dedup_keeps_outer_area():
    sq = lambda x, y, s: box(x, y, x + s, y + s)  # noqa: E731
    raw = gpd.GeoDataFrame({
        "element": ["way", "relation", "node", "way", "node"], "oid": [1, 2, 3, 4, 5],
        "name": ["A High", "A High", None, None, None], "amenity": ["school", "school", "school", "hospital", "school"],
        "social_facility": None, "power": None, "man_made": None,
    }, geometry=[sq(0, 0, 100), sq(10, 10, 20), Point(50, 50), sq(0, 0, 100), Point(500, 500)], crs=exposure.UTM)
    zones = gpd.GeoDataFrame({"zone_id": ["Z"]}, geometry=[sq(-10, -10, 1000)], crs=exposure.UTM)
    got = exposure.assets_from_osm(raw, zones)
    # inner relation and node collapse into way 1; the hospital (other type) and the far node stay
    assert list(got["asset_id"]) == ["osm_node_5", "osm_way_1", "osm_way_4"]


def test_west_altadena_zones_or_approximate():
    ids = set(BY_ID)
    assert len(ids & set(WEST_ALTADENA)) >= 13 or all(f["properties"]["approximate"] for f in ZONES)


def test_population_within_20pct_of_blocks():
    # Reference = POP100 of whole blocks whose interior point is in a zone (no area weighting, each
    # block once). Zones cover ~16% of the AOI, so the AOI-clipped block total isn't comparable; this
    # checks the area weighting and catches overlapping zones double-counting, not the zone set itself.
    total = sum(f["properties"]["population"] for f in ZONES)
    ref = ZFC["metadata"]["census_point_in_zone_pop"]
    assert ref > 0 and abs(total - ref) <= 0.2 * ref, (total, ref)


def test_zone_index_matches_template():
    t = grid.template(G)
    with rasterio.open(SCN / "static" / "zone_index.tif") as ds:
        arr = ds.read(1)
        assert ds.crs.to_epsg() == 32611
        assert ds.transform == grid.transform(G)
        assert json.loads(ds.tags()["zone_ids"]) == [f["properties"]["zone_id"] for f in ZONES]
    assert arr.shape == t.shape and arr.dtype == np.int16
    assert arr.min() >= -1 and arr.max() == len(ZONES) - 1


def test_zone_index_cell_lands_in_its_zone():
    with rasterio.open(SCN / "static" / "zone_index.tif") as ds:
        arr = ds.read(1)
    xs, ys = grid.cell_centers(G)
    for idx in np.unique(arr[arr >= 0])[::10]:
        r, c = np.argwhere(arr == idx)[0]
        lon, lat = grid.xy_to_lonlat(xs[c], ys[r], G)
        assert BY_ID[ZONES[idx]["properties"]["zone_id"]].buffer(1e-4).contains(Point(lon, lat))


def test_spawn_points_inside_their_zones():
    demo = load("web/spawn_points.json")["demo"]
    assert len(demo) == 6 and len({s["spawn_id"] for s in demo}) == 6
    for s in demo:
        assert set(s) == {"spawn_id", "lon", "lat", "zone_id", "street"}
        assert s["zone_id"] in WEST_ALTADENA and s["street"]
        assert BY_ID[s["zone_id"]].contains(Point(s["lon"], s["lat"]))


def test_assets_types_and_zone_join():
    for f in load("vectors/assets.geojson")["features"]:
        p = f["properties"]
        assert set(p) == {"asset_id", "type", "name", "zone_id"} and p["type"] in ASSET_TYPES
        if p["zone_id"] is not None:
            assert BY_ID[p["zone_id"]].buffer(1e-5).contains(shape(f["geometry"]))


def test_web_copies_identical():
    for name in ("zones.geojson", "assets.geojson"):
        assert (SCN / "web" / name).read_bytes() == (SCN / "vectors" / name).read_bytes()
