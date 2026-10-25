import numpy as np
from emberline.hazard.filter import (
    normalize,
    ess,
    temper,
    systematic_resample,
    liu_west,
)
from emberline.hazard.likelihoods import (
    GroundReportLikelihood,
    SatelliteLikelihood,
    residence_minutes,
)
from conftest import observation


def test_tempering_preserves_ess_and_weights_are_normalized():
    weights, alpha = temper(np.zeros(16), np.arange(16) * -20, 0.5)
    assert 0 < alpha < 1 and abs(weights.sum() - 1) < 1e-12 and ess(weights) >= 8 - 1e-8
    np.testing.assert_allclose(normalize(np.array([-1e9, -1e9])), [0.5, 0.5])


def test_systematic_resampling_and_liu_west_are_deterministic():
    weights = np.array([0.01, 0.09, 0.4, 0.5])
    values = np.arange(20, dtype=float).reshape(4, 5) / 100
    values[:, 3] = np.log(0.04)
    a = np.random.default_rng([7, 2])
    b = np.random.default_rng([7, 2])
    p = systematic_resample(weights, a)
    q = systematic_resample(weights, b)
    np.testing.assert_array_equal(p, q)
    np.testing.assert_array_equal(
        liu_west(values, weights, p, a, 0.97), liu_west(values, weights, q, b, 0.97)
    )


def test_ground_hot_negative_and_zero_trust(setup_model):
    model = setup_model[-1]()
    rec = observation(model, trust=1)
    arrivals = np.array([[[2]], [[np.inf]]], np.float32)
    prepared = {"spatial": np.ones((1, 1))}
    ll = GroundReportLikelihood.loglik(arrivals, rec, prepared, np.ones((1, 1)), 1, 5)
    assert ll[0] > ll[1]
    cold = rec.model_copy(update={"hot": False})
    negative = GroundReportLikelihood.loglik(
        arrivals, cold, prepared, np.ones((1, 1)), 1, 5
    )
    assert negative[0] < negative[1]
    zero = GroundReportLikelihood.loglik(
        arrivals, rec.model_copy(update={"trust": 0}), prepared, np.ones((1, 1)), 1, 5
    )
    np.testing.assert_array_equal(zero, [0, 0])


def test_satellite_uses_observation_time_not_issue_time(setup_model):
    model = setup_model[-1]()
    rec = observation(model, kind="goes_pixel", trust=1)
    arrivals = np.array([[[0]], [[np.inf]]], np.float32)
    prepared = {"spatial": np.ones((1, 1))}
    tau = np.array([[15]])
    early = SatelliteLikelihood.loglik(arrivals, rec, prepared, tau, 1, 10)
    late = SatelliteLikelihood.loglik(arrivals, rec, prepared, tau, 1, 50)
    assert early[0] > early[1] and late[0] == late[1]
    np.testing.assert_array_equal(
        residence_minutes(np.array([101, 121, 141, 161, 181, 201, 91])),
        [15, 45, 45, 90, 90, 90, 180],
    )
