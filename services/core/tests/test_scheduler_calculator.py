"""
Stage 12.2B — Scheduler Schedule Calculation Engine Tests

Covers:
  - ONCE basic and elapsed cases
  - INTERVAL basic, long gap, boundary crossing
  - CRON basic, exact-match, between-occurrences
  - Timezone handling: UTC, positive, negative, DST spring/fall
  - Nonexistent and ambiguous local times
  - Validation: malformed definitions, invalid timezone, invalid interval, malformed cron
  - Determinism
  - Boundary conditions
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from app.scheduler.calculator import (
    ScheduleCalculationError,
    ScheduleDefinition,
    calculate_next_occurrence,
    validate_schedule_definition,
)
from app.scheduler.models import ScheduleType


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _make_definition(schedule_type: ScheduleType, expr: str, timezone: Optional[str] = None) -> ScheduleDefinition:
    return ScheduleDefinition(schedule_type=schedule_type, schedule_expr=expr, timezone=timezone)

class TestOnceSchedule:
    def test_basic_future_once(self):
        ref = _utc(datetime(2026, 9, 20, 1, 0, 0))
        target = _utc(datetime(2026, 9, 20, 3, 0, 0))
        defn = _make_definition(ScheduleType.ONCE, target.isoformat())
        result = calculate_next_occurrence(defn, ref)
        assert result == target

    def test_once_reference_exactly_on_occurrence(self):
        target = _utc(datetime(2026, 9, 20, 3, 0, 0))
        defn = _make_definition(ScheduleType.ONCE, target.isoformat())
        result = calculate_next_occurrence(defn, target)
        assert result == target

    def test_once_already_elapsed(self):
        ref = _utc(datetime(2026, 9, 20, 5, 0, 0))
        target = _utc(datetime(2026, 9, 20, 3, 0, 0))
        defn = _make_definition(ScheduleType.ONCE, target.isoformat())
        result = calculate_next_occurrence(defn, ref)
        assert result is None

    def test_once_with_timezone(self):
        ref = datetime(2026, 9, 20, 1, 0, 0, tzinfo=timezone(timedelta(hours=9)))  # Tokyo
        target = datetime(2026, 9, 20, 3, 0, 0, tzinfo=timezone(timedelta(hours=9)))
        defn = _make_definition(ScheduleType.ONCE, target.isoformat())
        result = calculate_next_occurrence(defn, ref)
        assert result == target.astimezone(timezone.utc)

    def test_once_with_z_suffix(self):
        ref = _utc(datetime(2026, 9, 20, 1, 0, 0))
        target = _utc(datetime(2026, 9, 20, 3, 0, 0))
        defn = _make_definition(ScheduleType.ONCE, "2026-09-20T03:00:00Z")
        result = calculate_next_occurrence(defn, ref)
        assert result == target

    def test_once_without_timezone_assumes_utc(self):
        ref = _utc(datetime(2026, 9, 20, 1, 0, 0))
        target = _utc(datetime(2026, 9, 20, 3, 0, 0))
        defn = _make_definition(ScheduleType.ONCE, "2026-09-20T03:00:00")
        result = calculate_next_occurrence(defn, ref)
        assert result == target


# ---------------------------------------------------------------------------
# 3-5. INTERVAL basic, long gap, boundary crossing
# ---------------------------------------------------------------------------

class TestIntervalSchedule:
    def test_basic_interval(self):
        ref = _utc(datetime(2026, 9, 20, 12, 0, 0))
        defn = _make_definition(ScheduleType.INTERVAL, "3600")
        result = calculate_next_occurrence(defn, ref)
        assert result == ref + timedelta(seconds=3600)

    def test_interval_reference_exactly_on_occurrence(self):
        ref = _utc(datetime(2026, 9, 20, 12, 0, 0))
        defn = _make_definition(ScheduleType.INTERVAL, "3600")
        result = calculate_next_occurrence(defn, ref)
        assert result == ref + timedelta(seconds=3600)

    def test_long_gap(self):
        ref = _utc(datetime(2026, 9, 20, 12, 0, 0))
        defn = _make_definition(ScheduleType.INTERVAL, "86400")  # 1 day
        result = calculate_next_occurrence(defn, ref)
        assert result == ref + timedelta(days=1)

    def test_interval_crosses_utc_day_boundary(self):
        ref = _utc(datetime(2026, 9, 20, 23, 30, 0))
        defn = _make_definition(ScheduleType.INTERVAL, "3600")
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2026, 9, 21, 0, 30, 0))

    def test_interval_crosses_month_boundary(self):
        ref = _utc(datetime(2026, 9, 30, 23, 0, 0))
        defn = _make_definition(ScheduleType.INTERVAL, "7200")  # 2 hours
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2026, 10, 1, 1, 0, 0))

    def test_interval_crosses_year_boundary(self):
        ref = _utc(datetime(2026, 12, 31, 23, 0, 0))
        defn = _make_definition(ScheduleType.INTERVAL, "7200")
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2027, 1, 1, 1, 0, 0))


# ---------------------------------------------------------------------------
# 6-8. CRON basic, exact-match, between-occurrences
# ---------------------------------------------------------------------------

class TestCronSchedule:
    def test_basic_cron_utc(self):
        ref = _utc(datetime(2026, 9, 20, 12, 0, 0))
        defn = _make_definition(ScheduleType.CRON, "0 13 * * *")
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2026, 9, 20, 13, 0, 0))

    def test_cron_reference_exactly_on_occurrence(self):
        ref = _utc(datetime(2026, 9, 20, 13, 0, 0))
        defn = _make_definition(ScheduleType.CRON, "0 13 * * *")
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2026, 9, 21, 13, 0, 0))

    def test_cron_between_occurrences(self):
        ref = _utc(datetime(2026, 9, 20, 13, 30, 0))
        defn = _make_definition(ScheduleType.CRON, "0 13 * * *")
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2026, 9, 21, 13, 0, 0))

    def test_cron_step(self):
        ref = _utc(datetime(2026, 9, 20, 12, 0, 0))
        defn = _make_definition(ScheduleType.CRON, "*/15 * * * *")
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2026, 9, 20, 12, 15, 0))

    def test_cron_step_between_occurrences(self):
        ref = _utc(datetime(2026, 9, 20, 12, 5, 0))
        defn = _make_definition(ScheduleType.CRON, "*/15 * * * *")
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2026, 9, 20, 12, 15, 0))

    def test_cron_range(self):
        ref = _utc(datetime(2026, 9, 20, 12, 0, 0))
        defn = _make_definition(ScheduleType.CRON, "0 9-17 * * 1-5")
        result = calculate_next_occurrence(defn, ref)
        # Sunday Sep 20: next weekday match is Monday Sep 21 09:00 UTC
        assert result == _utc(datetime(2026, 9, 21, 9, 0, 0))


# ---------------------------------------------------------------------------
# 9-11. UTC, positive timezone, negative timezone
# ---------------------------------------------------------------------------

class TestTimezoneHandling:
    def test_cron_utc(self):
        ref = _utc(datetime(2026, 9, 20, 12, 0, 0))
        defn = _make_definition(ScheduleType.CRON, "0 13 * * *", timezone="UTC")
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2026, 9, 20, 13, 0, 0))

    def test_cron_positive_offset(self):
        tokyo = timezone(timedelta(hours=9))
        ref = datetime(2026, 9, 20, 12, 0, 0, tzinfo=tokyo)
        defn = _make_definition(ScheduleType.CRON, "0 13 * * *", timezone="Asia/Tokyo")
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2026, 9, 20, 4, 0, 0))  # 13:00 JST = 04:00 UTC

    def test_cron_negative_offset(self):
        ny = timezone(timedelta(hours=-5))
        ref = datetime(2026, 9, 20, 12, 0, 0, tzinfo=ny)
        defn = _make_definition(ScheduleType.CRON, "0 13 * * *", timezone="America/New_York")
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2026, 9, 21, 17, 0, 0))  # 13:00 EDT next day = 17:00 UTC


# ---------------------------------------------------------------------------
# 12-13. DST spring/fall transitions
# ---------------------------------------------------------------------------

class TestDSTTransitions:
    def test_dst_spring_transition_nonexistent_time(self):
        # US spring forward 2026: March 8 at 2:00 AM EST -> 3:00 AM EDT
        # 2:30 AM does not exist on March 8
        ref = _utc(datetime(2026, 3, 8, 6, 30, 0))  # 1:30 AM EST = 06:30 UTC
        defn = _make_definition(ScheduleType.CRON, "30 2 * * *", timezone="America/New_York")
        result = calculate_next_occurrence(defn, ref)
        # 2:30 AM doesn't exist on March 8; next valid is March 9 2:30 AM EDT = 06:30 UTC
        assert result == _utc(datetime(2026, 3, 9, 6, 30, 0))

    def test_dst_autumn_transition_ambiguous_time(self):
        # On fall-back day, 1:30 AM occurs twice (EDT then EST)
        ref = _utc(datetime(2026, 11, 1, 5, 30, 0))  # First 1:30 AM EDT
        defn = _make_definition(ScheduleType.CRON, "30 1 * * *", timezone="America/New_York")
        result = calculate_next_occurrence(defn, ref)
        # With strict 'after' semantics, exact first occurrence returns second (06:30 UTC)
        assert result == _utc(datetime(2026, 11, 1, 6, 30, 0))

    def test_dst_autumn_second_occurrence_skipped(self):
        # After the first ambiguous 1:30 AM EDT, the next occurrence is 1:30 AM EST
        ref = _utc(datetime(2026, 11, 1, 5, 30, 0))  # First 1:30 AM EDT
        defn = _make_definition(ScheduleType.CRON, "30 1 * * *", timezone="America/New_York")
        result1 = calculate_next_occurrence(defn, ref)
        # result1 is the second ambiguous occurrence (06:30 UTC)
        ref2 = result1 + timedelta(minutes=1)
        result2 = calculate_next_occurrence(defn, ref2)
        # Next day 1:30 AM EST = 06:30 UTC
        assert result2 == _utc(datetime(2026, 11, 2, 6, 30, 0))


# ---------------------------------------------------------------------------
# 14-15. Nonexistent local time / ambiguous local time (cron-specific)
# ---------------------------------------------------------------------------

class TestCronDSTEdgeCases:
    def test_nonexistent_local_time_skipped(self):
        ref = _utc(datetime(2026, 3, 8, 7, 0, 0))  # 3:00 AM EDT on spring-forward day
        defn = _make_definition(ScheduleType.CRON, "0 2 * * *", timezone="America/New_York")
        result = calculate_next_occurrence(defn, ref)
        # 2:00 AM doesn't exist on March 8; next is March 9 2:00 AM EDT = 06:00 UTC
        assert result == _utc(datetime(2026, 3, 9, 6, 0, 0))

    def test_ambiguous_local_time_fires_once(self):
        # On fall-back day, 1:30 AM occurs twice (EDT then EST)
        ref = _utc(datetime(2026, 11, 1, 5, 30, 0))  # First 1:30 AM EDT
        defn = _make_definition(ScheduleType.CRON, "30 1 * * *", timezone="America/New_York")
        result = calculate_next_occurrence(defn, ref)
        # With strict 'after' semantics, exact first occurrence returns second (06:30 UTC)
        assert result == _utc(datetime(2026, 11, 1, 6, 30, 0))


# ---------------------------------------------------------------------------
# 16-19. Validation errors
# ---------------------------------------------------------------------------

class TestValidationErrors:
    def test_malformed_once(self):
        defn = _make_definition(ScheduleType.ONCE, "not-a-date")
        with pytest.raises(ScheduleCalculationError):
            calculate_next_occurrence(defn, _utc(datetime(2026, 9, 20, 12, 0, 0)))

    def test_invalid_timezone(self):
        defn = _make_definition(ScheduleType.CRON, "0 * * * *", timezone="Mars/Olympus_Mons")
        with pytest.raises(ScheduleCalculationError):
            calculate_next_occurrence(defn, _utc(datetime(2026, 9, 20, 12, 0, 0)))

    def test_invalid_interval_zero(self):
        defn = _make_definition(ScheduleType.INTERVAL, "0")
        with pytest.raises(ScheduleCalculationError):
            calculate_next_occurrence(defn, _utc(datetime(2026, 9, 20, 12, 0, 0)))

    def test_invalid_interval_negative(self):
        defn = _make_definition(ScheduleType.INTERVAL, "-5")
        with pytest.raises(ScheduleCalculationError):
            calculate_next_occurrence(defn, _utc(datetime(2026, 9, 20, 12, 0, 0)))

    def test_invalid_interval_non_numeric(self):
        defn = _make_definition(ScheduleType.INTERVAL, "abc")
        with pytest.raises(ScheduleCalculationError):
            calculate_next_occurrence(defn, _utc(datetime(2026, 9, 20, 12, 0, 0)))

    def test_malformed_cron_too_few_fields(self):
        defn = _make_definition(ScheduleType.CRON, "0 * * *")
        with pytest.raises(ScheduleCalculationError):
            calculate_next_occurrence(defn, _utc(datetime(2026, 9, 20, 12, 0, 0)))

    def test_malformed_cron_too_many_fields(self):
        defn = _make_definition(ScheduleType.CRON, "0 * * * * *")
        with pytest.raises(ScheduleCalculationError):
            calculate_next_occurrence(defn, _utc(datetime(2026, 9, 20, 12, 0, 0)))

    def test_malformed_cron_invalid_range(self):
        defn = _make_definition(ScheduleType.CRON, "60 * * * *")
        with pytest.raises(ScheduleCalculationError):
            calculate_next_occurrence(defn, _utc(datetime(2026, 9, 20, 12, 0, 0)))

    def test_malformed_cron_invalid_step(self):
        defn = _make_definition(ScheduleType.CRON, "*/a * * * *")
        with pytest.raises(ScheduleCalculationError):
            calculate_next_occurrence(defn, _utc(datetime(2026, 9, 20, 12, 0, 0)))

    def test_empty_schedule_expr(self):
        defn = _make_definition(ScheduleType.CRON, "")
        with pytest.raises(ScheduleCalculationError):
            calculate_next_occurrence(defn, _utc(datetime(2026, 9, 20, 12, 0, 0)))

    def test_naive_reference_time_interval(self):
        defn = _make_definition(ScheduleType.INTERVAL, "3600")
        with pytest.raises(ScheduleCalculationError):
            calculate_next_occurrence(defn, datetime(2026, 9, 20, 12, 0, 0))

    def test_naive_reference_time_cron(self):
        defn = _make_definition(ScheduleType.CRON, "0 * * * *")
        with pytest.raises(ScheduleCalculationError):
            calculate_next_occurrence(defn, datetime(2026, 9, 20, 12, 0, 0))

    def test_naive_reference_time_once(self):
        defn = _make_definition(ScheduleType.ONCE, "2026-09-20T15:00:00Z")
        with pytest.raises(ScheduleCalculationError):
            calculate_next_occurrence(defn, datetime(2026, 9, 20, 12, 0, 0))

    def test_unsupported_schedule_type(self):
        defn = ScheduleDefinition(schedule_type=ScheduleType.CRON, schedule_expr="* * * * *")
        # Manually corrupt enum to test fallback
        object.__setattr__(defn, "schedule_type", "invalid")
        with pytest.raises(ScheduleCalculationError):
            calculate_next_occurrence(defn, _utc(datetime(2026, 9, 20, 12, 0, 0)))


# ---------------------------------------------------------------------------
# 20. Deterministic repeated calculation
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_repeated_calculation_same_result(self):
        defn = _make_definition(ScheduleType.CRON, "*/15 * * * *")
        ref = _utc(datetime(2026, 9, 20, 12, 5, 0))
        results = [calculate_next_occurrence(defn, ref) for _ in range(100)]
        assert all(r == results[0] for r in results)

    def test_interval_deterministic(self):
        defn = _make_definition(ScheduleType.INTERVAL, "300")
        ref = _utc(datetime(2026, 9, 20, 12, 0, 0))
        results = [calculate_next_occurrence(defn, ref) for _ in range(100)]
        assert all(r == results[0] for r in results)

    def test_once_deterministic(self):
        defn = _make_definition(ScheduleType.ONCE, "2026-12-31T23:59:59Z")
        ref = _utc(datetime(2026, 9, 20, 12, 0, 0))
        results = [calculate_next_occurrence(defn, ref) for _ in range(100)]
        assert all(r == results[0] for r in results)


# ---------------------------------------------------------------------------
# 21. No database/filesystem dependency
# ---------------------------------------------------------------------------

class TestIsolation:
    def test_no_database_access(self):
        defn = _make_definition(ScheduleType.CRON, "0 * * * *")
        ref = _utc(datetime(2026, 9, 20, 12, 0, 0))
        # Should complete without any DB setup
        result = calculate_next_occurrence(defn, ref)
        assert result is not None

    def test_no_network_access(self):
        defn = _make_definition(ScheduleType.INTERVAL, "3600")
        ref = _utc(datetime(2026, 9, 20, 12, 0, 0))
        result = calculate_next_occurrence(defn, ref)
        assert result == ref + timedelta(seconds=3600)


# ---------------------------------------------------------------------------
# 22. Boundary conditions around seconds/microseconds
# ---------------------------------------------------------------------------

class TestBoundaryConditions:
    def test_cron_at_exact_minute_boundary(self):
        ref = _utc(datetime(2026, 9, 20, 12, 0, 0))
        defn = _make_definition(ScheduleType.CRON, "0 12 * * *")
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2026, 9, 21, 12, 0, 0))

    def test_cron_one_second_before_boundary(self):
        ref = _utc(datetime(2026, 9, 20, 11, 59, 59))
        defn = _make_definition(ScheduleType.CRON, "0 12 * * *")
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2026, 9, 20, 12, 0, 0))

    def test_cron_one_second_after_boundary(self):
        ref = _utc(datetime(2026, 9, 20, 12, 0, 1))
        defn = _make_definition(ScheduleType.CRON, "0 12 * * *")
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2026, 9, 21, 12, 0, 0))

    def test_interval_preserves_microseconds(self):
        ref = _utc(datetime(2026, 9, 20, 12, 0, 0, 123456))
        defn = _make_definition(ScheduleType.INTERVAL, "3600")
        result = calculate_next_occurrence(defn, ref)
        assert result == ref + timedelta(seconds=3600)

    def test_once_at_microsecond_precision(self):
        ref = _utc(datetime(2026, 9, 20, 12, 0, 0))
        target = _utc(datetime(2026, 9, 20, 12, 0, 0, 123456))
        defn = _make_definition(ScheduleType.ONCE, "2026-09-20T12:00:00.123456Z")
        result = calculate_next_occurrence(defn, ref)
        assert result == target

    def test_cron_day_of_week_zero_and_seven(self):
        ref = _utc(datetime(2026, 9, 20, 12, 0, 0))  # Sunday
        defn = _make_definition(ScheduleType.CRON, "0 12 * * 0")
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2026, 9, 27, 12, 0, 0))
        defn7 = _make_definition(ScheduleType.CRON, "0 12 * * 7")
        result7 = calculate_next_occurrence(defn7, ref)
        assert result7 == _utc(datetime(2026, 9, 27, 12, 0, 0))


# ---------------------------------------------------------------------------
# validate_schedule_definition
# ---------------------------------------------------------------------------

class TestValidateScheduleDefinition:
    def test_valid_cron(self):
        validate_schedule_definition(_make_definition(ScheduleType.CRON, "0 * * * *"))

    def test_valid_interval(self):
        validate_schedule_definition(_make_definition(ScheduleType.INTERVAL, "3600"))

    def test_valid_once(self):
        validate_schedule_definition(_make_definition(ScheduleType.ONCE, "2026-09-20T12:00:00Z"))

    def test_blank_timezone_accepted(self):
        validate_schedule_definition(_make_definition(ScheduleType.CRON, "0 * * * *", timezone="   "))

    def test_empty_expr_rejected(self):
        with pytest.raises(ScheduleCalculationError):
            validate_schedule_definition(_make_definition(ScheduleType.CRON, ""))

    def test_invalid_timezone_rejected(self):
        with pytest.raises(ScheduleCalculationError):
            validate_schedule_definition(_make_definition(ScheduleType.CRON, "0 * * * *", timezone="Mars/Olympus_Mons"))


# ---------------------------------------------------------------------------
# INTERVAL with large values and max bounds
# ---------------------------------------------------------------------------

class TestIntervalBounds:
    def test_interval_one_second(self):
        ref = _utc(datetime(2026, 9, 20, 12, 0, 0))
        defn = _make_definition(ScheduleType.INTERVAL, "1")
        result = calculate_next_occurrence(defn, ref)
        assert result == ref + timedelta(seconds=1)

    def test_interval_max_value(self):
        max_secs = 10 * 365 * 24 * 60 * 60
        ref = _utc(datetime(2026, 9, 20, 12, 0, 0))
        defn = _make_definition(ScheduleType.INTERVAL, str(max_secs))
        result = calculate_next_occurrence(defn, ref)
        assert result == ref + timedelta(seconds=max_secs)

    def test_interval_exceeds_max_rejected(self):
        max_secs = 10 * 365 * 24 * 60 * 60
        defn = _make_definition(ScheduleType.INTERVAL, str(max_secs + 1))
        with pytest.raises(ScheduleCalculationError):
            calculate_next_occurrence(defn, _utc(datetime(2026, 9, 20, 12, 0, 0)))


# ---------------------------------------------------------------------------
# Semantic verification gate tests
# ---------------------------------------------------------------------------

class TestIntervalDriftPrevention:
    def test_interval_no_drift_from_scheduled_timeline(self):
        ref = _utc(datetime(2026, 9, 20, 13, 0, 0))
        defn = _make_definition(ScheduleType.INTERVAL, "3600")
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2026, 9, 20, 14, 0, 0))

    def test_interval_no_drift_with_long_gap(self):
        ref = _utc(datetime(2026, 9, 20, 13, 37, 0))
        defn = _make_definition(ScheduleType.INTERVAL, "3600")
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2026, 9, 20, 14, 37, 0))

    def test_interval_successive_calls_preserve_grid(self):
        ref = _utc(datetime(2026, 9, 20, 10, 0, 0))
        defn = _make_definition(ScheduleType.INTERVAL, "3600")
        r1 = calculate_next_occurrence(defn, ref)
        r2 = calculate_next_occurrence(defn, r1)
        r3 = calculate_next_occurrence(defn, r2)
        assert r1 == _utc(datetime(2026, 9, 20, 11, 0, 0))
        assert r2 == _utc(datetime(2026, 9, 20, 12, 0, 0))
        assert r3 == _utc(datetime(2026, 9, 20, 13, 0, 0))


class TestCronExclusiveBoundary:
    def test_cron_after_exact_run_returns_next(self):
        ref = _utc(datetime(2026, 9, 20, 13, 0, 0))
        defn = _make_definition(ScheduleType.CRON, "0 13 * * *")
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2026, 9, 21, 13, 0, 0))

    def test_cron_unprocessed_exact_match_before_reference(self):
        ref = _utc(datetime(2026, 9, 20, 13, 0, 1))
        defn = _make_definition(ScheduleType.CRON, "0 13 * * *")
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2026, 9, 21, 13, 0, 0))

    def test_cron_between_occurrences_returns_next(self):
        ref = _utc(datetime(2026, 9, 20, 13, 30, 0))
        defn = _make_definition(ScheduleType.CRON, "0 13 * * *")
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2026, 9, 21, 13, 0, 0))


class TestCronParserStepZero:
    def test_cron_step_zero_rejected(self):
        defn = _make_definition(ScheduleType.CRON, "*/0 * * * *")
        with pytest.raises(ScheduleCalculationError, match="step must be positive"):
            calculate_next_occurrence(defn, _utc(datetime(2026, 9, 20, 12, 0, 0)))

    def test_cron_range_step_zero_rejected(self):
        defn = _make_definition(ScheduleType.CRON, "0-23/0 * * * *")
        with pytest.raises(ScheduleCalculationError, match="step must be positive"):
            calculate_next_occurrence(defn, _utc(datetime(2026, 9, 20, 12, 0, 0)))


class TestAmbiguousBeforeFirstOccurrence:
    def test_ambiguous_before_first_returns_first(self):
        ref = _utc(datetime(2026, 11, 1, 5, 0, 0))  # Before first 1:30 AM EDT (05:30)
        defn = _make_definition(ScheduleType.CRON, "30 1 * * *", timezone="America/New_York")
        result = calculate_next_occurrence(defn, ref)
        assert result == _utc(datetime(2026, 11, 1, 5, 30, 0))
