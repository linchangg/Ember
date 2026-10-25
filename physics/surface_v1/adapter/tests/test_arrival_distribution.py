"""Unconditional quantiles from member outcomes, including censoring."""

from __future__ import annotations

import numpy as np
import pytest

from adapter.arrivals import (
    ARRIVAL_INITIAL_UNKNOWN,
    ARRIVAL_INVALID,
    ARRIVAL_MODELED,
    ARRIVAL_NOT_REACHED,
    INITIAL_UNKNOWN_REJECT,
    PARTIAL_AGGREGATE_USABLE,
    PARTIAL_REJECT,
    Q_CENSORED,
    Q_FINITE,
    Q_INITIAL_UNKNOWN,
    Q_INVALID,
    MemberOutcome,
    order_statistic_rank,
    unconditional_arrival_quantiles,
)
from adapter.conversions import SPECIFIED_APPLICATION_QUANTILES
from adapter.errors import UnsupportedMapping, ValidationError

HORIZON_S = 600.0
T_REF_MS = 1_736_294_400_000
T_START_MS = T_REF_MS


def _one(status: int, time_s: float = 0.0) -> MemberOutcome:
    return MemberOutcome(
        status=np.array(status, dtype=np.uint8),
        time_s=np.array(time_s, dtype=np.float64),
    )


def _quantiles(members: list[MemberOutcome], levels: tuple[float, ...] | list[float], **kwargs: object):
    params = dict(
        requested_member_count=len(members),
        quantile_levels=levels,
        horizon_s=HORIZON_S,
        partial_policy=PARTIAL_REJECT,
        t_start_ms=T_START_MS,
        t_ref_ms=T_REF_MS,
        initial_unknown_policy=INITIAL_UNKNOWN_REJECT,
    )
    params.update(kwargs)
    return unconditional_arrival_quantiles(members, **params)  # type: ignore[arg-type]


def test_all_members_arrive() -> None:
    members = [_one(ARRIVAL_MODELED, 30.0), _one(ARRIVAL_MODELED, 90.0), _one(ARRIVAL_MODELED, 150.0)]
    result = _quantiles(members, (0.1, 0.5, 1.0))
    assert result.usable_member_count == 3
    assert result.ensemble_coverage == 1.0
    assert result.calibrated_probability is False
    assert np.all(result.status == Q_FINITE)
    np.testing.assert_allclose(
        result.time_since_t_ref_min,
        np.array([0.5, 1.5, 2.5], dtype=np.float32),
    )


def test_mixture_of_arrivals_and_censored_outcomes() -> None:
    # 60 s, 120 s, not reached, not reached. N_usable = 4.
    members = [
        _one(ARRIVAL_MODELED, 60.0),
        _one(ARRIVAL_MODELED, 120.0),
        _one(ARRIVAL_NOT_REACHED, 0.0),
        _one(ARRIVAL_NOT_REACHED, 0.0),
    ]
    result = _quantiles(members, (0.25, 0.5, 0.75))
    assert result.time_since_t_ref_min[0] == np.float32(1.0)
    assert result.time_since_t_ref_min[1] == np.float32(2.0)
    assert np.isnan(result.time_since_t_ref_min[2])
    assert result.status[0] == Q_FINITE
    assert result.status[1] == Q_FINITE
    assert result.status[2] == Q_CENSORED
    # The two finite arrivals alone would put q75 at 120 s. That is not this distribution.
    assert not np.isfinite(result.time_since_t_ref_min[2])


def test_all_members_censored() -> None:
    members = [_one(ARRIVAL_NOT_REACHED), _one(ARRIVAL_NOT_REACHED)]
    result = _quantiles(members, (0.1, 0.9))
    assert np.all(result.status == Q_CENSORED)
    assert np.all(np.isnan(result.time_since_t_ref_min))


def test_invalid_cells_are_not_zero() -> None:
    members = [_one(ARRIVAL_INVALID), _one(ARRIVAL_MODELED, 10.0)]
    result = _quantiles(members, (0.5,))
    assert result.status[0] == Q_INVALID
    assert np.isnan(result.time_since_t_ref_min[0])
    assert result.cell_usable_count == 1
    assert result.cell_coverage == pytest.approx(0.5)


def test_failed_members_require_an_explicit_partial_policy() -> None:
    success = _one(ARRIVAL_MODELED, 40.0)
    failed = MemberOutcome(failed=True, failure_reason="numerical")
    with pytest.raises(ValidationError) as caught:
        _quantiles([success, failed], (0.5,), requested_member_count=2, partial_policy=PARTIAL_REJECT)
    assert caught.value.code == "partial_ensemble"
    result = _quantiles(
        [success, failed],
        (1.0,),
        requested_member_count=2,
        partial_policy=PARTIAL_AGGREGATE_USABLE,
    )
    assert result.requested_member_count == 2
    assert result.usable_member_count == 1
    assert result.failed_member_count == 1
    assert result.ensemble_coverage == pytest.approx(0.5)
    assert result.time_since_t_ref_min[0] == np.float32(40.0 / 60.0)
    assert result.label == "unweighted_scenario_frequency_uncalibrated"


def test_initially_burned_cells_are_rejected_until_interpreted() -> None:
    members = [
        _one(ARRIVAL_INITIAL_UNKNOWN, 0.0),
        _one(ARRIVAL_MODELED, 30.0),
    ]
    result = _quantiles(members, (0.5,))
    assert result.status[0] == Q_INITIAL_UNKNOWN
    assert np.isnan(result.time_since_t_ref_min[0])
    with pytest.raises(UnsupportedMapping):
        _quantiles(members, (0.5,), initial_unknown_policy="treat_as_time_zero")


def test_tied_arrivals_and_exact_cdf_threshold() -> None:
    tied = [
        _one(ARRIVAL_MODELED, 60.0),
        _one(ARRIVAL_MODELED, 60.0),
        _one(ARRIVAL_MODELED, 120.0),
        _one(ARRIVAL_NOT_REACHED),
    ]
    result = _quantiles(tied, (0.25, 0.5))
    # F(60) = 0.5, so both q25 and q50 are 60 s.
    assert result.time_since_t_ref_min[0] == np.float32(1.0)
    assert result.time_since_t_ref_min[1] == np.float32(1.0)
    pair = [_one(ARRIVAL_MODELED, 10.0), _one(ARRIVAL_MODELED, 20.0)]
    exact = _quantiles(pair, (0.5,))
    # F(10) = 0.5, so Q(0.5) is 10 s rather than a midpoint or the upper point.
    assert exact.time_since_t_ref_min[0] == np.float32(10.0 / 60.0)
    assert order_statistic_rank(0.5, 2) == 1
    assert order_statistic_rank(0.25, 4) == 1
    assert order_statistic_rank(0.75, 4) == 3


def test_empty_usable_sample_is_rejected() -> None:
    failed = MemberOutcome(failed=True, failure_reason="missing")
    with pytest.raises(ValidationError) as caught:
        _quantiles([failed], (0.5,), requested_member_count=1, partial_policy=PARTIAL_AGGREGATE_USABLE)
    assert caught.value.code == "empty_sample"


def test_invalid_quantile_levels() -> None:
    member = _one(ARRIVAL_MODELED, 10.0)
    for level in (0.0, -0.2, 1.1, float("nan")):
        with pytest.raises(ValidationError):
            _quantiles([member], (level,))
    with pytest.raises(ValidationError, match="duplicate"):
        _quantiles([member], (0.5, 0.5))
    with pytest.raises(UnsupportedMapping):
        _quantiles([member], (0.5,), weights=np.array([1.0]))


def test_quantile_monotonicity_and_specified_levels() -> None:
    rng = np.random.default_rng(7)
    times = rng.uniform(1.0, 500.0, size=8)
    members = []
    for index, time_s in enumerate(times):
        if index % 3 == 0:
            members.append(_one(ARRIVAL_NOT_REACHED, 0.0))
        else:
            members.append(_one(ARRIVAL_MODELED, float(time_s)))
    levels = SPECIFIED_APPLICATION_QUANTILES
    result = _quantiles(members, levels)
    assert result.quantile_levels == levels
    finite_so_far = -np.inf
    censored = False
    for status, minutes in zip(result.status, result.time_since_t_ref_min, strict=True):
        if censored:
            assert status == Q_CENSORED
            assert np.isnan(minutes)
        elif status == Q_CENSORED:
            censored = True
            assert np.isnan(minutes)
        else:
            assert status == Q_FINITE
            assert float(minutes) >= finite_so_far
            finite_so_far = float(minutes)


def test_seconds_to_minutes_with_unequal_origins() -> None:
    start = T_REF_MS + 120_000
    members = [_one(ARRIVAL_MODELED, 60.0), _one(ARRIVAL_NOT_REACHED)]
    result = _quantiles(members, (0.5, 0.9), t_start_ms=start, t_ref_ms=T_REF_MS)
    assert result.time_since_t_ref_min[0] == np.float32(3.0)
    assert result.status[1] == Q_CENSORED
    assert np.isnan(result.time_since_t_ref_min[1])


def test_two_dimensional_cells_keep_their_shape() -> None:
    status = np.array(
        [
            [ARRIVAL_MODELED, ARRIVAL_NOT_REACHED],
            [ARRIVAL_INVALID, ARRIVAL_INITIAL_UNKNOWN],
        ],
        dtype=np.uint8,
    )
    time_s = np.array([[15.0, 0.0], [0.0, 0.0]], dtype=np.float64)
    other_status = np.array(
        [
            [ARRIVAL_MODELED, ARRIVAL_NOT_REACHED],
            [ARRIVAL_INVALID, ARRIVAL_INITIAL_UNKNOWN],
        ],
        dtype=np.uint8,
    )
    other_time = np.array([[45.0, 0.0], [0.0, 0.0]], dtype=np.float64)
    result = _quantiles(
        [
            MemberOutcome(status=status, time_s=time_s),
            MemberOutcome(status=other_status, time_s=other_time),
        ],
        (0.5,),
    )
    assert result.time_since_t_ref_min.shape == (1, 2, 2)
    assert result.status[0, 0, 0] == Q_FINITE
    assert result.time_since_t_ref_min[0, 0, 0] == np.float32(15.0 / 60.0)
    assert result.status[0, 0, 1] == Q_CENSORED
    assert result.status[0, 1, 0] == Q_INVALID
    assert result.status[0, 1, 1] == Q_INITIAL_UNKNOWN
