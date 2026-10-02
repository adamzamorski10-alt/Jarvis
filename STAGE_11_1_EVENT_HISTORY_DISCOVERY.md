# STAGE_11_1_EVENT_HISTORY_DISCOVERY.md

## 1. Current Architecture

### Execution Entities and Lifecycle

| Entity | Definition Location | Persistence Table | Lifecycle / State Model | Timestamps | Relationships | Mutable? |
|--------|---------------------|-------------------|------------------------|------------|---------------|----------|
| **Task** | `app/tasks/models.py` — `DevelopmentTask` | `development_tasks` | `TaskStatus` enum: PENDING → RUNNING → VERIFYING → PASSED / REPAIR_REQUIRED → COMMITTED / FAILED / CANCELLED / ABORTED | created_at, started_at, completed_at, last_heartbeat, last_recovery_at, execution_terminated_at | parent_task_id (self), plan_id, task_key | **Mutable** |
| **Plan** | `app/manager/models.py` — `ManagerPlanResult`, `UserPlan` | `plan_fingerprints` (immutable fingerprint only) | No dedicated plan execution state machine. Plan identity = `plan_id`. Execution state is implicit in task states. | created_at | plan_id ↔ tasks (via task.plan_id) | **Fingerprint is immutable** |
| **Plan Execution** | `app/services/autonomous_loop.py` — `PlanExecutionResult`, `PlanExecutionState` | **Not persisted as a separate entity** | `PlanExecutionState`: PENDING → RUNNING → SUCCEEDED / FAILED / CANCELLED | — | Contains committed_tasks, failed_tasks, reason | **In-memory only** |
| **Task Execution / Run** | `app/orchestration/run_controller.py` — `RunStatus`, `AutonomousRunResult` | **Not persisted as a separate entity** | `RunStatus`: CREATED → STARTING → RUNNING → VERIFYING → SUCCEEDED / FAILED / CANCELLED / ABORTED / TIMED_OUT / RECOVERY_REQUIRED | started_at, completed_at | task_id, run_id | **In-memory only** |
| **Verification Result** | `app/verification/models.py` — `VerificationReport`, `CheckResult` | **Not persisted as a separate entity** | `GateDecision`: PASS / FAIL. CheckResult: PASS / FAIL / ERROR / SKIPPED | started_at, finished_at | task_id | **In-memory only** |
| **Approval** | `app/tools/approval_service.py`, `app/ai_manager/approval_models.py` | `capability_approvals`, `pending_approvals` | `ApprovalStatus`: PENDING / APPROVED / DENIED / EXPIRED / CONSUMED | created_at, expires_at | grant_id, approval_id | **Mutable** |
| **Permission Decision** | `app/tools/permission_engine.py` | `permission_grants` | N/A — grants are static-ish records | created_at | grant_id | **Mutable** |
| **Tool Execution** | `app/tools/tool_execution_service.py`, `app/tools/audit_models.py` | `audit_events` (via `AuditEventModel`) | `AuditEventType` enum. `execution_status` field. | timestamp | request_id, grant_id | **Append-only** |
| **Kilo Session** | `app/orchestration/kilo_session_models.py`, `app/orchestration/kilo_session_manager.py` | `kilo_sessions` | `KiloSessionState`: NEW → INITIALIZING → READY → RUNNING → COMPLETED / FAILED / CANCELLED / TIMED_OUT / ERROR | created_at, last_observed_at, completed_at, process_started_at, process_terminated_at | app_session_id, task_id, execution_id, project_id | **Mutable** |
| **Audit / Security Records** | `app/tools/audit_models.py` — `AuditEvent` | `audit_events` | `AuditEventType` enum. Immutable after creation. | timestamp | request_id, tool_id, grant_id | **Append-only** |
| **Errors / Failures** | Distributed — `failure_reason` in `DevelopmentTask`, `error_message` in `AuditEventModel`, `KiloSessionModel`, `OrchestrationResult` | Embedded in parent records | N/A | Various | Various | **Embedded in mutable records** |
| **Git / Workspace Checkpoints** | `app/workspace/workspace_manager.py` | **Not persisted as a separate entity** — checkpoint SHA stored in `DevelopmentTask.workspace_path` and run result | N/A | — | workspace_path | **Mutable** |

### Key Observation

The application already has two append-only event-like tables:

1. **`activity_events`** — generic, low-information activity log (level, category, message, JSON details). Categories currently: `system`, `database`, `application`, `api`, `settings`. Designed for future extensibility.
2. **`audit_events`** — structured, security-focused audit trail for tool operations, permission decisions, and approvals.

However, neither captures the full lifecycle of autonomous tasks, plans, or Kilo sessions. The execution flow is largely invisible except through mutable state changes in `development_tasks` and scattered `failure_reason` strings.

---

## 2. Existing Persistence Mechanisms

### Database Layer

- **Engine**: SQLite via `create_async_engine` with `aiosqlite` dialect (`app/db/database.py`)
- **ORM**: SQLAlchemy 2.x async (`AsyncSession`, `async_sessionmaker`)
- **Session management**: Module-level `AsyncSessionLocal()` factory, `get_db()` dependency injection
- **Migrations**: Lightweight inline migrations in `init_db()` (ALTER TABLE for schema changes)
- **Base model**: `DeclarativeBase` subclass

### Repository Layer

All repositories follow the same pattern:
- Constructor takes `AsyncSession`
- CRUD methods are `async`
- Methods flush/refresh as needed
- No transaction management at repository level (caller owns transaction)

Repositories:
- `TaskRepository` — `development_tasks`, `task_dependencies`, `plan_fingerprints`
- `AuditLogRepository` — `audit_events` (append-only)
- `ActivityRepository` — `activity_events` (append-only)
- `SettingsRepository` — `app_settings` (key-value)
- `KiloSessionRepository` — `kilo_sessions`
- `ApprovalRepository` / `GrantRepository` — approvals and grants

### Service Layer

Services own business logic and orchestrate repositories:
- `SettingsService` — key-value configuration
- `PlanService` — plan persistence, idempotency, DAG edge resolution
- `PlanExecutionService` — bridges plan to execution, validates eligibility
- `VerificationGate` — executes checks, produces `VerificationReport` (in-memory)
- `DevelopmentAgentFactory` — creates `KiloDevelopmentAgent` instances
- `AutonomousPlanExecutor` — top-level plan execution loop
- `AutonomousTaskOrchestrator` — single-task lifecycle
- `AutonomousRunController` — single-run lifecycle
- `AuditLogService` — records `AuditEvent` records

### Event-Like Mechanisms Today

| Mechanism | Scope | Persistence | Query Model |
|-----------|-------|-------------|-------------|
| `activity_events` | Generic system activity | SQLite | Paginated list, recent, by category/level |
| `audit_events` | Tool operations, permissions, approvals | SQLite | Paginated list, filtered by timestamp/type/tool/grant |
| `kilo_sessions` | Kilo CLI session lifecycle | SQLite | By app_session_id, kilo_session_id, task_id+execution_id |
| `DevelopmentTask.failure_reason` | Task failure diagnostics | Embedded in task row | Via TaskRepository |
| `OrchestrationResult` | Single-task final outcome | **Not persisted** | Returned to caller only |

---

## 3. Event vs History Definition

### Event

A **durable, immutable, append-only record** that a specific, discrete action occurred within the system.

Events are the *source of truth*. They are written at the moment something happens and are never modified or deleted (except by retention policies).

Examples:
- `task.created`
- `task.started`
- `task.completed`
- `task.failed`
- `plan.started`
- `plan.completed`
- `approval.requested`
- `approval.granted`
- `verification.started`
- `verification.passed`
- `verification.failed`
- `agent.started`
- `agent.completed`
- `checkpoint.created`
- `kilol.session.started`
- `kilol.session.completed`
- `tool.execution.started`
- `tool.execution.completed`

### History

A **queryable, read-optimized representation** of what happened over time.

History is a *projection* over Events (and potentially state snapshots). It answers questions like:
- "What happened to this task?"
- "When did verification fail?"
- "What was the sequence of events for plan X?"
- "Which approvals authorized this action?"
- "What happened before the failure?"

**Recommendation:** History should be a **projection over Events**, not a separate record type.

Rationale:
- Avoids duplication and inconsistency between two sources of truth
- Events are already immutable — perfect for projection
- Future systems (Scheduler, Notifications, Memory, Decision Engine) can all consume the same event stream
- Query views, materialized tables, or in-memory caches can be built on top without changing the event contract

---

## 4. Proposed Minimal Event Contract

### Core Fields

| Field | Type | Required | Indexed | Untrusted Content | Secrets Possible | Rationale |
|-------|------|----------|---------|-------------------|------------------|-----------|
| `event_id` | `str` (UUIDv7 or ULID) | **Yes** | **Primary key** | No | No | Application-generated unique identity. Stable, sortable, URL-safe. |
| `event_type` | `str` (enum-like) | **Yes** | **Yes** | No | No | Categorical identity of the event. Bounded vocabulary. |
| `occurred_at` | `datetime` (UTC) | **Yes** | **Yes** | No | No | When the action actually happened (from the actor's perspective). |
| `recorded_at` | `datetime` (UTC) | **Yes** | No | No | No | When the event was persisted. Needed for crash/recovery analysis. |
| `source` | `str` | **Yes** | No | No | No | Component that emitted the event (e.g., `autonomous_task_orchestrator`, `verification_gate`). |
| `event_version` | `str` (semver) | **Yes** | No | No | No | Schema version of the payload. Allows forward/backward compatibility. Default: `1.0`. |
| `correlation_id` | `str` | No | **Yes** | No | No | Groups events from the same causal chain (e.g., same user request → plan → tasks → runs). |
| `causation_id` | `str` | No | No | No | No | The `event_id` of the event that directly caused this event. Enables causal chain traversal. |
| `task_id` | `str` | No | **Yes** | No | No | Task identifier if the event is task-scoped. |
| `plan_id` | `str` | No | **Yes** | No | No | Plan identifier if the event is plan-scoped. |
| `run_id` | `str` | No | **Yes** | No | No | Run identifier if the event is run-scoped. |
| `execution_id` | `str` | No | Yes | No | No | Execution identifier if the event is execution-scoped. |
| `actor` | `str` | No | No | No | No | Who/what caused the event: `user`, `system`, `ai_manager`, `scheduler`, `repair_loop`. |
| `payload` | `JSON` (bounded) | No | No | **Yes** | **Yes** (must be redacted) | Structured data specific to the event type. Application-owned schema per event type. Bounded to 4 KB. |
| `sensitivity` | `str` (enum) | No | No | No | No | Classification: `public`, `internal`, `sensitive`, `secret`. Default: `internal`. |
| `metadata` | `JSON` (bounded) | No | No | No | No | Non-payload auxiliary data (e.g., timing, counts). Application-owned. Bounded to 1 KB. |

### Design Decisions

1. **event_id is the primary key** — No composite keys. Simplifies idempotency, referencing, and causal linking.
2. **occurred_at vs recorded_at** — Distinguishes "when it happened" from "when we wrote it down". Critical for crash recovery analysis.
3. **correlation_id vs causation_id** — `correlation_id` groups a user request across multiple components; `causation_id` links a specific cause→effect pair. Both are needed.
4. **Payload is bounded and classified** — Prevents unbounded growth and enforces secret redaction at the boundary.
5. **No raw secrets in payload** — Payload is explicitly marked as potentially containing untrusted content and must be redacted before persistence.
6. **Append-only table** — No UPDATE or DELETE statements (except retention DELETE). Primary key is event_id.

### Event Type Vocabulary (Initial)

Strictly bounded, application-owned:

```
task.created
task.started
task.completed
task.failed
task.cancelled
task.aborted
task.recovery_claimed
task.recovery_completed
task.recovery_failed

plan.started
plan.completed
plan.failed
plan.cancelled

verification.started
verification.passed
verification.failed
verification.error

agent.started
agent.completed
agent.failed
agent.error
agent.cancelled
agent.timeout

checkpoint.created

approval.requested
approval.granted
approval.denied
approval.expired
approval.consumed

kilol.session.created
kilol.session.started
kilol.session.completed
kilol.session.failed
kilol.session.cancelled

tool.execution.started
tool.execution.completed
tool.execution.failed

system.error
```

---

## 5. Correlation and Causation Model

### Roles of Each Identifier

| Identifier | Role | Scope | Example |
|------------|------|-------|---------|
| `event_id` | Unique identity for this specific event | Single event | `evt_01HQBX...` |
| `correlation_id` | Groups all events from the same top-level user request | Plan-level | User asks for "add text stats" → correlation_id is the same for plan.created, task.created, task.started, agent.started, verification.passed, etc. |
| `causation_id` | Direct cause→effect link between two events | Event pair | `task.started` has `causation_id = plan.completed`'s `event_id` |
| `task_id` | Task identity | Task-level | Present on all task-scoped events |
| `plan_id` | Plan identity | Plan-level | Present on all plan-scoped events |
| `run_id` | Run identity | Run-level | Present on all run-scoped events |
| `execution_id` | Execution identity | Execution-level | Present on all execution-scoped events |

### Rules

1. **correlation_id** is set once at the top level (plan creation) and propagated unchanged to all descendant events.
2. **causation_id** is set by the producer to point to the `event_id` of the event that directly triggered the current event.
3. If no clear cause exists (e.g., system startup, external trigger), `causation_id` is `null`.
4. `task_id`, `plan_id`, `run_id`, `execution_id` are redundant with `correlation_id` for filtering, but they enable efficient indexed queries without joining.
5. No event may reference an `event_id` that does not exist (causation integrity is best-effort; the event store does not enforce foreign keys on event_id).

### Example Causal Chain

```
User request → AI Manager decision
  event_id: evt_001, correlation_id: corr_abc, event_type: plan.started

PlanManager normalizes plan
  event_id: evt_002, correlation_id: corr_abc, causation_id: evt_001, event_type: plan.created

Task created
  event_id: evt_003, correlation_id: corr_abc, causation_id: evt_002, event_type: task.created, task_id: task_...

Task started
  event_id: evt_004, correlation_id: corr_abc, causation_id: evt_003, event_type: task.started, task_id: task_...

Agent started
  event_id: evt_005, correlation_id: corr_abc, causation_id: evt_004, event_type: agent.started, task_id: task_..., run_id: run_...

Verification passed
  event_id: evt_006, correlation_id: corr_abc, causation_id: evt_005, event_type: verification.passed, task_id: task_...

Checkpoint created
  event_id: evt_007, correlation_id: corr_abc, causation_id: evt_006, event_type: checkpoint.created, task_id: task_...

Task completed
  event_id: evt_008, correlation_id: corr_abc, causation_id: evt_007, event_type: task.completed, task_id: task_...

Plan completed
  event_id: evt_009, correlation_id: corr_abc, causation_id: evt_008, event_type: plan.completed, plan_id: plan_...
```

---

## 6. Idempotency Model

### Event Identity

- `event_id` alone uniquely identifies an event.
- `event_id` must be generated by the application **before** attempting persistence, using a cryptographically random or time-sortable identifier (UUIDv7, ULID, or UUIDv4).
- The producer must be idempotent: if it is retried, it must either:
  - Re-emit the same `event_id` for the same logical event (preferred), or
  - Detect that the event was already recorded and skip re-emission.

### Duplicate Delivery Handling

- The event store **MUST** enforce uniqueness on `event_id` (primary key).
- Duplicate `event_id` insertions must be silently ignored or return the existing record — never create a second record.
- For some event categories (e.g., `system.error` during retries), duplicates are acceptable as long as they don't corrupt state.

### Crash Between Action and Event Persistence

- The action (task state transition, verification result, etc.) and the event record must be persisted in the **same transaction** where possible.
- If the action succeeds but the event fails to persist, the action must be rolled back (fail-closed).
- Exception: truly asynchronous side effects (e.g., notification dispatch) may fire after the transaction commits, but their events must be recorded separately and clearly marked as deferred.

### Invariants That Must Never Be Violated

1. An event is never modified or deleted after insertion (no UPDATE/DELETE on event rows).
2. `event_id` is globally unique.
3. `occurred_at` is never in the future (bounded by clock skew tolerance ±5 minutes).
4. Event ordering within a `correlation_id` must be reconstructable from `occurred_at` + `event_id` (for ties).

---

## 7. Ordering and Time Semantics

### Timestamps

- **`occurred_at`**: UTC timestamp when the action happened, from the actor's local clock. Set by the component that performed the action.
- **`recorded_at`**: UTC timestamp when the event was persisted to the database. Set by the event store.

### Ordering Guarantees

The system needs **causal ordering**, not strict wall-clock ordering. Requirements:

1. Events with different `correlation_id` values have no ordering guarantee relative to each other.
2. Events with the same `correlation_id` must be orderable by `(occurred_at, event_id)`.
3. Concurrent events within the same `correlation_id` may have identical `occurred_at` values; `event_id` provides tie-breaking.
4. Process restarts must not break ordering — `occurred_at` is preserved from the original action.
5. Clock differences between components are tolerated as long as `occurred_at` is within ±5 minutes of `recorded_at`. If the gap exceeds 5 minutes, the event is still recorded but flagged in `metadata`.

### What the System Actually Needs

- **Intra-task ordering**: All events for a single task must be orderable. This is satisfied by `(occurred_at, event_id)` within a `correlation_id`.
- **Cross-component causal chains**: `causation_id` provides explicit ordering where it matters (e.g., task.started → agent.started).
- **Time-window queries**: "What happened in the last hour?" — satisfied by `recorded_at` index.
- **No requirement for global linearizability**: The system does not require a total order across all events.

---

## 8. Failure Semantics

### A. Action succeeds but event persistence fails

**Behavior:** The action must be rolled back. The system returns an error. The caller may retry the action (which may re-emit the event with the same `event_id`).

**Rationale:** Security and authorization decisions must not become invisible due to event store unavailability. Auditing is non-negotiable.

### B. Action fails and event persistence succeeds

**Behavior:** The failure event is recorded. The action's side effects must not have occurred (or must be compensated). If the action partially succeeded, the failure event records the incomplete state.

**Rationale:** A failure event without a corresponding success event is the correct historical record.

### C. Event persistence is retried

**Behavior:** Retries use the same `event_id`. The event store ignores duplicates.

**Rationale:** Idempotency prevents duplicate records from transient failures.

### D. Process crashes between action and event recording

**Behavior:** On restart, the recovery process must check for actions that completed without events. This requires:
- Action outcomes that are not yet reflected in events must be detected.
- The recovery manager (already partially implemented as `RecoveryManager`) can emit compensating events.

**Current gap:** `DevelopmentTask` state transitions happen in-memory before events would be recorded. A crash between state transition and event recording would leave the task in a state with no event history. This must be addressed in Stage 11.2.

### E. Event storage is temporarily unavailable

**Behavior:** The action fails closed. No state mutation occurs. The caller receives an error.

**Rationale:** Event store unavailability must not create an inconsistent security or authorization state.

---

## 9. Security and Data Classification

### Threat Model

Event/history data must not become a secret-leak channel. The event store is readable by application code and potentially by future APIs. It must not become a repository for credentials or sensitive raw data.

### Classification Rules

| Data Category | May Be Stored Directly | Must Be Redacted | Must Never Be Persisted |
|---------------|------------------------|------------------|------------------------|
| API keys, access tokens, passwords | No | N/A | Yes |
| Provider credentials (encrypted or plaintext) | No | N/A | Yes |
| Authorization headers | No | N/A | Yes |
| Raw provider responses | No | Yes (headers only, body truncated) | N/A |
| Tool arguments | Yes (bounded) | Yes (secrets redacted) | N/A |
| User content (prompts, descriptions) | Yes (bounded) | No (but sensitivity: `internal`) | N/A |
| Model-generated content | Yes (bounded) | No (but sensitivity: `internal`) | N/A |
| Filesystem paths | Yes | No (sensitivity: `internal`) | N/A |
| Command output | Yes (bounded) | Yes (secrets redacted) | N/A |
| Personal data (PII) | Yes (bounded) | Yes (if identified) | N/A |
| Approval information | Yes | No (sensitivity: `sensitive`) | N/A |
| Verification results | Yes | No | N/A |
| Git SHAs | Yes | No | N/A |

### Payload Bounds

- **Maximum payload size**: 4 KB (4096 bytes) per event
- **Maximum metadata size**: 1 KB (1024 bytes) per event
- Payloads exceeding the limit must be truncated and a `truncated: true` flag added to metadata.
- The event store must validate bounds before persistence and reject oversize events.

### Untrusted Content Marking

- All `payload` fields from external sources (model output, user input, provider responses) are **untrusted**.
- The event emitter must sanitize payloads before persistence.
- Sanitization rules:
  - Strip or redact known secret patterns (API keys, tokens, passwords)
  - Truncate long strings
  - Reject binary/non-UTF8 content
  - Never store raw provider response bodies — store structured fields only

### Integration with Existing Audit Guarantees

- The existing `audit_events` table already enforces sanitization via `ensure_sanitized_payload()`.
- The new event store must use the **same sanitization contract** for any overlapping data types.
- PermissionEngine, ApprovalService, ToolExecutionService, and Audit guarantees are **not modified** by this design.

---

## 10. Storage Strategy

### Append-Only Event Table

**Table name:** `events`

**Schema:**

```sql
CREATE TABLE events (
    event_id        TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    occurred_at     TEXT NOT NULL,  -- ISO8601 UTC
    recorded_at     TEXT NOT NULL,  -- ISO8601 UTC
    source          TEXT NOT NULL,
    event_version   TEXT NOT NULL DEFAULT '1.0',
    correlation_id  TEXT,
    causation_id    TEXT,
    task_id         TEXT,
    plan_id         TEXT,
    run_id          TEXT,
    execution_id    TEXT,
    actor           TEXT,
    payload         TEXT,           -- JSON, bounded 4KB
    metadata        TEXT,           -- JSON, bounded 1KB
    sensitivity     TEXT NOT NULL DEFAULT 'internal'
);

CREATE INDEX ix_events_occurred_at ON events(occurred_at);
CREATE INDEX ix_events_event_type ON events(event_type);
CREATE INDEX ix_events_correlation_id ON events(correlation_id);
CREATE INDEX ix_events_task_id ON events(task_id);
CREATE INDEX ix_events_plan_id ON events(plan_id);
CREATE INDEX ix_events_run_id ON events(run_id);
CREATE INDEX ix_events_execution_id ON events(execution_id);
CREATE INDEX ix_events_source ON events(source);
```

**Key decisions:**

1. **Append-only**: No UPDATE/DELETE except for retention cleanup.
2. **TEXT for JSON**: SQLite has no native JSON type in all versions; TEXT with JSON serialization is portable and indexable via generated columns if needed later.
3. **Timestamps as ISO8601 TEXT**: SQLite `DateTime(timezone=True)` returns aware datetimes, but TEXT is simpler for cross-tool compatibility and avoids timezone surprises.
4. **No foreign keys**: `causation_id` references `event_id`, but we do not enforce FK constraints. This allows the event store to be fully append-only without circular dependencies.
5. **No soft deletes**: Events are immutable. Retention is hard DELETE by time window.

### Retention

- Default retention: **90 days** for full events.
- After 90 days, events may be archived (exported to cold storage) and deleted from the primary database.
- Retention policy is configurable via `SettingsService`.
- Retention deletion is a **background batch job**, not a per-event operation.
- Audit and security events may have longer retention requirements — this is a policy decision outside the event store.

### Querying Strategy

For MVP, all queries go directly against the `events` table with indexed filters. As volume grows, consider:

- **Materialized views** for common history queries (e.g., task history)
- **Summary tables** for dashboard metrics (event counts by type per time window)
- **Cold storage export** for long-term retention

These are future optimizations, not MVP requirements.

---

## 11. Integration Boundaries (Initial)

The smallest useful set of event emission points. Do NOT instrument every function.

### Tier 1 — Core Lifecycle (MVP)

These are the minimum events needed to reconstruct what happened to a task/plan:

| Event | Emitted By | When |
|-------|-----------|------|
| `plan.started` | `PlanExecutionService` or `AutonomousPlanExecutor` | When plan execution begins |
| `plan.completed` | `AutonomousPlanExecutor` | When all tasks reach terminal state |
| `plan.failed` | `AutonomousPlanExecutor` | When plan cannot proceed |
| `task.created` | `PlanService` or `TaskRepository` | When task is first persisted |
| `task.started` | `AutonomousTaskOrchestrator.start_task()` | When task execution begins |
| `task.completed` | `AutonomousTaskOrchestrator` | When task reaches COMMITTED |
| `task.failed` | `AutonomousTaskOrchestrator` | When task reaches terminal FAILED/ABORTED/CANCELLED |
| `verification.started` | `VerificationGate` | When verification begins |
| `verification.passed` | `VerificationGate` | When gate decision is PASS |
| `verification.failed` | `VerificationGate` | When gate decision is FAIL |
| `agent.started` | `AutonomousTaskOrchestrator` | When agent execution begins |
| `agent.completed` | `AutonomousTaskOrchestrator` | When agent returns SUCCESS |
| `agent.failed` | `AutonomousTaskOrchestrator` | When agent returns FAILURE/ERROR |
| `checkpoint.created` | `WorkspaceManager.create_checkpoint()` | When Git commit is created |

### Tier 2 — Observability and Recovery (Stage 11.2+)

| Event | Emitted By | When |
|-------|-----------|------|
| `kilol.session.created` | `KiloSessionManager` | When Kilo session record is created |
| `kilol.session.started` | `KiloProcess` | When process actually starts |
| `kilol.session.completed` | `KiloProcess` | When process exits successfully |
| `kilol.session.failed` | `KiloProcess` | When process exits with error |
| `approval.requested` | `CapabilityApprovalService` | When approval is created |
| `approval.granted` | `CapabilityApprovalService` | When approval is granted |
| `approval.denied` | `CapabilityApprovalService` | When approval is denied |
| `tool.execution.started` | `ToolExecutionService` | Before tool execution |
| `tool.execution.completed` | `ToolExecutionService` | After tool execution |

### Non-Goals for Stage 11.2

- Do NOT emit events for every internal function call.
- Do NOT emit events for pure computation or validation.
- Do NOT emit events for database reads.
- Do NOT emit events for configuration changes (use existing `activity_events`).

---

## 12. Future Compatibility

### Scheduler

- `scheduled.execution.created` → `plan.started` → task events → `plan.completed`
- `correlation_id` links scheduled execution to plan events
- `run_id` distinguishes scheduled runs from ad-hoc runs

### Notifications

- Notification rules subscribe to `event_type` patterns
- `notification.sent` can be recorded as a downstream event with `causation_id` pointing to the triggering event
- No changes to event contract needed

### Memory

- Event stream is the raw material for memory extraction
- `MemoryExtractionService` can read events and write `memory` records (separate table, future stage)
- No changes to event contract needed

### Decision Engine

- Event history provides context for decision-making
- `DecisionEngine` reads events by `correlation_id` or `task_id` to build context
- No changes to event contract needed

### Computer Use

- `observation.captured` → `action.executed` → `action.completed` events
- Follows same correlation/causation model
- No changes to event contract needed

---

## 13. Query Model

### Minimal Required Queries

| Query | Filter | Sort | Purpose |
|-------|--------|------|---------|
| Get event by ID | `event_id` | — | Direct lookup |
| List events for task | `task_id` | `occurred_at ASC` | Task history |
| List events for execution | `run_id` OR `execution_id` | `occurred_at ASC` | Run/debug history |
| List events for plan | `plan_id` | `occurred_at ASC` | Plan history |
| List events by correlation | `correlation_id` | `occurred_at ASC` | Full causal chain |
| List recent events | `recorded_at` time window | `recorded_at DESC` | Dashboard / monitoring |
| List events by type | `event_type` | `occurred_at DESC` | Filter by lifecycle phase |
| List events by source | `source` | `recorded_at DESC` | Component-specific debugging |

### Pagination

- Default limit: 50
- Max limit: 500
- Max offset: 100,000 (to prevent unbounded scans)
- Cursor-based pagination for time-window queries (using `recorded_at` + `event_id` as cursor)

### Not Required for MVP

- Full-text search across payloads
- Aggregation queries (counts, averages) — use separate summary tables
- Cross-correlation joins
- Real-time streaming (WebSocket) — use HTTP polling or server-sent events later

---

## 14. Test Strategy

### Mandatory for First Implementation Gate (Stage 11.2)

| Category | Tests |
|----------|-------|
| **Schema/contract** | Verify event table schema, required fields, bounds, enum values |
| **Persistence** | Insert, duplicate rejection, retrieval by ID and indexes |
| **Idempotency** | Same event_id inserted twice → second insert ignored or returns existing |
| **Ordering** | Events with same correlation_id order by (occurred_at, event_id) |
| **Concurrency** | Concurrent inserts for different events succeed; duplicate inserts are handled |
| **Crash/recovery** | Action succeeds, event fails → action rolled back |
| **Payload bounds** | Oversize payload rejected or truncated |
| **Secret redaction** | Known secret patterns in payload are redacted before persistence |
| **Security** | No event field allows privilege escalation or state mutation |
| **Integration** | Emitting events from orchestrator produces correct event stream |
| **Failure injection** | Simulated DB unavailability → action fails closed |

### Test Approach

- Unit tests for `EventStore` repository class
- Integration tests with real SQLite (in-memory or temp file)
- Property-based tests for idempotency and ordering
- Mock-based tests for event emission from orchestrator components

---

## 15. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **Performance under high event volume** | Medium | Medium | Start with indexes on the most common filter fields. Plan for summary tables. SQLite handles ~100K rows well; growth beyond that requires archival strategy. |
| **Event store becomes a secret leak channel** | Low (if redaction is enforced) | High | Enforce redaction at the event emitter boundary. Add automated tests for known secret patterns. |
| **Crash between action and event creates invisible state** | Medium | High | Require action+event in same transaction where possible. Add recovery checks in Stage 11.2. |
| **Unbounded payload growth** | Medium | Medium | Enforce 4KB payload limit at insertion. Truncate with metadata flag. |
| **Schema evolution breaks consumers** | Medium | Medium | Use `event_version` field. Keep initial schema stable. Add new fields as nullable. |
| **Event ordering ambiguity under concurrency** | Medium | Low | Use `(occurred_at, event_id)` tie-breaking. Document that intra-component ordering is guaranteed, cross-component is best-effort. |
| **Over-instrumentation bloats event volume** | High (if not disciplined) | Medium | Strict Tier 1 / Tier 2 policy. Do not emit events for internal reads or pure computations. |

---

## 16. Explicit Non-Goals

This discovery phase does NOT include:

- Creating the event system implementation
- Migrating existing services to emit events
- Adding Scheduler, Notifications, Memory, or Decision Engine
- Modifying the autonomous coding architecture
- Modifying Kilo, provider integrations, or the Development Manager
- Changing VerificationGate, PermissionEngine, ApprovalService, or ToolExecutionService
- Adding new API endpoints
- Implementing archival or cold storage
- Adding real-time event streaming
- Modifying the existing `activity_events` or `audit_events` tables
- Redesigning task state machines or plan structures

---

## 17. Recommended Stage 11.2 Implementation Plan

### Scope

Build the **minimal viable Event foundation** with Tier 1 integration points only.

### Deliverables

1. **Event store infrastructure**
   - `app/events/models.py` — `EventModel` ORM
   - `app/events/repository.py` — `EventRepository` with idempotent insert, indexed queries, retention
   - `app/events/schemas.py` — `Event` domain model, `EventType` enum, `Sensitivity` enum
   - Database migration in `init_db()` to create `events` table and indexes

2. **Event emission contract**
   - `app/events/emitter.py` — `EventEmitter` service
   - Sanitization and bounds enforcement
   - `correlation_id` and `causation_id` propagation helpers

3. **Tier 1 integration**
   - Instrument `AutonomousTaskOrchestrator` to emit task lifecycle events
   - Instrument `VerificationGate` to emit verification events
   - Instrument `WorkspaceManager.create_checkpoint()` to emit checkpoint events
   - Instrument `AutonomousPlanExecutor` to emit plan lifecycle events

4. **Tests**
   - All test categories listed in Section 14
   - Regression tests to ensure existing tests still pass

### Out of Scope for Stage 11.2

- Kilo session events (Tier 2)
- Approval events (Tier 2)
- Tool execution events (Tier 2)
- History projection/query API
- UI integration
- Retention job (define policy, but don't build background job yet)

### Estimated Complexity

Low-to-medium. The event store is a simple append-only table. Integration requires touching 4-5 existing components. The main risk is ensuring transaction boundaries are correct so that crashes don't create inconsistent state.

---

## 18. Open Questions

1. **Should `PlanExecutionResult` and `AutonomousRunResult` be persisted as entities, or only as events?**
   Recommendation: Persist as **events** only. If query performance suffers, add materialized views later.

2. **Should the `events` table use native JSON or TEXT for payload/metadata?**
   Recommendation: Use TEXT with explicit JSON serialization in Python. SQLite JSON functions work on TEXT. Simpler migrations.

3. **Should `occurred_at` use ISO8601 TEXT or native DateTime?**
   Recommendation: ISO8601 TEXT. More portable, easier to debug, works consistently across SQLite versions.

4. **How does the event store interact with the existing `activity_events` table?**
   Recommendation: Keep them separate. `activity_events` remains for generic system activity. `events` is for structured lifecycle events. They serve different query patterns.

5. **Should event emission be synchronous or asynchronous?**
   Recommendation: **Synchronous within the transaction** for Tier 1 events. This ensures crash safety. Asynchronous emission can be added later for high-throughput scenarios.
