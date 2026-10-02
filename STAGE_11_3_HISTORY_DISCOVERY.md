# STAGE 11.3 — HISTORY QUERY LAYER DISCOVERY

## 1. Current Event Store Capabilities

### Persisted Data
The Event Store is a single `events` table with the following durable fields:

| Field | Type | Notes |
|-------|------|-------|
| event_id | VARCHAR(64) PK | `evt_` + UUID hex |
| event_type | VARCHAR(128) | 10 Tier 1 types |
| occurred_at | TIMESTAMP WITH TIME ZONE | When action happened |
| recorded_at | TIMESTAMP WITH TIME ZONE | When persisted |
| source | VARCHAR(128) | Component name |
| event_version | VARCHAR(32) | Default `1.0` |
| correlation_id | VARCHAR(128) | Optional grouping |
| causation_id | VARCHAR(64) | Optional prior event_id |
| task_id | VARCHAR(74) | Optional task identifier |
| plan_id | VARCHAR(64) | Optional plan identifier |
| run_id | VARCHAR(64) | Optional run identifier |
| execution_id | VARCHAR(64) | Optional execution identifier |
| actor | VARCHAR(128) | Optional actor |
| payload | TEXT | Sanitized JSON, max 4KB |
| metadata | TEXT | Sanitized JSON, max 1KB |
| sensitivity | VARCHAR(20) | `public` / `internal` / `sensitive` |

### Existing Query Capabilities
`EventRepository.list()` supports filtering by:
- `correlation_id`
- `task_id`
- `plan_id`
- `run_id`
- `execution_id`
- `event_type`
- `source`
- `from_occurred_at` / `to_occurred_at`
- `from_recorded_at` / `to_recorded_at`

Ordering: `occurred_at ASC, event_id ASC` (deterministic, not causal).

Pagination: `limit` (default 50, max 500), `offset` (max 100,000).

`EventRepository.count()` supports same filters.

### Index Coverage
11 indexes exist on: `occurred_at`, `recorded_at`, `event_type`, `source`, `correlation_id`, `causation_id`, `task_id`, `plan_id`, `run_id`, `execution_id`, `sensitivity`.

All proposed History queries are covered by existing indexes. No new indexes required.

### Idempotency
Insert is idempotent by `event_id` primary key. Conflicting content raises `EventIntegrityConflict`.

### Current Tier 1 Emissions

**AutonomousTaskOrchestrator** emits:
- `TASK_STARTED` (task_id, plan_id, attempt_count, actor)
- `TASK_FAILED` (task_id, plan_id, attempt_count, actor, reason)
- `VERIFICATION_STARTED` (task_id, plan_id, actor)
- `VERIFICATION_PASSED` (task_id, plan_id, actor, verification_decision)
- `VERIFICATION_FAILED` (task_id, plan_id, actor, reason)
- `CHECKPOINT_CREATED` (task_id, plan_id, actor, commit_sha, checkpoint_id)
- `TASK_COMPLETED` (task_id, plan_id, commit_sha, attempt_count, actor)

**AutonomousPlanExecutor** emits:
- `PLAN_STARTED` (plan_id, actor)
- `PLAN_COMPLETED` (plan_id, actor, reason, completed_tasks_count, total_tasks_count)
- `PLAN_FAILED` (plan_id, actor, reason, completed_tasks_count, total_tasks_count)

**VerificationGate**: No events emitted (instrumentation added in Stage 11.2 but not yet used).

**WorkspaceManager**: No events emitted (instrumentation added in Stage 11.2 but not yet used).

### Missing Event Coverage
- No explicit `RUN_STARTED` / `RUN_COMPLETED` / `RUN_FAILED` events.
- No `TASK_RETRY` or `TASK_REPAIR` events (retries are implicit via `attempt_count` in `TASK_STARTED`).
- No plan-level task events (individual task events are emitted by the orchestrator).

---

## 2. History Semantics

**Definition:** History is a read-side projection of durable Event Store records, organized by entity identity (task, plan, run, execution, correlation). It answers "what happened" without introducing new state or authority.

**Key principles:**
- Event Store is the sole source of truth.
- History is a query layer, not a cache or materialized view.
- History does not modify, interpret, or authorize — it retrieves.
- History does not guarantee causal ordering; it provides deterministic chronological ordering.
- History does not fill gaps from non-event sources (e.g., BackgroundExecutionManager in-memory handles).

### Task History
Answers: What happened to task X?
- All events where `task_id == X`
- Ordered by `occurred_at ASC, event_id ASC`
- Includes: started, verification, failed, checkpoint, completed

### Plan History
Answers: What happened during plan Y execution?
- All events where `plan_id == Y`
- Ordered by `occurred_at ASC, event_id ASC`
- Includes: plan started, all task events for tasks in the plan, plan completed/failed

### Execution History
Answers: What events belong to execution Z?
- All events where `execution_id == Z`
- Ordered by `occurred_at ASC, event_id ASC`

### Correlation History
Answers: What happened during a complete top-level request?
- All events where `correlation_id == X`
- Ordered by `occurred_at ASC, event_id ASC`
- Spans task, plan, execution, and verification events

### Run History
Answers: What events belong to run W?
- All events where `run_id == W`
- Ordered by `occurred_at ASC, event_id ASC`

---

## 3. Minimal History API

### Proposed Service: `HistoryService`

**Location:** `app/events/history.py`

**Dependencies:** `EventRepository`, `TaskRepository` (for ownership verification)

**Methods:**

#### `get_task_history(task_id: str, *, limit: int = 50, offset: int = 0) -> HistoryPage`
- **Input:** task_id
- **Output:** `HistoryPage` containing `HistoryEntry` list and pagination metadata
- **Ordering:** `occurred_at ASC, event_id ASC`
- **Pagination:** limit/offset with hard caps (default 50, max 500)
- **Error behavior:** Returns empty page if task has no events. Raises `PersistenceError` on database failure.
- **Authorization:** Verify task exists and caller has access via `TaskRepository.get_task()` before returning events.

#### `get_plan_history(plan_id: str, *, limit: int = 50, offset: int = 0) -> HistoryPage`
- **Input:** plan_id
- **Output:** `HistoryPage`
- **Ordering:** `occurred_at ASC, event_id ASC`
- **Pagination:** limit/offset with hard caps
- **Error behavior:** Returns empty page if plan has no events.
- **Authorization:** Verify plan exists by checking if any task belongs to the plan via `TaskRepository.get_tasks_by_plan()`.

#### `get_execution_history(execution_id: str, *, limit: int = 50, offset: int = 0) -> HistoryPage`
- **Input:** execution_id
- **Output:** `HistoryPage`
- **Ordering:** `occurred_at ASC, event_id ASC`
- **Pagination:** limit/offset with hard caps
- **Error behavior:** Returns empty page if execution has no events.
- **Authorization:** None beyond event-level sensitivity filtering (events already sanitized).

#### `get_correlation_history(correlation_id: str, *, limit: int = 50, offset: int = 0) -> HistoryPage`
- **Input:** correlation_id
- **Output:** `HistoryPage`
- **Ordering:** `occurred_at ASC, event_id ASC`
- **Pagination:** limit/offset with hard caps
- **Error behavior:** Returns empty page if correlation has no events.
- **Authorization:** None beyond event-level sensitivity filtering.

#### `get_history_between(start: datetime, end: datetime, *, limit: int = 50, offset: int = 0, event_type: EventType | None = None) -> HistoryPage`
- **Input:** time range, optional event_type filter
- **Output:** `HistoryPage`
- **Ordering:** `occurred_at ASC, event_id ASC`
- **Pagination:** limit/offset with hard caps
- **Error behavior:** Raises `ValueError` if `start > end` or invalid timezone-naive datetime.
- **Authorization:** None — this is an administrative query. Should be gated at API layer.

#### `get_recent_history(limit: int = 50) -> HistoryPage`
- **Input:** limit
- **Output:** `HistoryPage`
- **Ordering:** `recorded_at DESC, event_id DESC` (most recent first)
- **Pagination:** limit with hard cap (max 500)
- **Error behavior:** Returns empty page if no events exist.
- **Authorization:** None — this is an administrative query. Should be gated at API layer.

### Methods NOT Proposed Now
- `get_run_history(run_id)` — `run_id` is not yet a first-class entity; can be added when Run entity exists. Current `run_id` on events can be queried via `EventRepository.list(run_id=...)` directly.
- `get_failed_tasks()` — analytics; non-goal for Stage 11.3.
- `get_entity_timeline()` — premature grouping; defer until consumers need it.

---

## 4. Return Model

### Option Chosen: Dedicated `HistoryEntry` Model

```python
class HistoryEntry(BaseModel):
    event_id: str
    event_type: EventType
    occurred_at: datetime
    recorded_at: datetime
    source: str
    task_id: str | None
    plan_id: str | None
    run_id: str | None
    execution_id: str | None
    correlation_id: str | None
    causation_id: str | None
    actor: str | None
    payload: dict[str, Any] | None
    metadata: dict[str, Any] | None
    sensitivity: Sensitivity
```

**Rationale:**
- Preserves all durable Event fields.
- Adds structural typing (EventType, Sensitivity enums) for consumer safety.
- Does not project, aggregate, or interpret.
- Consumers can reconstruct timeline, group by entity, or derive summaries.
- Keeps Event Store as source of truth — HistoryEntry is a typed view.

### Pagination Model

```python
class HistoryPage(BaseModel):
    entries: tuple[HistoryEntry, ...]
    total: int  # from EventRepository.count() with same filters
    limit: int
    offset: int
    has_more: bool  # offset + len(entries) < total
```

---

## 5. Ordering Semantics

- **Primary ordering:** `occurred_at ASC, event_id ASC` for all entity-scoped queries.
- **Recent history:** `recorded_at DESC, event_id DESC` (most recently persisted first).
- **Causation:** `causation_id` links events but does NOT guarantee ordering. Two events with the same `occurred_at` may have ambiguous causal relationship. Consumers must not infer causality from position alone.
- **Consumer guidance:** History consumers that need causal ordering must implement their own causal-chain traversal using `causation_id` linkage.

---

## 6. Pagination / Bounds

| Parameter | Default | Maximum | Notes |
|-----------|---------|---------|-------|
| `limit` | 50 | 500 | Hard cap |
| `offset` | 0 | 100,000 | Hard cap |
| `limit` (recent) | 50 | 500 | Hard cap |

- `has_more` in `HistoryPage` indicates whether additional pages exist.
- Consumers must not request offset beyond 100,000; HistoryService clamps automatically.
- Time-range queries without a `limit` use the default (50). No unbounded queries are permitted.

---

## 7. Authorization / Security

### Existing Authorization Landscape
- `PermissionEngine` evaluates tool execution permissions. It does NOT govern data access.
- No current authorization exists on `TaskRepository`, `EventRepository`, or API status endpoints (`get_task_status`, `get_plan_execution_status`, `get_run_status`).
- API routes currently expose task/plan/run status without authentication/authorization middleware.

### History Authorization Design
**Stage 11.3 will NOT introduce a new authorization system.**

Authorization strategy:
1. **Task-scoped queries** (`get_task_history`, `get_plan_history`): Verify the underlying task/plan exists via `TaskRepository`. If the caller cannot access the task entity, the History layer returns 404 (or empty) — same semantics as current status endpoints.
2. **Execution/correlation queries**: No entity ownership check beyond event-level sensitivity filtering. Events are already sanitized at emission.
3. **Administrative queries** (`get_recent_history`, `get_history_between`): No task-level authorization. These must be gated at the API/router layer if the deployment requires it. HistoryService itself does not enforce role-based access.

### Security Guarantees
- **No secret leakage:** All payloads/metadata are sanitized at emission. HistoryEntry returns sanitized content only.
- **No scope expansion:** History cannot return events for entities the caller cannot access, because the underlying task/plan existence check gates access.
- **No permission bypass:** History does not invoke PermissionEngine. It is a read-only data retrieval layer.
- **Sensitivity classification:** Events marked `SENSITIVE` are returned in full to authorized callers. If sensitivity-based filtering is needed, it belongs at the API/presentation layer, not in HistoryService.

---

## 8. Failure Semantics

| Scenario | Behavior |
|----------|----------|
| Task with no events | Returns `HistoryPage(entries=(), total=0)` |
| Plan with no events | Returns `HistoryPage(entries=(), total=0)` |
| Execution with no events | Returns `HistoryPage(entries=(), total=0)` |
| Correlation with no events | Returns `HistoryPage(entries=(), total=0)` |
| Invalid time range (`start > end`) | Raises `ValueError` |
| Naive datetime | Raises `ValueError` (must be UTC-aware) |
| Database/persistence failure | Raises `EventRepository` exception (not swallowed) |
| Event deserialization failure | Logs warning, returns `HistoryEntry` with `payload=None` or `metadata=None` |

**Key rule:** Storage failures are never converted to empty history. Empty history means "no events found," not "query failed."

---

## 9. Performance / Index Analysis

### Query Plans

| Query | Filter | Index Used | Ordering |
|-------|--------|------------|----------|
| `get_task_history` | `task_id = ?` | `ix_events_task_id` | `occurred_at ASC, event_id ASC` |
| `get_plan_history` | `plan_id = ?` | `ix_events_plan_id` | `occurred_at ASC, event_id ASC` |
| `get_execution_history` | `execution_id = ?` | `ix_events_execution_id` | `occurred_at ASC, event_id ASC` |
| `get_correlation_history` | `correlation_id = ?` | `ix_events_correlation_id` | `occurred_at ASC, event_id ASC` |
| `get_history_between` | `occurred_at BETWEEN ? AND ?` | `ix_events_occurred_at` | `occurred_at ASC, event_id ASC` |
| `get_recent_history` | none | full scan (bounded by limit) | `recorded_at DESC, event_id DESC` |

**No new indexes required.** All queries use existing indexes or bounded full scans (for recent history with small limit).

---

## 10. Future Compatibility

### Scheduler
- Needs: "What happened during previous scheduled executions?"
- Compatibility: `get_plan_history(plan_id)` provides all plan execution events. Scheduler can derive execution windows from `occurred_at` ranges.

### Notifications
- Needs: "Did this task already complete/fail?"
- Compatibility: `get_task_history(task_id)` returns terminal events (`TASK_COMPLETED`, `TASK_FAILED`). Consumer checks for presence of terminal event type.

### Memory
- Needs: "What durable events are relevant to this context?"
- Compatibility: `get_correlation_history(correlation_id)` returns all events in a request chain. Memory can filter by `event_type` or `source`.

### Decision Engine
- Needs: "What happened previously before deciding?"
- Compatibility: `get_task_history` and `get_plan_history` provide full lifecycle context.

### Computer Use
- Needs: "What actions/results occurred during this session?"
- Compatibility: `get_execution_history(execution_id)` provides all events for an execution session.

**No History API changes required for any future consumer.** All consumers can be built on top of the proposed `HistoryPage` / `HistoryEntry` return types.

---

## 11. Event Foundation Blocker Analysis

**NO EVENT FOUNDATION BLOCKER**

The Stage 11.2 Event Foundation is sufficient for History:
- All necessary entity identifiers (`task_id`, `plan_id`, `run_id`, `execution_id`, `correlation_id`) are persisted.
- Ordering fields (`occurred_at`, `recorded_at`, `event_id`) are present and deterministic.
- Payload/metadata sanitization occurs before persistence.
- Idempotency and integrity constraints prevent duplicate/conflicting events.
- Query bounds (limit/offset) are enforced.
- No missing fields that would prevent History reconstruction.

**Minor observation (not a blocker):**
- `run_id` and `execution_id` are not yet populated by Tier 1 emitters. They are `None` in current emissions. This means `get_run_history` and `get_execution_history` will return empty results until these fields are populated in future stages. This is expected and acceptable.

---

## 12. Test Strategy

### Repository Tests (test_history_repository.py)
- `test_get_task_history_returns_events` — insert events with same task_id, verify all returned
- `test_get_plan_history_returns_events` — insert events with same plan_id, verify all returned
- `test_get_execution_history_returns_events` — insert events with same execution_id
- `test_get_correlation_history_returns_events` — insert events with same correlation_id
- `test_get_history_between_time_range` — verify time filtering
- `test_get_recent_history_ordering` — verify recorded_at DESC ordering
- `test_deterministic_ordering` — verify occurred_at ASC, event_id ASC for ties
- `test_pagination_limit` — verify limit cap at 500
- `test_pagination_offset` — verify offset cap at 100,000
- `test_empty_history` — no events for entity returns empty page
- `test_has_more` — verify has_more calculation
- `test_persistence_failure_propagates` — database failure raises, does not return empty
- `test_no_secret_leakage` — verify redacted payloads in HistoryEntry

### Service Tests (test_history_service.py)
- `test_get_task_history_verifies_task_exists` — returns empty if task not in TaskRepository
- `test_get_plan_history_verifies_plan_exists` — returns empty if no tasks for plan
- `test_invalid_time_range_raises` — start > end raises ValueError
- `test_naive_datetime_raises` — timezone-naive datetime raises ValueError

### Integration Tests (test_history_integration.py)
- `test_full_task_lifecycle_history` — emit TASK_STARTED, VERIFICATION_STARTED, VERIFICATION_PASSED, CHECKPOINT_CREATED, TASK_COMPLETED; verify HistoryEntry sequence
- `test_full_plan_lifecycle_history` — emit PLAN_STARTED, multiple TASK_STARTED/COMPLETED, PLAN_COMPLETED; verify HistoryEntry sequence
- `test_correlation_chain_history` — emit events with same correlation_id across task/plan boundaries; verify all present
- `test_no_cross_contamination` — events for task A do not appear in task B history

---

## 13. Explicit Non-Goals

- No event projections or materialized views
- No analytics or aggregations
- No dashboards
- No UI components
- No notification triggers
- No scheduler integration
- No memory/context injection
- No event streaming or WebSockets
- No external API exposure beyond existing REST patterns
- No search engine integration
- No archival or retention redesign
- No new authorization system
- No causal ordering guarantees beyond deterministic chronological ordering

---

## 14. Stage 11.3 Implementation Scope

### Files to Create
- `app/events/history.py` — `HistoryService`, `HistoryEntry`, `HistoryPage`
- `tests/test_history_repository.py` — repository-level tests
- `tests/test_history_service.py` — service-level tests
- `tests/test_history_integration.py` — integration tests

### Files to Modify
- `app/events/__init__.py` — export new public API
- `app/api/routes/history.py` — REST endpoints (if API exposure is approved)

### API Endpoints (Proposed)
```
GET /history/tasks/{task_id}
GET /history/plans/{plan_id}
GET /history/executions/{execution_id}
GET /history/correlations/{correlation_id}
GET /history/between?start=...&end=...&event_type=...
GET /history/recent?limit=...
```

**Note:** API endpoint implementation is optional for Stage 11.3. The service layer (`HistoryService`) is the core deliverable. API routes can be added in a follow-up stage.

### Exact Scope Boundaries
- Implement only the `HistoryService` with the 5 methods listed in Section 3.
- Implement `HistoryEntry` and `HistoryPage` models.
- Add repository and service tests.
- Do NOT add API routes unless explicitly requested.
- Do NOT add any new event types.
- Do NOT modify Tier 1 emission points.
- Do NOT add background workers or streaming.
- Do NOT add projections or materialized views.
