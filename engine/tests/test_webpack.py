"""T9b: web manifest, terrain, story geometry, timeline (01 §6-§10). Offline, against written files.

Build first: `uv run --no-sync python -m ember.data.webpack --scenario eaton` (needs network once).
"""

from __future__ import annotations

import json

import numpy as np
import pytest
import rasterio
from PIL import Image

from ember.data.config import load_scenario, scenario_dir, utc_ms
from ember.data.grid import transform
from ember.data.terrain import aoi_bbox, dem10_path, terrarium_decode, terrarium_encode
from ember.data.truth import pst_to_utc_ms
from ember.data.webpack import (
    EVIDENCE, SENSOR_DATA, ZONES_DISCLOSED, ZONES_LABEL, has_data, parse_tle, tle_checksum_ok, zones_from_snapshot,
)

SCN = "eaton"
WEB = scenario_dir(SCN) / "web"
NUM = (int, float)

# ScenarioManifest, copied from spec/00 §4.1. Values: python type(s) or a nested spec.
MANIFEST: dict = {
    "scenario_id": str, "title": str, "tz": str, "config_hash": (str, type(None)), "is_fixture": bool,
    "honesty": {"real": list, "simulated": list},
    "aoi_bbox": list,
    "initial_view": {"longitude": NUM, "latitude": NUM, "zoom": NUM, "pitch": NUM, "bearing": NUM},
    "t_ignition_sim_ms": int, "replay_start_sim_ms": int, "replay_end_sim_ms": int,
    "horizon_min": NUM, "step_min": NUM, "quantiles": list,
    "grid": {"nx": int, "ny": int, "cell_m": NUM, "crs": str,
             "corners": {"tl": list, "tr": list, "br": list, "bl": list}},
    "terrain": {"elevation_url": str, "encoding": str, "bounds": list,
                "texture_url": str, "texture_date": str, "attribution": list},
    "static": {"zones_url": str, "assets_url": str, "sensors_url": str, "satellites_url": str,
               "timeline_url": str, "area_x_url": str, "spawn_points_url": str, "scans_dir": (str, type(None))},
    "lake_ave": list,
}  # fmt: skip


def _need(path):
    if not path.exists():
        pytest.skip(f"{path} not built; run python -m ember.data.webpack")
    return path


def _check(obj: dict, spec: dict, where: str = "") -> None:
    assert set(obj) == set(spec), f"{where}: keys {sorted(set(obj) ^ set(spec))}"
    for k, t in spec.items():
        if isinstance(t, dict):
            assert isinstance(obj[k], dict), f"{where}{k}"
            _check(obj[k], t, f"{where}{k}.")
        else:
            assert isinstance(obj[k], t) and not (t is NUM and isinstance(obj[k], bool)), f"{where}{k}: {obj[k]!r}"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(_need(WEB / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_keys_and_types(manifest):
    _check(manifest, MANIFEST)
    cfg = load_scenario(SCN)
    assert manifest["is_fixture"] is False and manifest["config_hash"] is None
    assert manifest["static"]["scans_dir"] is None
    # Only sources listed in scenario.yaml, and only those whose data is in the package, are claimed as real.
    real = [ZONES_LABEL if s == ZONES_DISCLOSED else s for s in manifest["honesty"]["real"]]
    assert set(real) <= set(cfg["honesty"]["real"])
    # Zones from the Jan 2025 event snapshot must carry the disclosure, never the bare label.
    if zones_from_snapshot(SCN):
        assert ZONES_DISCLOSED in manifest["honesty"]["real"] and ZONES_LABEL not in manifest["honesty"]["real"]
    # forcing/ is git-ignored (rebuilt locally, about 25 min), so a CI checkout can't see that evidence.
    local_only = {s for s, rel in EVIDENCE.items() if rel.startswith("forcing/")}
    assert all(has_data(SCN, EVIDENCE[s]) for s in real if s in EVIDENCE and s not in local_only)
    assert manifest["honesty"]["simulated"] == cfg["honesty"]["simulated"]
    assert manifest["t_ignition_sim_ms"] == utc_ms(cfg["t_ignition_utc"])
    assert manifest["grid"]["crs"] == "EPSG:32611" and manifest["terrain"]["encoding"] == "terrarium"
    assert len(manifest["aoi_bbox"]) == 4 and manifest["terrain"]["bounds"] == manifest["aoi_bbox"]
    for c in manifest["grid"]["corners"].values():
        assert len(c) == 2 and all(isinstance(v, float) for v in c)
    for k, url in manifest["static"].items():
        if url is not None:
            assert url.startswith(f"static/{SCN}/"), (k, url)
    for k in ("elevation_url", "texture_url"):
        assert (WEB / manifest["terrain"][k].split("/")[-1]).exists()
    assert manifest["terrain"]["texture_date"].strip()


def test_lake_ave_lon(manifest):
    coords = np.array(manifest["lake_ave"], dtype=float)
    assert coords.ndim == 2 and coords.shape[1] == 2 and len(coords) >= 2
    assert abs(coords[:, 0].mean() - (-118.1316)) <= 0.003
    feat = json.loads(_need(scenario_dir(SCN) / "vectors" / "lake_ave.geojson").read_text(encoding="utf-8"))
    assert feat["geometry"]["coordinates"] == manifest["lake_ave"]


def test_terrarium_roundtrip():
    elev = np.array([-12.3, 0.0, 68.3, 1234.567, 2241.5])
    assert np.abs(terrarium_decode(terrarium_encode(elev)) - elev).max() < 1 / 256 + 1e-9


def _raw_dem(dem, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    """Raw 10 m DEM (its own CRS, nearest sample) at the lon/lat centres of PNG pixels; independent of dem_lonlat."""
    from pyproj import Transformer

    w, s, e, n = aoi_bbox(SCN)
    lon, lat = w + (cols + 0.5) * (e - w) / 2048, n - (rows + 0.5) * (n - s) / 2048
    with rasterio.open(dem) as src:
        xs, ys = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True).transform(lon, lat)
        return np.array([v[0] for v in src.sample(zip(xs, ys, strict=True))], dtype=float)


def test_terrarium_png_matches_dem():
    png = _need(WEB / "terrain_elev.png")
    dem = _need(dem10_path(SCN))
    rgb = np.asarray(Image.open(png).convert("RGB"))
    assert rgb.shape[0] == rgb.shape[1] == 2048
    elev = terrarium_decode(rgb)
    assert 0 < elev.min() and elev.max() < 3100  # LA basin .. San Gabriels
    # known pixel: flat Pasadena, PNG vs the raw 10 m DEM sample at its centre
    assert abs(elev[1500, 800] - _raw_dem(dem, np.array([1500]), np.array([800]))[0]) < 0.5
    # georeferencing: median over 200 random pixels (foothills included) is ~0.58 m aligned,
    # 0.86-1.0 m at a half-pixel shift and ~2 m at one pixel, so 0.8 m catches a shift
    rc = np.random.default_rng(0).integers(2, 2046, size=(200, 2))
    err = np.abs(elev[rc[:, 0], rc[:, 1]] - _raw_dem(dem, rc[:, 0], rc[:, 1]))
    assert np.median(err) < 0.8, np.median(err)


def test_parse_tle_checks_name_and_id():
    # canned TLE (made-up elements, real NORAD id); checksum digits computed, not copied
    l1 = _with_checksum("1 43013U 17073A   25007.50000000  .00000000  00000-0  00000-0 0  999")
    l2 = _with_checksum("2 43013  98.7000 100.0000 0001000  90.0000 270.0000 14.1950000000000")
    tle = "\n".join(["NOAA 20", l1, l2]) + "\n"
    assert parse_tle(tle, 43013, "NOAA 20") == ("NOAA 20", [l1, l2])
    with pytest.raises(AssertionError, match="expected SUOMI NPP"):
        parse_tle(tle, 43013, "SUOMI NPP")
    with pytest.raises(AssertionError, match="NORAD id mismatch"):
        parse_tle(tle, 37849, "NOAA 20")
    bad_l2 = l2[:68] + str((int(l2[68]) + 1) % 10)
    with pytest.raises(AssertionError, match="checksum"):
        parse_tle("\n".join(["NOAA 20", l1, bad_l2]), 43013, "NOAA 20")


def _with_checksum(line68: str) -> str:
    assert len(line68) == 68
    return line68 + str(sum(int(c) if c.isdigit() else (1 if c == "-" else 0) for c in line68) % 10)


def test_area_x_index():
    path = _need(scenario_dir(SCN) / "static" / "area_x_index.tif")
    g = load_scenario(SCN)["grid"]
    with rasterio.open(path) as src:
        a = src.read(1)
        assert (src.height, src.width) == (g["ny"], g["nx"])
        assert src.crs.to_string() == g["crs"] and src.transform.almost_equals(transform(g))
        assert src.dtypes[0] == "int8"
    assert set(np.unique(a)) <= {0, 1} and a.sum() > 0


def test_timeline():
    rows = json.loads(_need(WEB / "timeline.json").read_text(encoding="utf-8"))
    assert rows == json.loads(_need(scenario_dir(SCN) / "truth" / "timeline.json").read_text(encoding="utf-8"))
    assert rows[0]["t_sim_ms"] == utc_ms(load_scenario(SCN)["t_ignition_utc"]) == pst_to_utc_ms("2025-01-07 18:15")
    assert [r["t_sim_ms"] for r in rows] == sorted(r["t_sim_ms"] for r in rows)
    for r in rows:
        assert r["kind"] in ("official", "report", "satellite")
        if r.get("as_observation"):
            assert r["approximate"] is True and r["note"]
        if "lon" in r:
            assert -118.29 < r["lon"] < -117.95 and 34.06 < r["lat"] < 34.34


def test_satellites_and_sensors():
    sats = json.loads(_need(WEB / "satellites.json").read_text(encoding="utf-8"))
    geo = {s["sat_id"]: s["geo_lon_deg"] for s in sats if s["orbit"] == "geo"}
    assert geo == {"goes18": -137.2, "goes16": -75.2}
    leo = {s["norad_id"]: s["tle"] for s in sats if s["orbit"] == "leo"}
    assert set(leo) == {37849, 43013, 54234}
    for norad, (l1, l2) in leo.items():
        assert int(l1[2:7]) == int(l2[2:7]) == norad and tle_checksum_ok(l1) and tle_checksum_ok(l2)
    sensors = json.loads(_need(WEB / "sensors.json").read_text(encoding="utf-8"))
    assert [s["sensor_id"] for s in sensors] == load_scenario(SCN)["assimilate_sensors"]
    assert all(s["approximate"] is True and s["note"] for s in sats if s["orbit"] == "geo")


def test_overpass_remark_is_not_cached(monkeypatch, tmp_path):
    import httpx

    from ember.data import story

    timeout = {"elements": [], "remark": 'runtime error: Query timed out in "query" at line 1 after 2 seconds.'}
    monkeypatch.setattr(story, "scenario_dir", lambda scn: tmp_path)
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(200, json=timeout, request=httpx.Request("POST", url)))
    with pytest.raises(RuntimeError, match="runtime error"):
        story.overpass("t", "[out:json];way(1);out;")
    assert not (tmp_path / "raw" / "osm").exists()


def test_sensors_without_data_are_not_shown_live():
    rows = json.loads(_need(WEB / "sensors.json").read_text(encoding="utf-8"))
    for r in rows:
        live = r["sensor_id"] not in SENSOR_DATA or has_data(SCN, SENSOR_DATA[r["sensor_id"]])
        assert (r["enabled"], r["status"]) == ((True, "ok") if live else (False, "down")), r["sensor_id"]
