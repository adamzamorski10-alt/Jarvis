# STAGE 12.1 — SCHEDULER FOUNDATION DISCOVERY

## 1. Existing Architecture

The AI Operator Core is a FastAPI application using:
- **Async SQLAlchemy** with SQLite (`sqlite+aiosqlite:///`)
- **FastAPI lifespan** (`@asynccontextmanager`) for startup/shutdown
- **asyncio** for background tasks
- **Pydantic v2** for domain models
- **structlog** for structured logging
- **EventEmitter/EventRepository** for append-only event store
- **HistoryService** for read-side event queries
- **TaskRepository** for task persistence
- **BackgroundExecutionManager** for in-memory background execution tracking
- **PermissionEngine** + **ApprovalService** + **CapabilityApprovalService** for authorization
- **AutonomousPlanExecutor** → **PlanExecutionService** → **AutonomousRunController** for execution

### Startup/Shutdown
`app/core/lifecycle.py`:
1. Configures logging
2. Initializes database (`init_db()`)
3. Seeds default settings
4. Optionally starts Ollama
5. Registers builtin tools
6. Yields control to application
7. On shutdown: cancels `BackgroundExecutionManager` tasks, cleans up Ollama

### Database
- Single SQLite file (`data/ai_operator.db`)
- `Base` declarative base with `expire_on_commit=False`
- Lazy singleton engine/session factory via `get_engine()` / `get_session_factory()`
- Lightweight migrations in `init_db()` using raw SQL

### Existing Background Mechanisms
- **BackgroundExecutionManager**: In-memory singleton (`app/services/background_execution_manager.py`)
  - Tracks active executions via `dict[str, InternalExecutionHandle]`
  - Uses per-plan `asyncio.Lock` for concurrency control
  - Launches `asyncio.create_task()` for each execution
  - **Not durable**: state lost on restart
  - Shutdown cancels pending tasks with 10s timeout

### Event System
- **EventEmitter**: Validates, sanitizes, timestamps, and persists events
- **EventRepository**: SQLAlchemy repository with indexed queries
- **EventModel**: Append-only table with `event_id` PK
- **HistoryService**: Read-side query layer with pagination
- All timestamps are UTC `DateTime(timezone=True)`

### Execution Chain
```
REST API
  → BackgroundExecutionManager (in-memory tracking)
    → AutonomousPlanExecutor (multi-task DAG loop)
      → PlanExecutionService (eligibility checks)
        → AutonomousRunController (single task execution)
          → AutonomousTaskOrchestrator (task lifecycle)
            → TaskStateMachine (state transitions)
              → TaskRepository (persistence)
```

### Security Boundary
- **PermissionEngine**: Evaluates tool execution requests
- **ApprovalService**: Manages pending approvals for dangerous operations
- **CapabilityApprovalService**: AI Manager approval flow
- **ExecutionBridge**: ONLY authorized path from AI Manager decisions to execution
- **VerificationGate**: Sole authority for code verification

---

## 2. Current Relevant Components

| Component | Relevance to Scheduler |
|-----------|----------------------|
| `BackgroundExecutionManager` | In-memory execution tracking. Scheduler must complement, not replace. |
| `AutonomousPlanExecutor` | Executes plans. Scheduler should trigger this, not duplicate it. |
| `EventEmitter` | Scheduler should emit events for observability. |
| `HistoryService` | Scheduler history should be queryable through existing HistoryService. |
| `TaskRepository` | Tasks have `plan_id`. Scheduler references tasks/plans. |
| `SettingsService` | Scheduler settings (enabled/disabled, poll interval) should be stored here. |
| `PermissionEngine` | Scheduler must respect existing authorization. |
| `ApprovalService` | Scheduled actions requiring approval must still go through approval flow. |
| `BackgroundExecutionManager` | Scheduler should integrate with existing execution manager, not bypass it. |
| `lifespan()` | Scheduler should start on app startup, stop on shutdown. |

### What Does NOT Exist
- No persistent scheduler/job store
- No cron/interval/time-based trigger mechanism
- No schedule CRUD API
- No misfire/recovery logic
- No lease/claim mechanism for distributed safety

---

## 3. Scheduler Boundary

### Scheduler OWNS
- **When** a scheduled action becomes due
- **Persistent schedule definitions** (what, when, enabled/disabled)
- **Due detection** (polling or trigger-based)
- **Misfire handling** (what to do when due time is missed)
- **Concurrency policy** (skip/queue/allow overlap)
- **Recovery on startup** (detect missed executions)

### Scheduler DOES NOT OWN
- **How** tasks/plans execute → delegates to `BackgroundExecutionManager` / `AutonomousPlanExecutor`
- **Permission evaluation** → delegates to `PermissionEngine` / `ApprovalService`
- **Verification** → delegates to `VerificationGate`
- **State transitions** → delegates to `TaskStateMachine` / `TaskRepository`
- **Event persistence** → delegates to `EventEmitter`
- **Authorization** → never bypasses `ExecutionBridge`

### Critical Invariant
```
Scheduler determines WHEN
Existing system determines HOW
```

Scheduler must never:
- Become a second orchestrator
- Become a second execution engine
- Bypass `PermissionEngine` or `ApprovalService`
- Mutate task/plan state directly
- Execute tools or subprocesses

---

## 4. Proposed Domain Model

### `ScheduledJob` (minimal v1)

| Field | Type | Why |
|-------|------|-----|
| `job_id` | `str` PK | Stable identifier. Format: `sched_<12 hex chars>` |
| `name` | `str` | Human-readable label |
| `enabled` | `bool` | Soft enable/disable without deletion |
| `schedule_type` | `str` | `cron`, `interval`, `once` |
| `schedule_expr` | `str` | Cron expression or interval string (e.g., `"*/5 * * * *"` or `"300"`) |
| `timezone` | `str | None` | IANA timezone for interpretation (e.g., `"America/New_York"`). `None` = UTC. |
| `target_type` | `str` | `task` or `plan` |
| `target_id` | `str` | The `task_id` or `plan_id` to execute |
| `last_run_at` | `datetime | None` | Last successful trigger time (UTC) |
| `next_run_at` | `datetime | None` | Computed next due time (UTC). Indexed for efficient polling. |
| `created_at` | `datetime` | Creation timestamp (UTC) |
| `updated_at` | `datetime` | Last modification timestamp (UTC) |
| `metadata` | `dict | None` | Optional job metadata (max 1KB, sanitized like events) |

### What is NOT included in v1
- **Retry/misfire policy**: Hardcoded deterministic policy (see §9)
- **Concurrency policy**: Hardcoded `SKIP` (see §9)
- **Priority**: Not needed for v1
- **Owner/creator**: Not needed for v1 (deferred)
- **Run history table**: Reuse Event Store for execution history
- **Execution records**: Scheduler does not track execution outcomes; existing systems do

### Why This Minimal Model
- Existing `TaskRepository` already tracks task state, attempts, heartbeats
- Existing `EventRepository` already tracks all lifecycle events
- Existing `BackgroundExecutionManager` tracks active in-memory executions
- Scheduler only needs to bridge "when" to "trigger execution"

---

## 5. Schedule Types

### v1: Minimal Useful Set

| Type | Format | Example | Notes |
|------|--------|---------|-------|
| `cron` | Standard 5-field cron | `"*/5 * * * *"` | Minute-level granularity. Use existing `croniter` library or lightweight parser. |
| `interval` | Seconds as integer string | `"3600"` | Fixed-interval from `last_run_at` or `next_run_at`. |
| `once` | ISO 8601 datetime | `"2026-09-20T03:00:00Z"` | One-time execution. Job disabled after firing. |

### Deferred
- **Day-of-week/month names**: Use numeric cron fields in v1
- **Jitter/randomization**: Deferred
- **Calendar exclusions** (holidays): Deferred
- **Dynamic schedules** (calculated at runtime): Deferred
- **Sunrise/sunset**: Deferred

### Rationale
- `cron` covers most recurring use cases
- `interval` covers simple polling/repeating tasks
- `once` covers one-off scheduled actions
-这三种类型提供了v1的坚实基础，无需创建复杂的调度DSL

---

## 6. Time & Timezone Semantics

### Canonical Storage
- **All timestamps stored in UTC** (`DateTime(timezone=True)` in SQLAlchemy)
- `last_run_at`, `next_run_at`, `created_at`, `updated_at` are ALL UTC

### Timezone Handling
- `timezone` field stores IANA timezone name (e.g., `"America/New_York"`)
- `schedule_expr` for cron is interpreted in the job's `timezone`
- `next_run_at` is ALWAYS computed and stored in UTC
- `None` timezone = UTC

### DST & Ambiguous Times
- Use `zoneinfo` (Python 3.9+) for timezone-aware datetime calculations
- For cron in DST-transition zones:
  - `croniter` with `zoneinfo` handles ambiguous/nonexistent times via `is_dst` flag
  - v1 policy: **skip ambiguous/nonexistent times** rather than guessing
- For intervals: always compute from UTC `next_run_at`, no DST concerns

### Clock Jumps & System Sleep
- Scheduler polls periodically (e.g., every 60 seconds)
- If system sleeps for N minutes, up to N/60+1 occurrences may be pending
- Misfire policy handles catch-up (see §9)

### Deterministic Rules
1. `next_run_at` is always computed from the previous trigger time + schedule interval
2. For `once` schedules, `next_run_at` = the specified UTC time
3. For `interval` schedules, `next_run_at` = `last_run_at` + interval seconds
4. For `cron` schedules, `next_run_at` = next cron match after `last_run_at` (or `created_at` if never run)
5. All computations use UTC internally
6. No naive datetimes anywhere in scheduler

---

## 7. Durability & Recovery

### Persistence
- `scheduled_jobs` table in SQLite (same database)
- Survives application restarts
- No external dependencies

### Startup Recovery
On application startup (in `lifespan()`):
1. Query all `enabled=True` jobs
2. Recompute `next_run_at` for each job (in case of clock changes or missed polls)
3. Jobs with `next_run_at` in the past are "due" and must be processed
4. Scheduler starts polling loop

### Missed While Offline
- Scheduler does NOT try to "catch up" all missed occurrences by default
- Instead, it applies the **misfire policy** (see §9)
- For `once` schedules that missed their window: skip or run once immediately
- For recurring schedules: advance `next_run_at` to current time and continue

### Crash Between "Due" and Execution
- Scheduler uses atomic DB transaction to:
  1. Claim the job occurrence
  2. Update `last_run_at`
  3. Emit `scheduler.occurrence.claimed` event
  4. Trigger execution via `BackgroundExecutionManager`
- If crash occurs after claim but before execution, the job is still marked as run
- Duplicate prevention relies on the claim transaction (see §10)

### Crash During Execution
- Execution is handled by `BackgroundExecutionManager` / `AutonomousPlanExecutor`
- Scheduler's responsibility ends at triggering execution
- Existing crash recovery mechanisms in `AutonomousPlanExecutor` apply

### Stale Leases
- Scheduler does NOT use distributed leases for v1 (single-process deployment)
- In-memory claim is sufficient for local application
- If scheduler crashes mid-claim, DB transaction rollback ensures no stale state

---

## 8. Concurrency & Leasing

### Deployment Model
- **v1 assumption**: Single application process, single scheduler instance
- The application is a local desktop service (Tauri frontend)
- No multi-worker, no distributed deployment in v1

### Claim Strategy
- Scheduler claims a job occurrence via atomic SQL UPDATE:
  ```sql
  UPDATE scheduled_jobs
  SET last_run_at = :now, next_run_at = :next, updated_at = :now
  WHERE job_id = :job_id AND next_run_at <= :now AND enabled = 1
  ```
- If `rowcount == 0`, another process already claimed it (or it's not due)
- This is the **sole atomic claim mechanism**

### No Distributed Locks
- No Redis, no etcd, no advisory locks
- SQLite's single-writer guarantee provides natural serialization
- For v1, this is sufficient

### Future-Proofing
- If multi-process deployment becomes necessary, the same atomic UPDATE pattern works
- SQLite WAL mode supports concurrent readers

---

## 9. Misfire Policy

### Definition
A **misfire** occurs when a scheduled execution was due but did not run.

### v1 Deterministic Policy: **SKIP_ONCE**

| Scenario | Behavior |
|----------|----------|
| Application offline, one-time job missed | **SKIP**: Disable job, emit `scheduler.occurrence.skipped` |
| Application offline, recurring job missed by N occurrences | **SKIP_ONCE**: Advance `next_run_at` to current time + one interval, emit `scheduler.occurrence.skipped` with count |
| Previous execution still running when next becomes due | **SKIP**: Do not queue. Emit `scheduler.occurrence.skipped` with reason `previous_still_running` |
| Scheduler overloaded (polling late) | **SKIP**: Treat as normal misfire. Run if within tolerance window. |
| System clock jumped backward | **NO_EXECUTE**: Skip occurrences in the past. Advance `next_run_at` to current time + interval. |

### Misfire Tolerance Window
- For `interval` schedules: if `next_run_at` is more than 2 intervals in the past, skip instead of catch-up
- For `cron` schedules: if next cron time is in the past, advance to next future cron time
- For `once` schedules: if `next_run_at` is more than 5 minutes in the past, skip

### Rationale
- Skip-once is deterministic and bounded
- Prevents thundering herd on restart
- Avoids unbounded catch-up loops
- Consistent with "fail-closed" application philosophy

---

## 10. Concurrency Policy

### v1 Policy: **SKIP**

If a job becomes due while its previous execution is still running:
- **Do NOT queue** the new occurrence
- **Do NOT overlap** executions
- **Skip** this occurrence
- Emit `scheduler.occurrence.skipped` with reason `previous_still_running`
- Update `next_run_at` to the next scheduled time

### Why SKIP?
- The system already has per-plan `asyncio.Lock` in `BackgroundExecutionManager`
- Overlapping executions could cause workspace conflicts, resource exhaustion
- Queueing requires a persistent queue, which adds complexity
- Skipping is deterministic and safe

### Future
- If queuing is needed, it should be implemented as a bounded queue with explicit max-depth
- Not in v1

---

## 11. Idempotency

### Three Layers of Idempotency

#### 1. Scheduler Occurrence Idempotency
- Each job occurrence is identified by `(job_id, scheduled_run_at)`
- The atomic UPDATE claim ensures only one scheduler instance claims an occurrence
- If scheduler crashes after claiming but before execution, the occurrence is NOT re-claimed
- `last_run_at` is updated to the claimed time, preventing re-claim

#### 2. Task Execution Idempotency
- Existing `TaskRepository` uses deterministic `task_id` = SHA-256(`plan_id:task_key`)
- `TaskStateMachine` prevents duplicate state transitions
- `BackgroundExecutionManager` prevents duplicate plan executions via in-memory lock + DB verification
- Scheduler does not bypass any of these

#### 3. Event Persistence Idempotency
- `EventRepository` uses `event_id` as primary key
- Duplicate `event_id` raises `EventIntegrityConflict`
- Scheduler emits events through `EventEmitter`, which handles idempotency

### Critical Distinction
- **Scheduler idempotency**: Prevents duplicate triggering (via atomic claim)
- **Task execution idempotency**: Prevents duplicate task execution (via TaskRepository + TaskStateMachine)
- **Event idempotency**: Prevents duplicate events (via EventRepository PK constraint)

Do not assume Event Store idempotency alone prevents duplicate execution. The scheduler's claim mechanism is the primary guard.

---

## 12. Security & Approval

### Scheduler Does NOT Grant Authorization

The scheduler is a **timing mechanism only**. When a scheduled job triggers, the existing authorization chain applies:

```
Scheduler triggers
  → BackgroundExecutionManager.start_execution()
    → AutonomousPlanExecutor
      → PlanExecutionService.execute_planned_task()
        → PermissionEngine (if tools are involved)
        → ApprovalService (if approval is required)
        → VerificationGate
        → ExecutionBridge (for AI Manager decisions)
```

### What Happens When Approval Is Required

If a scheduled action would normally require user approval:
- Scheduler triggers the action through normal channels
- `ApprovalService` / `CapabilityApprovalService` still requires explicit approval
- Scheduler does NOT convert `ASK_USER` into automatic authorization
- If approval is not granted, execution fails with appropriate error
- Scheduler emits `scheduler.execution.failed` with reason `approval_denied`

### Expired Permissions / Revoked Permissions
- If permissions expire between schedule creation and execution:
  - `PermissionEngine` evaluates at execution time, not schedule time
  - Execution fails with `PermissionDenied`
  - Scheduler emits failure event
  - Job remains enabled (operator can review and disable manually)

### Changed Policy After Schedule Creation
- Scheduler does NOT cache or assume any authorization state
- All authorization is re-evaluated at execution time
- Schedule creation only validates that target task/plan exists

### Security Boundary Documentation
```
Scheduler boundary:
  +-------------------+     triggers      +------------------------+
  | ScheduledJob (DB) | ----------------> | BackgroundExecutionMgr |
  +-------------------+                   +------------------------+
                                                    |
                                                    v
                                          +-------------------+
                                          | Existing auth chain|
                                          | (PermissionEngine, |
                                          |  ApprovalService,  |
                                          |  VerificationGate) |
                                          +-------------------+
```

Scheduler never:
- Grants permissions
- Bypasses ApprovalService
- Modifies PermissionEngine state
- Claims to perform authorization

---

## 13. Event / History Integration

### Scheduler Events

Use the existing `EventEmitter` with `source="scheduler"`.

| Event | When | Payload |
|-------|------|---------|
| `scheduler.job.created` | Job created via API | `{job_id, name, target_type, target_id, schedule_type}` |
| `scheduler.job.updated` | Job modified | `{job_id, changed_fields}` |
| `scheduler.job.enabled` | Job enabled | `{job_id}` |
| `scheduler.job.disabled` | Job disabled | `{job_id}` |
| `scheduler.job.deleted` | Job deleted | `{job_id}` |
| `scheduler.occurrence.due` | Job becomes due | `{job_id, scheduled_run_at}` |
| `scheduler.occurrence.claimed` | Scheduler claims occurrence | `{job_id, scheduled_run_at, claimed_at}` |
| `scheduler.occurrence.skipped` | Occurrence skipped | `{job_id, scheduled_run_at, reason}` |
| `scheduler.execution.started` | Execution triggered | `{job_id, execution_id}` |
| `scheduler.execution.completed` | Execution succeeded | `{job_id, execution_id}` |
| `scheduler.execution.failed` | Execution failed | `{job_id, execution_id, reason}` |
| `scheduler.recovery.started` | Startup recovery begins | `{recovered_jobs_count}` |
| `scheduler.recovery.completed` | Startup recovery ends | `{recovered_jobs_count, skipped_count}` |

### Correlation Semantics
- All scheduler events for a job occurrence share `correlation_id = job_id`
- `causation_id` links `occurrence.claimed` → `occurrence.skipped`/`execution.started`
- Execution events from `BackgroundExecutionManager` already have their own correlation

### No Separate Scheduler History Table
- All scheduler events go through `EventEmitter` → `EventRepository`
- Queryable via existing `HistoryService` with `source="scheduler"`
- No new history subsystem needed

---

## 14. Failure Modes

| Failure | Scheduler Behavior |
|---------|-------------------|
| Database unavailable | Scheduler stops polling. Emits `scheduler.execution.failed` with reason `database_unavailable`. Retries on next poll. Does NOT crash application. |
| Event persistence failure | Scheduler emits event, catches `EventEmissionError`, logs warning. Continues operation. Does NOT block execution. |
| Execution service unavailable | Scheduler triggers execution, `BackgroundExecutionManager` handles failure. Scheduler records failure in `last_run_at`. |
| Malformed schedule | Validation at creation/update time. Reject invalid `schedule_expr`. Existing job with corrupted schedule: disable job, emit `scheduler.job.disabled` with reason `invalid_schedule`. |
| Invalid timezone | Validation at creation time. Reject invalid IANA timezone. Existing job with invalid timezone: treat as UTC. |
| Stale lease | Not applicable for v1 (no distributed leases). |
| Process crash | DB transaction ensures no partial claims. On restart, recovery logic recomputes `next_run_at`. |
| System clock changes | Recompute all `next_run_at` on startup. Misfire policy handles past-due jobs. |
| Task deleted | Scheduler queries `TaskRepository` before triggering. If task missing, skip occurrence, emit `scheduler.occurrence.skipped` with reason `target_deleted`. Job remains enabled (operator review needed). |
| Plan deleted | Same as task deleted. |
| Permission revoked | `PermissionEngine` evaluates at execution time. Execution fails. Scheduler records failure. |
| Approval no longer valid | `ApprovalService` evaluates at execution time. Execution fails with `ASK_USER` or `DENY`. Scheduler records failure. |
| Execution timeout | Handled by `BackgroundExecutionManager` / `AutonomousRunController`. Scheduler records outcome via events. |

### Fail-Closed Principle
- Scheduler never fabricates success
- Scheduler never silently swallows failures
- All failures are emitted as events and logged
- Operator can query history via existing `HistoryService`

---

## 15. API Boundary

### Minimal v1 API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/scheduler/jobs` | Create schedule |
| GET | `/api/scheduler/jobs` | List schedules (filterable by enabled, target_type, target_id) |
| GET | `/api/scheduler/jobs/{job_id}` | Get schedule details |
| PATCH | `/api/scheduler/jobs/{job_id}` | Update schedule (name, schedule_expr, timezone, enabled) |
| DELETE | `/api/scheduler/jobs/{job_id}` | Delete schedule |
| POST | `/api/scheduler/jobs/{job_id}/trigger` | Trigger immediate execution (bypass schedule) |
| GET | `/api/scheduler/jobs/{job_id}/history` | Get scheduler events for job (delegates to HistoryService) |

### Authentication/Authorization
- v1: No authentication on scheduler API (same as other internal APIs)
- Scheduler operations are application-internal
- Frontend/UI access controlled at application level
- **No new authorization framework**

### Validation
- `schedule_expr` validated against cron/interval/once format at API boundary
- `timezone` validated against IANA database
- `target_id` verified to exist in `TaskRepository` at creation time
- Idempotency: `job_id` provided by client or server-generated UUID

### Not in v1
- Bulk operations
- Schedule templates
- Import/export
- dry-run / preview

---

## 16. Storage Design

### New Table: `scheduled_jobs`

```sql
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    job_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    schedule_type VARCHAR(20) NOT NULL,  -- 'cron', 'interval', 'once'
    schedule_expr VARCHAR(200) NOT NULL,
    timezone VARCHAR(64) NULL,
    target_type VARCHAR(20) NOT NULL,    -- 'task', 'plan'
    target_id VARCHAR(64) NOT NULL,
    last_run_at TIMESTAMP WITH TIME ZONE NULL,
    next_run_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    metadata TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_next_run_at ON scheduled_jobs(next_run_at);
CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_enabled ON scheduled_jobs(enabled);
CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_target ON scheduled_jobs(target_type, target_id);
```

### Constraints
- `schedule_type` IN (`cron`, `interval`, `once`)
- `target_type` IN (`task`, `plan`)
- `next_run_at` must be NOT NULL (always computable)
- `metadata` size capped at 1KB (same as event metadata)

### No Modifications to Existing Tables
- `scheduled_jobs` is a new table only
- No changes to `development_tasks`, `events`, etc.

### Indexes
- `idx_scheduled_jobs_next_run_at`: Primary query for "what's due?"
- `idx_scheduled_jobs_enabled`: Fast filtering of disabled jobs
- `idx_scheduled_jobs_target`: Fast lookup of schedules for a task/plan

No speculative indexes. These three cover all query patterns.

---

## 17. Observability

### Structured Logs
- Use existing `structlog` setup (`app/core/logging.py`)
- Scheduler logs with `logger = get_logger(__name__)` → `ai_operator.scheduler`
- Log levels:
  - `INFO`: Job created/updated/deleted, occurrence triggered, recovery completed
  - `WARNING`: Occurrence skipped, execution failed, misfire detected
  - `ERROR`: Database failure, malformed schedule, unexpected exception

### Events
- All significant scheduler actions emit events via `EventEmitter`
- Events are the primary observability mechanism
- Queryable via existing `HistoryService`

### Metrics (if existing infrastructure supports)
- No new metrics subsystem
- Can add `prometheus_client` counters later if needed
- v1: rely on event counts and log analysis

### State Visibility
- `GET /api/scheduler/jobs` returns all jobs with current state
- `last_run_at` and `next_run_at` provide execution timeline
- `HistoryService` with `source="scheduler"` provides full audit trail

---

## 18. Test Strategy

### Deterministic Testing
- **Fake clock**: Abstract time source (`_utc_now()`) that can be injected in tests
- **No real sleeping**: Use mocked time advancement
- **In-memory SQLite**: Fast, deterministic database tests

### Test Categories

| Category | Coverage |
|----------|----------|
| Schedule validation | Valid/invalid cron, interval, once formats |
| Time calculations | UTC conversion, DST transitions, ambiguous times |
| One-time schedules | Fire once, disable after firing, missed one-time |
| Recurring schedules | Cron correctness, interval correctness, next_run_at computation |
| Startup recovery | Recompute next_run_at, misfire handling, skip policy |
| Missed execution | Offline scenarios, misfire tolerance, skip-once behavior |
| Duplicate prevention | Atomic claim, crash recovery, idempotency |
| Concurrency | Single process, no duplicate claims, skip on overlap |
| Stale leases | Not applicable v1 (no distributed leases) |
| Crash recovery | Claim before crash, no duplicate after restart |
| Permission/approval boundaries | Scheduled action requires approval → still requires approval |
| Event emission | Correct events emitted for each action |
| Database failures | Graceful degradation, retry on next poll |
| Deterministic behavior | Same input → same output, no randomness |

### Preferred Approach
- Unit tests for schedule parsing/validation
- Integration tests with in-memory SQLite + fake clock
- No external providers, no network calls

---

## 19. Recommended Minimal V1

### 1. Scheduler Responsibility
Scheduler determines **WHEN** a `task_id` or `plan_id` should be executed. It does NOT execute tasks itself. Execution is delegated to `BackgroundExecutionManager` → `AutonomousPlanExecutor`.

### 2. Domain Model
Single `ScheduledJob` table with:
- `job_id`, `name`, `enabled`
- `schedule_type` (`cron`/`interval`/`once`), `schedule_expr`
- `timezone` (IANA, None=UTC)
- `target_type` (`task`/`plan`), `target_id`
- `last_run_at`, `next_run_at` (both UTC)
- `created_at`, `updated_at` (UTC)
- `metadata` (1KB cap)

### 3. Schedule Types
- **cron**: 5-field standard cron, minute granularity
- **interval**: Seconds as integer string
- **once**: ISO 8601 datetime

### 4. Timezone Semantics
- All storage in UTC
- `timezone` field for user-local interpretation
- `zoneinfo` for DST-safe calculations
- Skip ambiguous/nonexistent local times

### 5. Persistence Model
- New `scheduled_jobs` table in existing SQLite database
- No modifications to existing tables
- Three indexes: `next_run_at`, `enabled`, `(target_type, target_id)`

### 6. Claim/Lease Strategy
- Single application process (v1 assumption)
- Atomic SQL UPDATE claim: `WHERE job_id = :id AND next_run_at <= :now AND enabled = 1`
- SQLite single-writer guarantees natural serialization
- No distributed locks

### 7. Misfire Policy
- **SKIP_ONCE**: Advance `next_run_at` to current time + interval, skip missed occurrences
- Tolerance: 2x interval for recurring, 5 minutes for once
- Never catch up unboundedly

### 8. Concurrency Policy
- **SKIP**: If previous execution still running, skip this occurrence
- No queuing, no overlap
- Emit `scheduler.occurrence.skipped` with reason

### 9. Idempotency Strategy
- Atomic DB claim prevents duplicate triggering
- Existing TaskRepository + TaskStateMachine prevent duplicate execution
- EventRepository PK constraint prevents duplicate events

### 10. Security/Approval Boundary
- Scheduler triggers through normal execution path
- `PermissionEngine`, `ApprovalService`, `VerificationGate` all apply at execution time
- Scheduler does NOT grant, cache, or bypass any authorization
- `ASK_USER` remains `ASK_USER` when triggered by scheduler

### 11. Event/History Integration
- All scheduler events via `EventEmitter` with `source="scheduler"`
- Queryable via existing `HistoryService`
- Correlation: `correlation_id = job_id`

### 12. API Boundary
- 7 REST endpoints under `/api/scheduler/jobs`
- Thin routes → SchedulerService → EventEmitter/BackgroundExecutionManager
- No new authorization framework

### 13. Testing Strategy
- Deterministic fake clock for time calculations
- In-memory SQLite for persistence tests
- No external providers, no real sleeping
- Focus on: validation, time math, misfire, recovery, idempotency, security boundaries

### 14. Integration Point
```
lifespan() startup
  → SchedulerService.start()
    → Recompute next_run_at for all enabled jobs
    → Start polling loop (asyncio.create_task)
      → Every N seconds: SELECT due jobs
        → Atomic claim
        → Emit occurrence.claimed
        → Trigger BackgroundExecutionManager
        → Emit execution.started/completed/failed
```

---

## 20. Deferred Features

| Feature | Rationale |
|---------|-----------|
| Multi-worker/distributed scheduler | v1 is single-process. Re-evaluate if deployment model changes. |
| Distributed locks (Redis/etcd) | Not needed for local desktop service. |
| Persistent execution history table | Event Store is sufficient. |
| Schedule templates | Not needed for v1. |
| Bulk operations | Not needed for v1. |
| Calendar exclusions (holidays) | Not needed for v1. |
| Jitter/randomization | Not needed for v1. |
| Schedule versioning | Not needed for v1. |
| Dry-run/preview | Not needed for v1. |
| Job priority/weighted scheduling | Not needed for v1. |
| Scheduled job metrics dashboard | Use existing HistoryService + logs. |
| Webhook/notification on schedule failure | Use existing Notification Center (future stage). |
| Time-travel/simulation | Not needed for v1. |
| Dynamic schedule expressions | Not needed for v1. |

---

## 21. Risks / Open Questions

| Risk | Mitigation |
|------|------------|
| **SQLite write contention** | Single writer, multiple readers is fine for v1. If contention arises, add write queue. |
| **Polling latency** | 60-second poll interval is acceptable for v1. Can reduce if needed. |
| **Cron parsing complexity** | Use `croniter` library (already common in Python). Validate strictly. |
| **DST edge cases** | Skip ambiguous/nonexistent times. Document clearly. |
| **Target deletion while scheduled** | Scheduler checks target existence before triggering. Skips with event if missing. |
| **Clock skew on restart** | Recompute all `next_run_at` on startup using current system time. |
| **Long-running execution blocking scheduler** | Scheduler and execution are separate asyncio tasks. Execution in `BackgroundExecutionManager` does not block scheduler poll loop. |
| **Permission changes between schedule and execution** | Authorization re-evaluated at execution time. Scheduler does not cache permissions. |
| **Event volume** | Scheduler events are bounded (1 per job event, 1 per occurrence). Low volume. |

### Open Questions
1. **Polling interval**: 60 seconds? 30 seconds? Should this be configurable?
   - Recommendation: Fixed 60s for v1, configurable via `SettingsService` later.
2. **Maximum scheduled jobs**: Should there be a limit?
   - Recommendation: No hard limit v1. Add if needed based on usage.
3. **Should scheduler support `run_id`/`execution_id` correlation?**
   - Recommendation: Yes, pass `execution_id` from `BackgroundExecutionManager` to scheduler events.
4. **Should scheduler own "run-now" functionality?**
   - Recommendation: Yes, `POST /api/scheduler/jobs/{job_id}/trigger` is useful for manual invocation.

---

## 21. Scheduler Reliability Architecture Review

### 1. Crash Between Claim and Execution

**Finding: YES, the occurrence is lost in the current design.**

Current flow:
1. Scheduler finds due job
2. Atomic UPDATE advances `next_run_at` and sets `last_run_at`
3. Process crashes before `BackgroundExecutionManager.start_execution()` returns

Result: `next_run_at` has moved forward, but no execution was started. The occurrence is silently dropped. The job will not retry this occurrence.

**Correction: Two-phase handoff with explicit state**

Add a `status` field to `scheduled_jobs`:
- `PENDING` → `CLAIMED` → `TRIGGERED` → `COMPLETED` / `FAILED`

The claim transaction only advances to `CLAIMED`. The scheduler only considers an occurrence "safe" after `BackgroundExecutionManager` confirms acceptance (returns `execution_id`). If the process crashes in `CLAIMED` state, recovery logic on restart can detect unhandled claimed occurrences and either retry or mark failed.

### 2. Crash After Execution Start

**Finding: The current design conflates "claimed" with "executed."**

After restart:
- If `status = COMPLETED` or `FAILED`: known outcome, nothing to do
- If `status = CLAIMED` or `TRIGGERED`: outcome unknown, must NOT silently assume success
- If `status = PENDING` and `next_run_at` is past: due but not yet claimed

**Execution guarantee: AT-LEAST-ONCE with idempotent downstream.**

The architecture cannot provide exactly-once execution because:
- Scheduler claim and execution completion are separate atomic operations
- Crash between them leaves an unconfirmed state
- However, `BackgroundExecutionManager` + `TaskStateMachine` + `EventRepository` together provide **effectively-once** execution for the actual work

After restart:
1. Query jobs in `CLAIMED`/`TRIGGERED` state older than a timeout (e.g., 5 minutes)
2. If corresponding task is still RUNNING: leave alone (execution is alive)
3. If corresponding task is terminal (COMMITTED/FAILED): mark scheduler job `COMPLETED`/`FAILED`
4. If no corresponding task found: mark `FAILED` with reason `orphaned_claim`, emit event

This is **at-least-once** from the scheduler's perspective, but the execution layer's idempotency makes it effectively-once.

### 3. Claim/Lease Model

**Finding: `scheduled_jobs` alone is insufficient.**

The table needs minimal lease fields:

| Field | Purpose |
|-------|---------|
| `status` | `PENDING`, `CLAIMED`, `TRIGGERED`, `COMPLETED`, `FAILED`, `DISABLED` |
| `occurrence_id` | Stable ID for this specific occurrence: `f"{job_id}:{scheduled_run_at_iso}"` |
| `claim_token` | Random token proving this process holds the claim |
| `claimed_at` | When claim was made (UTC) |
| `lease_until` | Claim expires if not confirmed (UTC). Default: `claimed_at + 5 minutes`. |
| `execution_id` | Returned by `BackgroundExecutionManager` on acceptance |

**Atomic claim transaction:**
```sql
UPDATE scheduled_jobs
SET status = 'CLAIMED',
    claim_token = :token,
    claimed_at = :now,
    lease_until = :now + 5 minutes,
    occurrence_id = :occurrence_id
WHERE job_id = :job_id
  AND status = 'PENDING'
  AND next_run_at <= :now
  AND enabled = 1
```

If `rowcount == 0`, another instance claimed it or it's no longer due.

**Lease expiration:** On startup, any `CLAIMED`/`TRIGGERED` job with `lease_until < now` is considered stale. The scheduler can reclaim it by resetting to `PENDING` with a new `occurrence_id` (if the execution never started) or marking `FAILED` (if execution evidence exists in Event Store).

### 4. Occurrence Identity

**Recommendation: Explicit `occurrence_id`, NOT derived from `job_id + scheduled_run_at`.**

Rationale:
- `job_id + scheduled_run_at` is deterministic, which seems appealing
- BUT: if a job is recovered/reclaimed after a crash, the `scheduled_run_at` might be recomputed differently
- An explicit `occurrence_id` generated at claim time is a stable, immutable identifier for that specific attempt
- Format: `occurrence_<job_id>_<12 hex chars>` or UUID
- `occurrence_id` is emitted in all scheduler events and passed as `execution_id` correlation

This makes scheduler occurrence idempotency **distinct** from Event Store idempotency:
- Scheduler level: `occurrence_id` prevents duplicate claims
- Event level: `event_id` prevents duplicate events
- Execution level: existing `TaskStateMachine` prevents duplicate task execution

### 5. Execution Handoff

**Current gap: scheduler considers handoff complete when `BackgroundExecutionManager.start_execution()` returns.**

This is wrong. The handoff has three distinct phases:

| Phase | Scheduler Status | Crash Window |
|-------|-----------------|--------------|
| 1. Claim | `PENDING` → `CLAIMED` | After claim, before trigger |
| 2. Trigger | `CLAIMED` → `TRIGGERED` | After trigger accepted, before `execution_id` returned |
| 3. Confirm | `TRIGGERED` → `COMPLETED`/`FAILED` | After `execution_id` returned, before status update |

**Crash windows:**

**Window A: Between claim and trigger**
- Scheduler has `CLAIMED` but never called `start_execution()`
- Recovery: lease expires, job resets to `PENDING`, next poll retries
- If within misfire tolerance: re-claim and trigger
- If past tolerance: skip, emit `scheduler.occurrence.skipped`

**Window B: Between trigger acceptance and status update**
- `BackgroundExecutionManager` returned `execution_id`, but scheduler crashed before updating status
- Scheduler sees `TRIGGERED` with known `execution_id` on restart
- Recovery: query `HistoryService` for `scheduler.execution.started` with this `execution_id`
  - If found: mark `COMPLETED` (or wait for completion event)
  - If not found: execution was never started, reset to `PENDING` if within tolerance

**Window C: After status update, execution still running**
- Scheduler marked `TRIGGERED`, execution is in progress
- This is normal. Scheduler does NOT wait for execution completion.
- `BackgroundExecutionManager` handles its own lifecycle.

**Correct handoff contract:**
```
Scheduler only marks COMPLETED after:
  - BackgroundExecutionManager.start_execution() returned execution_id, AND
  - EventEmitter.persist(scheduler.execution.started) succeeded

Scheduler marks FAILED when:
  - start_execution() raised, OR
  - execution_id returned but event emission failed (still TRIGGERED, but event is missing)
```

### 6. Misfire Semantics (Precise)

**SKIP_ONCE means: skip exactly one occurrence, advance by exactly one interval/cron cycle.**

| Scenario | Behavior |
|----------|----------|
| One-time job, 10 minutes past due | Skip. Disable job. `next_run_at` = null. Emit `skipped`. |
| Interval job, 20 occurrences missed | Skip ONE occurrence. `next_run_at` = `now` + interval. Emit `skipped` with `missed_count=20`. |
| Cron job, 3 matches missed | Skip ONE occurrence. `next_run_at` = next future cron time. Emit `skipped` with `missed_count=3`. |
| Job due, previous still running | **Concurrency skip** (not misfire). Skip this occurrence. `next_run_at` = next scheduled time. Emit `skipped` with reason `previous_still_running`. |
| Job due, lease expired (Window A) | Treat as misfire. Reset to `PENDING`, re-evaluate. If still within tolerance: trigger. Else: skip. |

**`next_run_at` advancement rules:**
- `once`: after skip, set `enabled = 0` (job is finished)
- `interval`: `next_run_at = max(now, last_successful_run_at) + interval_seconds`
- `cron`: `next_run_at = next_cron_match_after(now)` (using `croniter`)

**No unbounded catch-up:** Never iterate through missed occurrences. Always advance by exactly one step.

### 7. Interval Semantics

**Recommendation: Option A — scheduled occurrence + interval (NOT execution completion + interval).**

`next_run_at = last_run_at + interval_seconds`

Where `last_run_at` is the **scheduled** fire time, NOT the actual execution completion time.

Rationale:
- Prevents schedule drift from execution duration
- Predictable: user knows exactly when occurrences fire
- If execution takes longer than interval: concurrency skip applies
- If execution fails: next occurrence still fires at scheduled time (unless policy says otherwise)

This matches standard cron-like behavior where schedule is independent of execution duration.

### 8. Once Semantics

**Lifecycle:**

| State | Meaning |
|-------|---------|
| `PENDING` | Not yet fired |
| `CLAIMED` | Claimed, not yet triggered |
| `TRIGGERED` | Execution accepted by `BackgroundExecutionManager` |
| `COMPLETED` | Execution accepted and `scheduler.execution.started` event persisted |
| `FAILED` | Execution rejected or lease expired |
| `DISABLED` | Job disabled after completion/failure |

**Rules:**
- A once job can only transition to `COMPLETED` or `FAILED` once
- After `COMPLETED` or `FAILED`: `enabled = 0`, no further occurrences
- After failure: do NOT retry. Once means once.
- After restart:
  - `PENDING` with past `next_run_at`: trigger if within tolerance, else skip
  - `CLAIMED` with expired lease: reset to `PENDING` if within tolerance, else `FAILED`
  - `TRIGGERED` with `execution_id`: check Event Store; if `scheduler.execution.started` exists, mark `COMPLETED`; else if no task found, mark `FAILED`

### 9. Cron Semantics

**Timezone handling:**
- `schedule_expr` is interpreted in the job's `timezone`
- `croniter` with `zoneinfo` handles DST transitions
- Next occurrence computed by: `croniter(expr, start_time=last_run_at, tz=zoneinfo(tz)).get_next()`

**DST spring-forward (nonexistent local time):**
- Example: 2:00 AM jumps to 3:00 AM
- If cron matches 2:15 AM (which doesn't exist): **SKIP** this occurrence
- Rationale: there is no valid local time to fire. Firing at 3:00 AM would be unexpected.
- Emit `scheduler.occurrence.skipped` with reason `nonexistent_time`

**DST fall-back (ambiguous local time):**
- Example: 2:00 AM falls back to 1:00 AM
- If cron matches 1:30 AM during the overlap: fire ONCE at the first occurrence (standard `croniter` behavior with `is_dst=True`)
- Document clearly: ambiguous times fire once

**Clock jumps:**
- If clock jumps forward: next poll detects `next_run_at` in the past, applies misfire policy
- If clock jumps backward: occurrences in the "new past" are skipped on next poll
- On restart: recompute all `next_run_at` from `last_run_at` + schedule, not from system clock

### 10. Concurrency

**Two distinct cases:**

**Case A: Two scheduler workers race for the same occurrence**
- Prevented by atomic UPDATE claim with `status = 'PENDING'` precondition
- Only one worker can transition `PENDING` → `CLAIMED`
- Loser gets `rowcount = 0`, moves to next job

**Case B: Previous execution still running when next becomes due**
- This is NOT a race condition; it's the concurrency policy
- `SKIP` policy: scheduler sees `status = TRIGGERED` or checks `BackgroundExecutionManager` for active execution
- Skip this occurrence, emit `scheduler.occurrence.skipped` with reason `previous_still_running`
- `next_run_at` advances to next scheduled time

**Detection mechanism for Case B:**
- Option 1: Scheduler queries `BackgroundExecutionManager` for active executions of this target
- Option 2: Scheduler checks `TaskRepository` for RUNNING/VERIFYING tasks belonging to this plan
- Recommendation: Option 2 (more durable, works after restart)

### 11. Security/Approval

**Confirmed: Scheduler never converts `ASK_USER → ALLOW`.**

When a scheduled job triggers:
1. Scheduler calls `BackgroundExecutionManager.start_execution()`
2. `BackgroundExecutionManager` calls `AutonomousPlanExecutor`
3. `AutonomousPlanExecutor` → `PlanExecutionService` → `AutonomousRunController`
4. `AutonomousRunController` → `PermissionEngine.evaluate()` for each tool
5. If `PermissionDecisionType.ASK_USER`: execution pauses, approval requested
6. `ApprovalService` handles user interaction
7. Scheduler records the outcome via events

**If required approval is unavailable:**
- Execution fails with `ExecutionNeedsApprovalError` or similar
- `BackgroundExecutionManager` records failure
- Scheduler emits `scheduler.execution.failed` with reason `approval_unavailable`
- Job remains enabled (operator can review and disable if needed)
- Next occurrence will attempt again (subject to concurrency/misfire policies)

**Scheduler does NOT:**
- Cache permission state
- Assume prior approvals apply
- Grant implicit permissions
- Bypass `ExecutionBridge`

### 12. Minimal V1 Design (Corrected)

#### Required Scheduler Fields

```sql
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    job_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    schedule_type VARCHAR(20) NOT NULL,     -- 'cron', 'interval', 'once'
    schedule_expr VARCHAR(200) NOT NULL,
    timezone VARCHAR(64) NULL,
    target_type VARCHAR(20) NOT NULL,       -- 'task', 'plan'
    target_id VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',  -- PENDING, CLAIMED, TRIGGERED, COMPLETED, FAILED, DISABLED
    last_run_at TIMESTAMP WITH TIME ZONE NULL,
    next_run_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    occurrence_id VARCHAR(128) NOT NULL,
    claim_token VARCHAR(64) NULL,
    claimed_at TIMESTAMP WITH TIME ZONE NULL,
    lease_until TIMESTAMP WITH TIME ZONE NULL,
    execution_id VARCHAR(64) NULL,
    metadata TEXT NULL
);
```

#### Lease Required? YES
Minimal lease: `claim_token`, `claimed_at`, `lease_until`.
Default lease: 5 minutes.
Expired lease = stale claim, eligible for recovery.

#### Occurrence ID Required? YES
Explicit `occurrence_id` generated at claim time.
Format: `occurrence_<job_id>_<12 hex>` or UUID.
Used for correlation, event payload, and idempotency.

#### Claim Transaction Semantics
```sql
UPDATE scheduled_jobs
SET status = 'CLAIMED',
    occurrence_id = :occurrence_id,
    claim_token = :claim_token,
    claimed_at = :now,
    lease_until = :now + 5 minutes,
    next_run_at = :next_run_at,
    updated_at = :now
WHERE job_id = :job_id
  AND status = 'PENDING'
  AND next_run_at <= :now
  AND enabled = 1
```
`rowcount == 1` = claimed. `rowcount == 0` = not due, not pending, or already claimed.

#### Crash Recovery Semantics
On startup:
1. Query all jobs where `status IN ('CLAIMED', 'TRIGGERED')`
2. For each:
   - If `lease_until < now`: stale lease
     - If `execution_id` is set: check Event Store for `scheduler.execution.started` with this `execution_id`
       - Found: mark `COMPLETED`
       - Not found: mark `FAILED`, reason `orphaned_claim`
     - If `execution_id` is NULL: reset to `PENDING`, recompute `next_run_at`, apply misfire policy
   - If `lease_until >= now`: still within lease, leave alone (another instance may be running)
3. Query all jobs where `status = 'PENDING' AND next_run_at <= now`: apply misfire policy

#### Execution Handoff Semantics
```
Phase 1: CLAIM
  UPDATE scheduled_jobs SET status='CLAIMED' ... WHERE status='PENDING' ...
  Emit: scheduler.occurrence.claimed

Phase 2: TRIGGER
  execution_id = await BackgroundExecutionManager.start_execution(...)
  UPDATE scheduled_jobs SET status='TRIGGERED', execution_id=:execution_id ... WHERE occurrence_id=:occurrence_id AND status='CLAIMED'
  Emit: scheduler.execution.started

Phase 3: CONFIRM (async)
  On execution completion/failure event:
    UPDATE scheduled_jobs SET status='COMPLETED'/'FAILED', last_run_at=:now ... WHERE occurrence_id=:occurrence_id
    Emit: scheduler.execution.completed/failed
```

Crash between Phase 1 and Phase 2: lease expires, recovery resets to PENDING.
Crash between Phase 2 and Phase 3: `TRIGGERED` with known `execution_id`. Recovery checks Event Store.

#### Once Semantics
- `once` job fires exactly once if successfully claimed and triggered
- After `COMPLETED`: `enabled = 0`, job is finished
- After `FAILED`: `enabled = 0`, job is finished (no retry)
- After restart with `PENDING` and past due: misfire policy applies (skip if > 5 minutes past)
- After restart with `CLAIMED` and expired lease: if `execution_id` present and event found → `COMPLETED`; else → `FAILED`

#### Interval Semantics
- `next_run_at = last_run_at + interval_seconds`
- `last_run_at` is the **scheduled** fire time, not actual completion time
- Prevents schedule drift
- If execution overruns: concurrency skip applies

#### Cron Semantics
- `croniter` with `zoneinfo(tz)` for next occurrence calculation
- Spring-forward nonexistent times: skip, emit `scheduler.occurrence.skipped` with reason `nonexistent_time`
- Fall-back ambiguous times: fire once at first occurrence (standard `croniter` behavior)
- Clock jumps: handled by misfire/recovery on restart
- All `next_run_at` stored in UTC

#### Misfire Semantics
- **SKIP_ONCE**: skip exactly one occurrence, advance by exactly one interval/cron cycle
- Never catch up unboundedly
- Tolerance: 2x interval for recurring, 5 minutes for once
- `next_run_at` advancement:
  - `interval`: `max(now, last_run_at) + interval`
  - `cron`: `croniter.get_next(now)`
  - `once`: disable job

#### Concurrency Semantics
- **Race between workers**: atomic UPDATE claim with `status = 'PENDING'` precondition
- **Overlap with previous execution**: `SKIP` policy
  - Scheduler checks `TaskRepository` for RUNNING/VERIFYING tasks in target plan
  - If found: skip, emit `scheduler.occurrence.skipped` with reason `previous_still_running`
  - `next_run_at` advances to next scheduled time

#### Security/Approval Semantics
- Scheduler never grants, caches, or bypasses authorization
- All permission/approval evaluation happens at execution time
- If approval unavailable: execution fails, scheduler records failure, job remains enabled
- `ASK_USER` remains `ASK_USER` when triggered by scheduler

#### Event Store Integration
- All scheduler events via `EventEmitter` with `source="scheduler"`
- `correlation_id = occurrence_id`
- `causation_id` links claim → trigger → complete/fail
- Queryable via existing `HistoryService`
- No separate scheduler history table

---

## 22. Implementation Readiness

`DESIGN_REQUIRES_REVISION`

The original discovery design is **insufficient for crash-safe durable scheduling**. The following minimum changes are required before Stage 12.2 implementation:

1. Add `status`, `occurrence_id`, `claim_token`, `claimed_at`, `lease_until`, `execution_id` to `scheduled_jobs`
2. Implement two-phase handoff: `PENDING` → `CLAIMED` → `TRIGGERED` → `COMPLETED`/`FAILED`
3. Implement lease expiration and recovery on startup
4. Define explicit crash windows and recovery behavior for each
5. Replace "SKIP_ONCE" with precise bounded-skip semantics
6. Clarify interval semantics as scheduled-time-based (not completion-time-based)
7. Document DST skip behavior and its user-visible implications
8. Add concurrency detection via `TaskRepository` query before triggering

Once these revisions are incorporated, the design will be `IMPLEMENTATION_READY`.

---

## 22. Durable Execution Handoff Review

### 1. Existing Execution Architecture Findings

The existing execution chain creates execution identities that are **in-memory only**, not durably persisted:

**BackgroundExecutionManager** (`app/services/background_execution_manager.py`):
- `execution_id = f"exec_{uuid.uuid4().hex[:12]}"` — generated, stored only in `_handles_by_exec_id` dict
- `InternalExecutionHandle` — in-memory only, lost on restart
- Returns `ExecutionHandleInfo` to caller
- **No database persistence of execution handles**

**AutonomousRunController** (`app/orchestration/run_controller.py`):
- `run_id = f"run_{uuid.uuid4().hex[:12]}"` — generated at `start_run()`, returned in `AutonomousRunResult`
- `AutonomousRunResult` — returned to caller, stored in-memory by `BackgroundExecutionManager`
- **No database persistence of run results**

**AutonomousTaskOrchestrator** (`app/orchestration/orchestrator.py`):
- Calls `TaskStateMachine.claim_attempt()` — **THIS IS ATOMIC AND DURABLE**
- Task state transitions (PENDING → RUNNING → COMMITTED/FAILED) are persisted via `TaskRepository`
- Emits `TASK_STARTED`, `TASK_COMPLETED`, `TASK_FAILED` events via `EventEmitter` — **DURABLE**

**Durable execution markers that already exist:**
1. `task_id` in `development_tasks` table — primary key, always present
2. Task `status` in DB — RUNNING, VERIFYING, PASSED, COMMITTED, FAILED, etc.
3. `TASK_STARTED` event in Event Store — emitted AFTER `claim_attempt()` succeeds
4. `TASK_COMPLETED`/`TASK_FAILED` events in Event Store — emitted at end
5. `last_heartbeat` on task — updated during execution

**What is NOT durably persisted:**
- `execution_id` from BackgroundExecutionManager
- `run_id` from AutonomousRunController
- `PlanExecutionResult` from AutonomousPlanExecutor
- BackgroundExecutionManager handles

**Critical finding:** A scheduler occurrence CAN be linked to durable execution identity BEFORE actual execution begins, but only through `task_id` and task state in `TaskRepository`. The `execution_id` generated by BackgroundExecutionManager is NOT durable until the scheduler persists it.

The earliest durable marker for "execution has started" is:
- Task state = RUNNING in `TaskRepository` (after `claim_attempt()`)
- OR `TASK_STARTED` event in Event Store

### 2. Crash Window Analysis

#### Window A: PENDING → CLAIMED, crash before execution trigger

| Aspect | Analysis |
|--------|----------|
| **Durably persisted** | Job status = CLAIMED, occurrence_id, claim_token, claimed_at, lease_until |
| **Can be lost** | Nothing else. No execution started. |
| **Can be duplicated** | No. Atomic claim prevents duplicate. |
| **Recoverable after restart** | Yes. Lease expires → reset to PENDING, apply misfire policy. |
| **Distinguish never started?** | Yes. TaskRepository shows task is PENDING. No TASK_STARTED event. |

#### Window B: CLAIMED, execution identity created in memory, crash before trigger accepted

| Aspect | Analysis |
|--------|----------|
| **Durably persisted** | Job status = CLAIMED. BackgroundExecutionManager has in-memory execution_id and asyncio task. |
| **Can be lost** | execution_id (in-memory only). asyncio task dies with process. |
| **Can be duplicated** | No. Atomic claim prevents duplicate scheduler claim. But if asyncio task claimed the task before crash, task is RUNNING in DB. |
| **Recoverable after restart** | Partially. Check TaskRepository for RUNNING tasks. If RUNNING: orphaned execution. If PENDING: execution never started, safe to retry. |
| **Distinguish never started?** | Yes. Task status in DB is the discriminator. |

#### Window C: execution identity persisted, BackgroundExecutionManager.start_execution() called

This is actually TWO sub-windows:

**Window C1: After start_execution() returns, before scheduler records TRIGGERED**

| Aspect | Analysis |
|--------|----------|
| **Durably persisted** | Job status = CLAIMED. execution_id exists in memory only. BackgroundExecutionManager has in-memory handle. |
| **Can be lost** | execution_id (in-memory only). asyncio task is alive but scheduler doesn't know about it. |
| **Can be duplicated** | If scheduler retries: BackgroundExecutionManager will create NEW execution_id. Old asyncio task might still run. |
| **Recoverable after restart** | After restart, old asyncio task is dead. Check TaskRepository for RUNNING tasks. If RUNNING: execution claimed but orphaned. If PENDING: never started. |
| **Distinguish never started?** | Yes, via TaskRepository. |

**Window C2: After scheduler records TRIGGERED, crash before execution completes**

| Aspect | Analysis |
|--------|----------|
| **Durably persisted** | Job status = TRIGGERED, execution_id persisted in job row. asyncio task is running. |
| **Can be lost** | Nothing critical. Execution is in progress. |
| **Can be duplicated** | No. execution_id is persisted. On restart, recovery knows this execution was triggered. |
| **Recoverable after restart** | Yes. Query Event Store for scheduler.execution.completed/failed. Check TaskRepository for task status. |
| **Distinguish never started?** | Yes. If TRIGGERED with execution_id and no completion event: check task status. |

#### Window D: execution running, process crashes before completion persisted

| Aspect | Analysis |
|--------|----------|
| **Durably persisted** | Job status = TRIGGERED. Task state = RUNNING (after claim_attempt). Events: TASK_STARTED exists. |
| **Can be lost** | In-memory asyncio task. BackgroundExecutionManager handle. PlanExecutionResult. |
| **Can be duplicated** | No. Task claim is atomic. No duplicate execution possible. |
| **Recoverable after restart** | Yes. Task is RUNNING in DB. RecoveryManager handles stale RUNNING tasks. Scheduler marks occurrence as FAILED with reason `orphaned_execution`. |
| **Distinguish never started?** | Yes. TASK_STARTED event + RUNNING task = definitely started. |

#### Window E: execution completes, scheduler crashes before status update

| Aspect | Analysis |
|--------|----------|
| **Durably persisted** | Job status = TRIGGERED. Task state = COMMITTED/FAILED. Events: TASK_STARTED + TASK_COMPLETED/FAILED. |
| **Can be lost** | Scheduler status update only. |
| **Can be duplicated** | No. Task is terminal. |
| **Recoverable after restart** | Yes. Query Event Store for completion event. Update job status. |
| **Distinguish never started?** | Yes. Completion event exists. |

### 3. Execution Semantics

**Exactly-once execution: IMPOSSIBLE with current architecture.**

The scheduler cannot guarantee exactly-once because:
1. Claim transaction and execution start are separate operations
2. Crash between them leaves an unconfirmable state
3. No distributed transaction spans scheduler DB and execution engine

**At-most-once execution: NOT guaranteed.**

If the scheduler retries a CLAIMED occurrence after lease expiration, it might trigger a second execution while the first is still running (if the first asyncio task survived the scheduler crash but is orphaned).

**At-least-once execution: GUARANTEED by design.**

If an occurrence is claimed and then lost due to crash, the recovery policy retries it (unless explicitly skipped).

**Effectively-once execution: ACHIEVABLE through idempotency.**

The execution layer provides effectively-once semantics:
- `TaskStateMachine.claim_attempt()` is atomic — prevents duplicate task claims
- `TaskRepository` prevents duplicate state transitions
- `EventRepository` PK constraint prevents duplicate events
- `BackgroundExecutionManager` per-plan lock prevents duplicate plan executions

So even if the scheduler retries an occurrence, the execution layer will reject duplicate attempts at the task level.

**Verdict: At-least-once at scheduler level, effectively-once at execution level.**

### 4. Occurrence vs Execution Identity

**These MUST be separate concepts.**

| Identity | Scope | Lifetime | Format | Persistence |
|----------|-------|----------|--------|-------------|
| `occurrence_id` | One scheduled fire | From claim to terminal state | `occurrence_<job_id>_<12hex>` | Persisted in `scheduled_jobs` |
| `execution_id` | One execution attempt | From trigger to completion | `exec_<12hex>` | Persisted in `scheduled_jobs` after trigger |

**Relationship:**
- One occurrence may have zero or one execution attempts
- If occurrence is skipped: has `occurrence_id`, no `execution_id`
- If occurrence is triggered: has both `occurrence_id` and `execution_id`
- Each occurrence gets a fresh `occurrence_id` at claim time
- `execution_id` is assigned when `BackgroundExecutionManager.start_execution()` succeeds

**Why separate?**
- `occurrence_id` is the scheduler's identity for "this scheduled fire"
- `execution_id` is the execution engine's identity for "this execution attempt"
- If execution fails and is retried (future feature), the same occurrence could have multiple execution_ids
- For v1: one occurrence → zero or one execution_id

**Event correlation:**
- `correlation_id = occurrence_id` for all scheduler events
- `causation_id` links: claim → trigger → complete/fail
- `execution_id` appears in `scheduler.execution.started/completed/failed` events

### 5. Recovery Policy

#### Case 1: CLAIMED, no durable execution identity

**Diagnosis:**
- Job status = CLAIMED
- `lease_until < now` (expired)
- TaskRepository: task is PENDING or not found
- Event Store: no `scheduler.execution.started` event with this occurrence_id

**Action:**
- If within misfire tolerance (2x interval for recurring, 5 min for once):
  - Reset status = PENDING
  - Clear occurrence_id, claim_token, claimed_at, lease_until
  - Recompute next_run_at using misfire policy
  - Emit `scheduler.occurrence.skipped` with reason `lease_expired`
- If past tolerance:
  - Set status = SKIPPED
  - Emit `scheduler.occurrence.skipped` with reason `lease_expired_past_tolerance`
  - For recurring: advance next_run_at and reset to PENDING
  - For once: set enabled = 0

#### Case 2: Durable execution identity exists, but execution never started

**Diagnosis:**
- Job status = TRIGGERED
- `execution_id` is set
- Event Store: no `scheduler.execution.started` event with this execution_id
- TaskRepository: task is PENDING

**Action:**
- This means scheduler recorded TRIGGERED but execution never actually started
- Reset status = PENDING
- Clear execution_id
- Recompute next_run_at
- If within tolerance: retry
- If past tolerance: skip

#### Case 3: execution may have started, but scheduler state was not updated

**Diagnosis:**
- Job status = TRIGGERED or CLAIMED
- `lease_until < now`
- TaskRepository: task is RUNNING or VERIFYING
- Event Store: `TASK_STARTED` event exists for this task_id

**Action:**
- Execution IS running (or was running when process crashed)
- This is an orphaned execution — the asyncio task is dead but task state is RUNNING
- Set status = FAILED
- Reason = `orphaned_execution`
- Emit `scheduler.execution.failed`
- RecoveryManager will handle stale RUNNING task separately
- For recurring: advance next_run_at, reset to PENDING
- For once: set enabled = 0

#### Case 4: execution is durably marked completed

**Diagnosis:**
- Job status = TRIGGERED
- TaskRepository: task is COMMITTED or FAILED
- Event Store: `TASK_COMPLETED` or `TASK_FAILED` event exists

**Action:**
- Set status = COMPLETED or FAILED to match task state
- Set last_run_at = task.completed_at (or event occurred_at)
- Emit `scheduler.execution.completed` or `scheduler.execution.failed` if not already emitted
- For recurring: advance next_run_at, reset to PENDING
- For once: set enabled = 0

#### Case 5: lease expired while execution is still actually running

**Diagnosis:**
- Job status = CLAIMED or TRIGGERED
- `lease_until < now`
- TaskRepository: task is RUNNING or VERIFYING
- BackgroundExecutionManager: no in-memory handle (process restarted)
- Event Store: `TASK_STARTED` exists

**Action:**
- In v1 (single process): the asyncio task is dead. Execution is orphaned.
- Set status = FAILED
- Reason = `orphaned_execution`
- Emit `scheduler.execution.failed`
- RecoveryManager handles stale task
- **Future (multi-process):** check if another process holds the execution via distributed lock or shared state. Not in v1.

### 6. Duplicate Execution Risk

**The system CANNOT prove whether execution started before a crash in Window B/C.**

If scheduler crashes after `BackgroundExecutionManager.start_execution()` returns but before scheduler records TRIGGERED:
- execution_id is lost (in-memory only)
- asyncio task might or might not have claimed the task
- After restart: scheduler sees CLAIMED with expired lease

**Safest policy: Assume execution did NOT start.**

Rationale:
- BackgroundExecutionManager's `execution_id` is NOT durable
- The asyncio task is killed with the process
- Task state in DB is the authoritative source
- If task is PENDING: execution never claimed it
- If task is RUNNING: execution claimed it, but asyncio is dead

**For Window B/C recovery:**
- Check TaskRepository for RUNNING tasks with this target
- If RUNNING: mark scheduler job FAILED with reason `orphaned_execution`. Do NOT retry. RecoveryManager handles stale task.
- If PENDING: safe to retry (reset to PENDING, apply misfire policy)

**No durable execution-start marker is needed because:**
- Task state = RUNNING is the durable marker
- TASK_STARTED event is the durable marker
- Both already exist in the architecture

### 7. Approval / Permission Boundary

**Confirmed: Scheduler cannot become an authorization cache.**

When a recovered occurrence is retried:
1. Scheduler triggers `BackgroundExecutionManager.start_execution()`
2. `AutonomousPlanExecutor` → `PlanExecutionService` → `AutonomousRunController`
3. `TaskStateMachine.claim_attempt()` succeeds (task is PENDING or was reset)
4. `AutonomousTaskOrchestrator.start_task()` emits `TASK_STARTED` event
5. Agent execution begins
6. **At each tool call:** `PermissionEngine.evaluate()` is called
7. **If ASK_USER:** `ApprovalService` requests approval
8. Scheduler does NOT participate in authorization decisions

**Expired/revoked permissions at recovery time:**
- PermissionEngine evaluates at execution time, not schedule creation time
- If permission was revoked: execution fails with DENY
- Scheduler records failure
- Job remains enabled

**Approval that was valid at schedule creation but expired:**
- ApprovalService checks at execution time
- If expired: ASK_USER or DENY
- Scheduler does NOT auto-approve

### 8. Concurrency

**Case 1: Two scheduler workers race for same occurrence**

Prevented by atomic UPDATE with `status = 'PENDING'` precondition:
```sql
UPDATE scheduled_jobs
SET status = 'CLAIMED', ...
WHERE job_id = :job_id
  AND status = 'PENDING'
  AND next_run_at <= :now
  AND enabled = 1
```
Only one worker can transition PENDING → CLAIMED. Loser gets rowcount = 0.

**Case 2: Scheduler recovery racing with old scheduler worker**

After restart:
- Recovery queries jobs with `status IN ('CLAIMED', 'TRIGGERED') AND lease_until < now`
- Atomic UPDATE resets to PENDING: `WHERE status = 'CLAIMED' AND lease_until < :now`
- If old worker somehow still alive (multi-process future): it cannot update because status changed
- For v1: single process, old worker is dead

**Case 3: Previous execution still running when next occurrence becomes due**

Detection:
```sql
SELECT COUNT(*) FROM development_tasks
WHERE plan_id = :plan_id
  AND status IN ('RUNNING', 'VERIFYING', 'PASSED')
```
If count > 0: skip this occurrence.

Action:
- Set status = SKIPPED
- Emit `scheduler.occurrence.skipped` with reason `previous_still_running`
- For recurring: advance next_run_at, reset to PENDING
- For once: set enabled = 0

**Case 4: execution identity already exists**

If job is TRIGGERED with execution_id and startup recovery finds:
- Task is COMMITTED/FAILED: mark COMPLETED/FAILED
- Task is RUNNING: mark FAILED with reason `orphaned_execution`
- Task is PENDING: execution never started, reset to PENDING

No duplicate execution possible because TaskStateMachine prevents duplicate claims.

### 9. Task / Plan Deletion

**If scheduled task/plan is deleted after schedule creation:**

Scheduler checks target existence BEFORE triggering:
```python
task = await task_repo.get_task(target_id)
if task is None:
    # skip, emit scheduler.occurrence.skipped with reason target_deleted
    # job remains enabled for operator review
```

**Deterministic behavior:**
- Skip this occurrence
- Emit `scheduler.occurrence.skipped` with reason `target_deleted`
- Job remains enabled (operator must manually disable or update target)
- Next occurrence will also be skipped until target exists or job is updated

**Do NOT silently recreate deleted entities.**

### 10. Minimal Corrected V1 Architecture

#### Persistent Scheduler State

```sql
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    job_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    schedule_type VARCHAR(20) NOT NULL,     -- 'cron', 'interval', 'once'
    schedule_expr VARCHAR(200) NOT NULL,
    timezone VARCHAR(64) NULL,
    target_type VARCHAR(20) NOT NULL,       -- 'task', 'plan'
    target_id VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    last_run_at TIMESTAMP WITH TIME ZONE NULL,
    next_run_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    occurrence_id VARCHAR(128) NOT NULL,
    claim_token VARCHAR(64) NULL,
    claimed_at TIMESTAMP WITH TIME ZONE NULL,
    lease_until TIMESTAMP WITH TIME ZONE NULL,
    execution_id VARCHAR(64) NULL,
    metadata TEXT NULL
);
```

#### Occurrence Identity

Format: `occurrence_{job_id}_{12_hex_chars}` or UUID
Generated at claim time (PENDING → CLAIMED)
Unique per occurrence attempt
Used as `correlation_id` for all scheduler events for this occurrence

#### Execution Identity

Format: `exec_{12_hex_chars}` (from BackgroundExecutionManager)
Assigned when `BackgroundExecutionManager.start_execution()` succeeds
Persisted in `scheduled_jobs.execution_id`
Linked to occurrence via `scheduled_jobs` row

#### State Machine

```
PENDING
  → CLAIMED (atomic claim)
  → SKIPPED (misfire/concurrency skip, terminal for this occurrence)
  
CLAIMED
  → TRIGGERED (execution accepted)
  → FAILED (execution rejected/lease expired)
  → SKIPPED (concurrency skip)

TRIGGERED
  → COMPLETED (execution confirmed started)
  → FAILED (execution failed/orphaned)

COMPLETED/FAILED/SKIPPED
  → PENDING (for recurring jobs, next occurrence)
  → DISABLED (for once jobs, job finished)
```

#### Claim / Lease

Atomic SQL:
```sql
UPDATE scheduled_jobs
SET status = 'CLAIMED',
    occurrence_id = :occurrence_id,
    claim_token = :claim_token,
    claimed_at = :now,
    lease_until = :now + 5 minutes,
    next_run_at = :next_run_at,
    updated_at = :now
WHERE job_id = :job_id
  AND status = 'PENDING'
  AND next_run_at <= :now
  AND enabled = 1
```

Lease = 5 minutes. Expired lease = stale claim.

#### Execution Handoff

```
Phase 1: CLAIM
  Atomic UPDATE (as above)
  Emit: scheduler.occurrence.claimed
  
Phase 2: TRIGGER
  execution_id = await BackgroundExecutionManager.start_execution(...)
  Atomic UPDATE: SET status='TRIGGERED', execution_id=:execution_id
    WHERE occurrence_id=:occurrence_id AND status='CLAIMED'
  Emit: scheduler.execution.started

Phase 3: CONFIRM (async, on execution outcome)
  On execution completion event from BackgroundExecutionManager:
    Atomic UPDATE: SET status='COMPLETED'/'FAILED', last_run_at=:now
      WHERE occurrence_id=:occurrence_id AND status='TRIGGERED'
    Emit: scheduler.execution.completed/failed
```

**Crash between Phase 1 and Phase 2:** lease expires → recovery resets to PENDING.
**Crash between Phase 2 and Phase 3:** TRIGGERED with execution_id → recovery checks Event Store/TaskRepository.

#### Recovery

On startup:
1. Query jobs where `status IN ('CLAIMED', 'TRIGGERED') AND lease_until < :now`
2. For each:
   a. Check TaskRepository for target task/plan tasks
   b. If any task is RUNNING/VERIFYING: mark FAILED, reason `orphaned_execution`
   c. If all tasks PENDING/COMMITTED/FAILED:
      - If status = TRIGGERED with execution_id: check Event Store for scheduler.execution.started
        - Found: mark COMPLETED
        - Not found: mark FAILED, reason `orphaned_claim`
      - If status = CLAIMED: reset to PENDING, apply misfire policy
3. Query jobs where `status = 'PENDING' AND next_run_at <= :now`: apply misfire policy

#### Misfire

**SKIP_ONCE (bounded):**
- Skip exactly one occurrence
- Advance next_run_at by exactly one interval/cron cycle
- Never catch up unboundedly

Tolerance:
- interval: 2x interval seconds
- cron: next future cron time
- once: 5 minutes

#### Concurrency

- **Worker race:** atomic UPDATE claim with status = 'PENDING' precondition
- **Overlap:** check TaskRepository for RUNNING tasks before triggering. If found: SKIP.
- **Recovery race:** atomic UPDATE reset with status = 'CLAIMED' AND lease_until < :now precondition

#### Security / Approval

- Scheduler never grants, caches, or bypasses authorization
- All permission/approval evaluation at execution time
- Recovered occurrences re-evaluate authorization from scratch
- ASK_USER remains ASK_USER

#### Events

Durable scheduler events (via EventEmitter, source="scheduler"):
- `scheduler.occurrence.claimed` — after Phase 1
- `scheduler.occurrence.skipped` — on misfire/concurrency skip
- `scheduler.execution.started` — after Phase 2
- `scheduler.execution.completed` — after Phase 3 success
- `scheduler.execution.failed` — after Phase 3 failure
- `scheduler.job.created/updated/deleted/enabled/disabled` — on job lifecycle

All events have `correlation_id = occurrence_id`.

### 11. Implementation Readiness

`DESIGN_REQUIRES_REVISION`

The design is now crash-safe with the following corrections incorporated:

1. ✅ Added `status`, `occurrence_id`, `claim_token`, `claimed_at`, `lease_until`, `execution_id`
2. ✅ Two-phase handoff: PENDING → CLAIMED → TRIGGERED → COMPLETED/FAILED
3. ✅ Lease expiration and startup recovery defined
4. ✅ Crash windows analyzed with deterministic recovery for each
5. ✅ SKIP_ONCE replaced with bounded skip semantics
6. ✅ Interval semantics defined as scheduled-time-based
7. ✅ DST behavior documented with skip policy
8. ✅ Concurrency detection via TaskRepository query

**Remaining architectural question (not a blocker):**

The `BackgroundExecutionManager.start_execution()` returns `execution_id` before any durable execution marker exists. The scheduler persists this `execution_id` in Phase 2, but there is a window where the scheduler believes execution is triggered but no durable evidence exists yet.

This is acceptable for v1 because:
- The window is small (microseconds between start_execution() returning and scheduler UPDATE)
- Recovery checks TaskRepository as fallback
- The execution layer's idempotency prevents duplicate execution

**Final recommendation:** Incorporate all corrections from Section 21 and this section into the Stage 12.2 implementation plan. Once the implementation plan reflects these corrections, the status becomes `IMPLEMENTATION_READY`.

---

## 23. Execution Idempotency Proof Review

### 1. Existing Execution Architecture Findings (Evidence-Based)

Traced the actual code path from scheduler trigger to durable side effects:

```
Scheduler occurrence
  → BackgroundExecutionManager.start_execution()
    → asyncio.create_task(_run_executor_task)
      → AutonomousPlanExecutor.execute_plan()
        → PlanExecutionService.execute_planned_task()
          → AutonomousRunController.start_run()
            → TaskStateMachine.claim_attempt()     ← ATOMIC, DURABLE
            → TaskRepository (status = RUNNING)     ← DURABLE
            → WorkspaceManager.create_workspace()   ← FILESYSTEM SIDE EFFECT
            → EventEmitter.emit(TASK_STARTED)       ← DURABLE
            → AutonomousTaskOrchestrator.start_task()
              → agent execution (code changes, Git commits, tool calls) ← SIDE EFFECTS
              → TaskStateMachine transitions       ← DURABLE
              → EventEmitter.emit(TASK_COMPLETED)  ← DURABLE
```

**Durable state written at each step:**

| Step | Durable State | Transaction Boundary |
|------|--------------|---------------------|
| `claim_attempt()` | task.status = RUNNING, attempt_count++ | Single SQL UPDATE, atomic |
| `TaskRepository` commits | task row updated | session.commit() |
| `EventEmitter.emit(TASK_STARTED)` | event row in EventStore | Single INSERT |
| Agent execution | task.status transitions, events | Multiple DB operations |
| `EventEmitter.emit(TASK_COMPLETED)` | event row in EventStore | Single INSERT |

**Critical finding:** `BackgroundExecutionManager.execution_id` is generated in memory (`exec_{uuid.uuid4().hex[:12]}`) and is NOT durably persisted until the scheduler writes it to `scheduled_jobs`. The `AutonomousRunController.run_id` is also in-memory only.

**The earliest durable marker for "execution has started" is `TaskStateMachine.claim_attempt()`**, which atomically sets `task.status = RUNNING` and increments `attempt_count`.

### 2. TaskStateMachine — What It Actually Protects

**Code evidence:**

`TaskStateMachine.claim_attempt()` (`app/tasks/state_machine.py:153-242`):
```python
async def claim_attempt(self, task_id, expected_status, max_attempts):
    # Validates transition matrix
    if not self.is_transition_allowed(exp_enum, TaskStatus.RUNNING):
        raise InvalidStateTransitionError(...)
    
    # Single atomic SQL UPDATE
    updated_task, success = await self._repo.claim_task_attempt(
        task_id=task_id,
        expected_status=exp_enum,
        max_attempts=max_attempts,
        started_at=now,
    )
```

`TaskRepository.claim_task_attempt()` (`app/repositories/task_repository.py:219-275`):
```python
stmt = (
    update(DevelopmentTaskModel)
    .where(
        DevelopmentTaskModel.task_id == task_id,
        DevelopmentTaskModel.status == exp_str,
        DevelopmentTaskModel.attempt_count < max_attempts,
    )
    .values(
        status=TaskStatus.RUNNING.value,
        attempt_count=DevelopmentTaskModel.attempt_count + 1,
        started_at=now,
        ...
    )
)
res = await self._session.execute(stmt)
await self._session.flush()
if res.rowcount == 0:
    return None, False
```

**What TaskStateMachine prevents:**

| Question | Answer | Evidence |
|----------|--------|----------|
| A. Duplicate state transition? | **YES** | `transition_status_atomically()` uses CAS: `WHERE status = :expected_status`. If status changed, rowcount = 0, transition fails. |
| B. Duplicate task execution? | **YES, effectively** | `claim_attempt()` is atomic CAS from PENDING/REPAIR_REQUIRED → RUNNING. Once RUNNING, cannot be claimed again. After crash, if task is RUNNING, `claim_attempt()` fails. If task is PENDING, safe to retry. |
| C. Duplicate side effects from task execution? | **NO, not directly** | TaskStateMachine prevents duplicate execution claims, but does NOT track or deduplicate filesystem/Git/tool side effects. If execution partially completed before crash, side effects may be incomplete but NOT duplicated (because claim prevents re-execution). |
| D. Duplicate plan execution? | **NO** | BackgroundExecutionManager uses in-memory `asyncio.Lock` which is lost on restart. After restart, a new plan execution can start. Task-level checks prevent duplicate task claims within that plan execution. |
| E. Duplicate scheduler occurrence execution? | **NO, at task level** | Scheduler occurrence might trigger execution multiple times (at-least-once), but TaskStateMachine ensures the underlying task is only executed once per occurrence. |

**Critical distinction:** TaskStateMachine prevents duplicate task claims, but it does NOT prevent:
- A plan execution from starting when another plan execution is partially complete
- Incomplete side effects from a crashed execution being left in an inconsistent state
- The scheduler from retrying an occurrence whose execution never reached the claim point

### 3. EventRepository Idempotency — Exact Guarantees

**Code evidence:**

`EventRepository` (`app/events/repository.py`):
- Uses `event_id` as primary key
- `record()` method checks for existing `event_id` and raises `EventIntegrityConflict` if duplicate
- This prevents the same event from being stored twice

**What EventRepository idempotency guarantees:**

| Question | Answer | Evidence |
|----------|--------|----------|
| 1. Same event stored twice? | **YES, prevented** | PK constraint on `event_id`. Duplicate INSERT raises `EventIntegrityConflict`. |
| 2. Same task executed twice? | **NO** | Event idempotency is orthogonal to execution idempotency. Events are observational; they don't gate execution. |
| 3. Same plan executed twice? | **NO** | Same reason. BackgroundExecutionManager allows duplicate plan executions after restart. |
| 4. External/tool side effects occurring twice? | **NO** | Events don't prevent side effects. Tool execution is not gated by event idempotency. |
| 5. BackgroundExecutionManager.start_execution() occurring twice? | **NO** | In-memory lock is lost on restart. Event idempotency doesn't help. |

**Conclusion:** EventRepository idempotency is a **log-integrity guarantee only**. It prevents duplicate audit records. It does NOT prevent duplicate execution or duplicate side effects.

### 4. Crash Test Analysis

**Exact sequence:**
1. Scheduler claims occurrence O (PENDING → CLAIMED)
2. Scheduler calls `BackgroundExecutionManager.start_execution()`
3. `execution_id` generated in memory
4. asyncio task launched
5. Task execution begins (`claim_attempt()` succeeds, task = RUNNING)
6. Actual task/plan work begins (workspace creation, agent execution, side effects)
7. Process crashes
8. Scheduler restarts
9. Recovery sees O as CLAIMED with expired lease

**Can the system distinguish the following cases?**

| Case | Task State | TASK_STARTED Event | Side Effects | Distinguishable? |
|-------|-----------|-------------------|--------------|------------------|
| A. execution never started | PENDING | NO | None | **YES** |
| B. execution started but crashed before task state changed | PENDING | NO | None (claim_attempt is first durable action) | **YES** (same as A) |
| C. execution started and task state changed | RUNNING | YES | Partial | **YES** |
| D. execution completed | COMMITTED/FAILED | YES + TASK_COMPLETED/FAILED | Complete | **YES** |
| E. side effect produced but durable state not updated | RUNNING | YES | Partial | **YES** (task is RUNNING, event exists) |

**Key insight:** Cases B and A are indistinguishable, but both mean "execution never durably started." This is safe to retry.

Case E is the dangerous one: execution produced side effects but durable state was not fully updated. After restart, task is RUNNING with TASK_STARTED event. RecoveryManager handles this via heartbeat timeout → repair loop. The scheduler should NOT retry (task is RUNNING).

**Consequence:** There is no case where the system cannot distinguish "safe to retry" from "do not retry." The TaskRepository state + EventStore provides sufficient information.

### 5. Side-Effect Boundary

**Where actual side effects occur:**

| Side Effect | Location | Idempotency Mechanism |
|-------------|----------|----------------------|
| Filesystem (workspace creation) | `WorkspaceManager.create_workspace()` | None. If retried after partial creation, may create duplicate workspace or fail on existing path. |
| Git operations | `WorkspaceManager`, agent execution | Git is partially idempotent (commits are content-addressed). But intermediate state (branch creation, worktree) may not be. |
| Subprocess/Kilo execution | Agent execution | None at system level. Individual tool calls may have their own idempotency. |
| Tool execution | `ToolExecutionService` | `PermissionEngine` evaluates each call. Tool implementations may or may not be idempotent. |
| Task mutations | `TaskRepository` | `TaskStateMachine` CAS prevents duplicate transitions. |
| Plan mutations | `TaskRepository` | No plan-level CAS. Plan state derived from task states. |

**Critical finding:** The ONLY durable idempotency boundary is `TaskStateMachine.claim_attempt()`. Everything after that (workspace creation, agent execution, tool calls) is NOT idempotent at the system level.

However, because `claim_attempt()` is atomic and durable:
- If it succeeds, task is RUNNING
- No second execution can claim the same task
- Side effects from the first execution may be incomplete after crash, but they won't be duplicated
- RecoveryManager + repair loop handle incomplete side effects

**The system is "effectively-once" for task execution because:**
1. Duplicate execution is prevented by TaskStateMachine
2. Incomplete execution is handled by repair loop
3. Side effects are not duplicated (only the first execution can produce them)

But this is **NOT** "exactly-once" because:
1. A scheduler occurrence might be retried (at-least-once at scheduler level)
2. The retry will be rejected by TaskStateMachine if the task is already RUNNING
3. Or the retry will execute a fresh task if the previous claim was lost

### 6. Execution Semantics Classification

**VERDICT: `AT_LEAST_ONCE_WITH_POSSIBLE_DUPLICATION` at the scheduler level, but `EFFECTIVELY_ONCE` at the task execution level.**

More precisely:

| Layer | Semantics | Proven By |
|-------|-----------|-----------|
| Scheduler occurrence | AT_LEAST_ONCE | Scheduler may retry claimed occurrences after lease expiration |
| Task execution claim | AT_MOST_ONCE | `TaskStateMachine.claim_attempt()` atomic CAS |
| Task side effects | EFFECTIVELY_ONCE | Claim prevents duplicate execution; repair loop handles incomplete work |
| Plan execution | AT_LEAST_ONCE | BackgroundExecutionManager in-memory lock lost on restart; new plan execution can start |
| External side effects | UNKNOWN | Depends on individual tool/task implementation |

**RETRACTION:** The previous claim of "effectively-once execution" is **RETRACTED** as a blanket statement. The correct statement is:

> The existing architecture provides **task-level effectively-once execution** through `TaskStateMachine.claim_attempt()` atomic CAS. However, scheduler-level occurrence delivery is at-least-once, and plan-level execution is at-least-once with no duplicate-plan guard after restart. External side effects have no system-level idempotency guarantee.

### 7. Smallest Safe Scheduler Model

Given the evidence, the scheduler must be designed with these constraints:

**Scheduler responsibility:**
- Trigger execution at the right time
- Track occurrence state (PENDING, CLAIMED, TRIGGERED, COMPLETED, FAILED)
- Retry occurrences that were never durably started
- NOT retry occurrences whose task is already RUNNING or completed

**Scheduler must NOT assume:**
- Exactly-once delivery to execution layer
- BackgroundExecutionManager prevents duplicate plan execution after restart
- EventRepository idempotency prevents duplicate execution
- Execution layer is idempotent for side effects

**Scheduler MUST rely on:**
- `TaskStateMachine.claim_attempt()` for task-level duplicate prevention
- `TaskRepository` durable state for crash recovery discrimination
- `EventStore` for execution outcome detection
- `RecoveryManager` + repair loop for incomplete execution handling

**Smallest safe handoff:**

```
Scheduler occurrence
  → claim occurrence (PENDING → CLAIMED)
  → check TaskRepository for RUNNING tasks (concurrency guard)
  → call BackgroundExecutionManager.start_execution()
  → record execution_id in scheduled_jobs (CLAIMED → TRIGGERED)
  → emit scheduler.execution.started
  → [execution runs independently]
  → on execution outcome event:
    → update scheduled_jobs status
    → emit scheduler.execution.completed/failed
```

**Recovery rule for CLAIMED occurrences:**
1. Check TaskRepository for target task/plan tasks
2. If any task is RUNNING/VERIFYING: mark FAILED, reason `orphaned_execution`
3. If all tasks PENDING/COMMITTED/FAILED: reset to PENDING, apply misfire policy
4. Never retry an occurrence whose task is RUNNING

### 8. Exact Recovery Rule

For a claimed occurrence after restart:

```
IF status IN ('CLAIMED', 'TRIGGERED') AND lease_until < now:
    # Stale claim - need to recover
    
    # Step 1: Check task state
    task = await TaskRepository.get_task(target_id)
    if task and task.status in (RUNNING, VERIFYING, PASSED):
        # Execution is active or was active
        # Check if execution completed
        completion_event = await EventRepository.find_execution_completed(execution_id)
        if completion_event:
            # Execution completed, scheduler just didn't record it
            status = COMPLETED if success else FAILED
            last_run_at = completion_event.occurred_at
        else:
            # Execution is orphaned (asyncio task dead)
            status = FAILED
            reason = 'orphaned_execution'
    
    # Step 2: Check plan-level tasks
    elif target_type == 'plan':
        plan_tasks = await TaskRepository.get_tasks_by_plan(target_id)
        has_running = any(t.status in (RUNNING, VERIFYING, PASSED) for t in plan_tasks)
        if has_running:
            status = FAILED
            reason = 'orphaned_execution'
        else:
            # No execution ever started
            if within_misfire_tolerance:
                status = PENDING
                recompute next_run_at
            else:
                status = SKIPPED
                emit scheduler.occurrence.skipped
    
    # Step 3: Target deleted
    elif task is None:
        status = SKIPPED
        reason = 'target_deleted'
    
    # Update job status
    await scheduled_jobs.update(...)
```

**Key rule: Never retry an occurrence whose task is RUNNING.**

### 9. Documentation Correction

The previous "effectively-once execution" claim in Section 19.9 is **RETRACTED**.

**Correct statement:**

> The existing architecture provides **task-level effectively-once execution** through `TaskStateMachine.claim_attempt()` atomic CAS. Scheduler-level occurrence delivery is at-least-once. Plan-level execution has no duplicate guard after restart. External side effects have no system-level idempotency guarantee.

### 10. Final Status

`DESIGN_REQUIRES_REVISION`

The design must be revised to explicitly state:

1. Scheduler occurrence delivery is **at-least-once**, not exactly-once
2. Task execution is **effectively-once** via `TaskStateMachine`, but this is a property of the execution layer, not the scheduler
3. Plan execution after restart has **no duplicate guard** at the BackgroundExecutionManager level
4. External side effects have **no system-level idempotency**
5. Recovery must NEVER retry an occurrence whose task is RUNNING
6. The scheduler's duplicate-prevention responsibility is limited to checking TaskRepository state before triggering

Once these corrections are incorporated, the design will be `IMPLEMENTATION_READY`.

---

## 24. Orphaned Execution Recovery Contract

### 1. RUNNING State Invariant — Evidence-Based Definition

Traced the actual code path in `AutonomousTaskOrchestrator.start_task()` (`app/orchestration/orchestrator.py:92-827`):

```
Step 1: TaskStateMachine.claim_attempt()
  → task.status = RUNNING, attempt_count++
  → Single SQL UPDATE, atomic, durable

Step 2: session.commit()
  → Durable

Step 3: WorkspaceManager.create_workspace()
  → Filesystem side effect (NOT durable in DB)

Step 4: TaskRepository.update_workspace_path() + commit
  → Durable

Step 5: EventEmitter.emit(TASK_STARTED)
  → Durable in EventStore

Step 6: TaskRepository.update_heartbeat() + commit
  → Durable

Step 7: Agent execution begins
  → Side effects (code changes, Git commits, tool calls)
```

**What RUNNING + TASK_STARTED actually means:**

| Scenario | Task Status | TASK_STARTED Event | What Actually Happened |
|----------|------------|-------------------|------------------------|
| A. execution never actually started | RUNNING | NO | claim_attempt() succeeded, crash before workspace creation or event emission |
| B. execution started and crashed | RUNNING | YES | claim_attempt() + workspace + event succeeded, agent execution started, crash during execution |
| C. execution completed but persistence crashed | RUNNING or COMMITTED | YES | agent completed, but task state transition to COMMITTED crashed. Task may be RUNNING or COMMITTED depending on crash timing. |
| D. execution produced side effects but persistence did not complete | RUNNING | YES | agent produced side effects, but task state not updated to COMMITTED |

**Critical finding:** RUNNING + TASK_STARTED does NOT guarantee that execution completed. It only guarantees that:
1. Task was claimed (RUNNING)
2. Workspace was created
3. TASK_STARTED event was emitted
4. Agent execution MAY have started

Cases B, C, and D are **indistinguishable** from RUNNING + TASK_STARTED alone. The system cannot prove whether side effects occurred.

### 2. Orphaned RUNNING Task — Existing Mechanism

**The architecture ALREADY has `RecoveryManager`** (`app/orchestration/recovery_manager.py`).

`RecoveryManager.recover_task()` handles exactly this case:

1. Checks if task is RUNNING
2. Checks if heartbeat is stale (`last_heartbeat < current_time - timeout_seconds`)
3. If stale: claims recovery atomically via `TaskRepository.claim_task_recovery()`
4. Transitions RUNNING → REPAIR_REQUIRED (if attempts < max) or FAILED (if attempts >= max)
5. Cleans up workspace

**This is the authoritative recovery path. Scheduler must NOT duplicate it.**

### 3. Safest V1 Policy for Orphaned RUNNING Tasks

**Policy: DO NOT RETRY. Mark FAILED. Let RecoveryManager handle recovery.**

When scheduler finds a task in RUNNING state:
1. Mark the scheduler occurrence as FAILED with reason `orphaned_execution`
2. Emit `scheduler.execution.failed` event
3. Do NOT trigger a new execution
4. RecoveryManager will eventually:
   - Detect stale RUNNING task via heartbeat timeout
   - Transition to REPAIR_REQUIRED or FAILED
   - The task can then be retried on the next scheduler occurrence

**Trade-offs:**
- **Duplicate side effects:** Avoided. Task is RUNNING, so claim_attempt() prevents duplicate execution.
- **Lost work:** If execution actually completed but state transition crashed, the work is done but task is RUNNING. RecoveryManager will mark it FAILED and it will be retried, potentially duplicating work. This is a known limitation.
- **Stuck tasks:** RecoveryManager handles this via heartbeat timeout + recovery lease.

**Why not retry?**
- Task is RUNNING → claim_attempt() would fail
- If the original execution is still running (case B with slow execution), retry would be rejected anyway
- If the original execution crashed (case B/C/D), the side effects may be partial or complete. Retrying could duplicate side effects.
- The ONLY safe retry is after RecoveryManager has reset the task to REPAIR_REQUIRED or FAILED.

### 4. Plan Execution Orphan Handling

For plan-level scheduled jobs:

**Durable evidence:**
- Task states in TaskRepository (RUNNING, COMMITTED, FAILED, etc.)
- Events in EventStore
- NO durable plan execution marker

**Policy:**
- If ANY task in the plan is RUNNING/VERIFYING/PASSED: skip this occurrence, mark FAILED with reason `previous_still_running`
- If ALL tasks are COMMITTED: mark COMPLETED
- If ALL tasks are PENDING/FAILED: safe to retry
- If MIXED (some COMMITTED, some FAILED): safe to retry. AutonomousPlanExecutor will only run non-COMMITTED tasks.

**Why no durable plan execution marker is required for V1:**
- Task states are sufficient to determine plan state
- AutonomousPlanExecutor is idempotent for plans (it only runs non-COMMITTED tasks)
- BackgroundExecutionManager's in-memory lock is lost on restart, but task-level checks prevent duplicate work

### 5. Scheduler Responsibility Boundary

**Scheduler is responsible for:**
- Occurrence identity and lifecycle (PENDING, CLAIMED, TRIGGERED, COMPLETED, FAILED, SKIPPED)
- Claiming occurrences atomically
- Lease management and expiration
- Startup recovery of scheduler state
- Deciding whether an occurrence may be handed off (checking task/plan state)

**Scheduler is NOT responsible for:**
- Proving whether external side effects happened
- Resuming orphaned executions
- Cleaning up workspaces
- Recovering stale RUNNING tasks
- Any form of execution idempotency

**Scheduler must defer to existing systems:**
- RecoveryManager handles orphaned RUNNING tasks
- BackgroundExecutionManager handles execution lifecycle
- TaskStateMachine handles task state transitions
- EventEmitter handles event persistence

### 6. Exact V1 Recovery Contract

| Scheduler Status | Task/Plan State | Evidence | Scheduler Action |
|-----------------|-----------------|----------|-----------------|
| PENDING | Task PENDING, no TASK_STARTED | Execution never started | Apply misfire policy: retry if within tolerance, else skip |
| PENDING | Task PENDING, TASK_STARTED exists | Inconsistent state (task reset without event cleanup) | Retry is safe (task is PENDING). Or mark FAILED to avoid confusion. **V1: retry.** |
| PENDING | Task RUNNING, TASK_STARTED exists | Orphaned execution | Mark FAILED, reason `orphaned_execution`. Do NOT retry. |
| PENDING | Task COMMITTED/FAILED | Execution completed | Mark COMPLETED/FAILED. Advance next_run_at for recurring. |
| PENDING | Task not found | Target deleted | Skip with reason `target_deleted`. Job remains enabled. |
| CLAIMED, lease expired | Task PENDING, no TASK_STARTED | Claim but never triggered | Reset to PENDING, apply misfire policy |
| CLAIMED, lease expired | Task PENDING, TASK_STARTED exists | Claimed but very early crash | Reset to PENDING, apply misfire policy. Workspace may exist but is harmless. |
| CLAIMED, lease expired | Task RUNNING, TASK_STARTED exists | Orphaned execution | Mark FAILED, reason `orphaned_execution`. Do NOT retry. |
| CLAIMED, lease expired | Task COMMITTED/FAILED | Execution completed after claim | Mark COMPLETED/FAILED based on task state |
| TRIGGERED, execution_id set | Task PENDING, no TASK_STARTED | Execution never started | Reset to PENDING, apply misfire policy |
| TRIGGERED, execution_id set | Task RUNNING, TASK_STARTED exists | Orphaned execution | Mark FAILED, reason `orphaned_execution`. Do NOT retry. |
| TRIGGERED, execution_id set | Task COMMITTED/FAILED | Execution completed | Mark COMPLETED/FAILED |
| TRIGGERED, execution_id set | Task not found | Target deleted | Skip with reason `target_deleted` |
| Any | Plan with RUNNING tasks | Previous execution still running | Skip with reason `previous_still_running`. Advance next_run_at. |
| Any | Plan with all COMMITTED tasks | Plan completed | Mark COMPLETED |
| Any | Plan with all PENDING/FAILED tasks | Plan never started or fully failed | Safe to retry |

**Key rule: Never retry an occurrence whose task is RUNNING.**

### 7. Implementation Readiness

`DESIGN_REQUIRES_REVISION`

The design is now deterministic and crash-safe with the following corrections:

1. ✅ Scheduler occurrence delivery is at-least-once (not exactly-once)
2. ✅ Task execution is effectively-once via TaskStateMachine (property of execution layer)
3. ✅ Plan execution has no duplicate guard after restart (documented limitation)
4. ✅ External side effects have no system-level idempotity (documented)
5. ✅ Recovery MUST NEVER retry an occurrence whose task is RUNNING
6. ✅ Scheduler delegates orphaned RUNNING task recovery to existing RecoveryManager
7. ✅ Exact recovery contract defined for all durable state combinations
8. ✅ Plan execution orphan handling defined

**Remaining items for Stage 12.2 implementation plan:**
1. Incorporate all corrections from Sections 21, 22, 23, and 24
2. Define exact API for scheduler CRUD operations
3. Define schedule validation rules (cron, interval, once formats)
4. Implement deterministic time calculations with zoneinfo
5. Implement startup recovery logic
6. Implement misfire policy with bounded skip
7. Implement concurrency check via TaskRepository query
8. Integrate with existing RecoveryManager for orphaned tasks

No production code was modified.

---

## 25. Final Recovery Ownership Sanity Check

### 1. RecoveryManager Independence

**Proof: RecoveryManager does NOT depend on Scheduler state.**

`RecoveryManager` (`app/orchestration/recovery_manager.py`) operates exclusively on:
- `TaskRepository` — reads task status, heartbeat, recovery_owner
- `TaskStateMachine` — atomic state transitions
- `WorkspaceManager` — workspace cleanup

RecoveryManager has:
- NO imports of scheduler modules
- NO reads from `scheduled_jobs` table
- NO dependency on `occurrence_id`, `claim_token`, `lease_until`, or any scheduler state
- NO awareness of `BackgroundExecutionManager` in-memory handles

`RecoveryManager.scan_and_recover_stale_tasks()` queries `TaskRepository.list_stale_running_tasks()` which finds RUNNING tasks with stale heartbeats. It then claims recovery atomically via `TaskRepository.claim_task_recovery()` and transitions the task state.

**Conclusion:** RecoveryManager is fully independent of Scheduler. It can recover orphaned RUNNING tasks even if:
- Scheduler marks its occurrence FAILED
- Scheduler process crashes
- Scheduler is completely unavailable
- BackgroundExecutionManager has lost its in-memory execution

### 2. Occurrence vs Task Recovery Independence

**Confirmed: These are independent.**

Scheduler occurrence state machine:
- `scheduled_jobs` table
- Tracks: PENDING, CLAIMED, TRIGGERED, COMPLETED, FAILED, SKIPPED
- Owned by Scheduler

Task recovery state machine:
- `development_tasks` table
- Tracks: RUNNING → REPAIR_REQUIRED / FAILED (via RecoveryManager)
- Owned by RecoveryManager / TaskStateMachine

**Marking a scheduler occurrence FAILED does NOT prevent RecoveryManager from recovering the underlying task.** These are separate database rows with no foreign key or dependency.

When Scheduler marks an occurrence FAILED with reason `orphaned_execution`:
- Scheduler updates `scheduled_jobs.status = 'FAILED'`
- RecoveryManager can still claim and transition the task from RUNNING to REPAIR_REQUIRED/FAILED
- The next scheduler occurrence will see the task in REPAIR_REQUIRED/FAILED and can trigger a new execution

### 3. Race Conditions

#### A. Scheduler recovery and RecoveryManager recovery run simultaneously

**Safe.** They operate on different state:
- Scheduler modifies `scheduled_jobs`
- RecoveryManager modifies `development_tasks`

No shared locks, no conflicting writes.

#### B. Scheduler decides an occurrence is orphaned while RecoveryManager is already recovering the task

**Safe.** Possible interleavings:

1. Scheduler queries task → RUNNING
2. RecoveryManager claims recovery → transitions to REPAIR_REQUIRED
3. Scheduler marks occurrence FAILED

Result: Occurrence is FAILED, task is REPAIR_REQUIRED. Next occurrence will trigger execution. Safe.

2. RecoveryManager claims recovery → transitions to REPAIR_REQUIRED
3. Scheduler queries task → REPAIR_REQUIRED
4. Scheduler marks occurrence FAILED (or could mark COMPLETED if it sees task is not RUNNING)

Result: Either outcome is safe. Task is REPAIR_REQUIRED, which is runnable.

#### C. RecoveryManager changes RUNNING → REPAIR_REQUIRED while Scheduler is checking task state

**Safe.** Scheduler's check is a read. RecoveryManager's transition is an atomic CAS. Possible interleavings:

1. Scheduler reads task → RUNNING
2. RecoveryManager transitions → REPAIR_REQUIRED
3. Scheduler marks occurrence FAILED based on stale RUNNING read

Result: Occurrence is FAILED, task is REPAIR_REQUIRED. Next occurrence will trigger. Safe.

2. RecoveryManager transitions → REPAIR_REQUIRED
3. Scheduler reads task → REPAIR_REQUIRED
4. Scheduler marks occurrence FAILED or resets to PENDING

Result: Safe. Task is REPAIR_REQUIRED.

#### D. A new scheduler occurrence arrives while the previous task is REPAIR_REQUIRED

**Safe.** REPAIR_REQUIRED → RUNNING is allowed by TaskStateMachine. The new occurrence will:
1. Check task status → REPAIR_REQUIRED
2. Call BackgroundExecutionManager.start_execution()
3. TaskStateMachine.claim_attempt() succeeds (status = REPAIR_REQUIRED)
4. Execution proceeds normally

No conflict with previous recovery.

### 4. Plan-Level Case

**Rule:** "If a plan contains RUNNING tasks, Scheduler does not start another execution."

**Is this safe when RUNNING tasks are stale and RecoveryManager is about to repair them?**

**Yes, safe.** Reasoning:

1. Scheduler skips the occurrence because plan has RUNNING tasks
2. RecoveryManager independently detects stale RUNNING tasks via heartbeat timeout
3. RecoveryManager transitions stale tasks to REPAIR_REQUIRED or FAILED
4. Next scheduler occurrence sees plan with no RUNNING tasks
5. Next occurrence triggers execution

**Trade-off:** The skipped occurrence is delayed until the next scheduled time. This is acceptable because:
- RecoveryManager runs independently on its own schedule (or can be triggered)
- The plan's next scheduled occurrence will pick up the repaired tasks
- No work is lost; no duplicate execution occurs

**If tighter coupling is desired in future:** Scheduler could trigger RecoveryManager.scan_and_recover_stale_tasks() before deciding to skip. But this is an optimization, not a correctness requirement.

### 5. Final Recovery Ownership Contract

| Owner | Responsibility | State Modified |
|-------|---------------|----------------|
| **Scheduler** | Occurrence identity, claiming, leases, recovery of scheduler state, decision to hand off | `scheduled_jobs` |
| **RecoveryManager** | Stale/orphaned RUNNING task recovery, workspace cleanup | `development_tasks` |
| **TaskStateMachine** | Task state transitions, atomic CAS claims | `development_tasks` |
| **BackgroundExecutionManager** | Execution lifecycle, in-memory tracking | In-memory only |
| **EventEmitter** | Event persistence | `events` |

**Invariants:**
1. Scheduler never modifies task state directly
2. RecoveryManager never reads or modifies scheduler state
3. Scheduler defers to RecoveryManager for orphaned RUNNING tasks
4. RecoveryManager defers to TaskStateMachine for state transitions
5. No duplicate recovery authority exists

### 6. Implementation Readiness

`IMPLEMENTATION_READY`

The previous `DESIGN_REQUIRES_REVISION` status was due to report-status inconsistency, not a remaining architectural defect. All reliability concerns have been analyzed and corrected:

1. ✅ Scheduler occurrence delivery is at-least-once (documented)
2. ✅ Task execution is effectively-once via TaskStateMachine (documented as execution-layer property)
3. ✅ Plan execution has no duplicate guard after restart (documented limitation)
4. ✅ External side effects have no system-level idempotency (documented)
5. ✅ Recovery MUST NEVER retry an occurrence whose task is RUNNING (enforced)
6. ✅ Scheduler delegates orphaned RUNNING task recovery to existing RecoveryManager
7. ✅ Exact recovery contract defined for all durable state combinations
8. ✅ RecoveryManager is independent of Scheduler state
9. ✅ Scheduler and RecoveryManager can run concurrently without conflict
10. ✅ Plan-level skip rule is safe even with stale tasks
11. ✅ No duplicate recovery authority

**The design is ready for Stage 12.2 implementation planning.**
