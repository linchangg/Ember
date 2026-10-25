import json
import math
import time
from types import SimpleNamespace

import numpy as np
import pytest
from pyproj import Transformer
from shapely.geometry import shape

from ember.runtime import summarize as s
from ember.scripts import make_fixture

# Eaton grid numbers: 340x340 at 90 m.
G = {"cell_m": 90.0, "nx": 340, "ny": 340, "x0": 381_480.0, "y0": 3_800_280.0, "crs": "EPSG:32611"}
ROS = (30.0, 25.0, 20.0, 15.0, 10.0)  # m/min per quantile (q10 earliest)
TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32611", always_xy=True)


def ellipse(T: float = 60.0, heading: float = 240.0):
    """t_q = max(0, dist/ros_q - T), focus-ellipse distance pushed toward `heading`."""
    n = G["nx"]
    cc = (np.arange(n) - n / 2 + 0.5) * G["cell_m"]
    dx, dy = np.meshgrid(cc, -cc)  # row 0 = north
    h = math.radians(heading)
    along = dx * math.sin(h) + dy * math.cos(h)
    r = np.hypot(dx, dy)
    dist = (r - 0.6 * along) / 0.4
    t_q = np.stack([np.maximum(0.0, dist / ros - T) for ros in ROS]).astype(np.float32)
    t_q[t_q > 360] = np.nan
    p = (dist < ROS[2] * T).astype(np.float32)
    return SimpleNamespace(t_q=t_q, p_burned_now=p)


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    out = tmp_path_factory.mktemp("cyc")
    arrays = ellipse()
    t0 = time.perf_counter()
    assets, danger, stats = s.write_assets(arrays, out, "cache/x/main/1", G, prev_burned_ha=100.0)
    dt = time.perf_counter() - t0
    print(f"\nsummarize write_assets 340x340: {dt:.3f} s")
    return arrays, out, assets, danger, stats


def test_encode_matches_fixture():
    rng = np.random.default_rng(1)
    t = rng.uniform(-50, 400, size=(5, 40, 40)).astype(np.float32)
    t.flat[:8] = [0, -1, np.nan, np.inf, -np.inf, 0.1, 360, 361]
    assert np.array_equal(s.encode_arrival(t), make_fixture.encode_arrival(t))


def test_arrival_bin(run):
    arrays, out, assets, _, _ = run
    raw = (out / "arrival_q.bin").read_bytes()
    assert len(raw) == 5 * 340 * 340
    v = np.frombuffer(raw, dtype=np.uint8).reshape(5, 340, 340)
    t = arrays.t_q
    assert np.array_equal(v == 255, np.isnan(t))
    assert np.all(t[v == 0] == 0)
    mid = (v > 0) & (v < 255)
    assert np.all(np.abs(2.0 * v[mid] - t[mid]) <= 2.0)
    assert assets == {
        "arrival_q": "cache/x/main/1/arrival_q.bin",
        "perimeter": "cache/x/main/1/perimeter.geojson",
        "isochrones": "cache/x/main/1/isochrones.geojson",
    }


def utm_area_ha(geom: dict) -> float:
    g = shape(geom)
    import shapely

    return shapely.transform(g, lambda xy: np.column_stack(TO_UTM.transform(xy[:, 0], xy[:, 1]))).area / 1e4


def test_perimeter(run):
    _, out, _, _, stats = run
    fc = json.loads((out / "perimeter.geojson").read_text())
    feats = {f["id"]: f for f in fc["features"]}
    assert len(fc["features"]) == 2 and set(feats) == {"burned", "front"}
    assert feats["burned"]["geometry"]["type"] == "MultiPolygon"
    assert feats["front"]["geometry"]["type"] == "MultiLineString"
    assert feats["front"]["geometry"]["coordinates"]
    lon, lat = feats["burned"]["geometry"]["coordinates"][0][0][0]
    assert -119 < lon < -117 and 33 < lat < 35  # lon/lat, never UTM
    assert stats["burned_ha"] > 0 and stats["front_km"] > 0
    assert abs(utm_area_ha(feats["burned"]["geometry"]) / stats["burned_ha"] - 1) < 0.05
    assert stats["growth_ha_per_h"] == pytest.approx((stats["burned_ha"] - 100.0) * 12, abs=1e-3)


def test_isochrones(run):
    _, out, _, _, _ = run
    feats = json.loads((out / "isochrones.geojson").read_text())["features"]
    leads = [f["properties"]["lead_min"] for f in feats]
    assert leads and leads == sorted(set(leads))
    assert all(f["properties"]["q"] == 0.5 for f in feats)
    assert all(f["geometry"]["type"] == "MultiLineString" for f in feats)


def test_danger(run):
    _, _, _, danger, _ = run
    assert set(danger) == {"lead60_p30", "lead120_p30"}
    for geom in danger.values():
        assert geom["type"] == "MultiPolygon" and geom["coordinates"]
        assert len(json.dumps(geom)) <= 10_000
    assert shape(danger["lead120_p30"]).buffer(1e-9).covers(shape(danger["lead60_p30"]))


def test_head_bearing(run):
    *_, stats = run
    assert stats["head_bearing_deg"] is not None
    assert abs((stats["head_bearing_deg"] - 240 + 180) % 360 - 180) <= 15


def test_nothing_burned(tmp_path):
    arrays = ellipse(T=-30.0)
    arrays.p_burned_now[:] = 0
    _, danger, stats = s.write_assets(arrays, tmp_path, "cache/x/main/0", G)
    fc = json.loads((tmp_path / "perimeter.geojson").read_text())
    assert [f["geometry"]["coordinates"] for f in fc["features"]] == [[], []]
    assert stats["burned_ha"] == 0 and stats["head_bearing_deg"] is None
    assert stats["front_km"] == 0 and stats["growth_ha_per_h"] == 0.0
    assert set(danger) == {"lead60_p30", "lead120_p30"}


def test_all_nan_empty(tmp_path):
    arrays = SimpleNamespace(t_q=np.full((5, 340, 340), np.nan, np.float32), p_burned_now=np.zeros((340, 340), np.float32))
    _, danger, stats = s.write_assets(arrays, tmp_path, "r", G)
    assert danger["lead60_p30"] == {"type": "MultiPolygon", "coordinates": []}
    assert json.loads((tmp_path / "isochrones.geojson").read_text())["features"] == []
    assert s.mask_to_multipolygon(np.zeros((340, 340), bool), G, 30) is None


def test_asset_receipt_detects_interruption_corruption_and_refuses_replacement(tmp_path):
    arrays = ellipse()
    s.write_assets(arrays, tmp_path, "cache/test/main/1/assets/key", G)
    s.verify_assets(tmp_path, 5 * 340 * 340)
    binary = tmp_path / "arrival_q.bin"
    raw = binary.read_bytes()
    binary.write_bytes(raw[:-1])
    with pytest.raises(ValueError, match="integrity"):
        s.verify_assets(tmp_path, 5 * 340 * 340)
    with pytest.raises(ValueError, match="immutable"):
        s.write_assets(arrays, tmp_path, "cache/test/main/1/assets/key", G)
    (tmp_path / "receipt.json").unlink()
    with pytest.raises(FileNotFoundError):
        s.verify_assets(tmp_path, 5 * 340 * 340)
