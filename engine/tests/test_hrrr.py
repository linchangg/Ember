import math
import os

import numpy as np
import pytest
import xarray as xr

from emberline.data import hrrr
from emberline.data.config import load_scenario, scenario_dir

needs_net = pytest.mark.skipif(not os.environ.get("EMBERLINE_NETWORK_TESTS"), reason="network")

INIT_MS = 1736316000000  # 2025-01-08T06:00Z


def test_rotation_deg_altadena():
    assert abs(float(hrrr.rotation_deg(-118.1)) - (-12.8)) < 0.1


def test_earth_relative_identity_and_magnitude():
    rng = np.random.default_rng(0)
    u, v = rng.normal(size=50), rng.normal(size=50)
    ue, ve = hrrr.to_earth_relative(u, v, np.full(50, -97.5))  # alpha = 0
    assert np.allclose(ue, u) and np.allclose(ve, v)
    lon = rng.uniform(-125, -70, size=50)
    ue, ve = hrrr.to_earth_relative(u, v, lon)
    assert np.allclose(np.hypot(ue, ve), np.hypot(u, v))


def test_wind_from_deg():
    assert hrrr.wind_from_deg(-1.0, -1.0) == pytest.approx(45.0)
    assert hrrr.wind_from_deg(0.0, -1.0) == pytest.approx(0.0)  # blowing south = from north
    assert hrrr.wind_from_deg(1.0, 0.0) == pytest.approx(270.0)


def test_available_ms():
    lm = INIT_MS + 51 * 60_000
    assert hrrr.available_ms(INIT_MS, 3, lm) == lm
    fallback = INIT_MS + 60 * 60_000 + 2 * 60_000 * 3
    assert hrrr.available_ms(INIT_MS, 3, INIT_MS + 4 * 3_600_000) == fallback
    assert hrrr.available_ms(INIT_MS, 3, None) == fallback
    assert hrrr.available_ms(INIT_MS, 3, INIT_MS - 1) == fallback


def test_output_contract_round_trip(tmp_path):
    g = {"crs": "EPSG:32611", "cell_m": 90, "nx": 4, "ny": 3, "x0": 381480, "y0": 3800280}
    from emberline.data.grid import cell_centers

    xs, ys = cell_centers(g)
    shape = (10, 3, 4)
    avail = [hrrr.available_ms(INIT_MS, f, None) for f in hrrr.FXX]
    ds = hrrr.forcing_dataset(np.full(shape, -3.0), np.full(shape, -4.0), np.full(10, 15.0),
                              np.full(10, 0.1), xs, ys, INIT_MS, avail, hrrr.SOURCE)
    p = hrrr.write_forcing(ds, tmp_path / "hrrr_2025010806.nc")
    with xr.open_dataset(p) as r:
        assert r["u10"].dims == r["v10"].dims == ("fxx", "y", "x")
        assert r["temp_c"].dims == r["rh"].dims == ("fxx",)
        assert all(r[v].dtype == np.float32 for v in ("u10", "v10", "temp_c", "rh"))
        assert list(r["fxx"].values) == list(range(10))
        assert r["y"].values[0] > r["y"].values[-1]  # row 0 = north
        assert np.allclose(r["x"].values, xs)
        assert int(r.attrs["init_ms"]) == INIT_MS
        assert [int(a) for a in np.atleast_1d(r.attrs["available_ms_by_fxx"])] == avail
        assert r.attrs["source"] == hrrr.SOURCE
        assert r.attrs["rotation"] == "earth-relative"
        assert r.attrs["crs"] == "EPSG:32611"
        assert float(r["u10"].max()) == pytest.approx(-3.0, abs=0.01)
    tag, deg, _ = hrrr.sign_check(tmp_path, at=hrrr.INITS[0].replace(hour=7, day=8))  # f00 available 07:00Z
    assert tag == "2025010806 f01" and deg == pytest.approx(math.degrees(math.atan2(3, 4)), abs=0.1)


@needs_net
def test_build_and_santa_ana_sign():
    # 05Z is the newest init available by 06Z (LastModified ~05:52Z), so it carries the 06Z band.
    hrrr.build("eaton", ["2025010805", "2025010806"], force=False)
    tag, deg, _ = hrrr.sign_check(scenario_dir("eaton") / "forcing")
    assert 0.0 <= deg <= 90.0, f"{tag}: wind from {deg:.1f}°, expected NE (Santa Ana)"
    assert load_scenario("eaton")["grid"]["nx"] == 340
