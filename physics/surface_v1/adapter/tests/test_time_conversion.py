"""Time origins, availability, and float32 rounding."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from adapter.conversions import (
    TimeOrigins,
    assert_reference_preserved,
    assert_weather_available,
    bind_checkpoint_reference,
    float32_roundtrip_tolerance_minutes,
    format_rust_utc,
    minutes_after_issue,
    minutes_since_t_ref,
    parse_rust_utc_ms,
)
from adapter.errors import UnsupportedMapping, ValidationError


def _epoch_ms(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> int:
    stamped = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    return int(stamped.timestamp() * 1000)


def test_equal_and_unequal_origins() -> None:
    ref = _epoch_ms(2025, 1, 8)
    start = ref
    assert minutes_since_t_ref(start, ref, 60.0) == np.float32(1.0)
    start = ref + 135 * 60_000
    # 02:15 after t_ref, plus 90 seconds.
    expected = np.float32(135.0 + 90.0 / 60.0)
    assert minutes_since_t_ref(start, ref, 90.0) == expected
    assert minutes_since_t_ref(start, ref, 0.0) == np.float32(135.0)


def test_fractional_seconds() -> None:
    ref = _epoch_ms(2025, 1, 8)
    assert minutes_since_t_ref(ref, ref, 0.5) == np.float32(0.5 / 60.0)
    assert minutes_since_t_ref(ref, ref, 1.25) == np.float32(1.25 / 60.0)


def test_large_epoch_with_a_short_interval_uses_integer_milliseconds() -> None:
    ref = _epoch_ms(2025, 1, 8)
    start = ref + 12_500
    seconds = 12.5
    stored = minutes_since_t_ref(start, ref, seconds)
    exact = (start - ref) / 60_000 + seconds / 60.0
    assert stored == np.float32(exact)
    naive = np.float32(start / 60_000) - np.float32(ref / 60_000) + np.float32(seconds / 60.0)
    assert stored != naive
    assert abs(float(stored) - exact) <= float32_roundtrip_tolerance_minutes(exact)


def test_availability_cutoff_is_independent_of_valid_time() -> None:
    issue = _epoch_ms(2025, 1, 8, 2, 15)
    assert_weather_available(t_available_ms=issue, t_issue_ms=issue, t_valid_ms=issue)
    assert_weather_available(
        t_available_ms=issue - 1,
        t_issue_ms=issue,
        t_valid_ms=issue + 3_600_000,
        t_observed_ms=issue + 60_000,
    )
    with pytest.raises(ValidationError) as caught:
        assert_weather_available(t_available_ms=issue + 1, t_issue_ms=issue, t_valid_ms=issue - 10_000)
    assert caught.value.code == "unavailable"


def test_float32_round_trip_tolerance() -> None:
    ref = _epoch_ms(2025, 1, 8)
    start = ref + 86_400_000
    stored = minutes_since_t_ref(start, ref, 12.5)
    offset_min = (start - ref) / 60_000
    recovered_s = (float(stored) - offset_min) * 60.0
    again = minutes_since_t_ref(start, ref, recovered_s)
    assert again == stored
    assert abs(float(again) - float(stored)) <= float32_roundtrip_tolerance_minutes(stored)


def test_checkpoint_reference_time_stays_fixed() -> None:
    ref = _epoch_ms(2025, 1, 8)
    start = ref + 2 * 3_600_000 + 15 * 60_000
    meta = {
        "schema_version": 2,
        "model": "surface_v1",
        "run_manifest": {"start_utc": format_rust_utc(start)},
    }
    binding = bind_checkpoint_reference(meta, ref)
    assert binding.t_ref_ms == ref
    assert parse_rust_utc_ms(binding.t_start_utc) == start
    assert_reference_preserved(binding, meta, ref)
    shifted = {
        "schema_version": 2,
        "model": "surface_v1",
        "run_manifest": {"start_utc": format_rust_utc(start + 60_000)},
    }
    with pytest.raises(ValidationError) as caught:
        assert_reference_preserved(binding, shifted, ref)
    assert caught.value.code == "reference_time"
    with pytest.raises(ValidationError):
        assert_reference_preserved(binding, meta, ref + 1)


def test_nonfinite_and_unsupported_ranges_and_fractional_rust_timestamps() -> None:
    ref = _epoch_ms(2025, 1, 8)
    with pytest.raises(ValidationError):
        minutes_since_t_ref(ref, ref, float("nan"))
    with pytest.raises(ValidationError):
        minutes_since_t_ref(ref, ref, float("inf"))
    with pytest.raises(ValidationError):
        minutes_since_t_ref(ref, ref, 1.0e20)
    with pytest.raises(UnsupportedMapping, match="not truncated"):
        format_rust_utc(ref + 500)
    text = format_rust_utc(ref)
    assert text == "2025-01-08T00:00:00Z"
    with pytest.raises(UnsupportedMapping):
        format_rust_utc(-1000)


def test_minutes_after_issue_is_explicit_and_not_the_default_clock() -> None:
    ref = _epoch_ms(2025, 1, 8)
    start = ref + 10 * 60_000
    issue = ref + 25 * 60_000
    since_ref = minutes_since_t_ref(start, ref, 120.0)
    after_issue = minutes_after_issue(start, ref, issue, 120.0)
    assert since_ref == np.float32(12.0)
    assert after_issue == np.float32(12.0 - 25.0)
    origins = TimeOrigins(t_ref_ms=ref, t_start_ms=start, t_issue_ms=issue)
    assert origins.t_issue_ms != origins.t_start_ms
    assert origins.t_start_ms != origins.t_ref_ms
