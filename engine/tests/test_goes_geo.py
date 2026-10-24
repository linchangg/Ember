"""GOES filename times + parallax geometry (01 §2, research 01 §2). Network test gated."""

import os

import numpy as np
import pytest
from pyproj import Geod

from emberline.data.goes import correct_pixel, list_fdc, parallax_shift, parse_times, read_crop

needs_net = pytest.mark.skipif(not os.environ.get("EMBERLINE_NETWORK_TESTS"), reason="network")

KEY = "OR_ABI-L2-FDCC-M6_G18_s20250080200203_e20250080202576_c20250080203162.nc"


def test_parse_times():
    t = parse_times("noaa-goes18/ABI-L2-FDCC/2025/008/02/" + KEY)
    base = 1736301600000  # 2025-01-08T02:00:00Z
    assert t["scan_start_ms"] == base + 20_300
    assert t["scan_end_ms"] == base + 2 * 60_000 + 57_600
    assert t["created_ms"] == base + 3 * 60_000 + 16_200


def test_parallax_altadena_1000m():
    lon, lat = -118.12, 34.20
    lon_c, lat_c = parallax_shift(lon, lat, 1000.0)
    _, _, d = Geod(ellps="WGS84").inv(lon, lat, lon_c, lat_c)
    assert 900 <= d <= 1100
    assert lon_c < lon and lat_c < lat  # toward the south-west


def test_parallax_zero_elev():
    assert parallax_shift(-118.12, 34.20, 0.0) == (-118.12, 34.20)


def test_correct_pixel_moves_ring_with_centre():
    ring = [[-118.13, 34.19], [-118.11, 34.19], [-118.11, 34.21], [-118.13, 34.21]]
    lon_c, lat_c, ring_c = correct_pixel(-118.12, 34.20, ring, lambda lon, lat: 1000.0)
    assert (lon_c, lat_c) == parallax_shift(-118.12, 34.20, 1000.0)
    np.testing.assert_allclose(np.array(ring_c) - np.array(ring), [[lon_c + 118.12, lat_c - 34.20]] * 4, atol=1e-12)


@needs_net
def test_list_and_read_crop(tmp_path):
    keys = list_fdc("ABI-L2-FDCC", 8, range(2, 3))
    assert len(keys) == 12
    c = read_crop(keys[0], (-118.3, 34.05, -117.97, 34.33), cache=tmp_path / "c.npz")
    assert c["mask"].shape == c["dqf"].shape == c["power"].shape == c["lon"].shape == (len(c["y"]), len(c["x"]))
    c2 = read_crop(keys[0], (-118.3, 34.05, -117.97, 34.33), cache=tmp_path / "c.npz")  # from cache
    np.testing.assert_array_equal(c["mask"], c2["mask"])
