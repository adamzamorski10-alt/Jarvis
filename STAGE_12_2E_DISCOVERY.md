# Stage 12.2E — Scheduler Events + History Integration DISCOVERY

## Final Status

**STAGE_12_2E_SEMANTIC_READY**

## 1. Current Architecture

### Scheduler Subsystem (Stages 12.2A–12.2D)
- **Calculator**: Pure function `calculate_next_occurrence(definition, reference_time)` — no side effects
- **Repository**: `SchedulerRepository` — atomic claim, lease, finalization, recovery
- **Handoff**: `SchedulerHandoffService` — delegates to existing execution layer
- **State machine**: `PENDING → CLAIMED → TRIGGERED → COMPLETED/FAILED` (plus `SKIPPED`, `DISABLED`)
- **Occurrence identity**: Deterministic `occurrence_{job_id}_{sha256_digest[:12]}`
- **Claim token**: `secrets.token_hex(32)` — 256-bit CSPRNG

### Event Foundation (Stage 11.2)
- **Emitter**: `EventEmitter.emit()` — validates, bounds, sanitizes, persists
- **Repository**: `EventRepository.record()` — append-only, idempotent by `event_id`, raises `EventIntegrityConflict` on conflicting duplicate
- **History**: `HistoryService` — read-side query layer over EventRepository
- **Transaction**: Events are persisted via `session.add()` + `flush()` within the caller's transaction. No separate transaction boundary.

### Execution Layer
- **Task**: `AutonomousRunController.start_run()` → `TaskRepository.claim_task_attempt()` atomic CAS → `TASK_STARTED` event emitted
- **Plan**: `BackgroundExecutionManager.start_execution()` → in-memory `execution_id` → background task

## 2. Existing Event Foundation Contract

### Event Model
```python
class Event(BaseModel):
    event_id: str                    # evt_{uuid}, primary key, idempotency key
    event_type: EventType            # Enum — must be from allowed vocabulary
    occurred_at: datetime            # UTC, when the action happened
    recorded_at: datetime            # UTC, when persisted
    source: str                      # Component name, max 128 chars
    event_version: str               # Schema version, default "1.0"
    correlation_id: str | None       # Max 128 chars
    causation_id: str | None         # Max 64 chars
    task_id: str | None              # Max 74 chars
    plan_id: str | None              # Max 64 chars
    run_id: str | None               # Max 64 chars
    execution_id: str | None         # Max 64 chars
    actor: str | None                # Max 128 chars
    payload: dict | None             # Max 4KB, sanitized
    metadata: dict | None            # Max 1KB, sanitized
    sensitivity: Sensitivity         # PUBLIC, INTERNAL, SENSITIVE
```

### Event Types (current vocabulary)
- `task.started`, `task.completed`, `task.failed`
- `plan.started`, `plan.completed`, `plan.failed`
- `verification.started`, `verification.passed`, `verification.failed`
- `checkpoint.created`

### Idempotency Mechanism
- `event_id` is the primary key
- Duplicate `event_id` with same content: returns existing record (idempotent)
- Duplicate `event_id` with different content: raises `EventIntegrityConflict`
- This is the ONLY idempotency mechanism — no deterministic event identity

### Correlation/Causation Semantics
- `correlation_id`: Groups related events (e.g., all events in a plan execution)
- `causation_id`: Points to the `event_id` that caused this event
- These are free-form strings, not enforced by the schema

### Source Semantics
- `source`: Component name (e.g., `autonomous_task_orchestrator`, `scheduler`)
- Max 128 chars, validated but not enumerated

### Transaction Behavior
- `EventRepository.record()` calls `session.add()` + `flush()` + `refresh()`
- No separate transaction — participates in caller's transaction
- On `IntegrityError`: rolls back session, fetches existing event, returns it if equivalent

### Failure Behavior
- `EventEmissionError` on generic failure
- `EventPayloadOversizedError` / `EventMetadataOversizedError` on bounds violation
- `EventIntegrityConflict` on conflicting duplicate

## 3. Existing History Capabilities

### HistoryService Queries
| Method | Filter | Notes |
|--------|--------|-------|
| `get_task_history(task_id)` | `task_id` | Verifies task existence first |
| `get_plan_history(plan_id)` | `plan_id` | Direct query, no task verification |
| `get_execution_history(execution_id)` | `execution_id` | Direct query |
| `get_correlation_history(correlation_id)` | `correlation_id` | Direct query |
| `get_history_between(start, end)` | `occurred_at` range | Optional `event_type` filter |
| `get_recent_history()` | None | Most recent by `recorded_at` |

### Missing Filtering Capability
- **No `job_id` filter**: Cannot query all events for a scheduled job
- **No `occurrence_id` filter**: Cannot query all events for a specific occurrence
- **No `source` filter in HistoryService**: `EventRepository.list()` supports `source` filter, but `HistoryService` does not expose it

**Assessment**: `job_id` and `occurrence_id` filters are genuinely required for scheduler observability. The Event model does NOT have `job_id` or `occurrence_id` fields. Adding them would require schema changes. Alternative: use `correlation_id` to group scheduler events for a job, and embed `occurrence_id` in `payload`.

## 4. Proposed Scheduler Event Taxonomy

### Minimal Set (5 events)

| Event Type | Meaning | When Emitted |
|------------|---------|--------------|
| `scheduler.occurrence.claimed` | Durable claim exists | AFTER `claim_next_occurrence` commits |
| `scheduler.occurrence.triggered` | Handoff to execution layer succeeded | AFTER `finalize_triggered` commits |
| `scheduler.occurrence.failed` | Handoff or claim failed | AFTER `finalize_failed` commits |
| `scheduler.occurrence.recovered` | Expired lease reset to PENDING | AFTER `reset_expired_claim_to_pending` commits |
| `scheduler.occurrence.completed` | Occurrence reached terminal state | AFTER scheduler advances `next_run_at` (12.2E) |

### Events NOT Proposed
- `scheduler.occurrence.leased` — lease renewal is an internal optimization, not a lifecycle fact
- `scheduler.occurrence.released` — voluntary release is rare; can be inferred from state
- `scheduler.job.created` — job creation is configuration, not occurrence lifecycle
- `scheduler.job.updated` — too noisy, not lifecycle-critical

### Event Semantics (detailed)

#### `scheduler.occurrence.claimed`
- **Exact meaning**: A durable claim now exists for this occurrence. The occurrence is `CLAIMED` with a valid lease.
- **Producer**: `SchedulerRepository.claim_next_occurrence()` (after commit)
- **Source**: `scheduler`
- **correlation_id**: `job_id` — groups all events for this scheduled job
- **causation_id**: None (or previous occurrence's `event_id` for INTERVAL/CRON chains)
- **job_id**: NOT in Event model — use `payload.job_id`
- **occurrence_id**: NOT in Event model — use `payload.occurrence_id`
- **task_id**: None
- **plan_id**: None
- **execution_id**: None
- **Safe payload**:
  ```json
  {
    "job_id": "sched_abc123",
    "occurrence_id": "occurrence_sched_abc123_def",
    "schedule_type": "cron",
    "schedule_expr": "0 13 * * *",
    "target_type": "task",
    "target_id": "task_xyz"
  }
  ```
- **Sensitivity**: `INTERNAL`
- **Idempotency**: `event_id` is random UUID. To avoid duplicates on retry, the event emitter or repository must accept the same logical event with different `event_id`s, OR the caller must detect and skip duplicate emission. Since `event_id` is the primary key, deterministic event identity is required for idempotent retry.

#### `scheduler.occurrence.triggered`
- **Exact meaning**: Scheduler successfully delegated this occurrence to the execution layer. `execution_id` is recorded in `scheduled_jobs`.
- **Producer**: `SchedulerHandoffService` (after `finalize_triggered` commits)
- **Source**: `scheduler`
- **correlation_id**: `job_id`
- **causation_id**: `scheduler.occurrence.claimed` event_id
- **job_id**: `payload.job_id`
- **occurrence_id**: `payload.occurrence_id`
- **task_id**: `task.target_id` if target_type == 'task'
- **plan_id**: `job.target_id` if target_type == 'plan'
- **execution_id**: `scheduled_jobs.execution_id`
- **Safe payload**:
  ```json
  {
    "job_id": "sched_abc123",
    "occurrence_id": "occurrence_sched_abc123_def",
    "target_type": "task",
    "target_id": "task_xyz",
    "execution_id": "exec_abcdef123456"
  }
  ```
- **Sensitivity**: `INTERNAL`
- **Idempotency**: Must not be emitted twice for same occurrence. `finalize_triggered` is idempotent (only transitions CLAIMED → TRIGGERED once). Event emission must happen AFTER state mutation.

#### `scheduler.occurrence.failed`
- **Exact meaning**: Occurrence failed — either handoff failed or explicit failure finalization.
- **Producer**: `SchedulerHandoffService` or `SchedulerRepository.finalize_failed()`
- **Source**: `scheduler`
- **correlation_id**: `job_id`
- **causation_id**: Previous event in chain
- **job_id**: `payload.job_id`
- **occurrence_id**: `payload.occurrence_id`
- **task_id/plan_id**: As applicable
- **execution_id**: None (failure means no execution)
- **Safe payload**:
  ```json
  {
    "job_id": "sched_abc123",
    "occurrence_id": "occurrence_sched_abc123_def",
    "reason": "Task not startable: terminal status"
  }
  ```
- **Sensitivity**: `INTERNAL`

#### `scheduler.occurrence.recovered`
- **Exact meaning**: Expired lease was reset to PENDING. Occurrence is eligible for re-claim.
- **Producer**: `SchedulerRepository.reset_expired_claim_to_pending()`
- **Source**: `scheduler`
- **correlation_id**: `job_id`
- **causation_id**: None
- **job_id**: `payload.job_id`
- **occurrence_id**: `payload.occurrence_id`
- **Safe payload**:
  ```json
  {
    "job_id": "sched_abc123",
    "occurrence_id": "occurrence_sched_abc123_def",
    "previous_claim_token": "abc123...",
    "reason": "lease_expired"
  }
  ```
- **Sensitivity**: `INTERNAL`
- **Note**: `previous_claim_token` must NOT be included — it is sensitive. Use `reason` only.

#### `scheduler.occurrence.completed`
- **Exact meaning**: Occurrence reached terminal state and scheduler advanced `next_run_at`. This is emitted by 12.2E after the occurrence is COMPLETED, FAILED, or SKIPPED.
- **Producer**: 12.2E advancement logic
- **Source**: `scheduler`
- **correlation_id**: `job_id`
- **causation_id**: `scheduler.occurrence.triggered` or `scheduler.occurrence.failed`
- **job_id**: `payload.job_id`
- **occurrence_id**: `payload.occurrence_id`
- **last_run_at**: `payload.last_run_at`
- **next_run_at**: `payload.next_run_at`
- **Safe payload**:
  ```json
  {
    "job_id": "sched_abc123",
    "occurrence_id": "occurrence_sched_abc123_def",
    "terminal_status": "COMPLETED",
    "last_run_at": "2026-09-20T13:00:00Z",
    "next_run_at": "2026-09-20T14:00:00Z"
  }
  ```
- **Sensitivity**: `INTERNAL`

## 5. State/Event Atomicity Analysis

### Critical Issue: No Atomic State+Event Transaction

The Event Foundation's `EventRepository.record()` uses `session.add()` + `flush()` within the caller's transaction. If the caller's transaction rolls back, the event is also rolled back. This means:

**A. State mutation succeeds, event emission fails**
- If `record()` raises after state mutation, the caller can catch and handle
- If caller doesn't catch, transaction rolls back → state mutation also rolls back
- **Result**: Safe — either both succeed or both fail

**B. Event emission succeeds, state mutation fails**
- `record()` flushes first, then caller's state mutation fails
- If caller rolls back, event is also rolled back
- **Result**: Safe — event is not durable without state mutation

**C. Process crashes between state mutation and event emission**
- If crash happens AFTER state mutation commit but BEFORE event emission: state is durable, event is missing
- If crash happens AFTER event emission commit: both are durable
- **Result**: Observability gap — state may exist without corresponding event

**D. Process crashes between event emission and state mutation**
- `record()` flushes event, then state mutation hasn't committed yet
- If crash happens: both are rolled back
- **Result**: Safe — neither is durable

### Conclusion

The Event Foundation does NOT provide atomic state+event persistence across separate transactions. The current pattern is:

```python
# State mutation
await repo.update_job(...)
await session.commit()

# Event emission (separate transaction boundary in practice)
await event_emitter.emit(...)
```

If event emission happens in a separate transaction or session, a crash between the two leaves state without event.

**Recommendation**: Emit events WITHIN the same transaction as state mutation, OR accept the observability gap and design 12.2E to handle missing events during recovery.

## 6. Idempotency Design

### Problem
The Event Foundation uses random `event_id` as primary key. If event emission is retried after a crash, a new `event_id` is generated, creating a duplicate event.

### Solution: Deterministic Event Identity

For scheduler lifecycle events, derive `event_id` deterministically from the occurrence identity and event type:

```python
event_id = f"sched_evt_{occurrence_id}_{event_type.value}"
```

Examples:
- `sched_evt_occurrence_sched_abc123_def_scheduler.occurrence.claimed`
- `sched_evt_occurrence_sched_abc123_def_scheduler.occurrence.triggered`

This makes event emission idempotent: retrying the same state transition emits the same `event_id`, which `EventRepository.record()` handles as a duplicate.

**Caveat**: Two different occurrences must NOT share the same `event_id`. Since `occurrence_id` is unique per occurrence, this is guaranteed.

**Caveat**: Two different lifecycle events for the same occurrence must NOT share the same `event_id`. Since `event_type` is part of the derivation, this is guaranteed.

### Where to Emit

Events must be emitted AFTER the state mutation commits, within the same transaction if possible:

```python
# In SchedulerRepository or SchedulerHandoffService:
async def claim_next_occurrence(...):
    # 1. State mutation
    result = await _do_claim()
    if result is None:
        return None
    
    # 2. Emit event (same session, will commit together)
    await self._event_emitter.emit(
        event_type=EventType.SCHEDULER_OCCURRENCE_CLAIMED,
        source="scheduler",
        correlation_id=job_id,
        payload={...},
    )
    
    return result
```

## 7. Correlation/Causation Design

### Correlation
- `correlation_id` = `job_id` for all events in a job's lifecycle
- This allows `HistoryService.get_correlation_history(job_id)` to retrieve all scheduler events for a job

### Causation
- `scheduler.occurrence.triggered` → `causation_id` = `scheduler.occurrence.claimed` event_id
- `scheduler.occurrence.completed` → `causation_id` = `scheduler.occurrence.triggered` or `scheduler.occurrence.failed` event_id
- This creates a causal chain: claim → trigger → complete/fail

### Task/Plan Linkage
- For task handoffs: `task_id` = `job.target_id`
- For plan handoffs: `plan_id` = `job.target_id`
- `execution_id` = `scheduled_jobs.execution_id` (durable for tasks, non-durable for plans)

## 8. Security Analysis

### Safe to Persist
- `job_id`, `occurrence_id`, `target_type`, `target_id` — internal identifiers
- `schedule_type`, `schedule_expr` — already in `scheduled_jobs` table
- `execution_id` — already in `scheduled_jobs` table
- `reason` for failures — safe, non-sensitive

### MUST NOT Persist
- `claim_token` — cryptographically sensitive, internal ownership proof
- Full `schedule_expr` if it contains sensitive data — already bounded by `String(200)` in DB
- Any user-provided metadata that might contain secrets — existing sanitizer handles this

### Authorization
- Scheduler events are observational ONLY
- Event/history queries do NOT grant execution permission
- `HistoryService` does not invent authorization

### Payload Bounds
- Scheduler payloads are small (< 200 bytes) — well within 4KB limit
- No secret material in payloads

## 9. Failure Ownership

### Event Emission Fails After State Mutation
- If event emission fails AFTER state mutation commits, the state is durable but the event is missing
- **Owner**: Scheduler worker on next poll can detect state without corresponding event and re-emit
- **Not**: Roll back state mutation — that would break scheduler semantics

### Event Emission Fails Before State Mutation
- If event emission fails BEFORE state mutation, neither is durable
- **Owner**: Caller handles exception, no state change

### Crash Between State Mutation and Event Emission
- State may be durable without event
- **Owner**: 12.2E recovery logic must handle missing events
- **Mitigation**: Emit events within same transaction as state mutation

## 10. History Compatibility

### Automatic Visibility
Scheduler events will automatically become visible through existing `HistoryService` queries:

| Query | Scheduler Events Visible? | Notes |
|-------|--------------------------|-------|
| `get_recent_history()` | YES | All events, including scheduler |
| `get_history_between(start, end)` | YES | Filter by time range |
| `get_correlation_history(correlation_id)` | YES | If `correlation_id` = `job_id` |
| `get_task_history(task_id)` | YES | For task-targeted occurrences |
| `get_plan_history(plan_id)` | YES | For plan-targeted occurrences |
| `get_execution_history(execution_id)` | YES | For occurrences with execution_id |

### No New History Methods Required
Scheduler events will be queryable through existing `HistoryService` without modifications.

### Missing Capability
- No way to query "all events for a scheduled job" without knowing the `correlation_id`
- **Workaround**: Store `job_id` in `payload` and filter client-side, OR set `correlation_id = job_id`

## 11. Test Plan

### Unit Tests
1. Event schema validation for each scheduler event type
2. Payload bounds for scheduler events
3. Sensitivity defaults
4. Deterministic event_id derivation

### Integration Tests
5. Successful claim event emitted after state mutation
6. Successful triggered event emitted after finalize
7. Failed handoff event emitted on failure
8. Recovery event emitted after lease reset
9. Event emission failure does not corrupt state
10. Crash between state mutation and event emission (observability gap test)

### Idempotency Tests
11. Duplicate claim event emission (same occurrence_id + event_type)
12. Retry after event emission failure
13. Conflicting event_id raises EventIntegrityConflict

### History Tests
14. Scheduler events visible in `get_recent_history()`
15. Scheduler events visible in `get_correlation_history(job_id)`
16. Scheduler events visible in `get_task_history(task_id)`
17. Scheduler events visible in `get_plan_history(plan_id)`

### Security Tests
18. claim_token not in event payload
19. No secret leakage in schedule_expr
20. Payload size within bounds

### Concurrency Tests
21. Concurrent claim + event emission
22. Duplicate event during retry

## 12. Implementation Scope

### IN SCOPE
1. Add new `EventType` values: `SCHEDULER_OCCURRENCE_CLAIMED`, `SCHEDULER_OCCURRENCE_TRIGGERED`, `SCHEDULER_OCCURRENCE_FAILED`, `SCHEDULER_OCCURRENCE_RECOVERED`
2. Add event emission to `SchedulerRepository` and `SchedulerHandoffService`
3. Implement hash-based deterministic `event_id` derivation for scheduler events
4. Emit events AFTER state mutation `flush()`, BEFORE caller `commit()`, using the SAME `AsyncSession`
5. Define `reason_code` taxonomy for `scheduler.occurrence.failed`
6. Add focused tests

### DEFERRED (not in 12.2E)
- `scheduler.occurrence.completed` — requires scheduler advancement logic (future stage)

### OUT OF SCOPE
- Scheduler worker/polling loop
- REST API
- Distributed scheduler
- Distributed locks
- New execution engine
- New authorization system
- UI
- Metrics dashboard
- Notification system
- External side effects
- Changes to existing Event/History architecture

## 13. Known Limitations

1. **No atomic state+event transaction across separate sessions**: If event emission uses a different session than state mutation, a crash between the two leaves state without event. Mitigation: use same session.

2. **Event type vocabulary requires Stage approval**: Adding new `EventType` values requires explicit approval per the Event Foundation's design.

3. **No `job_id`/`occurrence_id` in Event model**: These must be embedded in `payload`. This is a workaround, not a schema change.

4. **Deterministic event_id limits flexibility**: If event semantics change, the deterministic `event_id` derivation must also change. This is acceptable for lifecycle events.

5. **Plan execution_id is non-durable**: Same limitation as 12.2D. `scheduler.occurrence.triggered` for plans may reference an `execution_id` that is lost on restart.

## 14. Semantic Verification Gate

### 14.1 Actual Transaction Boundaries

Traced from the actual code, not architecture descriptions.

**Common infrastructure:**
- `get_db()` FastAPI dependency yields an `AsyncSession` and calls `session.commit()` at the end of the request
- `SchedulerRepository` stores the session passed to its constructor
- `EventRepository` stores the session passed to its constructor
- `EventEmitter` stores the `EventRepository` passed to its constructor
- **Neither `SchedulerRepository` nor `EventRepository` calls `commit()` — they only call `flush()`**
- The CALLER is responsible for `commit()`

**Per-transition trace:**

| Transition | State mutation function | flush() | commit() | Event emission | Same session possible? |
|------------|------------------------|---------|----------|----------------|----------------------|
| CLAIMED | `claim_next_occurrence()` → `_do_claim()` | Line 350 | NO — caller | NOT YET IMPLEMENTED | YES |
| TRIGGERED | `finalize_triggered()` | Line 472 | NO — caller | NOT YET IMPLEMENTED | YES |
| FAILED | `finalize_failed()` | Line 507 | NO — caller | NOT YET IMPLEMENTED | YES |
| RECOVERED | `reset_expired_claim_to_pending()` | Line 561 | NO — caller | NOT YET IMPLEMENTED | YES |
| COMPLETED | NOT IMPLEMENTED | N/A | N/A | N/A | N/A |

**Concrete transaction timeline (CLAIMED as example):**

```
1. Caller invokes repo.claim_next_occurrence()
2. Repository executes UPDATE ... RETURNING
3. Repository calls session.flush()  [state is in DB but NOT durable]
4. Repository returns domain object
5. [If event emission added: EventEmitter.emit() → EventRepository.record() → session.add() + flush()]
6. Caller calls session.commit()  [BOTH state and event become durable]
7. [If exception at any point: caller rolls back → NEITHER is durable]
```

**Critical finding:** Atomic state+event persistence IS possible but NOT automatic. It requires:
1. Both `SchedulerRepository` and `EventRepository` (via `EventEmitter`) to receive the SAME `AsyncSession`
2. The caller to commit ONCE after both operations

The current code does NOT prove this pattern is used. The handoff service (`SchedulerHandoffService`) does NOT call `commit()`. No production code currently calls the handoff service. The tests call `repo._session.commit()` explicitly after each operation.

**Conclusion:** Atomic state+event persistence is POSSIBLE but not yet proven in the codebase. It is a CONTRACT REQUIREMENT for implementation, not a current fact.

### 14.2 Crash Matrix

Assumptions: same session, caller commits once after both state mutation and event emission.

| Scenario | Scheduler state | Event state | Duplicate event? | Missing event? | Notes |
|----------|----------------|-------------|------------------|----------------|-------|
| A. state mutation → event emission → commit | Not durable | Not durable | No | No | Both in same transaction |
| B. state mutation → commit → event emission | Durable | Not durable | No | YES | Observability gap if caller commits separately |
| C. event emission → commit → state mutation | Not durable | Durable (then rolled back) | No | No | EventRepository.record() raises on IntegrityError, session rolls back |
| D. crash before commit | Not durable | Not durable | No | No | Database rolls back uncommitted transaction |
| E. crash after commit | Durable | Durable | No | No | Both persisted atomically |

**Key finding:** If the caller uses the pattern `state_mutation → event_emission → commit()` with the SAME session, crash windows A, D, and E are all safe. Window B (observability gap) only occurs if the caller incorrectly commits state mutation before event emission.

**If different sessions are used:** Window B becomes a real observability gap. State is durable but event is missing. The discovery report's mitigation ("use same session") is correct but must be enforced as a contract requirement.

### 14.3 Deterministic Event ID Verification

**Discovery report proposed:** `event_id = f"sched_evt_{occurrence_id}_{event_type}"`

**Defect found:** This format exceeds the `String(64)` primary key limit.

Calculation:
- `occurrence_id` format: `occurrence_{job_id}_{sha256_digest[:12]}`
- `job_id` max length: 64 chars (per `ScheduledJobModel.job_id` `String(64)`)
- `occurrence_id` max length: `10 + 64 + 1 + 12 = 87` chars
- Event type max length: ~28 chars (e.g., `scheduler.occurrence.triggered`)
- Proposed event_id: `sched_evt_` (10) + occurrence_id (87) + `_` (1) + event_type (28) = **~126 chars**

**126 chars > 64 char limit — INVALID.**

**Corrected deterministic event ID scheme:**

```python
import hashlib

def make_scheduler_event_id(occurrence_id: str, event_type: str) -> str:
    """Return a deterministic event_id within String(64) limit."""
    message = f"{occurrence_id}\x00{event_type}"
    digest = hashlib.sha256(message.encode()).hexdigest()[:12]
    return f"sched_evt_{digest}"
```

Properties:
- Length: `sched_evt_` (10) + 12 hex chars = **22 chars** — fits in `String(64)`
- Deterministic: same `(occurrence_id, event_type)` always produces same ID
- Collision-resistant: 12 hex chars = 48 bits. For scheduler scope (< millions of occurrences), birthday-bound collision probability is negligible.
- Idempotent: `EventRepository.record()` will return existing event on retry

**Alternative (shorter prefix):** `evt_sched_{digest[:10]}` = 16 chars. But the `sched_evt_` prefix is clearer.

### 14.4 CLAIMED Event Semantics

**Exact meaning:** "A durable scheduler claim exists for this occurrence."

**Must never be emitted for a rolled-back claim:** Since event emission must happen AFTER `claim_next_occurrence()` and BEFORE `commit()`, a rolled-back claim will also roll back the event (same session). The event is only durable if the claim is durable.

**Observability semantics if atomicity is violated:** If the caller commits the state mutation before emitting the event (incorrect pattern), the event may be missing. This is an observability gap, not a correctness gap — the claim still exists durably.

**Producer:** `SchedulerRepository.claim_next_occurrence()` (after state mutation, before commit)

**Source:** `scheduler`

**correlation_id:** `job_id`

**causation_id:** None (or previous occurrence's event_id for chained occurrences)

**Safe payload:**
```json
{
  "job_id": "sched_abc123",
  "occurrence_id": "occurrence_sched_abc123_def",
  "schedule_type": "cron",
  "schedule_expr": "0 13 * * *",
  "target_type": "task",
  "target_id": "task_xyz"
}
```

**No claim_token.** No secrets.

### 14.5 TRIGGERED Event Semantics

**Exact meaning:** "The scheduler successfully handed this occurrence to the execution layer and recorded a durable execution reference."

**Must NOT mean:**
- Task started successfully (task may reject start)
- Plan completed
- Verification passed
- External side effect succeeded

**Producer:** `SchedulerHandoffService` (after `finalize_triggered()` commits)

**Source:** `scheduler`

**correlation_id:** `job_id`

**causation_id:** `scheduler.occurrence.claimed` event_id

**task_id:** `job.target_id` if target_type == 'task'

**plan_id:** `job.target_id` if target_type == 'plan'

**execution_id:** `scheduled_jobs.execution_id`
- For tasks: durable `task.execution_token`
- For plans: non-durable `BackgroundExecutionManager.execution_id` (in-memory only)

**Safe payload:**
```json
{
  "job_id": "sched_abc123",
  "occurrence_id": "occurrence_sched_abc123_def",
  "target_type": "task",
  "target_id": "task_xyz",
  "execution_id": "exec_abcdef123456"
}
```

**No claim_token.** No secrets.

### 14.6 FAILED Event Semantics

The discovery report proposed a single `scheduler.occurrence.failed` event with a free-form `reason` string. This is insufficient.

**Problem:** Collapsing all failure modes into one event makes history misleading and recovery difficult. Different failure classes have different recovery semantics:
- Invalid claim token → different claimant owns the occurrence
- Lease expired → recovery should reset
- Task terminal → do not retry
- Execution error → retry may succeed

**Corrected taxonomy:** Use a `reason_code` enum plus bounded `reason_detail`.

**reason_code values:**
- `claim_invalid_token` — claim_token mismatch
- `claim_lease_expired` — lease_until <= now
- `claim_wrong_status` — occurrence not in CLAIMED status
- `target_not_found` — task/plan does not exist
- `target_terminal` — task/plan in COMMITTED/CANCELLED
- `target_already_running` — task already RUNNING/VERIFYING/PASSED
- `execution_not_startable` — TaskNotStartableError
- `execution_max_attempts` — MaxAttemptsExceededError
- `execution_stale_state` — StaleStateTransitionError
- `execution_error` — unexpected exception during execution
- `trigger_failed` — finalize_triggered returned None (stale claim after execution succeeded)

**reason_detail:** Max 200 chars, sanitized, no exception stack traces.

**Producer:** `SchedulerHandoffService` or `SchedulerRepository.finalize_failed()`

**Source:** `scheduler`

**correlation_id:** `job_id`

**causation_id:** Previous event in chain (e.g., `scheduler.occurrence.claimed`)

**Safe payload:**
```json
{
  "job_id": "sched_abc123",
  "occurrence_id": "occurrence_sched_abc123_def",
  "reason_code": "execution_not_startable",
  "reason_detail": "Task is in terminal status 'COMMITTED'"
}
```

**No raw exception messages.** No claim_token.

### 14.7 RECOVERED Event Semantics

**Exact meaning:** "An expired scheduler lease was atomically reset to PENDING."

**Must NOT imply:** execution recovery occurred, task was re-run, or plan was restarted.

**Must NOT include:** claim_token, previous claim token, or any sensitive ownership proof.

**Producer:** `SchedulerRepository.reset_expired_claim_to_pending()`

**Source:** `scheduler`

**correlation_id:** `job_id`

**causation_id:** None

**Safe payload:**
```json
{
  "job_id": "sched_abc123",
  "occurrence_id": "occurrence_sched_abc123_def",
  "reason": "lease_expired"
}
```

**Fixed `reason` value:** Only `"lease_expired"`. No free-form reason.

### 14.8 COMPLETED Event Decision

**Recommendation: DEFERRED.**

The current scheduler architecture cannot prove a meaningful completion event. Here is why:

**What the scheduler CAN prove:**
- A claim was made (CLAIMED)
- Handoff was delegated (TRIGGERED)
- Handoff failed (FAILED)
- Lease expired and was recovered (PENDING again)

**What the scheduler CANNOT prove:**
- Task execution completed (the task layer tracks this)
- Plan execution completed (the plan layer tracks this)
- Scheduler occurrence reached a terminal state (no worker exists to set COMPLETED/FAILED/SKIPPED)
- Next occurrence was scheduled (no advancement logic exists)

The discovery report proposed:
> `scheduler.occurrence.completed` — Occurrence reached terminal state and scheduler advanced `next_run_at`. This is emitted by 12.2E after the occurrence is COMPLETED, FAILED, or SKIPPED.

This definition DEFERS to 12.2E, which has not been implemented. The event cannot be emitted today because:
1. No code transitions occurrences to COMPLETED, FAILED, or SKIPPED
2. No code advances `next_run_at` based on terminal execution state
3. The "completion" concept is ambiguous — task completion? plan completion? scheduler occurrence terminal state?

**If 12.2E is implemented later**, the `scheduler.occurrence.completed` event should be emitted AFTER the scheduler:
1. Observes the execution layer's terminal state (task.COMMITTED, plan.COMPLETED, etc.)
2. Advances `next_run_at` using the calculator
3. Sets `last_run_at` to the scheduled fire time
4. Sets `status = COMPLETED` (or FAILED/SKIPPED as appropriate)

At that point, the event means: "this scheduler occurrence reached its terminal state and the next occurrence was scheduled."

**Do not implement `scheduler.occurrence.completed` in 12.2E.** Defer to the stage that implements scheduler advancement.

### 14.9 Correlation/Causation Verification

**correlation_id = job_id:**
- `job_id` is `String(64)` — fits in `correlation_id`'s `String(128)` limit
- `job_id` is stable across the job's lifetime
- `HistoryService.get_correlation_history(job_id)` will return all scheduler events for a job
- No conflict with existing correlation semantics (task/plan events use different correlation_ids)

**causation_id:**
- References actual durable `event_id` values
- If deterministic event_id is used, causation_id references a known stable ID
- Chain: `scheduler.occurrence.claimed` → `scheduler.occurrence.triggered` → `scheduler.occurrence.completed` (when implemented)
- If an event is rolled back, its causation_id target may not exist — this is acceptable because the rolled-back event is also not durable

### 14.10 History Verification

Scheduler events will automatically become visible through existing `HistoryService` queries WITHOUT any modifications:

| HistoryService method | Scheduler events visible? | Filter |
|----------------------|--------------------------|--------|
| `get_recent_history()` | YES | All recent events |
| `get_history_between(start, end)` | YES | Time range filter |
| `get_correlation_history(job_id)` | YES | `correlation_id = job_id` |
| `get_task_history(task_id)` | YES | `task_id = job.target_id` for task-targeted occurrences |
| `get_plan_history(plan_id)` | YES | `plan_id = job.target_id` for plan-targeted occurrences |
| `get_execution_history(execution_id)` | YES | `execution_id = scheduled_jobs.execution_id` |

**No `SchedulerHistoryService` needed.** No `SchedulerEventRepository` needed. No duplicate history storage.

### 14.11 Security Verification

| Requirement | Status | Evidence |
|-------------|--------|----------|
| No claim_token in events | MUST ENFORCE | Event payload schema does not include claim_token field |
| No secrets in payload | MUST ENFORCE | `EventEmitter` sanitizes payload via `redact_payload()` |
| Bounded payload | YES | 4KB limit enforced by `EventEmitter` |
| Bounded failure reason | MUST ADD | `reason_detail` max 200 chars (new requirement) |
| No scheduler event as authorization | YES | `HistoryService` is read-only query layer |
| No untrusted event type | YES | `EventType` enum validation in `EventEmitter` |
| No arbitrary target execution | YES | Scheduler events are observational only |

### 14.12 Implementation-Ready Contract

**Corrected requirements for 12.2E implementation:**

1. **Transaction atomicity:** Event emission MUST use the SAME `AsyncSession` as state mutation. The caller MUST commit once after both operations. This is a contract requirement, not a current fact.

2. **Event ID:** Use hash-based deterministic event_id:
   ```python
   def make_scheduler_event_id(occurrence_id: str, event_type: str) -> str:
       message = f"{occurrence_id}\x00{event_type}"
       digest = hashlib.sha256(message.encode()).hexdigest()[:12]
       return f"sched_evt_{digest}"
   ```

3. **Event types:** Implement only 4 events in 12.2E:
   - `scheduler.occurrence.claimed`
   - `scheduler.occurrence.triggered`
   - `scheduler.occurrence.failed` (with `reason_code` taxonomy)
   - `scheduler.occurrence.recovered`
   
   DEFER `scheduler.occurrence.completed` to the stage that implements scheduler advancement.

4. **Failure taxonomy:** Use `reason_code` enum, not free-form `reason` strings.

5. **Emission point:** AFTER state mutation `flush()`, BEFORE caller `commit()`, within the same session.

6. **Security:** Never include `claim_token` in events. Sanitize all payload fields.

### 14.13 Defects Found in Discovery Report

1. **Event ID length violation:** Proposed deterministic format exceeds `String(64)` primary key limit. Requires hash-based approach.
2. **COMPLETED event premature:** Cannot be defined until scheduler advancement logic exists. Must be deferred.
3. **FAILED event underspecified:** Needs `reason_code` taxonomy, not just free-form `reason`.
4. **Atomicity claimed without proof:** Discovery report stated "atomic state+event persistence" as a fact, but the actual code only provides `flush()`, not `commit()`. Atomicity depends on caller pattern, not repository implementation.

### 14.14 Final Status

**STAGE_12_2E_SEMANTIC_READY**

The Event Foundation is sufficient for scheduler lifecycle integration. No architectural changes are required. The four defects found in the discovery report are correctable without redesign:

1. Use hash-based deterministic event_id (22 chars, fits in `String(64)`)
2. Defer `scheduler.occurrence.completed` to the advancement stage
3. Add `reason_code` taxonomy to `scheduler.occurrence.failed`
4. Document transaction atomicity as a caller contract requirement

Implementation is ready to proceed upon Stage approval with these corrections.

## 15. Final Semantic Correction

### 15.1 Defects Requiring Correction Before Implementation

Two additional defects were identified during the semantic audit of Stage 12.2E. Both must be resolved in the design before implementation proceeds.

#### Defect 1: Insufficient Event ID Hash Length

The Section 14.5 corrected scheme uses only 48 bits of SHA-256 output:

```python
digest = hashlib.sha256(message.encode()).hexdigest()[:12]  # 48 bits
```

48 bits is insufficient for a globally unique deterministic identifier. The birthday bound for 48 bits is ~2^24 (~16 million), which is within the plausible scale of scheduler events over a system lifetime.

**Correction:** Use at least 128 bits (32 hex characters) of SHA-256 output.

```python
def make_scheduler_event_id(occurrence_id: str, claim_sequence: int, event_type: str) -> str:
    message = f"{occurrence_id}\x00{claim_sequence}\x00{event_type}"
    digest = hashlib.sha256(message.encode()).hexdigest()[:32]
    return f"sched_evt_{digest}"
```

**Exact length verification:**
- `EventModel.event_id` column: `String(64)` — maximum 64 characters
- Prefix `sched_evt_`: 10 characters
- Hex digest: 32 characters
- Total: 42 characters
- 42 <= 64: **VALID**

#### Defect 2: Repeated Claims of the Same Occurrence

The Section 14.4 event identity `(occurrence_id, event_type)` cannot distinguish multiple `CLAIMED` events for the same occurrence across lease recovery cycles.

**Lifecycle trace showing the collision:**

```
Attempt 1:
  PENDING → CLAIMED  (occurrence_id = X, claim_sequence = 1)
  event_id = hash(X, 1, "scheduler.occurrence.claimed")  ← first CLAIMED event

  lease expires
  RECOVERED           (occurrence_id = X, claim_sequence = 1)
  event_id = hash(X, 1, "scheduler.occurrence.recovered")  ← recovery event

Attempt 2 (same scheduled firing):
  PENDING → CLAIMED  (occurrence_id = X, claim_sequence = 2)
  event_id = hash(X, 2, "scheduler.occurrence.claimed")  ← second CLAIMED event
```

In the current implementation, `reset_expired_claim_to_pending` clears `occurrence_id` to `None`. This destroys the traceability of the original occurrence. The corrected design preserves `occurrence_id` across recovery and introduces `claim_sequence` to distinguish multiple claims.

**Root cause:** `ScheduledJobModel` has no durable claim generation counter. The `claim_token` is a random secret and MUST NOT be used for event identity. There is no existing `claim_attempt` or `claim_sequence` field in the scheduler schema.

### 15.2 Claim-Generation Solution

#### Smallest Correct Design

Add a single integer column `claim_sequence` to `ScheduledJobModel`:

```python
claim_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
```

**Properties:**
- Durable: stored in the `scheduled_jobs` table
- Non-secret: monotonic integer, safe to persist and embed in event identity
- Changes for every new claim: atomically incremented with each successful claim
- Survives process restart: stored in database
- Assigned atomically with the claim: incremented in the same SQL UPDATE that transitions status to CLAIMED
- Does not expose `claim_token`: integer only
- Allows deterministic event identity: included in the hash input
- Preserves retry idempotency: if event emission fails and is retried, the same `claim_sequence` is read from the durable row

#### SQL Pattern

```sql
UPDATE scheduled_jobs
SET
    status = 'CLAIMED',
    occurrence_id = :occurrence_id,
    claim_token = :claim_token,
    claimed_at = :now,
    lease_until = :lease_until,
    claim_sequence = claim_sequence + 1,
    updated_at = :now
WHERE
    job_id = :job_id
    AND status = 'PENDING'
    AND next_run_at <= :now
    AND enabled = 1
RETURNING *
```

The `claim_sequence = claim_sequence + 1` is evaluated atomically by SQLite. Concurrent claims of the same job are serialized: only one UPDATE will match `status = 'PENDING'`.

#### Recovery Adjustment

`reset_expired_claim_to_pending` MUST NOT clear `occurrence_id`. It MUST preserve both `occurrence_id` and `claim_sequence` so the event chain remains traceable:

```sql
UPDATE scheduled_jobs
SET
    status = 'PENDING',
    occurrence_id = :occurrence_id,   -- PRESERVED, not cleared
    claim_token = NULL,
    claimed_at = NULL,
    lease_until = NULL,
    next_run_at = :next_run_at,
    updated_at = :now
WHERE
    occurrence_id = :occurrence_id
    AND status = 'CLAIMED'
    AND lease_until <= :now
RETURNING *
```

### 15.3 Event Identity Contract

| Event     | Can occur multiple times per occurrence? | Logical identity (deterministic hash input) | event_id format |
| --------- | ---------------------------------------- | ------------------------------------------- | --------------- |
| CLAIMED   | Yes — once per claim, and the same occurrence can be claimed multiple times after recovery | `occurrence_id + claim_sequence + "scheduler.occurrence.claimed"` | `sched_evt_` + sha256(input).hexdigest()[:32] |
| TRIGGERED | No — once per successful claim | `occurrence_id + claim_sequence + "scheduler.occurrence.triggered"` | `sched_evt_` + sha256(input).hexdigest()[:32] |
| FAILED    | No — once per failed claim | `occurrence_id + claim_sequence + "scheduler.occurrence.failed"` | `sched_evt_` + sha256(input).hexdigest()[:32] |
| RECOVERED | No — once per expired lease | `occurrence_id + claim_sequence + "scheduler.occurrence.recovered"` | `sched_evt_` + sha256(input).hexdigest()[:32] |

**Idempotency explanation:**

*Retrying the same state transition* (e.g., event emission fails after `claim_next_occurrence` commits, and the caller retries):
- The scheduler state mutation has already committed, so `claim_sequence` is durable in the database.
- The retry reads the same `occurrence_id` and `claim_sequence` from the database row.
- The same hash input produces the same `event_id`.
- `EventRepository.record()` finds the existing `event_id` and returns it idempotently.

*Genuinely new lifecycle occurrence* (e.g., same occurrence claimed again after lease recovery):
- The new claim increments `claim_sequence` atomically.
- The hash input includes the new `claim_sequence`, producing a different `event_id`.
- The new event is persisted as a distinct record.

### 15.4 Transaction-Boundary Solution

#### Requirement

The invariant is:

```
state mutation → event emission → single commit
```

using the SAME `AsyncSession`.

#### Narrowest Enforcement Point

The contract must be enforced at the application-owned handoff boundary, not by creating a new transaction abstraction. The specific places are:

1. `SchedulerRepository.claim_next_occurrence()` — emits `scheduler.occurrence.claimed` after `flush()`, before returning
2. `SchedulerRepository.finalize_triggered()` — emits `scheduler.occurrence.triggered` after `flush()`, before returning
3. `SchedulerRepository.finalize_failed()` — emits `scheduler.occurrence.failed` after `flush()`, before returning
4. `SchedulerRepository.reset_expired_claim_to_pending()` — emits `scheduler.occurrence.recovered` after `flush()`, before returning

In each case, the repository method receives an `AsyncSession`, performs the state mutation, calls `flush()`, emits the event through the same session, and returns. The CALLER is responsible for `commit()`.

#### Why the Caller Commits

The caller (FastAPI dependency or test harness) already owns the transaction boundary. `get_db()` yields the session and calls `session.commit()` at the end of the request. This is the existing pattern and MUST NOT be changed.

### 15.5 `EventRepository.record()` Rollback Interaction

#### Critical Bug

`EventRepository.record()` calls `session.rollback()` on `IntegrityError`:

```python
except IntegrityError as exc:
    await self._session.rollback()  # rolls back ENTIRE session
    existing = await self.get(event.event_id)
    ...
```

When `EventRepository` shares a session with `SchedulerRepository`, a duplicate `event_id` (idempotent retry) triggers this rollback. The rollback undoes ALL uncommitted changes in the session, including scheduler state mutations that were flushed earlier in the same transaction.

**Concrete failure scenario:**

```python
# 1. Scheduler state mutation (flushed)
await repo.claim_next_occurrence(...)  # flush() called internally
await event_emitter.emit(...)          # tries to insert duplicate event_id

# 2. EventRepository.record() hits IntegrityError
# 3. EventRepository calls session.rollback()
# 4. SCHEDULER STATE MUTATION IS ALSO ROLLED BACK
# 5. Caller calls session.commit()
# 6. Neither state nor event is durable
```

#### Required Fix (Prerequisite)

`EventRepository.record()` MUST use a SAVEPOINT to isolate the event insertion:

```python
await self._session.begin_nested()  # create savepoint
try:
    self._session.add(model)
    await self._session.flush()
    await self._session.refresh(model)
except IntegrityError as exc:
    await self._session.rollback()  # rolls back to savepoint only
    existing = await self.get(event.event_id)
    if existing is not None and _events_equivalent(existing, event):
        return existing
    raise EventIntegrityConflict(...) from exc
```

The `begin_nested()` / `rollback()` pair limits the rollback scope to the savepoint. Caller state mutations flushed before the savepoint remain intact.

**Status:** This is a prerequisite fix. It does not block the semantic readiness of Stage 12.2E, but it MUST be resolved before scheduler event emission is implemented.

### 15.6 Repeated-Claim Lifecycle Analysis

#### Full Lifecycle Trace

```
Time T0: Job X is PENDING, next_run_at = 13:00
Time T1: Worker A claims job X
  - claim_sequence: 0 → 1
  - occurrence_id: "occurrence_X_abc" (preserved from generation)
  - status: PENDING → CLAIMED
  - Event: scheduler.occurrence.claimed (claim_sequence=1)

Time T2: Worker A crashes before finalize_triggered
Time T3: Lease expires (lease_until < now)
Time T4: Recovery worker resets job X
  - claim_sequence: 1 (preserved)
  - occurrence_id: "occurrence_X_abc" (preserved, NOT cleared)
  - status: CLAIMED → PENDING
  - Event: scheduler.occurrence.recovered (claim_sequence=1)

Time T5: Worker B claims job X again
  - claim_sequence: 1 → 2
  - occurrence_id: "occurrence_X_abc" (same occurrence, same scheduled firing)
  - status: PENDING → CLAIMED
  - Event: scheduler.occurrence.claimed (claim_sequence=2)  ← DIFFERENT from T1

Time T6: Worker B finalizes triggered
  - claim_sequence: 2
  - status: CLAIMED → TRIGGERED
  - Event: scheduler.occurrence.triggered (claim_sequence=2)
```

**Key property:** Each CLAIMED event has a unique `(occurrence_id, claim_sequence)` pair. The second claim produces a different `event_id` from the first claim because `claim_sequence` differs.

#### Why Retry Idempotency Is Preserved

If event emission at Time T1 fails and is retried:
- The claim at T1 has already committed `claim_sequence = 1`
- The retry reads `claim_sequence = 1` from the database
- The same hash input produces the same `event_id`
- `EventRepository.record()` returns the existing record

If the claim at T1 itself is retried before commit:
- The UPDATE WHERE clause requires `status = 'PENDING'`
- After the first attempt, `status = 'CLAIMED'`
- The second attempt matches zero rows and returns `None`
- No state mutation, no event emission

### 15.7 Concurrency Implications

#### Claim Concurrency

Multiple workers may attempt to claim the same job simultaneously. The SQL UPDATE is atomic:

```sql
WHERE status = 'PENDING' AND next_run_at <= :now AND enabled = 1
```

Only one worker's UPDATE will match. The loser sees `rowcount = 0` and returns `None`. `claim_sequence` is incremented only for the winner.

#### Recovery Concurrency

Multiple workers may attempt to recover the same expired lease. The SQL UPDATE is atomic:

```sql
WHERE occurrence_id = :occurrence_id AND status = 'CLAIMED' AND lease_until <= :now
```

Only one worker's UPDATE will match. The loser sees `rowcount = 0` and returns `None`.

#### Event Emission Concurrency

Two workers may attempt to emit the same event (e.g., both retry after a crash). The database primary key on `event_id` ensures only one copy exists. The loser receives `IntegrityError`, which is handled as an idempotent duplicate.

### 15.8 Security Implications

#### Safe to Persist

- `claim_sequence` — monotonic integer, non-secret, safe in event identity and payloads
- `occurrence_id` — deterministic identifier, safe in event identity and payloads
- `job_id`, `schedule_type`, `target_type`, `target_id` — already in `scheduled_jobs` table
- `reason_code` — bounded enum, safe

#### MUST NOT Persist

- `claim_token` — cryptographically sensitive ownership proof. Must NOT appear in event payloads, event identity, or logs.
- `execution_id` for plans — in-memory only, non-durable. Including it in events is acceptable but must be documented as potentially stale after restart.

#### Hash Security

The deterministic `event_id` is a one-way SHA-256 hash truncated to 128 bits. An attacker who sees `sched_evt_abcdef123456...` cannot reverse-engineer the `occurrence_id`, `claim_sequence`, or `event_type`. The hash does not leak scheduler state.

#### What Is NOT Protected

- Event payloads are not encrypted. `scheduler.occurrence.claimed` payload includes `job_id`, `occurrence_id`, `schedule_expr`, and `target_id`. These are internal identifiers and are safe at `INTERNAL` sensitivity.
- `HistoryService` queries are not authorization-gated. Any principal with event read access can see scheduler event history. This is acceptable because scheduler events are observational only and do not grant execution permission.

### 15.9 Prerequisites for Implementation

The following changes must be completed BEFORE Stage 12.2E implementation:

1. **Add `claim_sequence` column** to `ScheduledJobModel` and `scheduled_jobs` table schema (migration).
2. **Preserve `occurrence_id` in recovery** — `reset_expired_claim_to_pending` must not clear `occurrence_id`.
3. **Fix `EventRepository.record()` rollback** — replace unconditional `session.rollback()` with savepoint-scoped rollback.
4. **Add `SCHEDULER_OCCURRENCE_CLAIMED`, `SCHEDULER_OCCURRENCE_TRIGGERED`, `SCHEDULER_OCCURRENCE_FAILED`, `SCHEDULER_OCCURRENCE_RECOVERED`** to `EventType` enum.

### 15.10 Final Status

**STAGE_12_2E_SEMANTIC_READY**

All semantic defects in the original discovery report have been identified and corrected:

1. **Event ID hash length:** Increased from 48 bits to 128 bits (32 hex chars). Total event_id length = 42 chars, within `String(64)` limit.
2. **Repeated claim identity:** Introduced `claim_sequence` integer column, incremented atomically with each claim. Event identity now includes `claim_sequence`, making each CLAIMED event uniquely identifiable.
3. **Occurrence stability:** `occurrence_id` is preserved across lease recovery, maintaining traceability of the same scheduled firing.
4. **Transaction safety:** Identified the `EventRepository.record()` rollback bug and documented the savepoint fix as a prerequisite.
5. **Event identity contract:** Produced explicit table with deterministic hash inputs for all four lifecycle events.
6. **Idempotency:** Retry of the same state transition produces the same event_id. New claims produce different event_ids.
7. **Concurrency:** Atomic SQL UPDATE patterns prevent race conditions in claim, recovery, and event emission.
8. **Security:** `claim_sequence` is non-secret. No `claim_token` in events. Deterministic event_id does not leak state.

No architectural changes are required. Implementation may proceed after the four prerequisites are completed.
