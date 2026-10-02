# STAGE 12.2A — SCHEDULER PERSISTENCE + DOMAIN MODEL REPORT

## 1. Implementation Summary

Implemented Stage 12.2A — Scheduler Persistence and Domain Model. Created the minimal durable scheduler foundation consisting of:

- **Domain model**: `ScheduledJob` Pydantic model with enums `ScheduleType` and `SchedulerJobStatus`
- **Database model**: `ScheduledJobModel` SQLAlchemy ORM model mapped to `scheduled_jobs` table
- **Repository**: `SchedulerRepository` with create/get/update/delete/list/count operations
- **Database migration**: Added `scheduled_jobs` table creation to `init_db()` with appropriate indexes
- **Tests**: 51 focused tests covering domain model, persistence, and repository operations

## 2. Files Changed

| File | Change |
|------|--------|
| `app/scheduler/models.py` | New — domain enums and `ScheduledJob` model |
| `app/scheduler/repository.py` | New — `SchedulerRepository` |
| `app/scheduler/__init__.py` | New — package exports |
| `app/db/models/scheduled_job.py` | New — `ScheduledJobModel` ORM |
| `app/db/models/__init__.py` | Modified — added `ScheduledJobModel` |
| `app/db/database.py` | Modified — added `scheduled_jobs` table creation and indexes |
| `tests/test_scheduler_models.py` | New — 27 domain model tests |
| `tests/test_scheduler_repository.py` | New — 24 repository tests |

## 3. Domain Model

### Enums

**`ScheduleType`**:
- `CRON` = "cron"
- `INTERVAL` = "interval"
- `ONCE` = "once"

**`SchedulerJobStatus`**:
- `PENDING` = "PENDING"
- `CLAIMED` = "CLAIMED"
- `TRIGGERED` = "TRIGGERED"
- `COMPLETED` = "COMPLETED"
- `FAILED` = "FAILED"
- `SKIPPED` = "SKIPPED"
- `DISABLED` = "DISABLED"

### `ScheduledJob` Fields

| Field | Type | Notes |
|-------|------|-------|
| `job_id` | `str` | Primary key, required, non-empty |
| `name` | `str` | Required, non-empty |
| `enabled` | `bool` | Default `True` |
| `schedule_type` | `ScheduleType` | Required: cron, interval, once |
| `schedule_expr` | `str` | Required, non-empty |
| `timezone` | `str | None` | IANA timezone, validated via `zoneinfo` |
| `target_type` | `str` | "task" or "plan" |
| `target_id` | `str` | Required, non-empty |
| `status` | `SchedulerJobStatus` | Default PENDING |
| `last_run_at` | `datetime | None` | UTC |
| `next_run_at` | `datetime` | Required, UTC |
| `created_at` | `datetime` | UTC, default now |
| `updated_at` | `datetime` | UTC, default now |
| `occurrence_id` | `str | None` | Occurrence identity |
| `claim_token` | `str | None` | Lease token |
| `claimed_at` | `datetime | None` | UTC |
| `lease_until` | `datetime | None` | UTC |
| `execution_id` | `str | None` | Execution identity |
| `metadata` | `dict | None` | Bounded JSON |

## 4. Database Schema

### `scheduled_jobs` Table

```sql
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    job_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    schedule_type VARCHAR(20) NOT NULL,
    schedule_expr VARCHAR(200) NOT NULL,
    timezone VARCHAR(64),
    target_type VARCHAR(20) NOT NULL,
    target_id VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    last_run_at TIMESTAMP WITH TIME ZONE,
    next_run_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    occurrence_id VARCHAR(128),
    claim_token VARCHAR(64),
    claimed_at TIMESTAMP WITH TIME ZONE,
    lease_until TIMESTAMP WITH TIME ZONE,
    execution_id VARCHAR(64),
    job_metadata TEXT
);
```

### Indexes

| Index | Columns | Purpose |
|-------|---------|---------|
| `idx_scheduled_jobs_next_run_at` | `next_run_at` | Polling due jobs |
| `idx_scheduled_jobs_enabled` | `enabled` | Filter enabled jobs |
| `idx_scheduled_jobs_target` | `(target_type, target_id)` | Lookup by task/plan |
| `idx_scheduled_jobs_status` | `status` | Filter by status |
| `idx_scheduled_jobs_occurrence_id` | `occurrence_id` | Unique occurrence lookup |
| `idx_scheduled_jobs_claim_token` | `claim_token` | Unique claim lookup |
| `idx_scheduled_jobs_lease_until` | `lease_until` | Stale lease recovery |

### Uniqueness Constraints

- `job_id`: PRIMARY KEY (unique by definition)
- `occurrence_id`: UNIQUE (via unique index)
- `claim_token`: UNIQUE (via unique index)

## 5. Repository Contract

### `SchedulerRepository` Methods

| Method | Purpose |
|--------|---------|
| `create_job(job)` | Persist new job |
| `get_job(job_id)` | Retrieve by PK |
| `list_jobs(filters, limit, offset)` | List with optional filters |
| `update_job(job_id, **updates)` | Update allowed fields |
| `delete_job(job_id)` | Delete by PK |
| `get_jobs_by_target(target_type, target_id)` | Lookup by task/plan |
| `count_jobs(filters)` | Count matching jobs |

### Supported Filters for `list_jobs` / `count_jobs`

- `target_type`: str
- `target_id`: str
- `status`: `SchedulerJobStatus | str`
- `enabled`: `bool`
- `schedule_type`: `ScheduleType | str`
- `limit`: int (default 50)
- `offset`: int (default 0)

### Error Propagation

Repository errors propagate as SQLAlchemy exceptions, consistent with existing `TaskRepository` behavior. No silent swallowing.

## 6. Validation Rules

| Validation | Rule |
|------------|------|
| `job_id` | Non-empty string, stripped |
| `name` | Non-empty string, stripped |
| `schedule_expr` | Non-empty string, stripped |
| `target_id` | Non-empty string, stripped |
| `target_type` | Must be "task" or "plan" |
| `timezone` | Valid IANA timezone or None (validated via `zoneinfo`) |
| `schedule_type` | Must be valid `ScheduleType` enum |
| `status` | Must be valid `SchedulerJobStatus` enum |
| `next_run_at` | Required datetime (UTC) |

## 7. Security Considerations

- **No secrets persisted**: Schedule expressions and metadata do not store credentials
- **Bounded strings**: All string fields have explicit length limits in the database schema
- **Parameterized queries**: All repository queries use SQLAlchemy parameterized statements
- **No authorization cache**: Scheduler persistence does not store or imply any permission state
- **No execution semantics**: Persistence of a `ScheduledJob` does not imply any execution occurred

## 8. Integration

- `ScheduledJobModel` registered in `app/db/models/__init__.py`
- `scheduled_jobs` table creation added to `init_db()` with lightweight migration
- `SchedulerRepository` follows existing `TaskRepository` patterns
- No REST endpoints added (explicitly out of scope for 12.2A)
- No execution/claim/lease logic implemented (explicitly out of scope for 12.2A)

## 9. Tests Executed

| Suite | Tests | Result |
|-------|-------|--------|
| `tests/test_scheduler_models.py` | 27 | PASS |
| `tests/test_scheduler_repository.py` | 24 | PASS |
| **Total new** | **51** | **PASS** |

### Test Coverage

- Domain model construction (valid/invalid)
- Enum values
- Frozen model immutability
- Empty string rejection
- Invalid enum rejection
- Metadata roundtrip
- Persistence roundtrip
- Create/get/update/delete
- List with filters
- Pagination
- Uniqueness constraints
- Datetime persistence
- Proof that persistence does not imply exactly-once execution

## 10. Full Regression Results

| Suite | Tests | Result |
|-------|-------|--------|
| `test_scheduler_models.py` | 27 | PASS |
| `test_scheduler_repository.py` | 24 | PASS |
| `test_event_foundation.py` | 39 | PASS |
| `test_task_repository.py` | 13 | PASS |
| `test_history_repository.py` | 17 | PASS |
| `test_history_service.py` | 25 | PASS |
| `test_history_integration.py` | 5 | PASS |
| `test_history_api.py` | 22 | PASS |
| **Total** | **173** | **PASS** |

No existing tests were broken.

## 11. Known Limitations

- No schedule calculation logic (cron/interval/once parsing deferred to 12.2B)
- No scheduler worker/loop (deferred to 12.2B)
- No claim/lease/recovery execution logic (deferred to 12.2B)
- No REST endpoints (deferred to 12.3)
- `job_metadata` is stored as JSON text; no schema enforcement beyond dict validation
- `timezone` validation requires `zoneinfo` (Python 3.9+), which is available in the project

## 12. Confirmation of Scope

This stage implemented ONLY:
- Domain model
- Database model and schema
- Repository persistence layer
- Validation
- Tests

This stage did NOT implement:
- Schedule calculation (cron/interval/once)
- Scheduler worker/polling loop
- Claim/lease/recovery execution logic
- REST API endpoints
- Execution handoff
- Misfire policy logic
- Any modification to existing execution semantics

## 13. Final Status

**STAGE_12_2A_PASS**

All 51 new tests pass. Full regression of 173 tests passes. No production code modifications outside the scheduler persistence layer.
