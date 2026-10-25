"""area_x hit definition (01 §2, 03 §9). Offline."""

from ember.data.area_x import area_x_satellite_hit, pixel_is_hit, t_sat_goes

CFG = {"area_x": {"lon_min": -118.165, "lon_max": -118.1316, "lat_min": 34.190, "lat_max": 34.235}}


def sq(lon0: float, lat0: float, w: float = 0.01, h: float = 0.01) -> list[list[float]]:
    return [[lon0, lat0], [lon0 + w, lat0], [lon0 + w, lat0 + h], [lon0, lat0 + h], [lon0, lat0]]


def test_inside_strong_is_hit():
    assert pixel_is_hit({"mask": 10, "ring": sq(-118.16, 34.20)}, CFG)
    assert area_x_satellite_hit([{"mask": 14, "ring": sq(-118.16, 34.20)}, {"mask": 30, "ring": sq(-118.16, 34.20)}], CFG)


def test_weak_mask_not_hit():
    assert not pixel_is_hit({"mask": 14, "ring": sq(-118.16, 34.20)}, CFG)
    assert not area_x_satellite_hit([{"mask": 14, "ring": sq(-118.16, 34.20)}], CFG)


def test_area_fraction():
    # box spans lat 34.190..; a 0.01° tall ring starting below lat_min by 60% / 40% of its height
    assert not pixel_is_hit({"mask": 10, "ring": sq(-118.16, 34.184)}, CFG)  # 40% inside
    assert pixel_is_hit({"mask": 10, "ring": sq(-118.16, 34.186)}, CFG)  # 60% inside


def test_vertex_east_of_lake_ave_not_hit():
    ring = sq(-118.145, 34.20, w=0.012)  # east edge at -118.133 is fine
    assert pixel_is_hit({"mask": 10, "ring": ring}, CFG)
    ring[1][0] = -118.131  # one vertex east of Lake Ave
    assert not pixel_is_hit({"mask": 10, "ring": ring}, CFG)


def scans(hits: list[bool]) -> list[tuple[int, bool]]:
    return [(1000 * (i + 1), h) for i, h in enumerate(hits)]


def test_t_sat_goes():
    F, T = False, True
    assert t_sat_goes(scans([F, T, F, F, T, T, F])) == 5000
    assert t_sat_goes(scans([T, F, F, T, F, F])) is None
    assert t_sat_goes(scans([F, T, T])) == 2000
    assert t_sat_goes([]) is None
