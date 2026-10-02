# Stage 13.3 — Event → Notification Policy Discovery

## 1. Current Architecture Findings

### Event Foundation

**Event model** (`app/events/schemas.py`):
- Immutable Pydantic model (`frozen=True`)
- Fields: `event_id`, `event_type`, `occurred_at`, `recorded_at`, `source`, `event_version`, `correlation_id`, `causation_id`, `task_id`, `plan_id`, `run_id`, `execution_id`, `actor`, `payload`, `metadata`, `sensitivity`
- `payload`: bounded 4KB JSON, sanitized by `redact_payload()`
- `metadata`: bounded 1KB JSON, sanitized by `redact_metadata()`
- Timestamps: UTC-aware, validated by `_validate_utc_datetime()`

**EventRepository** (`app/events/repository.py`):
- Append-only event store
- Idempotency: `event_id` primary key, savepoint-based insertion
- On duplicate `event_id`: returns existing record if content matches (`_events_equivalent()`), else raises `EventIntegrityConflict`
- Ordering: default `occurred_at ASC, event_id ASC`; `recent_first` uses `recorded_at DESC, event_id DESC`
- `delete_before(timestamp)`: retention-based deletion

**EventEmitter** (`app/events/emitter.py`):
- Application-facing write boundary
- Validates, bounds, sanitizes, timestamps, and persists events
- Raises `EventPayloadOversizedError` / `EventMetadataOversizedError` for size violations
- Does NOT modify permissions, approvals, task state, or Git authority

**Sanitizer** (`app/events/sanitizer.py`):
- Regex-based redaction of common credential patterns
- Target patterns: bearer tokens, API keys, access tokens, AWS keys, private keys, passwords, client secrets, generic secrets
- NOT exhaustive (documented limitation)

### Notification Foundation

**Notification model** (`app/notifications/models.py`):
- Mutable Pydantic model
- Immutable fields: `notification_id`, `source_event_id`, `notification_type`, `created_at`
- Mutable fields: `updated_at`, `read_at`, `severity`, `title`, `body`, `metadata`, `expires_at`
- Validation bounds:
  - `title`: max_length=200 (characters)
  - `body`: max_length=2000 or None
  - `metadata`: max 1024 bytes serialized JSON
  - `source_event_id`: max_length=64
  - `notification_type`: must be valid `NotificationType`
- Timestamps: UTC-aware

**NotificationRepository** (`app/notifications/repository.py`):
- Savepoint-based idempotency via `UNIQUE(source_event_id, notification_type)`
- Returns existing record for any duplicate key match
- Deterministic ordering: `created_at DESC, notification_id DESC`
- `mark_read`, `mark_all_read`, `delete_expired` operations
- `mark_read` uses `UPDATE ... RETURNING` for atomicity

**NotificationType enum**:
- `task.started`, `task.completed`, `task.failed`
- `plan.completed`, `plan.failed`
- `verification.failed`
- `scheduler.occurrence.failed`, `scheduler.occurrence.recovered`
- `approval.requested`
- `recovery.completed`
- `system.error`

### Event Producer Inventory

| Component | EventTypes Emitted | Notes |
|-----------|-------------------|-------|
| AutonomousTaskOrchestrator | `task.started`, `task.failed`, `verification.started`, `verification.failed`, `verification.passed`, `checkpoint.created`, `task.completed` | Primary task lifecycle emitter |
| AutonomousPlanExecutor | `plan.started`, `plan.completed`, `plan.failed` | Plan execution lifecycle |
| SchedulerRepository | `scheduler.occurrence.claimed`, `scheduler.occurrence.triggered`, `scheduler.occurrence.failed`, `scheduler.occurrence.recovered`, `scheduler.occurrence.skipped` | Scheduler occurrence lifecycle |
| RecoveryManager | None | No event emitter configured |
| ExecutionBridge | None | No event emitter configured |
| ApprovalService | AuditEventType only | Separate audit event system |
| WorkspaceManager | None | Stores emitter but never emits |
| PermissionEngine | AuditEventType only | Separate audit event system |

## 2. Responsibility Boundary

```
Durable Event (EventRepository)
    ↓
NotificationPolicy (new)
    ↓
Notification creation (NotificationRepository)
    ↓
Durable Notification
```

**Policy responsibilities:**
- Decide whether an Event deserves a Notification
- Determine notification type, severity, title, body
- Extract relevant references from Event
- Validate and bound notification content
- Persist via NotificationRepository

**Policy must NOT:**
- Execute tools
- Change task state
- Approve anything
- Bypass PermissionEngine
- Bypass ApprovalService
- Bypass VerificationGate
- Trigger scheduler execution
- Perform external delivery
- Mutate the source Event
- Become a second Event system

Notification generation is a side effect of processing an already durable Event.

## 3. Event → Notification Mapping

| EventType | NotificationType | Severity | Classification | Rationale |
|-----------|-----------------|----------|----------------|-----------|
| `task.started` | — | — | NO_NOTIFICATION | Noisy background activity; every task start would notify |
| `task.completed` | `task.completed` | INFO | NOTIFICATION_REQUIRED | User-facing success signal |
| `task.failed` | `task.failed` | ERROR | NOTIFICATION_REQUIRED | User-facing failure signal |
| `plan.started` | — | — | NO_NOTIFICATION | Noisy background activity |
| `plan.completed` | `plan.completed` | INFO | NOTIFICATION_REQUIRED | User-facing success signal |
| `plan.failed` | `plan.failed` | ERROR | NOTIFICATION_REQUIRED | User-facing failure signal |
| `verification.started` | — | — | NO_NOTIFICATION | Noisy background activity |
| `verification.passed` | — | — | NO_NOTIFICATION | Background activity; `task.completed` covers success |
| `verification.failed` | `verification.failed` | ERROR | NOTIFICATION_OPTIONAL | Diagnostic; `task.failed` already emitted for same failure; defer to V2 |
| `checkpoint.created` | — | — | NO_NOTIFICATION | Background activity |
| `scheduler.occurrence.claimed` | — | — | NO_NOTIFICATION | Noisy internal scheduler state |
| `scheduler.occurrence.triggered` | — | — | NO_NOTIFICATION | Noisy internal scheduler state |
| `scheduler.occurrence.failed` | `scheduler.occurrence.failed` | ERROR | NOTIFICATION_REQUIRED | User-facing scheduler failure |
| `scheduler.occurrence.recovered` | `scheduler.occurrence.recovered` | WARNING | NOTIFICATION_REQUIRED | User-facing recovery from failure |
| `scheduler.occurrence.skipped` | — | — | NO_NOTIFICATION | Concurrency optimization, not user-relevant |

**V1 scope:** Only NOTIFICATION_REQUIRED events produce notifications. NOTIFICATION_OPTIONAL events are logged but do not create notifications.

## 4. Notification Content

For each NOTIFICATION_REQUIRED EventType, define deterministic content:

### `task.completed`
- **title:** `"Task {task_id} completed"`
- **body:** `"Task {task_id} completed successfully in plan {plan_id}."`
- **severity:** INFO
- **references:** `task_id`, `plan_id`
- **metadata:** `{"commit_sha": "...", "attempt_count": N}` if available from event payload
- **expires_at:** None

### `task.failed`
- **title:** `"Task {task_id} failed"`
- **body:** `"Task {task_id} failed in plan {plan_id}: {reason}"`
- **severity:** ERROR
- **references:** `task_id`, `plan_id`
- **metadata:** `{"attempt_count": N, "error_message": "..."}` if available
- **expires_at:** None

### `plan.completed`
- **title:** `"Plan {plan_id} completed"`
- **body:** `"Plan {plan_id} completed successfully with {completed_count}/{total_count} tasks."`
- **severity:** INFO
- **references:** `plan_id`
- **metadata:** `{"completed_tasks_count": N, "total_tasks_count": M}`
- **expires_at:** None

### `plan.failed`
- **title:** `"Plan {plan_id} failed"`
- **body:** `"Plan {plan_id} failed: {reason}"`
- **severity:** ERROR
- **references:** `plan_id`
- **metadata:** `{"reason": "...", "completed_tasks_count": N, "total_tasks_count": M}`
- **expires_at:** None

### `scheduler.occurrence.failed`
- **title:** `"Scheduler occurrence failed"`
- **body:** `"Scheduled job {job_id} (occurrence {occurrence_id}) failed: {reason_code} — {reason_detail}"`
- **severity:** ERROR
- **references:** `execution_id`, `job_id`
- **metadata:** `{"job_id": "...", "occurrence_id": "...", "reason_code": "...", "reason_detail": "..."}`
- **expires_at:** None

### `scheduler.occurrence.recovered`
- **title:** `"Scheduler occurrence recovered"`
- **body:** `"Scheduled job {job_id} (occurrence {occurrence_id}) recovered after lease expiration."`
- **severity:** WARNING
- **references:** `execution_id`, `job_id`
- **metadata:** `{"job_id": "...", "occurrence_id": "...", "reason": "lease_expired"}`
- **expires_at:** None

**Content rules:**
- No Event payload is copied verbatim into Notification content
- Event payload fields are selectively extracted and bounded
- `reason`, `reason_detail` truncated to 200 chars
- All content is application-owned, deterministic, and inert
- No LLM involvement in V1

## 5. Event Payload Handling

### Safe fields to use

| Field | Source | Usage |
|-------|--------|-------|
| `event_id` | Event | `source_event_id` on Notification |
| `event_type` | Event | Maps to `notification_type` |
| `task_id` | Event | Reference on Notification |
| `plan_id` | Event | Reference on Notification |
| `execution_id` | Event | Reference on Notification |
| `occurred_at` | Event | `created_at` on Notification |
| `actor` | Event | Ignored (system actor is implicit) |

### Fields to ignore

- `payload` entire dict: treated as opaque data, never copied to notification content
- `metadata` entire dict: not copied
- `sensitivity`: not propagated
- `correlation_id`: not propagated (notification uses `source_event_id` for correlation)
- `causation_id`: not propagated
- `source`: used for audit trail but not notification content
- `event_version`: not propagated

### Sanitization rules

- All string fields from Event are bounded by Event validation before reaching the policy
- Policy truncates `reason`, `reason_detail` to 200 characters
- No new sanitization needed because policy constructs notification content from bounded primitives
- If Event payload contains suspicious text, paths, commands, or prompts, it remains inert data in the Event store

### Malformed/missing fields

- Missing `task_id` → omit from notification, use `"unknown"` in title/body
- Missing `plan_id` → omit from notification
- Missing `reason` → use generic fallback `"Task failed"`
- Missing `reason_detail` → omit from body
- Policy must never crash on malformed Event payload
- Policy logs warnings for unexpected payload shapes but continues

## 6. Idempotency

### Design

Stage 13.2 provides:

```
UNIQUE(source_event_id, notification_type)
```

This is sufficient for Stage 13.3 because:

1. **Deterministic mapping**: Same Event + same policy version → same `(source_event_id, notification_type)` pair
2. **At-most-once creation**: Unique constraint prevents duplicates
3. **Idempotent retrieval**: Repository returns existing record on duplicate key
4. **Replay safe**: Re-emitting the same Event produces the same Notification

### Cases

| Case | Description | Behavior |
|------|-------------|----------|
| A | Same Event re-emitted | Idempotent: returns existing Notification |
| B | Different Event, same source_event_id + type | Not possible: source_event_id = event_id, different Event → different event_id |
| C | Same event_id, different content | Event layer raises EventIntegrityConflict before policy runs |
| D | Concurrent processing | One session wins INSERT; other hits unique constraint, returns existing |
| E | Transaction retry | Same as A: re-emit → idempotent |
| F | Policy crash after Event committed | Event exists, Notification not created; on replay, policy creates Notification |

### Policy versioning

**Deferred to V2.** V1 uses a single deterministic policy. If policy logic changes later, a `policy_version` field or new `notification_type` suffix can be added. For V1, assume policy is immutable.

## 7. Transaction Boundary

### Recommended: Option B (same transaction)

**Event and Notification are written in the same transaction.**

Rationale:
- Existing architecture already commits Event via `session.begin_nested()` in `EventRepository.record()`
- Notification policy runs within the same session/transaction as Event persistence
- If Event commit succeeds but Notification fails, the entire transaction can be committed (Event is durable, Notification gap is handled by replay)
- If Notification creation fails, Event is still persisted; policy failure is logged, not fatal

### Crash semantics

| Scenario | State | Recovery |
|----------|-------|----------|
| Event exists, Notification not yet created | Event committed, Notification missing | Replay/re-run policy on existing Event |
| Notification created, processing crashes | Both committed | No recovery needed |
| Transaction rollback | Neither committed | Caller retries; Event and Notification both re-attempted |
| Policy failure after Event commit | Event committed, Notification missing | Logged; manual/replay recovery |

### Implementation

```python
async with session.begin_nested():
    event = await event_repo.record(event)
    notification = await notification_policy.process(event)
    await notification_repo.create(notification)
```

If policy raises an exception:
- Savepoint rolls back Notification creation
- Event remains persisted (savepoint only covers the nested transaction)
- Exception propagates to caller for logging

## 8. Failure Semantics

| Failure | Classification | Behavior |
|---------|---------------|----------|
| Event type not in mapping | Expected "no notification" | Return None; no Notification created |
| NotificationRepository failure | Infrastructure failure | Log error; Event remains durable; caller may retry |
| DB locked | Infrastructure failure | SQLAlchemy raises; caller retries |
| Malformed Event payload | Malformed event | Policy uses safe fallbacks; never crashes |
| Missing required Event fields | Malformed event | Policy uses safe fallbacks ("unknown", generic messages) |
| Notification already exists | Idempotent | Return existing Notification |
| Event payload contains suspicious content | Inert data | Policy ignores payload content for notification text |
| Policy version mismatch | Future concern | Deferred to V2 |

**Key invariant:** A single Notification failure must NOT corrupt Event history or cause Event processing to fail.

## 9. Processing Model

### Recommended: Synchronous after Event persistence

```
1. Producer creates Event
2. EventRepository.record() persists Event (savepoint-based)
3. NotificationPolicy.process(event) runs synchronously
4. NotificationRepository.create() persists Notification (savepoint-based)
5. Both committed in same transaction
```

**Advantages:**
- Simplest architecture
- Strong consistency: Event and Notification are created together
- No separate worker needed
- No outbox infrastructure
- Crash recovery via Event replay

**Disadvantages:**
- Event processing latency includes Notification creation
- If NotificationRepository is down, Event processing still succeeds (Notification gap handled by replay)

**Rejected alternatives:**
- **Polling worker**: Adds complexity, eventual consistency, ordering concerns
- **Outbox**: Unnecessary for V1; adds distributed messaging overhead
- **Hybrid**: Adds complexity without clear benefit for V1

## 10. Replay / Backfill

### V1 scope

- Replay processes one Event at a time
- Replay only processes Events that do NOT already have a Notification for their `(source_event_id, notification_type)` pair
- No automatic backfill for historical Events
- No batch replay UI/API in V1

### Deferred

- Replay UI/API
- Backfill historical notifications
- Batch processing
- Replay filtering by date range / event type

**Rationale:** V1 focuses on forward notification creation. Historical backfill can be added later if needed.

## 11. Event Ordering

### Requirements

- Notifications are created from Events; ordering of Notifications follows Event `occurred_at`
- No global ordering guarantee needed
- Concurrent Event processing is safe due to idempotency

### Ordering rules

- Notification `created_at` = Event `occurred_at` (normalized to UTC)
- Notification `updated_at` = same as `created_at` initially
- `list()` returns `created_at DESC, notification_id DESC` (already defined in NotificationRepository)

### Edge cases

- `task.failed` may arrive after `task.completed` in out-of-order processing
- Policy only creates Notification for the terminal state event
- `verification.failed` arriving before `task.failed`: policy creates Notification for `task.failed` when it arrives
- Duplicate/replayed events: idempotency prevents duplicate Notifications

## 12. Re-entrancy / Loop Protection

### Proof

```
Event → NotificationPolicy → Notification → (NO Event emission)
```

The policy MUST NOT emit Events. This is enforced by:

1. **Architectural constraint**: Policy is a pure function of Event → Notification
2. **Code review**: Policy has no EventEmitter dependency
3. **Test**: Verify policy does not call `emit()` on any EventEmitter

### Natural prevention

- Notification creation does not trigger Event emission
- No component currently listens for Notifications and emits Events
- HistoryService reads Events; it does not write Events
- No circular dependency between Notification and Event systems

### Concurrent policy processing

- Concurrent Event processing is safe due to unique constraint on `(source_event_id, notification_type)`
- Two concurrent policies processing the same Event:
  - One wins the INSERT
  - Other hits unique constraint, returns existing Notification
  - No duplicate Notifications created

## 13. Scheduler Interaction

### Analysis

| Scheduler Event | Notification | Rationale |
|-----------------|-------------|-----------|
| `scheduler.occurrence.claimed` | NO_NOTIFICATION | Internal state; noisy |
| `scheduler.occurrence.triggered` | NO_NOTIFICATION | Internal state; noisy |
| `scheduler.occurrence.failed` | `scheduler.occurrence.failed` (ERROR) | User-facing failure |
| `scheduler.occurrence.recovered` | `scheduler.occurrence.recovered` (WARNING) | User-facing recovery |
| `scheduler.occurrence.skipped` | NO_NOTIFICATION | Concurrency optimization; not user-relevant |

### Implementation notes

- Scheduler events carry `job_id`, `occurrence_id`, `execution_id`
- Notification references `execution_id` and `job_id`
- `reason_detail` truncated to 200 chars in payload
- No scheduler-specific notification logic outside the generic policy

## 14. Approvals / Recovery / Execution

### Analysis

| EventType | NotificationType | Rationale |
|-----------|-----------------|-----------|
| `task.failed` | `task.failed` | User attention required |
| `verification.failed` | OPTIONAL | Diagnostic; `task.failed` already covers the failure |
| `plan.failed` | `plan.failed` | User attention required |
| `approval.requested` | `approval.requested` (future) | ApprovalService uses AuditEventType, not EventType; defer to V2 |
| `recovery.completed` | `recovery.completed` (future) | RecoveryManager does not emit EventType events; defer to V2 |
| `system.error` | `system.error` (future) | No producer currently emits this; defer to V2 |

**Key findings:**
- `approval.requested`, `recovery.completed`, `system.error` have NotificationType entries but NO current Event producers
- These are future signals; the NotificationType enum already anticipates them
- No notification policy executes or approves anything; it only creates passive Notification records

## 15. Notification Flooding / Deduplication

### V1 policy

- **At-most-one-notification per `(source_event_id, notification_type)`**
- No rate limiting
- No aggregation
- No suppression
- No burst collapsing
- No repeated-event throttling

### Rationale

- V1 is deliberately selective (only 6 EventTypes produce notifications)
- Unique constraint provides natural deduplication
- Replayed Events produce the same Notification (idempotent)
- Flooding risk is low given selective mapping

### Future options (V2+)

- Time-window suppression for repeated failures
- Aggregation of multiple failures into a single digest
- User-configurable notification preferences
- Rate limiting per user/entity

## 16. Security / Prompt Injection

### Threat analysis

**Attack surface:** Event payload → Notification content

### Protections

1. **No payload verbatim copy**: Notification content is constructed from bounded primitives, not Event payload
2. **No LLM involvement**: V1 policy is deterministic, no model-generated text
3. **No execution authority**: Notification has no `execute`, `run`, `invoke` methods
4. **No PermissionEngine bypass**: Policy does not interact with authorization
5. **No ApprovalService bypass**: Policy does not interact with approvals
6. **No VerificationGate bypass**: Policy does not interact with verification
7. **Inert data**: Notification content remains data, not instructions
8. **Bounded inputs**: All content passes through Pydantic validators

### Hostile payload test

Event payload containing:
- `"ignore previous instructions"`
- `"exec('rm -rf /')"`
- `"API_KEY=secret123"`
- `"../../etc/passwd"`

**Result:** All remain in Event payload (sanitized by EventEmitter). Notification content uses only `task_id`, `plan_id`, bounded `reason` field. No payload content reaches Notification text.

## 17. Observability

### Policy processing observability

**Do NOT emit Events for policy processing.** This would create:

```
Event → NotificationPolicy → Event → NotificationPolicy → ...
```

**Recommended approach:**
- Use Python `logging` for policy processing diagnostics
- Log at DEBUG level: Event ID, NotificationType created/skipped, reason
- Log at WARNING level: Policy failures, malformed Events
- Log at ERROR level: NotificationRepository failures

**No new durable Events for policy observability in V1.**

## 18. Future Extensibility

### Provider-neutral policy interface

```python
class NotificationPolicy:
    async def process(self, event: Event) -> Notification | None:
        """Return a Notification or None if no notification is required."""
        raise NotImplementedError

class NotificationDelivery:
    async def deliver(self, notification: Notification) -> None:
        """Deliver a notification to an external channel."""
        raise NotImplementedError
```

### Future channels (NOT V1)

- In-app notification
- Email
- Push notification
- Webhook
- Mobile
- External channels

**Design principle:** Notification creation is independent from delivery. Stage 13.2's Notification model is already channel-agnostic.

## 19. Proposed Implementation Stages

### 13.3A — Policy Contract + Mapping
- Define `NotificationPolicy` interface
- Implement `DefaultNotificationPolicy` with deterministic mapping
- Define content templates for each NOTIFICATION_REQUIRED EventType
- Add safe fallbacks for missing/malformed fields
- Unit tests for each mapping rule

### 13.3B — Integration with Event Persistence
- Integrate policy into `EventEmitter.emit()` or `EventRepository.record()`
- Run policy synchronously after Event persistence
- Handle policy failures (log, don't crash Event processing)
- Integration tests: Event → Notification flow

### 13.3C — Replay / Idempotency / Concurrency Hardening
- Implement replay logic (process Events without Notifications)
- Add replay tests
- Concurrency tests: simultaneous Event processing
- Transaction boundary tests: crash, rollback, recovery

### 13.3D — Hostile Final Gate
- Full hostile audit
- Security review
- Test integrity review
- Scope verification
- Final report

## 20. Test Plan

### Mapping tests
- Every EventType classification verified
- NOTIFICATION_REQUIRED Event → correct NotificationType, severity, title, body
- NO_NOTIFICATION Event → no Notification created
- NOTIFICATION_OPTIONAL Event → no Notification created (V1)

### Content tests
- Deterministic title/body for each EventType
- Bounded content (200 char title, 2000 char body)
- Missing fields → safe fallback
- Malformed payload → safe fallback
- Hostile payload → inert in Notification content

### Idempotency tests
- Same Event re-emitted → same Notification returned
- Replay of committed Event → Notification created if missing
- Concurrent Event processing → exactly 1 Notification
- Duplicate `(source_event_id, notification_type)` → existing Notification returned

### Failure tests
- NotificationRepository failure → Event still committed
- Malformed Event → no crash, safe fallback
- DB lock → exception propagates correctly
- Unsupported EventType → no crash, no Notification

### Integration tests
- Real EventRepository → policy → NotificationRepository
- Restart/replay scenario
- Transaction rollback scenario

### Scheduler tests
- `scheduler.occurrence.failed` → `scheduler.occurrence.failed` Notification
- `scheduler.occurrence.recovered` → `scheduler.occurrence.recovered` Notification
- `scheduler.occurrence.claimed` → no Notification
- `scheduler.occurrence.triggered` → no Notification
- `scheduler.occurrence.skipped` → no Notification

### Security tests
- Prompt injection payload → no execution, inert content
- Secret-like payload → sanitized by EventEmitter, not propagated to Notification
- No tool execution path from policy
- No permission bypass path from policy

## 21. Explicit Non-Goals

- Notification REST API
- UI
- External delivery (email, push, webhook)
- Notification preferences
- AI-generated notification content
- Notification actions
- Analytics
- Dashboard
- Distributed event bus
- Outbox infrastructure
- Multi-user routing
- Policy versioning (V1)
- Replay UI/API (V1)
- Backfill historical notifications (V1)

## 22. Unresolved Risks / Blockers

| Risk | Severity | Mitigation |
|------|----------|------------|
| `verification.failed` + `task.failed` duplication | Low | Classify `verification.failed` as NOTIFICATION_OPTIONAL in V1 |
| Event payload size vs Notification content size | Low | Policy constructs bounded content; ignores payload |
| Scheduler `reason_detail` truncation | Low | Truncate to 200 chars in policy |
| RecoveryManager/ApprovalService events not in EventType | Low | Defer to V2 when those components emit EventType events |
| Policy failure after Event commit | Low | Event remains durable; replay handles gap |
| Concurrent policy processing | Low | Unique constraint provides safety |

## Final Status

STAGE_13_3_IMPLEMENTATION_READY
