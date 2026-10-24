import numpy as np
import pytest
from emberline.hazard.geometry import signed_distance
from conftest import observation


def test_true_lonlat_order_and_subcell_footprint(setup_model):
    model = setup_model[-1]()
    rec = observation(model, east_cells=3)
    cache = model.geometry
    point = cache.prepare(rec)
    row, col = np.unravel_index(np.argmax(point["spatial"]), point["spatial"].shape)
    assert col > 10 and abs(row - 10) <= 2
    g = rec.geometry
    small = 0.00001
    ring = [
        [g.lon - small, g.lat - small],
        [g.lon + small, g.lat - small],
        [g.lon + small, g.lat + small],
        [g.lon - small, g.lat + small],
        [g.lon - small, g.lat - small],
    ]
    footprint = rec.model_copy(
        update={
            "kind": "viirs_pixel",
            "geometry": __import__(
                "emberline.contracts", fromlist=["Footprint"]
            ).Footprint(ring=ring),
        }
    )
    prepared = cache.prepare(footprint)
    assert prepared["in_domain"] and prepared["spatial"].sum() > 0
    assert prepared["support"].sum() <= 4


def test_reused_footprint_key_does_not_alias_geometry(setup_model):
    model = setup_model[-1]()
    a = observation(model, east_cells=2).model_copy(update={"footprint_key": "same"})
    b = observation(model, east_cells=8).model_copy(update={"footprint_key": "same"})
    x = model.geometry.prepare(a)
    y = model.geometry.prepare(b)
    assert not np.array_equal(x["support"], y["support"])


def test_signed_distance_has_no_zero_cells_and_keeps_orientation():
    mask = np.zeros((5, 8), bool)
    mask[1:3, 2:4] = True
    phi = signed_distance(mask, 90)
    assert (phi[mask] < 0).all() and (phi[~mask] > 0).all() and phi[1, 2] == -90
    with pytest.raises(ValueError):
        signed_distance(np.zeros((3, 3), bool), 90)


def test_true_north_basis_accounts_for_utm_convergence(setup_model):
    geometry = setup_model[-1]().geometry
    east, north = geometry.true_east, geometry.true_north
    np.testing.assert_allclose(np.linalg.norm(east, axis=0), 1, atol=1e-12)
    np.testing.assert_allclose(np.linalg.norm(north, axis=0), 1, atol=1e-12)
    assert np.max(np.abs(np.sum(east * north, axis=0))) < 1e-5
    # Eaton is west of UTM zone 11's central meridian; geographic north is not grid north.
    assert abs(float(north[0, 10, 15])) > 0.005


def test_mismatched_sensor_geometry_is_rejected(setup_model):
    model = setup_model[-1]()
    rec = observation(model).model_copy(update={"kind": "goes_pixel"})
    with pytest.raises(ValueError, match="kind and geometry"):
        model.step(5, [rec])


def test_camera_true_bearing_uses_geographic_north(setup_model):
    from emberline.contracts import BearingRay

    model = setup_model[-1]()
    point = observation(model, east_cells=0).geometry
    rec = observation(model).model_copy(
        update={
            "kind": "camera_bearing",
            "geometry": BearingRay(
                lon=point.lon,
                lat=point.lat,
                bearing_deg=90,
                sigma_deg=10,
                max_range_m=1500,
            ),
        }
    )
    footprint = model.geometry.prepare(rec)
    assert footprint["support"].any()
    row, col = np.unravel_index(
        np.argmax(footprint["spatial"]), footprint["spatial"].shape
    )
    assert (
        model.geometry.x[row, col]
        > model.geometry.transform.transform(point.lon, point.lat)[0]
    )
