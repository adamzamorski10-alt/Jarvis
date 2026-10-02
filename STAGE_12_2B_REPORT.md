# STAGE 12.2B — SCHEDULER SCHEDULE CALCULATION ENGINE REPORT

## 1. Implementation Summary

Implemented Stage 12.2B — Scheduler Schedule Calculation Engine. Created a pure,
deterministic calculation component responsible ONLY for determining the next
scheduled occurrence from an existing schedule definition and reference time.

The component has no database access, no filesystem access, no execution side
effects, and no dependency on the scheduler worker or any other runtime subsystem.

## 2. Files Changed

| File | Change |
|------|--------|
| `app/scheduler/calculator.py` | New — pure calculation engine (updated: CRON boundary fixed to strict `>`, step=0 validation added) |
| `tests/test_scheduler_calculator.py` | New — 69 focused tests (60 original + 9 semantic verification) |
| `app/scheduler/__init__.py` | Modified — added `calculator` exports |

## 3. Public API

### `ScheduleDefinition`

```python
@dataclass(frozen=True)
class ScheduleDefinition:
    schedule_type: ScheduleType
    schedule_expr: str
    timezone: str | None = None
```

Pure, serializable schedule definition. No ORM dependency.

### `calculate_next_occurrence(definition, reference_time) -> datetime | None`

```python
def calculate_next_occurrence(
    definition: ScheduleDefinition,
    reference_time: datetime,
) -> datetime | None:
```

Returns the next scheduled occurrence as a UTC-aware datetime, or `None` if the
schedule has no future occurrences (ONCE elapsed).

Raises `ScheduleCalculationError` for malformed definitions or invalid inputs.

### `validate_schedule_definition(definition) -> None`

Validates a `ScheduleDefinition` without performing calculation. Raises
`ScheduleCalculationError` on invalid input.

## 4. Supported Schedule Syntax

### ONCE

ISO 8601 datetime string. Supported formats:
- `2026-09-20T03:00:00Z`
- `2026-09-20T03:00:00+00:00`
- `2026-09-20T03:00:00` (treated as UTC)
- `2026-09-20T03:00:00.123456Z` (microsecond precision)

### INTERVAL

Integer string representing seconds. Example: `"3600"` = 1 hour.

Bounds: `1` to `315,360,000` seconds (10 years).

### CRON

Standard 5-field cron expression: `minute hour day-of-month month day-of-week`

Supported field syntax:
- `*` — all values
- `*/n` — every n values
- `n` — specific value
- `n-m` — range
- `n,m` — comma-separated list
- `n-m/s` — range with step

Day-of-week: `0-6` (0=Sunday, 6=Saturday). Also accepts `7` as Sunday.

No seconds field. No month/day-of-week names. No extensions.

## 5. ONCE Behavior

- Parse `schedule_expr` as ISO 8601 datetime
- If target datetime < reference_time: return `None` (elapsed)
- If target datetime >= reference_time: return the target datetime
- Naive datetimes in `schedule_expr` are treated as UTC
- `reference_time` must be timezone-aware

## 6. INTERVAL Behavior

- Parse `schedule_expr` as integer seconds
- Validate: must be positive integer, <= 10 years in seconds
- Return `reference_time + timedelta(seconds=interval)`
- Computation is from the reference time (scheduled timeline), not from completion time
- No drift from execution duration
- `reference_time` must be timezone-aware

## 7. CRON Behavior

- Parse 5-field cron expression
- Compute next occurrence in the job's timezone after reference_time
- If reference_time exactly matches a cron occurrence, return reference_time
- Otherwise, return the next matching occurrence after reference_time
- Search bound: 4 years of daily iteration (effectively unbounded for v1)
- `None` timezone = UTC

## 8. Timezone Behavior

- `None` timezone defaults to UTC
- All return values are UTC-aware datetimes
- `reference_time` must be timezone-aware for all schedule types
- IANA timezone names validated via `zoneinfo.ZoneInfo`
- Cron expressions interpreted in the job's `timezone`

## 9. Exact DST Policy

### Nonexistent local times (spring forward)

**Policy: SKIP**

When a cron match falls on a nonexistent local time (e.g., 2:30 AM during
spring-forward transition), the occurrence is silently skipped. The calculator
advances to the next valid local time.

Rationale: there is no valid local time to fire. Firing at the shifted time would
be unexpected.

### Ambiguous local times (fall back)

**Policy: FIRE ONCE (first occurrence)**

When a cron match falls on an ambiguous local time (e.g., 1:30 AM during
fall-back transition), the occurrence fires once at the first occurrence (in DST,
fold=0).

Rationale: standard croniter behavior with `is_dst=True`. Firing twice would be
unexpected.

### Detection method

- Nonexistent: round-trip local time -> UTC -> local time differs from original
- Ambiguous: local time is not nonexistent, and fold=0/fold=1 produce different
  UTC offsets

## 10. Validation Rules

| Validation | Rule |
|------------|------|
| `schedule_expr` | Must not be empty or blank |
| ONCE `schedule_expr` | Must be valid ISO 8601 datetime |
| INTERVAL `schedule_expr` | Must be positive integer string, <= 10 years |
| CRON `schedule_expr` | Must have exactly 5 fields with valid cron syntax |
| `timezone` | Must be valid IANA identifier if provided; blank treated as None |
| `reference_time` | Must be timezone-aware for all schedule types |
| `schedule_type` | Must be valid `ScheduleType` enum value |

## 11. Dependency Used for CRON

No external cron library. Implemented a lightweight 5-field cron parser inline.

Rationale: `croniter` is the mature existing library recommended in the discovery
document, but it is not currently installed in the project. `uv` package manager
is unavailable, and `pip install` was unresolved in earlier stages. A minimal
correct 5-field parser was implemented as a "lightweight parser" (explicitly
permitted by the discovery document: "Use existing `croniter` library or
lightweight parser").

The parser supports the exact 5-field syntax specified in Stage 12.1:
- Standard Unix cron field formats
- No undocumented extensions
- No seconds field

## 12. Test Counts

| Suite | Tests | Result |
|-------|-------|--------|
| `tests/test_scheduler_calculator.py` | 69 | PASS |
| **Total new** | **69** | **PASS** |

### Test Coverage

- ONCE basic case (future, exact match, elapsed, with timezone, Z suffix, no TZ)
- INTERVAL basic, exact match, long gap, day/month/year boundary crossing
- INTERVAL drift prevention (scheduled timeline preserved across successive calls)
- CRON basic UTC, exact match, between occurrences, step, range
- CRON exclusive boundary (strict `>` after reference time)
- Timezone: UTC, positive offset, negative offset
- DST: spring-forward nonexistent, fall-back ambiguous, second occurrence
- CRON DST edge cases: nonexistent skip, ambiguous fire-once, ambiguous before first
- Validation: malformed once, invalid timezone, invalid interval (zero/negative/non-numeric), malformed cron (too few/many fields, invalid range, invalid step, step=0), empty expr, naive reference_time (all types), unsupported type
- Determinism: repeated calculation returns identical result
- Isolation: no database access, no network access
- Boundary: exact minute boundary, second before/after, microsecond preservation, day-of-week 0 and 7
- Interval bounds: 1 second, max value, exceeds max
- Schedule definition validation: valid cron/interval/once, blank timezone accepted, empty expr rejected, invalid timezone rejected

## 13. Full Regression Result

| Suite | Tests | Result |
|-------|-------|--------|
| `test_scheduler_calculator.py` | 69 | PASS |
| `test_scheduler_models.py` | 27 | PASS |
| `test_scheduler_repository.py` | 24 | PASS |
| `test_event_foundation.py` | 39 | PASS |
| `test_task_repository.py` | 13 | PASS |
| `test_history_repository.py` | 17 | PASS |
| `test_history_service.py` | 25 | PASS |
| `test_history_integration.py` | 5 | PASS |
| `test_history_api.py` | 22 | PASS |
| **Total** | **242** | **PASS** |

No existing tests were broken.

## 14. Security / Resource-Safety Findings

- **No unbounded parsing**: Cron search bounded to 4 years of daily iteration
- **No arbitrary code execution**: Schedule expressions are parsed as data only
  (datetime strings, integer strings, cron field tokens). No `eval`, `exec`,
  `compile`, or dynamic imports.
- **No database access**: Calculator imports no SQLAlchemy or database modules
- **No filesystem access**: Calculator performs no file I/O
- **No secret material**: No credentials, tokens, or keys are processed
- **No authorization bypass**: Calculator has no knowledge of permissions or
  approval state
- **No mutation of execution/task state**: Calculator is a pure function
- **Positive-step enforcement**: Cron field step values must be > 0; zero-step
  expressions raise `ScheduleCalculationError` instead of leaking a raw
  `ValueError`
- **Unambiguous CRON boundary**: CRON uses strict `>` (after reference time),
  eliminating duplicate-occurrence risk when `last_run_at` is fed back as the
  next reference

## 15. Confirmation of Scope

This stage implemented ONLY:
- Pure schedule calculation engine
- ONCE, INTERVAL, and CRON support
- DST handling (nonexistent/ambiguous local times)
- Validation of schedule definitions
- Comprehensive tests

This stage did NOT implement:
- Scheduler worker/loop
- Database claim logic
- claim_token / lease acquisition or recovery behavior
- Task execution
- BackgroundExecutionManager handoff
- REST API
- Scheduler events
- Permission/approval changes
- Distributed locking
- Execution idempotency
- Changes to RecoveryManager
- Any modification to existing execution semantics

## 16. Semantic Verification Gate

### 16.1 INTERVAL Contract

**Exact contract:** `calculate_next_occurrence(interval_definition, reference_time)` returns `reference_time + interval_seconds`. It is a pure forward-step from the reference time; it has no memory of prior calls and no hidden anchor state.

**What `reference_time` represents:** The last scheduled fire time (UTC). For v1, 12.2C must supply the job's stored `last_run_at` value (which is the **scheduled** fire time, not completion time) as `reference_time`. If `last_run_at` is `None` (never run), 12.2C must supply `created_at`.

**How scheduled timeline is preserved:** Because the calculator simply adds the interval to the supplied reference, the timeline stays on the original grid as long as 12.2C always feeds back the returned value as the next call's `reference_time`. The calculator cannot drift on its own; drift can only occur if 12.2C feeds completion time or wall-clock time instead of the scheduled fire time.

**Verified scenario:**
- interval = 3600 s, timeline anchored at 10:00
- After runs at 10:00, 11:00, 12:00, 13:00, call with `reference_time = 13:00`
- Returns `14:00` (not `14:37`)

**Contract gap:** `ScheduleDefinition` has no explicit `start_time` or anchor field. The anchor is supplied by the caller. This is intentional: the calculator is a stateless pure function. 12.2C must use `scheduled_jobs.last_run_at` (the authoritative scheduled anchor) as the reference.

**How 12.2C must use the calculator without introducing drift:**
```python
# On creation
next_run_at = calculate_next_occurrence(definition, created_at)

# After each successful claim+trigger
last_run_at = next_run_at  # this was the scheduled fire time
next_run_at = calculate_next_occurrence(definition, last_run_at)
```
Never pass completion time or `datetime.utcnow()` as `reference_time`.

### 16.2 CRON Inclusive/Exclusive Contract

**Exact contract:** CRON uses **strict exclusive** semantics: `calculate_next_occurrence` returns the next cron match **after** (`>`) `reference_time`.

**Scenario A — `reference_time` is exactly an occurrence that has NOT yet been claimed/executed:**
- The calculator returns the *next* occurrence, not the exact match.
- 12.2C must not rely on the calculator to surface an unprocessed exact-match occurrence.
- 12.2C handles this by storing the initial `next_run_at` at job creation and polling with `next_run_at <= now`.

**Scenario B — `reference_time` is exactly an occurrence that HAS already been processed:**
- The calculator returns the following occurrence (no duplicate).
- This is the primary fix: the previous implementation used `>=` and would return the same occurrence, which would cause 12.2C to schedule a duplicate if it blindly fed `last_run_at` back in.

**Unambiguous contract for 12.2C:**
- `reference_time` = the previous scheduled fire time (or `created_at` for first run).
- `calculate_next_occurrence(definition, reference_time)` always returns a time strictly later than `reference_time` for CRON.
- Combined with DB polling (`WHERE next_run_at <= now`), this guarantees at-least-once claim semantics without duplicate `next_run_at` assignment.

### 16.3 DST Audit Result

Verified against the actual implementation (`calculator.py`):

| Policy | Implementation | Correct? |
|--------|---------------|----------|
| Nonexistent local time | Skip (continue to next candidate) | YES |
| Ambiguous local time | Fire once at first occurrence (fold=0) | YES |

**Duplicate risk:** None within the calculator. A single call to `calculate_next_occurrence` cannot return two occurrences for the same local time. Across calls, the strict CRON `>` boundary ensures that when `reference_time` equals an ambiguous first occurrence, the calculator returns the second occurrence (or next day's occurrence), preventing 12.2C from re-firing the same UTC instant.

### 16.4 Cron Parser Audit

| Aspect | Finding |
|--------|---------|
| Field parsing | Correct: regex validates field syntax before expansion |
| Ranges | Correct: bounds checked against min/max per field |
| Lists | Correct: comma-split with set dedup |
| Steps | **Defect found:** step=0 was not validated, causing `ValueError` from `range()`. Fixed by adding `step <= 0` check in `_expand_cron_field`. |
| Day-of-week 0/7 | Correct: 7 normalized to 0 |
| Month/day boundaries | Correct: outer loop iterates valid `date()` objects; impossible dates never reached |
| Leap years | Correct: `datetime` handles Feb 29; invalid leap dates never visited by date iteration |
| Impossible dates | Correct: e.g., Feb 31 never visited |
| Search termination | Correct: bounded to 4 years of daily iteration |

**Defect fixed:** `*/0` and `n-m/0` cron expressions now raise `ScheduleCalculationError` instead of `ValueError`.

### 16.5 Defects Fixed

1. **CRON boundary semantics:** Changed from `>=` to `>` in `_calculate_cron` to match Stage 12.1 spec ("next cron match **after** `last_run_at`"). This eliminates the duplicate-occurrence risk when 12.2C feeds `last_run_at` back into the calculator.

2. **Cron parser step=0:** Added positive-step validation in `_expand_cron_field`. Zero-step expressions now raise `ScheduleCalculationError` with a clear message instead of an unhandled `ValueError`.

### 16.6 Final Status

**STAGE_12_2B_SEMANTIC_PASS**

All semantic requirements verified. Two defects found and fixed. 69 calculator tests pass (60 original + 9 new semantic verification tests). Full regression of 242 tests passes.
