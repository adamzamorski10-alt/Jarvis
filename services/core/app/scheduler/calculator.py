"""
Pure schedule-calculation engine.

This module is responsible ONLY for determining the next scheduled occurrence
from an existing schedule definition and a reference time. It has no database
access, no filesystem access, no execution side effects, and no dependency
on the scheduler worker or any other runtime subsystem.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.scheduler.models import ScheduleType


class ScheduleCalculationError(Exception):
    """Raised when a schedule expression cannot be parsed or calculated."""
    pass


@dataclass(frozen=True)
class ScheduleDefinition:
    """Pure, serializable schedule definition for calculation."""
    schedule_type: ScheduleType
    schedule_expr: str
    timezone: Optional[str] = None


def calculate_next_occurrence(
    definition: ScheduleDefinition,
    reference_time: datetime,
) -> Optional[datetime]:
    """Return the next scheduled occurrence as a UTC datetime, or None if elapsed.

    Deterministic for identical definition + timezone + reference_time.
    Does not access the database, filesystem, network, or current wall clock.
    """
    if definition.schedule_type == ScheduleType.ONCE:
        return _calculate_once(definition.schedule_expr, reference_time)
    if definition.schedule_type == ScheduleType.INTERVAL:
        return _calculate_interval(definition.schedule_expr, reference_time)
    if definition.schedule_type == ScheduleType.CRON:
        return _calculate_cron(definition, reference_time)
    raise ScheduleCalculationError(f"Unsupported schedule type: {definition.schedule_type}")


def validate_schedule_definition(definition: ScheduleDefinition) -> None:
    """Raise ScheduleCalculationError if the schedule definition is malformed."""
    if not definition.schedule_expr or not definition.schedule_expr.strip():
        raise ScheduleCalculationError("schedule_expr must not be empty")
    expr = definition.schedule_expr.strip()
    if definition.schedule_type == ScheduleType.ONCE:
        _parse_iso_datetime(expr)
    elif definition.schedule_type == ScheduleType.INTERVAL:
        _parse_interval(expr)
    elif definition.schedule_type == ScheduleType.CRON:
        _parse_cron(expr)
    else:
        raise ScheduleCalculationError(f"Unsupported schedule type: {definition.schedule_type}")
    if definition.timezone is not None and not definition.timezone.strip():
        definition = ScheduleDefinition(
            schedule_type=definition.schedule_type,
            schedule_expr=definition.schedule_expr,
            timezone=None,
        )
    if definition.timezone is not None:
        _validate_timezone(definition.timezone)
# ---------------------------------------------------------------------------
# ONCE
# ---------------------------------------------------------------------------

_ISO_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f%z",
    "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M%z",
    "%Y-%m-%dT%H:%MZ",
    "%Y-%m-%dT%H:%M",
)


def _parse_iso_datetime(expr: str) -> datetime:
    for fmt in _ISO_FORMATS:
        try:
            dt = datetime.strptime(expr, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    raise ScheduleCalculationError(f"Invalid ISO datetime: {expr!r}")


def _calculate_once(expr: str, reference_time: datetime) -> Optional[datetime]:
    target = _parse_iso_datetime(expr)
    if reference_time.tzinfo is None:
        raise ScheduleCalculationError("reference_time must be timezone-aware")
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    if target < reference_time:
        return None
    return target


# ---------------------------------------------------------------------------
# INTERVAL
# ---------------------------------------------------------------------------

_MAX_INTERVAL_SECONDS = 10 * 365 * 24 * 60 * 60  # 10 years


def _parse_interval(expr: str) -> int:
    expr = expr.strip()
    if not expr:
        raise ScheduleCalculationError("Interval expression must not be empty")
    if not re.fullmatch(r"[0-9]+", expr):
        raise ScheduleCalculationError(f"Invalid interval expression: {expr!r}")
    seconds = int(expr)
    if seconds <= 0:
        raise ScheduleCalculationError(f"Interval must be positive, got {seconds}")
    if seconds > _MAX_INTERVAL_SECONDS:
        raise ScheduleCalculationError(f"Interval exceeds maximum allowed ({_MAX_INTERVAL_SECONDS}s): {seconds}")
    return seconds


def _calculate_interval(expr: str, reference_time: datetime) -> datetime:
    seconds = _parse_interval(expr)
    delta = timedelta(seconds=seconds)
    # Ensure reference_time is timezone-aware (calculator boundary requirement)
    if reference_time.tzinfo is None:
        raise ScheduleCalculationError("reference_time must be timezone-aware")
    return reference_time + delta


# ---------------------------------------------------------------------------
# CRON
# ---------------------------------------------------------------------------

_CRON_FIELD_PATTERN = re.compile(
    r"^(?:[0-9]+(?:-[0-9]+)?(?:/[0-9]+)?|\*)(?:,(?:[0-9]+(?:-[0-9]+)?(?:/[0-9]+)?|\*))*$"
)


def _expand_cron_field(field: str, min_val: int, max_val: int) -> list[int]:
    """Expand a single cron field into a sorted list of matching values."""
    values = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            range_part, step_part = part.split("/", 1)
            if not step_part.isdigit():
                raise ScheduleCalculationError(f"Invalid cron step: {step_part!r}")
            step = int(step_part)
            if step <= 0:
                raise ScheduleCalculationError(f"Cron step must be positive, got {step}")
        else:
            range_part = part

        if range_part == "*":
            values.update(range(min_val, max_val + 1, step))
        elif "-" in range_part:
            segments = range_part.split("-", 1)
            if len(segments) != 2:
                raise ScheduleCalculationError(f"Invalid cron range: {range_part!r}")
            start_str, end_str = segments
            if not (start_str.isdigit() and end_str.isdigit()):
                raise ScheduleCalculationError(f"Invalid cron range: {range_part!r}")
            start = int(start_str)
            end = int(end_str)
            if not (min_val <= start <= end <= max_val):
                raise ScheduleCalculationError(f"Cron range out of bounds: {range_part!r}")
            values.update(range(start, end + 1, step))
        else:
            if not range_part.isdigit():
                raise ScheduleCalculationError(f"Invalid cron value: {part!r}")
            val = int(range_part)
            if not (min_val <= val <= max_val):
                raise ScheduleCalculationError(f"Cron value out of bounds: {val!r}")
            values.add(val)

    return sorted(values)


def _parse_cron(expr: str) -> tuple[list[int], list[int], list[int], list[int], list[int]]:
    expr = expr.strip()
    if not expr:
        raise ScheduleCalculationError("Cron expression must not be empty")
    parts = expr.split()
    if len(parts) != 5:
        raise ScheduleCalculationError(
            f"Cron expression must have exactly 5 fields, got {len(parts)}: {expr!r}"
        )
    minute = _expand_cron_field(parts[0], 0, 59)
    hour = _expand_cron_field(parts[1], 0, 23)
    day_of_month = _expand_cron_field(parts[2], 1, 31)
    month = _expand_cron_field(parts[3], 1, 12)
    day_of_week_raw = _expand_cron_field(parts[4], 0, 7)
    # Normalize 7 to 0 (Sunday), keep 0-6
    day_of_week = sorted(set(0 if v == 7 else v for v in day_of_week_raw))
    return minute, hour, day_of_month, month, day_of_week


def _validate_timezone(tz_name: str) -> None:
    from zoneinfo import ZoneInfo
    try:
        ZoneInfo(tz_name)
    except Exception:
        raise ScheduleCalculationError(f"Invalid timezone identifier: {tz_name!r}")


def _get_zoneinfo(tz_name: Optional[str]):
    from zoneinfo import ZoneInfo
    if tz_name is None:
        return ZoneInfo("UTC")
    _validate_timezone(tz_name)
    return ZoneInfo(tz_name)


def _is_ambiguous_local_time(naive_dt: datetime, tz) -> bool:
    if _is_nonexistent_local_time(naive_dt, tz):
        return False
    dt0 = naive_dt.replace(tzinfo=tz, fold=0)
    dt1 = naive_dt.replace(tzinfo=tz, fold=1)
    return dt0.utcoffset() != dt1.utcoffset()


def _is_nonexistent_local_time(naive_dt: datetime, tz) -> bool:
    dt = naive_dt.replace(tzinfo=tz)
    utc_dt = dt.astimezone(timezone.utc)
    back = utc_dt.astimezone(tz)
    return back.replace(tzinfo=None) != naive_dt


def _naive_to_utc(naive_dt: datetime, tz) -> datetime:
    if _is_nonexistent_local_time(naive_dt, tz):
        raise ScheduleCalculationError(
            f"Nonexistent local time: {naive_dt.isoformat()}"
        )
    if _is_ambiguous_local_time(naive_dt, tz):
        dt = naive_dt.replace(tzinfo=tz, fold=0)
    else:
        dt = naive_dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def _day_of_week_matches(date_obj: datetime.date, day_of_week: list[int]) -> bool:
    dow = (date_obj.weekday() + 1) % 7
    return dow in day_of_week


def _calculate_cron(definition: ScheduleDefinition, reference_time: datetime) -> datetime:
    if reference_time.tzinfo is None:
        raise ScheduleCalculationError("reference_time must be timezone-aware")
    tz = _get_zoneinfo(definition.timezone)
    minute, hour, day_of_month, month, day_of_week = _parse_cron(definition.schedule_expr)

    ref_utc = reference_time.astimezone(timezone.utc)
    ref_local = ref_utc.astimezone(tz)

    current_date = ref_local.date()

    max_days = 4 * 365

    for _ in range(max_days):
        if (current_date.month in month and
            current_date.day in day_of_month and
            _day_of_week_matches(current_date, day_of_week)):
            for h in hour:
                for m in minute:
                    naive_dt = datetime(
                        current_date.year, current_date.month, current_date.day, h, m
                    )
                    if _is_nonexistent_local_time(naive_dt, tz):
                        continue
                    if _is_ambiguous_local_time(naive_dt, tz):
                        utc0 = _naive_to_utc(naive_dt, tz)
                        utc1 = naive_dt.replace(tzinfo=tz, fold=1).astimezone(timezone.utc)
                        for utc_dt in sorted((utc0, utc1)):
                            if utc_dt > ref_utc:
                                return utc_dt
                    else:
                        utc_dt = _naive_to_utc(naive_dt, tz)
                        if utc_dt > ref_utc:
                            return utc_dt

        current_date += timedelta(days=1)

    raise ScheduleCalculationError(
        "Cron schedule has no future occurrence within search bound"
    )
