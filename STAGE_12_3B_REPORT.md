# Stage 12.3B — Scheduler REST Control Plane Implementation Report

**Status:** STAGE_12_3B_PASS

---

## 1. Implementation Summary

Implemented the complete Stage 12.3B Scheduler REST Control Plane, exposing the existing `SchedulerService` through 8 HTTP endpoints. The route layer is thin: it handles HTTP concerns, Pydantic validation, response serialization, and error mapping. All business logic remains in `SchedulerService`. The existing Scheduler execution architecture (worker, handoff, lease, calculator) is unchanged.

A minimal contract enhancement was required in `SchedulerService.create_job`: the method now raises a dedicated `JobIdempotentReturn` exception to signal idempotent hits, allowing the route to return the correct HTTP 200 status instead of always returning 201. This is a genuine contract defect for the API layer, not a business-rule change.

---

## 2. Endpoints Implemented

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/scheduler/jobs` | Create job (idempotent) |
| `GET` | `/api/scheduler/jobs` | List jobs |
| `GET` | `/api/scheduler/jobs/{job_id}` | Get single job |
| `PATCH` | `/api/scheduler/jobs/{job_id}` | Partial update |
| `DELETE` | `/api/scheduler/jobs/{job_id}` | Delete job |
| `POST` | `/api/scheduler/jobs/{job_id}/enable` | Enable job |
| `POST` | `/api/scheduler/jobs/{job_id}/disable` | Disable job |
| `GET` | `/api/scheduler/jobs/{job_id}/history` | Correlation history |

Router registration: `app/main.py:109` (`prefix="/api"`, router paths include `/scheduler/jobs`).

---

## 3. Service / DI Integration

- **Dependency provider added** in `app/dependencies.py`:
  - `get_scheduler_service` constructs `SchedulerRepository` (with `EventEmitter`) and `SchedulerService`.
  - Follows the same `AsyncGenerator` + `yield` pattern as `get_history_service`.
  - Reuses the single request-scoped `AsyncSession` from `get_db`.

- **Router registration** in `app/main.py`:
  - `app.include_router(scheduler_routes.router, prefix=prefix, tags=["Scheduler"])`
  - Final paths are exactly `/api/scheduler/jobs` etc.

---

## 4. Error Mapping

Route-layer `_map_service_error()` translates `ValueError` from `SchedulerService`:

| Condition | HTTP Status |
|-----------|-------------|
| `not found` (job / target) | `404` |
| `different definition` (idempotency conflict) | `409` |
| `active claim` / `execution in progress` | `409` |
| `cannot modify ... while occurrence is CLAIMED` | `409` |
| `cannot delete` | `409` |
| `in the past` (ONCE validation) | `422` |
| `fields cannot be modified` | `422` |
| Unexpected infrastructure failure | `500` (via global exception handler / Starlette default) |

Pydantic validation errors (schema, enum, timezone, pagination bounds, `extra="forbid"`) are handled automatically by FastAPI → `422`.

No stack traces, SQL statements, claim tokens, or internal paths are exposed in error responses.

---

## 5. Security Review

- **No sensitive fields exposed**: `ScheduledJobResponse` excludes `occurrence_id`, `claim_token`, `lease_until`, `execution_id`, `metadata`, `claimed_at`. Only durable scheduler state is returned.
- **No execution triggered by control plane**: Verified by tests that POST / PATCH / ENABLE / DISABLE do not advance job status beyond `PENDING` or populate `execution_id`.
- **No direct DB access from routes**: All routes delegate to `SchedulerService` / `HistoryService`.
- **No business rules duplicated in routes**: Status-dependent PATCH restrictions, DELETE state checks, schedule recalculation, and idempotency comparison all live in `SchedulerService`.

---

## 6. Test Matrix

### New API tests (`tests/test_scheduler_api.py`) — 60 tests

| Category | Count | Tests |
|----------|-------|-------|
| CREATE | 16 | Valid 201, ONCE future/past, INTERVAL, CRON, invalid schedule, invalid timezone, task target exists/missing, plan target accepted, idempotent 200, conflict 409 (name/schedule/timezone/target), never overwrite |
| GET (list + single) | 6 | Empty list, populated list, deterministic ordering, get existing, get missing 404, sanitized response |
| PATCH | 16 | Partial update, PENDING name/target/schedule, CLAIMED enabled only, CLAIMED schedule/target/name → 409, TRIGGERED, FAILED, SKIPPED, DISABLED, schedule recalculation, last_run_at preserved, immutable occurrence fields rejected, arbitrary fields rejected |
| ENABLE / DISABLE | 6 | Enable, disable, no schedule recalculation, no status reset, missing 404, active execution not cancelled |
| DELETE | 7 | PENDING/FAILED/SKIPPED/DISABLED → 204, CLAIMED/TRIGGERED → 409, missing → 404 |
| HISTORY | 5 | Populated, empty, pagination, invalid pagination 422, correlation convention |
| ERROR / SECURITY | 4 | Malformed JSON 422, unexpected repository failure 500, no sensitive fields, no execution triggered |

### Existing service tests (`tests/test_scheduler_service.py`) — 29 tests
- All 29 pass after updating the idempotency test to expect `JobIdempotentReturn`.

---

## 7. Exact Test Results

### Focused tests (new API + service)
```
tests/test_scheduler_api.py ............ 60 passed
tests/test_scheduler_service.py ........... 29 passed
```
**Focused total: 89 passed, 0 failed, 0 skipped.**

### Scheduler / API subset
```
tests/test_scheduler_api.py       60 passed
tests/test_scheduler_service.py   29 passed
tests/test_history_api.py          26 passed
tests/test_history_integration.py   5 passed
```
**Scheduler/API subset: 120 passed, 0 failed.**

### Full regression
```
2048 passed, 4 skipped, 45 warnings in 473.46s (0:07:53)
```
**Full regression: 0 failures.**

All 45 warnings are pre-existing (SQLAlchemy connection lifecycle warnings, coroutine cleanup warnings). No new warnings introduced.

---

## 8. Files Changed

| File | Change |
|------|--------|
| `services/core/app/api/routes/scheduler.py` | **NEW** — 8 thin route handlers, error mapping |
| `services/core/app/dependencies.py` | **MODIFIED** — added `get_scheduler_service` provider |
| `services/core/app/main.py` | **MODIFIED** — registered scheduler router under `/api` |
| `services/core/app/scheduler/service.py` | **MODIFIED** — added `JobIdempotentReturn` exception for idempotency signaling |
| `services/core/tests/test_scheduler_api.py` | **NEW** — 60 integration tests |
| `services/core/tests/test_scheduler_service.py` | **MODIFIED** — updated 1 test to handle `JobIdempotentReturn` |

---

## 9. Deviations

None. The implementation follows the finalized Stage 12.3 addendum exactly.

---

## 10. Known Limitations

- **No authentication / authorization**: As specified in the scope, no auth or multi-user controls were added.
- **No large filtering language**: The list endpoint exposes only the filters already supported by `SchedulerService.list_jobs` (status, enabled, target_type, target_id, limit, offset).
- **Pagination bounds are hard-coded**: limit max 500, offset max 100,000, matching the existing project convention.

---

## 11. Recommended Next Step

Proceed to Stage 12.3C (Scheduler Worker Control Plane) or the next scheduled stage. The REST Control Plane is complete and verified.
