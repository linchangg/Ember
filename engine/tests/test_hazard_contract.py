from dataclasses import fields
import inspect
import numpy as np
import pytest
from ember.hazard.api import HazardModel, ForecastArrays, ModelConfig
from ember.hazard.cdf import summarize_arrivals, encode_arrival, region_arrivals
from ember.hazard.geometry import north_to_south
from ember.hazard.model import RustHazardModel


def test_exact_public_contract():
    for name in [
        "init",
        "update_weather",
        "step",
        "region_arrivals",
        "save_state",
        "load_state",
    ]:
        assert list(inspect.signature(getattr(HazardModel, name)).parameters) == list(
            inspect.signature(getattr(RustHazardModel, name)).parameters
        )
    assert [f.name for f in fields(ForecastArrays)] == [
        "t_issue_min",
        "t_q",
        "p_burned_now",
        "weights",
        "ess",
        "tempering_alpha",
        "resampled",
        "filter_mode",
        "params_summary",
        "obs_used",
        "duration_ms",
    ]
    assert ModelConfig().quantiles == (0.1, 0.3, 0.5, 0.7, 0.9)


def test_unconditional_cdf_never_normalizes_away_no_arrival():
    t = np.array([[[11]], [[21]], [[np.inf]], [[np.inf]]], np.float32)
    q, p = summarize_arrivals(
        t, [0.1, 0.3, 0.3, 0.3], 10, 60, [0.1, 0.3, 0.5, 0.7, 0.9]
    )
    np.testing.assert_array_equal(q[:2, 0, 0], [2, 12])
    assert np.isnan(q[2:]).all() and p[0, 0] == 0


def test_cdf_burned_bins_and_frontend_bytes():
    t = np.array([[[0, 10.1, 12, 100]]], np.float32)
    q, p = summarize_arrivals(t, [1], 10, 60, [0.1, 0.3, 0.5, 0.7, 0.9])
    np.testing.assert_array_equal(encode_arrival(q)[0], [[0, 1, 1, 255]])
    assert p[0, 0] == 1 and p[0, 1] == 0
    assert q.dtype == np.float32 and p.dtype == np.float32


def test_north_south_conversion_is_asymmetric_and_contiguous():
    a = np.arange(24).reshape(2, 3, 4)
    flipped = north_to_south(a)
    np.testing.assert_array_equal(flipped[:, 0], a[:, -1])
    assert flipped.flags.c_contiguous and flipped.dtype == np.dtype("<f8")


def test_regions_equal_brute_force_and_missing_region_is_infinite():
    t = np.arange(24, dtype=np.float32).reshape(2, 3, 4)
    index = np.array([[0, 0, -1, -1], [1, 1, 1, -1], [-1, -1, -1, -1]], np.int32)
    got = region_arrivals(t, index, 3)
    np.testing.assert_array_equal(got[:, :2], [[0, 4], [12, 16]])
    assert np.isinf(got[:, 2]).all()
    with pytest.raises(ValueError):
        region_arrivals(t, np.zeros((2, 2), int), 1)


@pytest.mark.parametrize("weights", [[0, 0], [-1, 2], [np.nan, 1]])
def test_bad_cdf_weights_rejected(weights):
    with pytest.raises(ValueError):
        summarize_arrivals(np.ones((2, 2, 2)), weights, 0, 60, [0.5])


def test_shared_api_compatibility_probe():
    from ember.hazard.compatibility import check_contracts

    assert check_contracts()["wire_schema"] == 2
