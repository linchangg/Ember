import numpy as np
import pytest
from scipy.special import expit
from ember.hazard.calibration import gaussian_footprint, heat_detection_probability


def test_heat_likelihood_distinguishes_unburned_recent_and_old_fire():
    p = heat_detection_probability(
        np.array([[[np.inf]], [[10.0]], [[-90.0]]]), 10, [[1.0]], decay_min=10, a=8, b=4
    )
    assert p[0] == pytest.approx(expit(-4))
    assert p[1] == pytest.approx(expit(4))
    assert p[0] < p[2] < p[1]


def test_geolocation_is_an_average_of_probabilities_not_of_logits():
    arrivals = np.array([[[0.0, np.inf]]])
    p = heat_detection_probability(arrivals, 0, [[0.25, 0.75]], decay_min=10, a=8, b=4)
    assert p[0] == pytest.approx(0.25 * expit(4) + 0.75 * expit(-4))


def test_elliptical_location_kernel_normalizes_and_responds_to_covariance():
    y, x = np.mgrid[-2:3, -2:3]
    weights = gaussian_footprint(np.stack([x, y], axis=-1), [0, 0], [[4, 0], [0, 1]])
    assert weights.sum() == pytest.approx(1)
    assert weights[2, 3] > weights[3, 2]
    assert np.argmax(weights) == 12


@pytest.mark.parametrize("kwargs", [{"decay_min": 0}, {"a": -1}, {"b": np.nan}])
def test_invalid_calibration_parameters(kwargs):
    values = dict(decay_min=10, a=8, b=4)
    values.update(kwargs)
    with pytest.raises(ValueError):
        heat_detection_probability(np.zeros((2, 1, 1)), 0, [[1.0]], **values)


def test_invalid_covariance_and_unnormalized_footprint():
    with pytest.raises(ValueError):
        gaussian_footprint(np.zeros((2, 2, 2)), [0, 0], [[1, 2], [2, 1]])
    with pytest.raises(ValueError):
        heat_detection_probability(
            np.zeros((2, 1, 1)), 0, [[2]], decay_min=10, a=8, b=4
        )
