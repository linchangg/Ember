"""Unconditional arrival distributions from individual member outcomes.

Rust ensemble exports are conditional on modeled arrival. This utility does
not reconstruct members from those q10/q50/q90 arrays. Not-reached outcomes
are right-censored at the horizon. They are not treated as events that never
arrive, and they are not dropped before the quantile is evaluated.

The estimator is the unweighted empirical distribution

    F(t) = (1 / N_usable) * count(T_k <= t)

    Q(q) = inf { t : F(t) >= q }

with censored members contributing no arrival at any finite t. That is
scenario frequency, not a calibrated or weighted probability.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adapter.conversions import minutes_since_t_ref
from adapter.errors import UnsupportedMapping, ValidationError

# Rust arrival_status codes from fire-types/src/output.rs.
ARRIVAL_INVALID = 0
ARRIVAL_NOT_REACHED = 1
ARRIVAL_INITIAL_UNKNOWN = 2
ARRIVAL_MODELED = 3

# Per-quantile research status. These are not arrival_q.bin sentinels.
Q_FINITE = 0
Q_CENSORED = 1
Q_INVALID = 2
Q_INITIAL_UNKNOWN = 3

PARTIAL_REJECT = "reject_incomplete"
PARTIAL_AGGREGATE_USABLE = "aggregate_usable"
INITIAL_UNKNOWN_REJECT = "reject"
FREQUENCY_UNWEIGHTED = "unweighted_scenario_frequency"

_QUANTILE_SCALE = 1_000_000


@dataclass(frozen=True)
class MemberOutcome:
    """One completed member, in one spatial order.

    ``failed`` members contribute no arrays. Their absence is counted against
    ``requested_member_count`` and is not filled with a zero arrival.
    """

    status: np.ndarray | None = None
    time_s: np.ndarray | None = None
    failed: bool = False
    failure_reason: str | None = None


@dataclass(frozen=True)
class ArrivalDistribution:
    """Research quantiles. Times are float32 minutes since ``t_ref``.

    Censored, invalid, and initial-unknown quantiles are NaN. ``status`` says
    which. ``label`` is scenario frequency, not a calibrated probability.
    """

    quantile_levels: tuple[float, ...]
    time_since_t_ref_min: np.ndarray
    status: np.ndarray
    requested_member_count: int
    usable_member_count: int
    failed_member_count: int
    ensemble_coverage: float
    cell_usable_count: np.ndarray
    cell_coverage: np.ndarray
    horizon_s: float
    t_ref_ms: int
    t_start_ms: int
    partial_policy: str
    initial_unknown_policy: str
    label: str
    calibrated_probability: bool = False


def order_statistic_rank(level: float, usable_count: int) -> int:
    """1-based rank ``ceil(level * N)`` for a level that is a multiple of 1e-6."""
    if usable_count < 1:
        raise ValidationError("order statistic rank needs a usable sample")
    scaled = _scaled_level(level)
    numerator = scaled * usable_count
    return (numerator + _QUANTILE_SCALE - 1) // _QUANTILE_SCALE


def _scaled_level(level: float) -> int:
    try:
        value = float(level)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"invalid quantile level {level!r}") from exc
    if not np.isfinite(value) or value <= 0.0 or value > 1.0:
        raise ValidationError(f"quantile level {level!r} is outside (0, 1]")
    scaled = round(value * _QUANTILE_SCALE)
    if abs(value - scaled / _QUANTILE_SCALE) > 1.0e-9:
        raise ValidationError(
            f"quantile level {level!r} is not a multiple of 1e-6"
        )
    if scaled <= 0 or scaled > _QUANTILE_SCALE:
        raise ValidationError(f"quantile level {level!r} is outside (0, 1]")
    return scaled


def validate_quantile_levels(levels: tuple[float, ...] | list[float]) -> tuple[float, ...]:
    if not isinstance(levels, (tuple, list)) or not levels:
        raise ValidationError("at least one quantile level is required")
    cleaned = tuple(float(level) for level in levels)
    for level in cleaned:
        _scaled_level(level)
    if len(set(cleaned)) != len(cleaned):
        raise ValidationError("duplicate quantile levels")
    return cleaned


def unconditional_arrival_quantiles(
    members: list[MemberOutcome] | tuple[MemberOutcome, ...],
    *,
    requested_member_count: int,
    quantile_levels: tuple[float, ...] | list[float],
    horizon_s: float,
    partial_policy: str,
    t_start_ms: int,
    t_ref_ms: int,
    initial_unknown_policy: str = INITIAL_UNKNOWN_REJECT,
    frequency_semantics: str = FREQUENCY_UNWEIGHTED,
    weights: np.ndarray | None = None,
) -> ArrivalDistribution:
    """Convert member outcomes to unconditional quantiles.

    ``partial_policy`` and ``frequency_semantics`` are required choices.
    ``weights`` is rejected: the planned application CDF is weighted, and this
    research utility does not silently drop or apply weights.
    """
    if frequency_semantics != FREQUENCY_UNWEIGHTED:
        raise UnsupportedMapping(
            "only unweighted scenario frequency is implemented; "
            "that is not a calibrated probability"
        )
    if weights is not None:
        raise UnsupportedMapping(
            "member weights belong to the planned weighted CDF; "
            "they are not applied or discarded by this unweighted utility"
        )
    if partial_policy not in {PARTIAL_REJECT, PARTIAL_AGGREGATE_USABLE}:
        raise ValidationError(
            "partial_policy must be 'reject_incomplete' or 'aggregate_usable'"
        )
    if initial_unknown_policy != INITIAL_UNKNOWN_REJECT:
        raise UnsupportedMapping(
            "initially burned cells have unknown arrival history; "
            f"policy {initial_unknown_policy!r} is not agreed"
        )
    if not isinstance(requested_member_count, int) or isinstance(requested_member_count, bool):
        raise ValidationError("requested_member_count must be an int")
    if requested_member_count < 1:
        raise ValidationError("requested_member_count must be positive")
    levels = validate_quantile_levels(quantile_levels)
    try:
        horizon = float(horizon_s)
    except (TypeError, ValueError) as exc:
        raise ValidationError("horizon_s must be a real number") from exc
    if not np.isfinite(horizon) or horizon <= 0.0:
        raise ValidationError("horizon_s must be finite and positive")

    if len(members) > requested_member_count:
        raise ValidationError("more member records than requested_member_count")
    failed = [member for member in members if member.failed]
    usable = [member for member in members if not member.failed]
    # Members that were requested but omitted entirely are also failures.
    omitted = requested_member_count - len(members)
    failed_count = len(failed) + omitted
    usable_count = len(usable)
    if usable_count + failed_count != requested_member_count:
        raise ValidationError("member counts do not add up to the request")
    if usable_count == 0:
        raise ValidationError(
            "empty usable sample; missing members are not treated as zero risk",
            code="empty_sample",
        )
    if usable_count != requested_member_count and partial_policy == PARTIAL_REJECT:
        raise ValidationError(
            f"{failed_count} of {requested_member_count} members are unavailable; "
            "reject_incomplete does not aggregate a partial ensemble",
            code="partial_ensemble",
        )

    statuses, times, shape = _stack_usable(usable)
    _reject_bad_modeled_times(statuses, times, horizon)
    invalid = np.any(statuses == ARRIVAL_INVALID, axis=0)
    unknown = np.any(statuses == ARRIVAL_INITIAL_UNKNOWN, axis=0)
    # A member that marks the cell invalid does not count toward that cell.
    cell_usable = np.count_nonzero(statuses != ARRIVAL_INVALID, axis=0).astype(np.int32)
    mixed_invalid = invalid & (cell_usable > 0)
    fully_invalid = invalid & (cell_usable == 0)
    # Any invalid member makes the cell's coverage incomplete. Quantiles from
    # the remaining members would hide that hole, so the cell stays undefined.
    blocked_invalid = invalid | mixed_invalid | fully_invalid

    sort_key = np.where(statuses == ARRIVAL_MODELED, times, np.inf)
    ordered = np.sort(sort_key, axis=0)
    q_time_s = np.empty((len(levels),) + shape, dtype=np.float64)
    q_status = np.empty((len(levels),) + shape, dtype=np.uint8)
    for index, level in enumerate(levels):
        rank = order_statistic_rank(level, usable_count)
        sample = ordered[rank - 1]
        finite = np.isfinite(sample)
        q_time_s[index] = np.where(finite, sample, np.nan)
        q_status[index] = np.where(finite, Q_FINITE, Q_CENSORED).astype(np.uint8)

    _mask_quantiles(q_time_s, q_status, blocked_invalid, Q_INVALID)
    # Initial-unknown wins over a finite arrival in another member: the
    # production meaning of that history is not agreed, so the cell is rejected
    # rather than dated at zero or at the other members' arrivals.
    reject_unknown = unknown & ~blocked_invalid
    _mask_quantiles(q_time_s, q_status, reject_unknown, Q_INITIAL_UNKNOWN)

    minutes = np.full(q_time_s.shape, np.nan, dtype=np.float32)
    finite_mask = q_status == Q_FINITE
    if np.any(finite_mask):
        converted = [
            minutes_since_t_ref(t_start_ms, t_ref_ms, float(seconds))
            for seconds in np.asarray(q_time_s[finite_mask], dtype=np.float64).ravel()
        ]
        minutes[finite_mask] = np.asarray(converted, dtype=np.float32)

    coverage = usable_count / requested_member_count
    cell_usable = np.asarray(cell_usable, dtype=np.int32)
    cell_coverage = cell_usable.astype(np.float64) / requested_member_count
    return ArrivalDistribution(
        quantile_levels=levels,
        time_since_t_ref_min=minutes,
        status=q_status,
        requested_member_count=requested_member_count,
        usable_member_count=usable_count,
        failed_member_count=failed_count,
        ensemble_coverage=coverage,
        cell_usable_count=cell_usable,
        cell_coverage=cell_coverage,
        horizon_s=horizon,
        t_ref_ms=t_ref_ms,
        t_start_ms=t_start_ms,
        partial_policy=partial_policy,
        initial_unknown_policy=initial_unknown_policy,
        label="unweighted_scenario_frequency_uncalibrated",
        calibrated_probability=False,
    )


def _mask_quantiles(
    time_s: np.ndarray,
    status: np.ndarray,
    mask: np.ndarray,
    code: int,
) -> None:
    if status.ndim == 1 and np.ndim(mask) == 0:
        if bool(mask):
            status[:] = code
            time_s[:] = np.nan
        return
    status[:, mask] = code
    time_s[:, mask] = np.nan


def _stack_usable(
    usable: list[MemberOutcome],
) -> tuple[np.ndarray, np.ndarray, tuple[int, ...]]:
    statuses = []
    times = []
    shape: tuple[int, ...] | None = None
    for member in usable:
        if member.status is None or member.time_s is None:
            raise ValidationError("a usable member is missing status or time")
        status = np.asarray(member.status)
        time_s = np.asarray(member.time_s, dtype=np.float64)
        if status.shape != time_s.shape:
            raise ValidationError("member status and time shapes differ", code="shape_mismatch")
        if status.ndim not in {0, 1, 2}:
            raise ValidationError("member outcomes must be scalar, 1D, or 2D")
        if shape is None:
            shape = tuple(status.shape)
        elif tuple(status.shape) != shape:
            raise ValidationError("member outcome shapes differ", code="shape_mismatch")
        if not np.isin(
            status,
            (
                ARRIVAL_INVALID,
                ARRIVAL_NOT_REACHED,
                ARRIVAL_INITIAL_UNKNOWN,
                ARRIVAL_MODELED,
            ),
        ).all():
            raise ValidationError("member status contains an unknown code")
        statuses.append(status.astype(np.uint8, copy=False))
        times.append(time_s)
    assert shape is not None
    return np.stack(statuses, axis=0), np.stack(times, axis=0), shape


def _reject_bad_modeled_times(status: np.ndarray, time_s: np.ndarray, horizon_s: float) -> None:
    modeled = status == ARRIVAL_MODELED
    if not modeled.any():
        return
    values = time_s[modeled]
    if not np.isfinite(values).all():
        raise ValidationError("modeled arrival times must be finite")
    if np.any(values < 0.0) or np.any(values > horizon_s + 1.0e-9):
        raise ValidationError("modeled arrival times must lie within the horizon")
