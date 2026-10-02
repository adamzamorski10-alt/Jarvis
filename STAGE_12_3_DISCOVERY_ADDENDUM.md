# Stage 12.3 — Scheduler MVP Discovery Addendum

## Purpose

Resolve contract ambiguities identified during pre-implementation review of `STAGE_12_3_DISCOVERY.md`.

This addendum supersedes conflicting statements in the discovery report. All other discovery content remains valid.

---

## 1. CREATE Idempotency — CRITICAL

### Corrected semantic

`POST /api/scheduler/jobs` idempotency is based on **semantic equality of the authoritative create payload**, not merely `job_id` existence.

**Authoritative create fields** (all must match for idempotent success):
- `name`
- `schedule_type`
- `schedule_expr`
- `timezone`
- `target_type`
- `target_id`
- `enabled`

### Behavior

| Condition | HTTP status | Body |
|-----------|-------------|------|
| `job_id` does not exist | 201 | Created job |
| `job_id` exists + all authoritative fields equal | 200 | Existing job (idempotent) |
| `job_id` exists + any authoritative field differs | 409 | `{"error": "conflict", "detail": "Job exists with different definition"}` |
| Invalid request body | 422 | Validation error |

### Implementation note

Comparison must be field-by-field on the stored job vs. request payload. Do NOT compare raw JSON strings (ordering/normalization differences would cause false 409s). Do NOT guess equality — if any field differs, return 409.

### Rationale

Silently returning an unrelated existing job on POST retry is a correctness defect. The client must know whether their intended schedule was accepted.

---

## 2. PATCH While CLAIMED/TRIGGERED — CRITICAL

### State-by-state PATCH behavior

| Status | Allowed fields | Disallowed fields | Behavior |
|--------|---------------|-------------------|----------|
| `PENDING` | All mutable fields | None | Full update; schedule recalculated if `schedule_type`, `schedule_expr`, or `timezone` changes |
| `CLAIMED` | `enabled` only | `schedule_type`, `schedule_expr`, `timezone`, `target_type`, `target_id`, `name` | Only `enabled` can toggle. Schedule mutations rejected with 409. |
| `TRIGGERED` | All except immutable occurrence fields | `occurrence_id`, `claim_token`, `claim_sequence`, `claimed_at`, `lease_until`, `execution_id` | Schedule changes recalculate `next_run_at` from current `last_run_at`. Current occurrence already handed off; future occurrences use new schedule. |
| `FAILED` | All mutable fields | None | Full update; can reschedule or re-enable. |
| `SKIPPED` | All mutable fields | None | Full update; can reschedule or re-enable. |
| `DISABLED` | All mutable fields | None | Full update; can reschedule or re-enable. |

### Immutable occurrence fields

These fields MUST NEVER be modified by PATCH after creation:
- `job_id` (primary key)
- `occurrence_id`
- `claim_token`
- `claim_sequence`
- `claimed_at`
- `lease_until`
- `execution_id`
- `created_at`

### Schedule recalculation on PATCH

When `schedule_type`, `schedule_expr`, or `timezone` changes:
- Reference = `last_run_at` if not None, else `now`
- `next_run_at = calculate_next_occurrence(updated_definition, reference)`
- If `calculate_next_occurrence` returns `None` (ONCE elapsed, CRON exhausted): set `enabled = false`, `status = DISABLED`, `next_run_at = reference`
- `last_run_at` is NEVER modified by PATCH

### Rationale

A PATCH must not alter an already-claimed or already-triggered occurrence. The current occurrence is durable state. Changing its schedule retroactively could:
- Invalidate `occurrence_id`
- Alter the meaning of `last_run_at`
- Cause the same logical occurrence to execute twice
- Make advancement use inconsistent state

For `CLAIMED`, the safest behavior is to reject schedule changes entirely. The job can still be disabled to prevent re-claim after recovery.

For `TRIGGERED`, the occurrence is already handed off. Future occurrences are independent. Schedule changes are safe because `last_run_at` is preserved.

---

## 3. DELETE Semantics — CRITICAL

### Behavior by status

| Status | DELETE allowed? | Rationale |
|--------|----------------|-----------|
| `PENDING` | Yes | No active occurrence |
| `CLAIMED` | **No** | Active occurrence; delete would orphan the claim and lose durable evidence |
| `TRIGGERED` | **No** | Execution in progress; delete would destroy scheduler's ability to reason about the occurrence |
| `FAILED` | Yes | Terminal state; evidence preserved in events |
| `SKIPPED` | Yes | Terminal state; evidence preserved in events |
| `DISABLED` | Yes | No future occurrences |

### Rejected DELETE responses

| Status | HTTP status | Detail |
|--------|-------------|--------|
| `CLAIMED` | 409 | `{"error": "conflict", "detail": "Cannot delete job with active claim"}` |
| `TRIGGERED` | 409 | `{"error": "conflict", "detail": "Cannot delete job with execution in progress"}` |

### Rationale

Deleting a `CLAIMED` or `TRIGGERED` job would:
- Destroy the scheduler's durable evidence needed to reason about the occurrence
- Potentially orphan the execution (task RUNNING but scheduler record gone)
- Prevent recovery from finding the occurrence on restart

Events (`scheduler.occurrence.claimed`, `scheduler.occurrence.triggered`) remain in the `events` table even after job deletion, preserving audit trail. But the scheduler should not allow deletion while an occurrence is active.

### Soft-delete

NOT required for MVP. Hard delete is acceptable because:
- Events preserve audit trail
- Terminal states (FAILED, SKIPPED, DISABLED) can be safely deleted
- Active states (CLAIMED, TRIGGERED) are protected

---

## 4. ENABLE / DISABLE Semantics — Clean Up

### State model (corrected and unified)

Two independent axes:
1. **`enabled`** (boolean): polling eligibility. Controlled by enable/disable endpoints.
2. **`status`** (enum): occurrence lifecycle state. Controlled by worker/repository.

### Disable behavior

| Current status | Effect of `enabled=false` |
|----------------|---------------------------|
| `PENDING` | Excluded from `get_due_pending_jobs`. Remains `PENDING`. |
| `CLAIMED` | Excluded from future polls after recovery. Current occurrence continues. Lease may expire → recovery sets `status=PENDING` but `enabled=0` prevents re-claim. |
| `TRIGGERED` | Excluded from future polls. Current execution continues. Next occurrence not scheduled until re-enabled. |
| `FAILED` | No effect. |
| `SKIPPED` | No effect. |
| `DISABLED` | No effect. |

**Disable MUST NOT:**
- Cancel in-flight execution
- Reset occurrence state
- Modify `next_run_at`
- Emit scheduler events

### Enable behavior

| Current status | Effect of `enabled=true` |
|----------------|--------------------------|
| `PENDING` with future `next_run_at` | Eligible for polling at `next_run_at` |
| `PENDING` with past `next_run_at` | Eligible for polling on next worker iteration |
| `CLAIMED` | No effect (should not occur if properly managed) |
| `TRIGGERED` | No effect (execution continues) |
| `FAILED` | No effect (terminal state) |
| `SKIPPED` | No effect (terminal state) |
| `DISABLED` ONCE | No effect. ONCE has no future occurrences. Job must be recreated. |
| `DISABLED` after terminalization | No effect on eligibility. `status` remains `DISABLED`. To make eligible, user must also PATCH `status=PENDING`. |

**Important:** Enable/disable only toggles the `enabled` field. It does NOT change `status`. The worker queries `status='PENDING'` AND `enabled=1`. A job with `status=DISABLED` and `enabled=1` is NOT eligible for polling. To make a DISABLED job eligible, the user must explicitly set both `enabled=true` AND `status=PENDING` (or recreate the job).

### Re-enable and schedule recalculation

Re-enabling does NOT recalculate `next_run_at`. The existing `next_run_at` is preserved. If it is in the past, SKIP_ONCE semantics apply on the next poll.

This is consistent: enable/disable is a polling eligibility toggle, not a schedule modification.

### Contradiction resolved

The discovery report contained:
- "Re-enable preserves `next_run_at`" (correct)
- "Re-enable recalculates `next_run_at`" (incorrect)

**Correct behavior: Re-enable preserves `next_run_at`. No recalculation.**

---

## 5. CREATE Initial next_run_at — VERIFY

### ONCE

- Future timestamp: `next_run_at = target_timestamp`. Status = `PENDING`.
- Elapsed timestamp (`target < now`): `calculate_next_occurrence` returns `None`. **Must NOT create a repeatedly runnable job.** Return 422 with detail: "ONCE schedule target time is in the past".

### INTERVAL

- `next_run_at = now + interval_seconds`
- This means "first occurrence occurs one interval after creation."
- Example: created at 10:00 with interval 3600s → `next_run_at = 11:00`

### CRON

- `next_run_at = next_cron_match_after(now)`
- Strictly greater than `now` (calculator boundary)
- Example: created at 10:17 with `*/5 * * * *` → `next_run_at = 10:20`

### Creation reference time

All schedule types use `now` (UTC, timezone-aware) as the reference time for initial `next_run_at` calculation.

---

## 6. UPDATE Semantics — AVOID FAKE FALLBACKS

### Corrected behavior when schedule has no future occurrence

If `calculate_next_occurrence(updated_definition, reference)` returns `None`:

| Schedule type | Behavior |
|---------------|----------|
| `ONCE` | Set `enabled = false`, `status = DISABLED`, `next_run_at = reference` |
| `INTERVAL` | This should not occur unless interval > 10 years (validator rejects). If it does, set `enabled = false`, `status = DISABLED`. |
| `CRON` | This should not occur unless cron has no matches within 4 years. If it does, set `enabled = false`, `status = DISABLED`. |

**Never create a `next_run_at` in the past.**

### Update recalculation

When schedule changes:
1. `reference = last_run_at if last_run_at is not None else now`
2. `next_run_at = calculate_next_occurrence(updated_definition, reference)`
3. If `next_run_at is None`: terminalize as above
4. `last_run_at` is NEVER modified by update

### Restricted updates by status

| Status | Schedule change allowed? | Other updates allowed? |
|--------|-------------------------|----------------------|
| `PENDING` | Yes (recalculates `next_run_at`) | Yes |
| `CLAIMED` | **No** (409) | `enabled` only |
| `TRIGGERED` | Yes (recalculates `next_run_at` from `last_run_at`) | All except immutable occurrence fields |
| `FAILED` | Yes | Yes |
| `SKIPPED` | Yes | Yes |
| `DISABLED` | Yes (may re-enable if future occurrences exist) | Yes |

---

## 7. API / Service Boundary

### Required: `app/scheduler/service.py`

The API layer must not contain scheduler business logic. A new application service layer is required to encapsulate:

```python
class SchedulerService:
    def __init__(self, repo, task_repo, calculator):
        self._repo = repo
        self._task_repo = task_repo
        self._calculator = calculator
    
    async def create_job(self, request, now) -> ScheduledJob:
        # 1. Validate schedule definition
        # 2. Check target existence
        # 3. Check idempotency (semantic equality)
        # 4. Calculate next_run_at
        # 5. Create job
        pass
    
    async def update_job(self, job_id, updates, now) -> ScheduledJob:
        # 1. Load existing job
        # 2. Validate status allows requested updates
        # 3. Recalculate next_run_at if schedule changed
        # 4. Apply updates
        pass
    
    async def delete_job(self, job_id) -> None:
        # 1. Load existing job
        # 2. Reject if CLAIMED or TRIGGERED
        # 3. Delete
        pass
    
    async def enable_job(self, job_id) -> ScheduledJob:
        # 1. Load existing job
        # 2. Set enabled=true
        pass
    
    async def disable_job(self, job_id) -> ScheduledJob:
        # 1. Load existing job
        # 2. Set enabled=false
        pass
```

### Route layer (thin)

```python
@router.post("/scheduler/jobs")
async def create_job(request: CreateScheduledJobRequest, svc: SchedulerService = Depends(get_scheduler_service)):
    return await svc.create_job(request, now_utc())

@router.patch("/scheduler/jobs/{job_id}")
async def update_job(job_id: str, request: UpdateScheduledJobRequest, svc: SchedulerService = Depends(get_scheduler_service)):
    return await svc.update_job(job_id, request, now_utc())
```

### Why a service layer is required

The following business rules MUST NOT live in route handlers:
- Schedule validation and `next_run_at` calculation
- Idempotency semantic equality check
- Status-based update restrictions (CLAIMED rejects schedule changes)
- Target existence verification
- Terminalization when schedule has no future occurrences

The service layer owns these rules. The route layer owns HTTP concerns (status codes, request parsing, response serialization).

---

## 8. Target Existence / Authorization

### Current repository interfaces

| Target type | Existence check | Current interface |
|-------------|----------------|-------------------|
| Task | `TaskRepository.get_task(task_id)` | Returns `Task | None` |
| Plan | `TaskRepository.get_tasks_by_plan(plan_id)` | Returns `list[Task]`; empty list means no tasks with that plan_id |

### Plan existence contract — CORRECTED

**`get_tasks_by_plan()` is NOT an authoritative plan-existence check.**

The existing `HistoryService.get_plan_history()` docstring explicitly states:

> Queries events directly by the persisted plan_id. A plan may have events even when it currently has no associated tasks. Absence of tasks is not treated as absence of plan history.

The test suite confirms: `test_history_service.py` creates events for `plan_no_tasks` (a plan with zero tasks).

There is no separate `Plan` entity or `PlanRepository` in the current architecture. A `plan_id` is simply a string tag on `DevelopmentTask` rows and event metadata.

### Implication for scheduler target validation

The scheduler CANNOT safely determine whether a `target_type=plan` exists using the current repository interfaces. Rejecting a create/update because `get_tasks_by_plan()` returns an empty list would incorrectly reject valid plans that have history/events but currently zero tasks.

### Corrected MVP behavior

| Target type | Validation at API layer |
|-------------|------------------------|
| `task` | Check `TaskRepository.get_task(target_id)` is not None |
| `plan` | **No existence validation at API layer.** Defer to execution layer (`SchedulerHandoffService` → `BackgroundExecutionManager`), which has the authoritative plan-execution check. |

The scheduler API must NOT invent a fake plan-existence mechanism. If the execution layer determines the plan is not executable, it returns a handoff failure, and the worker classifies it appropriately (FAILED or SKIPPED).

### Authorization boundary (current state)

The project is currently a **single-user desktop application**. There is no authenticated principal, no multi-tenancy, and no per-user authorization at the API layer.

The existing execution authorization boundary is:
1. `PermissionEngine` — tool-level grants
2. `ApprovalService` — human-in-the-loop approvals
3. `VerificationGate` — post-execution verification

The scheduler API MUST NOT bypass these. However, for MVP:
- The scheduler API does not execute tasks/plans directly
- Execution authorization is enforced by `SchedulerHandoffService` → existing execution layer
- The scheduler API only creates/updates schedule definitions

### MVP authorization model

For MVP, the scheduler API assumes the caller is authorized to schedule the referenced target. This is consistent with:
- The existing `/api/plans` endpoints, which also do not perform full authorization at the API layer
- The single-user desktop application context

If multi-user authentication is added later, the scheduler API should add target-execution permission checks at that time.

### Validation required at API layer

| Check | Where | Why |
|-------|-------|-----|
| Target task exists | Service layer | Prevent scheduling non-existent tasks |
| Schedule expression valid | Service layer (via calculator) | Prevent invalid schedules |
| Timezone valid | Pydantic model | Already validated |
| `job_id` uniqueness | Service layer (idempotency check) | Prevent duplicates |
| Plan existence | **NOT checked at API layer** | No authoritative plan registry; defer to execution layer |

---

## 9. History Endpoint Decision

### Decision: INCLUDE for MVP

`GET /api/scheduler/jobs/{job_id}/history` is included in MVP scope.

### Rationale

While the generic `GET /api/history/correlations/{correlation_id}` already exposes scheduler events, a dedicated endpoint:
- Improves discoverability (users don't need to know the correlation_id convention)
- Provides a stable contract even if History API pagination/filtering changes
- Is trivial to implement (delegates to existing `HistoryService`)
- Is consistent with the existing API pattern of having both generic and specific endpoints

### Implementation

```python
@router.get("/scheduler/jobs/{job_id}/history")
async def get_job_history(
    job_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=100000),
    svc: HistoryService = Depends(get_history_service),
):
    page = await svc.get_correlation_history(job_id, limit=limit, offset=offset)
    return _to_response(page)
```

No new history implementation. No new event types. Pure delegation.

---

## 10. Final Implementation Scope

### In scope for Stage 12.3

| Component | File(s) | Description |
|-----------|---------|-------------|
| API schemas | `app/api/schemas/scheduler.py` | Request/response Pydantic models |
| API routes | `app/api/routes/scheduler.py` | 9 endpoints (8 CRUD/enable/disable + history) |
| Service layer | `app/scheduler/service.py` | Business logic: validation, calculation, idempotency, state checks |
| DI update | `app/dependencies.py` | Add `get_scheduler_service` |
| Router registration | `app/main.py` | Include scheduler router |
| Tests | `tests/test_scheduler_api.py` | ~40 tests |

### Endpoints (final)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/scheduler/jobs` | Create job (idempotent) |
| `GET` | `/api/scheduler/jobs` | List jobs (optional filters) |
| `GET` | `/api/scheduler/jobs/{job_id}` | Get job detail |
| `PATCH` | `/api/scheduler/jobs/{job_id}` | Update job (status-dependent restrictions) |
| `DELETE` | `/api/scheduler/jobs/{job_id}` | Delete job (reject if CLAIMED/TRIGGERED) |
| `POST` | `/api/scheduler/jobs/{job_id}/enable` | Enable job |
| `POST` | `/api/scheduler/jobs/{job_id}/disable` | Disable job |
| `GET` | `/api/scheduler/jobs/{job_id}/history` | Get scheduler events for job |

### What remains unchanged

- `app/scheduler/worker.py`
- `app/scheduler/handoff.py`
- `app/scheduler/repository.py`
- `app/scheduler/calculator.py`
- `app/scheduler/lease.py`
- `app/scheduler/models.py`
- `app/events/schemas.py`
- `app/events/history.py`
- `app/db/database.py`
- `app/db/models/scheduled_job.py`
- `app/core/lifecycle.py`

### Test matrix (final)

| Category | Count |
|----------|-------|
| CRUD | 8 |
| Idempotency | 3 |
| Validation | 6 |
| Schedule semantics | 8 |
| Enable/disable | 6 |
| Delete by status | 6 |
| PATCH by status | 8 |
| Authorization/target existence | 3 |
| History | 2 |
| Worker interaction | 3 |
| Error handling | 3 |

**Total: ~56 new tests**

---

## 11. Final Decision

**STAGE_12_3_CONTRACT_CONFIRMED**

All contract ambiguities are resolved. Implementation may proceed with the corrected semantics documented in this addendum.

---

## 12. Final Pre-Implementation Verification

### Verified facts from actual codebase

1. **No `Plan` entity exists.** Plans are identified by `plan_id` string on `DevelopmentTask` rows and event metadata. There is no `PlanRepository` or `Plan` model.

2. **`get_tasks_by_plan()` is not authoritative for plan existence.** `HistoryService.get_plan_history()` docstring explicitly states: "A plan may have events even when it currently has no associated tasks. Absence of tasks is not treated as absence of plan history." Test `test_history_service.py:244` confirms events exist for `plan_no_tasks`.

3. **`finalize_triggered_and_advance()` preserves `last_run_at`.** After trigger: `last_run_at = current.next_run_at` (scheduled fire time), `next_run_at` calculated from `max(now, current.next_run_at)`. PATCH recalculating from `last_run_at` is safe because current occurrence is already handed off.

4. **Worker queries `status='PENDING' AND enabled=1`.** A DISABLED job (`status=DISABLED`) is NOT eligible for polling even if `enabled=1`. Enable/disable only toggles `enabled`, not `status`.

5. **`ScheduledJobModel` has no foreign key to `events`.** Deleting a job does not cascade-delete events. Audit trail is preserved.

6. **`SchedulerRepository.update_job()` allows updating occurrence fields** (`occurrence_id`, `claim_token`, etc.) at the repository level. The API/service layer MUST enforce immutability.

7. **`calculate_next_occurrence()` returns `None` for elapsed ONCE schedules.** Verified: ONCE with past target returns `None`. Service layer must terminalize (set `enabled=false`, `status=DISABLED`).

8. **History correlation convention: `correlation_id = job_id`.** `GET /api/history/correlations/{job_id}` returns all scheduler events. Dedicated history endpoint delegates to `HistoryService.get_correlation_history()`.

9. **Existing `/api/plans` endpoints also use `get_tasks_by_plan()` for plan existence.** The scheduler's plan-validation limitation is consistent with existing application behavior.

### Corrections to previous contract

| Previous statement | Correction |
|--------------------|------------|
| "A plan exists if `get_tasks_by_plan()` returns non-empty" | **Incorrect.** No authoritative plan existence check. Scheduler must NOT validate plan existence at API layer. |
| "DISABLED after terminalization: job becomes eligible when enabled" | **Incorrect.** Enable/disable only toggles `enabled`. Status remains `DISABLED`. User must also PATCH `status=PENDING` to make eligible. |
| "Re-enable recalculates `next_run_at`" | **Incorrect.** Re-enable preserves `next_run_at`. No recalculation. |

### Final authoritative Stage 12.3 contract

1. **Create**: Idempotent on semantic equality of authoritative fields. ONCE with elapsed target → 422. Plan targets → no existence check at API layer.
2. **List/Get**: Thin delegation to `SchedulerRepository.list_jobs()` / `get_job()`.
3. **PATCH**: Status-dependent restrictions. CLAIMED rejects schedule changes. TRIGGERED allows schedule changes (recalculates from `last_run_at`). Immutable occurrence fields enforced at service layer.
4. **Delete**: Allowed for PENDING/FAILED/SKIPPED/DISABLED. Rejected for CLAIMED/TRIGGERED (409).
5. **Enable/Disable**: Toggle `enabled` only. Does not change `status`. DISABLED job requires `status=PENDING` to become eligible.
6. **History**: Delegates to `HistoryService.get_correlation_history(job_id)`.
7. **Service layer**: `app/scheduler/service.py` owns all business rules. Routes remain thin.
8. **No schema changes required.**

### What remains unchanged

- `app/scheduler/worker.py`
- `app/scheduler/handoff.py`
- `app/scheduler/repository.py`
- `app/scheduler/calculator.py`
- `app/scheduler/lease.py`
- `app/scheduler/models.py`
- `app/events/schemas.py`
- `app/events/history.py`
- `app/db/database.py`
- `app/db/models/scheduled_job.py`
- `app/core/lifecycle.py`

### Implementation-ready checklist

- [ ] `app/scheduler/service.py` created with business logic
- [ ] `app/api/schemas/scheduler.py` created with request/response models
- [ ] `app/api/routes/scheduler.py` created with 8 endpoints
- [ ] `app/dependencies.py` updated with `get_scheduler_service`
- [ ] `app/main.py` updated to include scheduler router
- [ ] `tests/test_scheduler_api.py` created with ~56 tests
- [ ] All existing tests pass (no regressions)
- [ ] Full repository test suite passes

**STAGE_12_3_CONTRACT_CONFIRMED**
