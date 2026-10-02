# Stage 12.3 — Scheduler MVP Discovery

## Executive Summary

The scheduler backend (Stages 12.1–12.2F) is complete and production-ready. However, the scheduler has **no external control surface**. Users and the AI Operator desktop application cannot currently:

* create scheduled jobs
* list/inspect jobs
* modify schedules
* enable/disable jobs
* delete jobs
* view scheduler-specific execution history

**Recommendation: B — A small Scheduler MVP control/API stage is required.**

The scope is strictly a thin REST control plane over the existing repository/service layer. No scheduler business logic moves into the API layer. No changes to worker, handoff, calculator, or event semantics are required.

---

## 1. Current Scheduler Capabilities

### Verified existing implementation

| Component | Status | File(s) |
|-----------|--------|---------|
| Domain model | Complete | `app/scheduler/models.py` |
| Persistence | Complete | `app/db/models/scheduled_job.py`, `app/scheduler/repository.py` |
| Schedule calculator | Complete | `app/scheduler/calculator.py` |
| Claim/lease | Complete | `app/scheduler/lease.py` |
| Worker | Complete | `app/scheduler/worker.py` |
| Handoff service | Complete | `app/scheduler/handoff.py` |
| Lifecycle integration | Complete | `app/core/lifecycle.py` |
| Event types | Complete | `app/events/schemas.py` |
| Event emission | Complete | `app/events/emitter.py`, `app/events/repository.py` |
| History integration | Complete | `app/events/history.py` |
| Database migration | Complete | `app/db/database.py` |

### Current API surface

Existing FastAPI routers:
- `/api/health` — health checks
- `/api/system` — system info
- `/api/settings` — application settings
- `/api/activity` — activity events
- `/api/ai` — AI operations
- `/api/approvals` — approval workflow
- `/api/plans` — plan execution control (execute, cancel, status)
- `/api/history` — event history (task, plan, execution, correlation, time range, recent)

**No scheduler routes exist.**

### Dependency injection

`app/dependencies.py` provides:
- `get_db` — async DB session
- `get_settings_service`
- `get_activity_service`
- `get_history_service`
- `get_task_repository`
- `get_background_execution_manager`

**No scheduler repository or worker dependencies are exported.**

---

## 2. User Control Surface — Gap Analysis

### What users can currently do

| Operation | Status | Evidence |
|-----------|--------|----------|
| Create scheduled job | **MISSING** | No API route, no service method |
| List jobs | **MISSING** | `SchedulerRepository.list_jobs()` exists but is not exposed |
| Retrieve one job | **MISSING** | `SchedulerRepository.get_job()` exists but is not exposed |
| Update job | **MISSING** | `SchedulerRepository.update_job()` exists but is not exposed |
| Delete job | **MISSING** | `SchedulerRepository.delete_job()` exists but is not exposed |
| Enable/disable job | **MISSING** | `ScheduledJob.enabled` field exists but no toggle endpoint |
| Inspect next_run_at | **MISSING** | Field exists in model |
| Inspect status | **MISSING** | `SchedulerJobStatus` enum exists |
| Inspect schedule definition | **MISSING** | `schedule_type`, `schedule_expr`, `timezone` exist |
| Inspect execution history | **PARTIAL** | History API supports `correlation_id=job_id` but no scheduler-specific endpoint |

### Conclusion

The scheduler is an internal component with no user-facing API. This is the primary gap blocking MVP usability.

---

## 3. Proposed Minimal API Contract

### Design principles

1. **Thin control plane**: API delegates to `SchedulerRepository`. No scheduler business logic in routes.
2. **Authorization boundary**: Scheduler API does NOT bypass `PermissionEngine`, `ApprovalService`, or `VerificationGate`. Target authorization is enforced by the handoff service during execution, not during schedule creation. For MVP, the scheduler API assumes the caller has authority to schedule the referenced target.
3. **Validation**: Strict input validation. No resource exhaustion vectors.
4. **Idempotency**: `job_id` is the authoritative identifier. Create should be idempotent (upsert or return existing).

### Proposed endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/scheduler/jobs` | Create a scheduled job |
| `GET` | `/api/scheduler/jobs` | List scheduled jobs |
| `GET` | `/api/scheduler/jobs/{job_id}` | Retrieve one job |
| `PATCH` | `/api/scheduler/jobs/{job_id}` | Update job (schedule_expr, enabled, timezone) |
| `DELETE` | `/api/scheduler/jobs/{job_id}` | Delete a job |
| `POST` | `/api/scheduler/jobs/{job_id}/enable` | Enable a job |
| `POST` | `/api/scheduler/jobs/{job_id}/disable` | Disable a job |
| `GET` | `/api/scheduler/jobs/{job_id}/history` | Scheduler-specific event history |

### Request/response schemas

```python
class CreateScheduledJobRequest(BaseModel):
    job_id: str  # required, user-provided identifier
    name: str
    schedule_type: Literal["once", "interval", "cron"]
    schedule_expr: str  # ISO datetime for once, seconds for interval, cron expr for cron
    timezone: str | None = None  # IANA timezone
    target_type: Literal["task", "plan"] = "task"
    target_id: str
    enabled: bool = True

class ScheduledJobResponse(BaseModel):
    job_id: str
    name: str
    enabled: bool
    schedule_type: str
    schedule_expr: str
    timezone: str | None
    target_type: str
    target_id: str
    status: str
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime
    claim_sequence: int

class ScheduledJobListResponse(BaseModel):
    jobs: list[ScheduledJobResponse]
    total: int
    limit: int
    offset: int
```

### Validation rules

| Field | Rule |
|-------|------|
| `job_id` | Required, non-empty, max 64 chars |
| `name` | Required, non-empty, max 200 chars |
| `schedule_type` | Must be `once`, `interval`, or `cron` |
| `schedule_expr` | Validated by calculator at creation time |
| `timezone` | Valid IANA timezone or null |
| `target_type` | Must be `task` or `plan` |
| `target_id` | Required, non-empty, max 64 chars |
| `enabled` | Boolean |

### Error semantics

| Condition | HTTP status |
|-----------|-------------|
| Invalid request body | 422 |
| Job already exists | 409 (if not idempotent) or 200 (if idempotent) |
| Job not found | 404 |
| Invalid schedule expression | 422 |
| Invalid timezone | 422 |
| Internal error | 500 |

### Idempotency

`POST /api/scheduler/jobs` should be idempotent: if `job_id` already exists, return the existing job with 200. This prevents duplicate schedules on retry.

---

## 4. Enable/Disable Semantics

### Current state model

```python
class SchedulerJobStatus(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    TRIGGERED = "TRIGGERED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    DISABLED = "DISABLED"
```

The `enabled` field on `ScheduledJob` controls whether the worker considers the job:
```sql
WHERE enabled = 1 AND status = 'PENDING' AND next_run_at <= :now
```

### Enable/disable behavior

| Action | Effect |
|--------|--------|
| Disable (enabled=false) | Job is excluded from `get_due_pending_jobs`. Current occurrence (if CLAIMED/TRIGGERED) completes naturally. |
| Enable (enabled=true) | Job is included in `get_due_pending_jobs` if `next_run_at <= now`. |
| Disable CLAIMED | Worker will not re-claim after lease expiry (status stays CLAIMED but enabled=0 prevents further claims). Recovery resets to PENDING but enabled=0 keeps it from being claimed. |
| Disable TRIGGERED | No effect on in-flight execution. Next occurrence won't be scheduled until re-enabled. |
| Re-enable DISABLED ONCE job | Job stays DISABLED (no future occurrences). Must be recreated. |
| Re-enable INTERVAL/CRON | Job becomes eligible for polling. `next_run_at` is preserved. If `next_run_at` is in the past, it will fire on next poll. |

### Open question: Should re-enabling an INTERVAL/CRON job with a past `next_run_at` recalculate?

**Recommendation: No.** Re-enable preserves `next_run_at`. If it's in the past, the job fires on the next poll (SKIP_ONCE semantics apply). This is consistent with the existing calculator behavior.

### ONCE after disable

If an ONCE job is disabled before firing:
- `next_run_at` retains the scheduled time
- If re-enabled before `next_run_at`, it fires at the scheduled time
- If re-enabled after `next_run_at`, it fires immediately on next poll (SKIP_ONCE applies)

If an ONCE job fires and becomes DISABLED:
- Cannot be re-enabled (no future occurrences)
- Must be recreated

---

## 5. Create/Update Schedule Semantics

### Creation

| Schedule type | `next_run_at` initialization | `last_run_at` |
|---------------|------------------------------|---------------|
| ONCE | `calculate_next_occurrence(once_def, now)` or parsed target datetime | `None` |
| INTERVAL | `calculate_next_occurrence(interval_def, now)` = `now + interval` | `None` |
| CRON | `calculate_next_occurrence(cron_def, now)` = next cron match after now | `None` |

### Creation with explicit start time

If the user provides a `start_at` or similar, use that as the reference. Otherwise use `now`.

### Update semantics

| Field | Behavior |
|-------|----------|
| `schedule_expr` | Recalculate `next_run_at` using current `last_run_at` as reference (or `now` if `last_run_at` is None). Preserve `last_run_at`. |
| `schedule_type` | Recalculate `next_run_at`. Preserve `last_run_at`. |
| `timezone` | Recalculate `next_run_at` if CRON. Preserve `last_run_at`. |
| `target_type` / `target_id` | Update immediately. Does not affect current occurrence if already CLAIMED/TRIGGERED. |
| `enabled` | Toggle eligibility. |
| `name` | Update only. |

### Critical: Updating a schedule does NOT reset occurrence state

If a job is CLAIMED and the user updates `schedule_expr`, the current occurrence continues. Only `next_run_at` for future occurrences is recalculated.

---

## 6. Validation / Security

### Existing validation

| Layer | Validation |
|-------|-----------|
| `ScheduledJob` Pydantic model | Non-empty `job_id`, `name`, `schedule_expr`, `target_id`; `target_type` ∈ {task, plan}; valid IANA timezone |
| `SchedulerRepository` | SQL-level constraints (unique `job_id`, `occurrence_id`, `claim_token`) |
| `SchedulerHandoffService` | Target existence, target state, authorization (via execution layer) |

### Missing validation for API

| Gap | Risk | Mitigation |
|-----|------|-----------|
| No target existence check at creation | User could schedule non-existent task/plan | API should check `TaskRepository.get_task()` or equivalent |
| No target authorization check | User could schedule target they cannot execute | API should verify user has permission to execute the target (same check as handoff) |
| No `schedule_expr` format validation | Invalid expressions reach calculator | Validate at API layer: ONCE requires valid ISO datetime, INTERVAL requires positive integer, CRON requires valid cron expression |
| No resource limits | User could create thousands of jobs | Add per-user or global limit (e.g., max 100 jobs per user) |
| No `job_id` uniqueness check at API level | Duplicate job_id causes DB error | Return 409 with clear message |

### Authorization boundary

Scheduler API must NOT:
- Bypass `PermissionEngine`
- Bypass `ApprovalService`
- Bypass `VerificationGate`
- Execute tasks or plans directly
- Grant new authority to the scheduler

For MVP, the scheduler API should:
1. Validate that the target exists
2. Optionally validate that the authenticated user has permission to execute the target
3. Delegate execution authorization to the handoff service (existing boundary)

### Untrusted input protection

| Input | Risk | Mitigation |
|-------|------|-----------|
| `schedule_expr` (CRON) | Resource exhaustion from complex expressions | Use existing `croniter` or similar with timeout |
| `timezone` | Invalid IANA string | Pydantic validator already checks `ZoneInfo(value)` |
| `target_id` | Path traversal | Not a file path; validated as identifier |
| `job_id` | SQL injection | Parameterized queries via SQLAlchemy |

---

## 7. Observability

### Currently observable

| Data | How |
|------|-----|
| Job status | `SchedulerRepository.get_job()` / `list_jobs()` |
| next_run_at | Direct field query |
| last_run_at | Direct field query |
| Schedule definition | `schedule_type`, `schedule_expr`, `timezone` fields |
| Occurrence history | History API with `correlation_id=job_id` |

### Missing observability

| Gap | MVP requirement |
|-----|-----------------|
| No scheduler-specific health indicator | Add worker status to health endpoint |
| No way to see scheduler events for a specific job | Add `/api/scheduler/jobs/{job_id}/history` endpoint |
| No way to see why a job was skipped/failed | Event payloads contain `reason_code`; expose via history |
| No worker liveness indicator | Worker logs are sufficient for MVP; no dashboard required |

### Minimum viable observability

1. List jobs with status, next_run_at, last_run_at
2. Get job detail
3. Get job history (scheduler events via correlation_id)
4. Worker running/stopped status in health check

---

## 8. Event / History Surface

### Existing scheduler events

| Event | Payload | Sufficient for MVP? |
|-------|---------|---------------------|
| `scheduler.occurrence.claimed` | job_id, occurrence_id, claim_sequence, schedule_type, target_type, target_id | Yes |
| `scheduler.occurrence.triggered` | occurrence_id, claim_sequence, execution_id | Yes |
| `scheduler.occurrence.failed` | reason_code, reason_detail | Yes |
| `scheduler.occurrence.recovered` | reason: "lease_expired" | Yes |
| `scheduler.occurrence.skipped` | reason_code, reason_detail | Yes |

### History API integration

The existing History API already supports:
- `GET /api/history/correlations/{correlation_id}` — returns all events with `correlation_id=job_id`

Since scheduler events use `correlation_id = job_id`, users can already query scheduler event history through the existing endpoint.

**No new event types or history tables are required for MVP.**

### Potential enhancement

A dedicated `/api/scheduler/jobs/{job_id}/history` endpoint would be a convenience wrapper around the existing History API. It is not strictly required but improves discoverability.

---

## 9. Concurrency / Multi-Worker Scope

### MVP scope: Single worker, single application instance

The existing implementation already handles:
- Multiple workers polling the same job (atomic claim)
- Stale claimant recovery (lease expiry)
- Concurrent claims (atomic UPDATE serialization)

### Not required for MVP

| Feature | Reason |
|---------|--------|
| Distributed locks | Single application instance |
| Leader election | Single worker |
| Multi-process coordination | Not in scope |
| Redis coordination | Not in scope |

### Future consideration

If multi-instance deployment becomes necessary, the existing atomic claim mechanism already provides correctness. No additional coordination is needed unless split-brain across separate databases is required (out of scope).

---

## 10. Known Limitations

### Documented and accepted for MVP

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| Plan execution may duplicate after restart | `BackgroundExecutionManager.execution_id` is in-memory only | Pre-existing; not scheduler's responsibility |
| 5-minute lease with no renewal | Long-running task handoffs (>5min) may fail | Task handoff typically completes within lease; plan handoff is fire-and-forget |
| External side effects have UNKNOWN exactly-once semantics | Scheduler cannot guarantee external side effects occur exactly once | Acceptable for MVP; execution layer provides best-effort guarantees |
| No scheduler-specific history endpoint | Users must use generic History API with correlation_id | Acceptable for MVP; can add convenience endpoint later |
| No user-scoped job quotas | Single-user desktop application; not a multi-tenant concern | Acceptable for MVP |

### Not blockers for MVP

- No plan duplicate prevention after restart
- No lease renewal
- No exactly-once external side effects

---

## 11. Database / Migration Impact

### Schema changes required: NONE

All required columns already exist in `scheduled_jobs`:
- `job_id`, `name`, `enabled`, `schedule_type`, `schedule_expr`, `timezone`
- `target_type`, `target_id`, `status`, `last_run_at`, `next_run_at`
- `occurrence_id`, `claim_token`, `claim_sequence`, `claimed_at`, `lease_until`
- `execution_id`, `job_metadata`, `created_at`, `updated_at`

### Indexes already present

| Index | Columns | Supports |
|-------|---------|----------|
| `idx_scheduled_jobs_next_run_at` | `next_run_at` | Due job query |
| `idx_scheduled_jobs_enabled` | `enabled` | Eligibility filter |
| `idx_scheduled_jobs_status` | `status` | Status filter |
| `idx_scheduled_jobs_occurrence_id` | `occurrence_id` | Claim/recovery |
| `idx_scheduled_jobs_claim_token` | `claim_token` | Claim/recovery |
| `idx_scheduled_jobs_lease_until` | `lease_until` | Recovery |

### Migration: None required

The MVP API layer adds no schema changes. Existing `init_db()` and ALTER TABLE migration for `claim_sequence` are sufficient.

---

## 12. Required Test Matrix

### API contract tests

| Test | What it proves |
|------|---------------|
| Create job — valid request | 200, job persisted with correct next_run_at |
| Create job — duplicate job_id | 200 (idempotent) or 409 |
| Create job — invalid schedule_type | 422 |
| Create job — invalid timezone | 422 |
| Create job — target does not exist | 404 or 400 |
| Create job — empty job_id | 422 |
| List jobs — empty | 200, empty list |
| List jobs — with filters | Correct filtering by status, enabled, target_type |
| Get job — existing | 200, correct fields |
| Get job — missing | 404 |
| Update job — valid fields | 200, fields updated, next_run_at recalculated if schedule changed |
| Update job — invalid schedule_expr | 422 |
| Delete job — existing | 204, job removed |
| Delete job — missing | 404 |
| Enable job | enabled=true, job eligible for polling |
| Disable job | enabled=false, job excluded from polling |
| Get job history | Returns scheduler events via correlation_id |

### Authorization tests

| Test | What it proves |
|------|---------------|
| Create job for unauthorized target | 403 or 400 |
| Update job for unauthorized target | 403 or 400 |
| Delete job for unauthorized target | 403 or 400 |

### Schedule semantics tests

| Test | What it proves |
|------|---------------|
| ONCE next_run_at | Correct future/past calculation |
| INTERVAL next_run_at | Correct interval advancement |
| CRON next_run_at | Correct next cron match |
| Timezone handling | UTC storage, IANA interpretation |
| Update schedule_expr | next_run_at recalculated, last_run_at preserved |
| Disable CLAIMED job | Current occurrence completes, no re-claim |
| Re-enable DISABLED ONCE | Job stays DISABLED |
| Re-enable INTERVAL with past next_run_at | Fires on next poll |

### Worker interaction tests

| Test | What it proves |
|------|---------------|
| Created job is picked up by worker | Worker claims and processes |
| Disabled job is not picked up | Worker skips disabled jobs |
| Updated schedule is respected | Worker uses new next_run_at |
| Deleted job is not processed | Worker skips deleted jobs |

### Error handling tests

| Test | What it proves |
|------|---------------|
| Invalid JSON body | 422 |
| Missing required fields | 422 |
| Database unavailable | 500 |
| Worker not running | API still works, worker status reflects this |

---

## 13. Architectural Decision

### Recommendation: B — A small Scheduler MVP control/API stage is required

The scheduler backend is complete and functional, but it has zero user-facing control surface. The existing execution APIs (`/api/plans`) demonstrate the established pattern for thin control-plane endpoints. The scheduler API should follow the same pattern.

### Smallest exact scope

1. **New API router**: `app/api/routes/scheduler.py`
2. **New schemas**: `app/api/schemas/scheduler.py` (request/response models)
3. **New dependencies**: `app/dependencies.py` — add `get_scheduler_repository`
4. **Update `main.py`**: Include scheduler router
5. **Tests**: `tests/test_scheduler_api.py`

### What is NOT in scope

- Dashboard or UI
- Distributed scheduling
- Leader election
- Multi-process coordination
- Bulk operations
- Templates
- Calendar exclusions
- Jitter
- Metrics/monitoring systems
- REST implementation for anything beyond CRUD + enable/disable + history
- Changes to worker, handoff, calculator, or event semantics

---

## 14. Explicit Non-Goals

The following are explicitly OUT OF SCOPE for the Scheduler MVP control stage:

1. **Distributed scheduling**: No Redis, no locks, no leader election
2. **Plan execution deduplication**: Pre-existing limitation, not scheduler's responsibility
3. **Lease renewal**: 5-minute lease is acceptable for MVP
4. **Exactly-once external side effects**: UNKNOWN is acceptable for MVP
5. **Scheduler worker advancement beyond 12.2F**: Worker is complete
6. **REST for execution layer**: `/api/plans` already exists
7. **UI/dashboard**: Desktop app will consume the API
8. **Bulk operations**: Single-job CRUD only
9. **Templates**: No job templates
10. **Calendar exclusions**: No holiday/exception calendars
11. **Jitter/backoff**: Fixed poll interval only
12. **Metrics systems**: Logging is sufficient for MVP

---

## 15. Enable/Disable Detailed Semantics

### State transitions on disable

| Current status | Disable behavior |
|----------------|------------------|
| PENDING | Remains PENDING, excluded from polling |
| CLAIMED | Remains CLAIMED. Lease may expire → recovery resets to PENDING. Enabled=0 prevents re-claim. |
| TRIGGERED | Remains TRIGGERED. Execution continues. Next occurrence not scheduled until re-enabled. |
| FAILED | Remains FAILED. No effect. |
| SKIPPED | Remains SKIPPED. No effect. |
| DISABLED | Already disabled. No effect. |

### State transitions on enable

| Current status | Enable behavior |
|----------------|-----------------|
| PENDING with future next_run_at | Eligible for polling at next_run_at |
| PENDING with past next_run_at | Eligible for polling on next worker iteration (SKIP_ONCE applies) |
| CLAIMED | No effect (should not happen if properly managed) |
| TRIGGERED | No effect (execution continues) |
| FAILED | No effect (terminal state) |
| SKIPPED | No effect (terminal state) |
| DISABLED ONCE | No future occurrences; re-enable has no effect |
| DISABLED after finalization | next_run_at recalculated on re-enable if schedule has future occurrences |

### Critical invariant

Disabling a job MUST NOT:
- Cancel in-flight execution
- Reset occurrence state
- Modify `next_run_at`
- Emit scheduler events

---

## 16. Create/Update Detailed Semantics

### Creation flow

```python
def create_job(request: CreateScheduledJobRequest, now: datetime) -> ScheduledJob:
    definition = ScheduleDefinition(
        schedule_type=request.schedule_type,
        schedule_expr=request.schedule_expr,
        timezone=request.timezone,
    )
    next_run_at = calculate_next_occurrence(definition, now)
    if next_run_at is None:
        raise ValueError("Schedule has no future occurrences")
    
    job = ScheduledJob(
        job_id=request.job_id,
        name=request.name,
        enabled=request.enabled,
        schedule_type=request.schedule_type,
        schedule_expr=request.schedule_expr,
        timezone=request.timezone,
        target_type=request.target_type,
        target_id=request.target_id,
        status=SchedulerJobStatus.PENDING,
        last_run_at=None,
        next_run_at=next_run_at,
        occurrence_id=None,
        claim_token=None,
        claim_sequence=0,
        claimed_at=None,
        lease_until=None,
        execution_id=None,
    )
    return repo.create_job(job)
```

### Update flow

```python
def update_job(job_id: str, updates: dict, now: datetime) -> ScheduledJob:
    job = repo.get_job(job_id)
    if job is None:
        raise NotFoundError()
    
    # If schedule changed, recalculate next_run_at
    if "schedule_expr" in updates or "schedule_type" in updates or "timezone" in updates:
        definition = ScheduleDefinition(
            schedule_type=updates.get("schedule_type", job.schedule_type),
            schedule_expr=updates.get("schedule_expr", job.schedule_expr),
            timezone=updates.get("timezone", job.timezone),
        )
        reference = job.last_run_at or now
        next_run_at = calculate_next_occurrence(definition, reference)
        if next_run_at is None:
            next_run_at = reference
        updates["next_run_at"] = next_run_at
    
    return repo.update_job(job_id, **updates)
```

### Important: Update does NOT reset occurrence state

If a job is CLAIMED and the user updates `schedule_expr`:
- Current occurrence continues with existing claim
- `next_run_at` is updated for future occurrences
- `last_run_at` is preserved

---

## 17. Authorization Boundary

### Scheduler API responsibility

The scheduler API is responsible for:
1. Input validation
2. Persistence
3. Schedule calculation
4. Enabling/disabling eligibility

### NOT responsible for

1. **Permission evaluation** — delegated to `PermissionEngine` during handoff
2. **Approval** — delegated to `ApprovalService` during handoff
3. **Verification** — delegated to `VerificationGate` during handoff
4. **Task execution** — delegated to `AutonomousRunController`
5. **Plan execution** — delegated to `BackgroundExecutionManager`

### Target validation at creation time

For MVP, the API should verify that the target exists:
```python
if target_type == "task":
    task = await task_repo.get_task(target_id)
    if task is None:
        raise HTTPException(404, "Target task not found")
elif target_type == "plan":
    tasks = await task_repo.get_tasks_by_plan(target_id)
    if not tasks:
        raise HTTPException(404, "Target plan not found")
```

Full authorization (can the current user execute this target?) is deferred to the handoff service. This is consistent with the existing execution architecture where `/api/plans` also does not perform full authorization at the API layer.

---

## 18. Observability Details

### Minimum viable observability for operators

| Question | How answered |
|----------|--------------|
| Is the scheduler running? | Health check includes worker status |
| What jobs are scheduled? | `GET /api/scheduler/jobs` |
| When is the next job due? | `next_run_at` in job list/detail |
| What happened to a job? | `GET /api/scheduler/jobs/{job_id}/history` |
| Why did a job fail/skip? | `reason_code` in event payload |
| Is the worker healthy? | Logs + health check |

### No additional infrastructure required

- No metrics system (logs are sufficient)
- No dashboards
- No alerting (out of scope)

---

## 19. Event/History Analysis

### Sufficiency of existing events

The 5 scheduler events (claimed, triggered, failed, recovered, skipped) are sufficient for MVP. Each event contains:
- `correlation_id = job_id` — links all events for a job
- `causation_id` — links triggered/failed/recovered/skipped to the corresponding claimed event
- `claim_sequence` — distinguishes multiple claims of the same occurrence
- Bounded payload with reason codes

### History queryability

Users can already query scheduler events via:
```
GET /api/history/correlations/{job_id}
```

This returns all events where `correlation_id = job_id`, which includes all scheduler lifecycle events for that job.

### No new event types required

The current event set covers all scheduler lifecycle transitions. No additional events (e.g., `scheduler.job.updated`, `scheduler.job.deleted`) are required for MVP. Those can be added later if audit requirements emerge.

---

## 20. Concurrency Scope

### Single-worker, single-instance MVP

The existing architecture already supports:
- Atomic claim via SQLite UPDATE with WHERE clause
- Lease-based recovery
- Stale claimant rejection

No additional concurrency mechanisms are needed for single-instance deployment.

### Multi-instance correctness (future)

If multi-instance deployment becomes necessary:
- Atomic claim already prevents duplicate claims
- Recovery already handles stale leases
- No distributed locks needed as long as all instances share the same database

---

## 21. Known Limitations (Repeated for clarity)

| Limitation | MVP acceptable? | Notes |
|------------|-----------------|-------|
| Plan duplicate after restart | Yes | Pre-existing; BackgroundExecutionManager is in-memory |
| 5-minute lease, no renewal | Yes | Handoff must complete within lease |
| External side effects UNKNOWN | Yes | Execution layer provides best effort |
| No scheduler-specific history endpoint | Yes | Generic History API works via correlation_id |
| No user quotas | Yes | Single-user desktop app |
| No distributed scheduling | Yes | Single instance |
| No exactly-once semantics | Yes | AT_LEAST_ONCE for scheduler delivery |

---

## 22. Database/Migration Impact

### No schema changes required

All scheduler MVP API functionality uses existing columns:
- CRUD: existing columns
- Enable/disable: `enabled` column
- History: existing `events` table with `correlation_id`

### No migration needed

`init_db()` already creates all required tables and indexes. The existing ALTER TABLE migration for `claim_sequence` is sufficient.

---

## 23. Test Matrix

### API contract tests (new)

| Category | Count | Tests |
|----------|-------|-------|
| CRUD | 8 | create, list, get, update, delete, create duplicate, get missing, delete missing |
| Enable/disable | 4 | enable, disable, disable CLAIMED, re-enable DISABLED |
| Validation | 6 | invalid schedule_type, invalid timezone, invalid target_type, empty job_id, empty target_id, invalid schedule_expr |
| Schedule semantics | 6 | ONCE next_run_at, INTERVAL next_run_at, CRON next_run_at, timezone, update schedule_expr, update timezone |
| Authorization | 3 | unauthorized target, nonexistent target, missing target |
| History | 2 | get job history, empty history |
| Worker interaction | 3 | created job picked up, disabled job skipped, updated schedule respected |
| Error handling | 3 | invalid JSON, missing fields, database error |

**Total: ~35 new tests**

### Regression tests (existing)

| Suite | Tests |
|-------|-------|
| Scheduler repository | 114 |
| Scheduler events | 26 |
| Scheduler worker | 25 |
| Scheduler claim/handoff | 150+ |
| Event foundation | included above |
| History integration | included above |
| Execution layer | 220 |

**No existing tests need modification.**

---

## 24. Recommended Next Stage

### Stage 12.3 — Scheduler MVP API

**Exact scope:**

1. **New file**: `services/core/app/api/routes/scheduler.py`
   - 8 endpoints (CRUD + enable/disable + history)
   - Thin delegation to `SchedulerRepository`
   - No scheduler business logic

2. **New file**: `services/core/app/api/schemas/scheduler.py`
   - Request/response Pydantic models
   - Validation rules

3. **Modify**: `services/core/app/dependencies.py`
   - Add `get_scheduler_repository` dependency

4. **Modify**: `services/core/app/main.py`
   - Include scheduler router

5. **New file**: `services/core/tests/test_scheduler_api.py`
   - ~35 tests covering contract, validation, authorization, semantics

### Files to change

| File | Action |
|------|--------|
| `app/api/routes/scheduler.py` | Create |
| `app/api/schemas/scheduler.py` | Create |
| `app/dependencies.py` | Modify (add dependency) |
| `app/main.py` | Modify (include router) |
| `tests/test_scheduler_api.py` | Create |

### What NOT to change

- `app/scheduler/worker.py`
- `app/scheduler/handoff.py`
- `app/scheduler/repository.py`
- `app/scheduler/calculator.py`
- `app/scheduler/lease.py`
- `app/events/schemas.py`
- `app/events/history.py`
- `app/db/database.py`
- `app/db/models/scheduled_job.py`
- `app/core/lifecycle.py`

---

## 25. Final Decision

**STAGE_12_3_IMPLEMENTATION_READY**

The scheduler backend is complete. A thin REST control plane (~8 endpoints, ~35 tests, 4 files changed) is the smallest remaining scope to make the scheduler a usable MVP component.

No semantic blockers. No schema changes. No modifications to existing scheduler internals.
