import json

import pytest

from emberline.data import grid
from emberline.data.config import load_scenario, scenario_dir, utc_ms

G = load_scenario("eaton")["grid"]


def test_utc_ms():
    assert utc_ms("2025-01-08T02:15:00Z") == 1736302500000


@pytest.mark.parametrize("key", ["tl", "tr", "br", "bl"])
def test_corner_round_trip_within_1m(key):
    x0, y0 = G["x0"], G["y0"]
    x1, y1 = x0 + G["nx"] * G["cell_m"], y0 - G["ny"] * G["cell_m"]
    utm = {"tl": (x0, y0), "tr": (x1, y0), "br": (x1, y1), "bl": (x0, y1)}[key]
    x, y = grid.lonlat_to_xy(*grid.corners_lonlat(G)[key], G)
    assert abs(x - utm[0]) < 1 and abs(y - utm[1]) < 1


def test_coverage_bbox():
    w, s, e, n = grid.bbox_lonlat(G)
    for got, want in [(w, -118.288), (s, 34.061), (e, -117.957), (n, 34.337)]:
        assert abs(got - want) < 0.01, (got, want)


@pytest.mark.parametrize("lon,lat", [(-118.088, 34.205), (-118.1316, 34.20)])
def test_story_points_inside(lon, lat):
    row, col = grid.xy_to_rowcol(*grid.lonlat_to_xy(lon, lat, G), G)
    assert 0 <= row < G["ny"] and 0 <= col < G["nx"]


def test_outside_is_minus_one():
    assert grid.xy_to_rowcol(G["x0"] - 1, G["y0"] - 1, G) == (-1, -1)
    assert grid.xy_to_rowcol(G["x0"] + 1, G["y0"] + 1, G) == (-1, -1)


def test_cell_centers():
    xs, ys = grid.cell_centers(G)
    assert (xs[0], ys[0]) == (381525, 3800235)
    assert (xs[339], ys[339]) == (381480 + 45 + 90 * 339, 3800280 - 45 - 90 * 339)
    assert grid.xy_to_rowcol(xs[339], ys[339], G) == (339, 339)


def test_template():
    t = grid.template(G)
    assert t.shape == (340, 340)
    assert t.rio.crs.to_epsg() == 32611
    assert t.rio.transform() == grid.transform(G)


def test_grid_json_fresh():
    on_disk = json.loads((scenario_dir("eaton") / "grid.json").read_text(encoding="utf-8"))
    assert on_disk == json.loads(json.dumps(grid.grid_json(G)))
