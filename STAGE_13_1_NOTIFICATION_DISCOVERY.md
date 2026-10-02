# Stage 13.1 — Notification & Action Feedback Foundation

## Discovery Report

**Status:** STAGE_13_1_IMPLEMENTATION_READY

**Date:** 2026-09-22

**Scope:** Repository discovery, domain design, and implementation planning for the durable application-owned Notification Foundation. No code changes, migrations, or tests are created by this document.

---

## 1. Current Architecture — Relevant Components

### 1.1 Event Foundation

The Event subsystem is mature and production-ready:

- **EventType enum** (`services/core/app/events/schemas.py`): Tier 1 lifecycle vocabulary.
  - Task: `TASK_STARTED`, `TASK_COMPLETED`, `TASK_FAILED`
  - Plan: `PLAN_STARTED`, `PLAN_COMPLETED`, `PLAN_FAILED`
  - Verification: `VERIFICATION_STARTED`, `VERIFICATION_PASSED`, `VERIFICATION_FAILED`
  - Workspace: `CHECKPOINT_CREATED`
  - Scheduler: `SCHEDULER_OCCURRENCE_CLAIMED`, `SCHEDULER_OCCURRENCE_TRIGGERED`, `SCHEDULER_OCCURRENCE_FAILED`, `SCHEDULER_OCCURRENCE_RECOVERED`, `SCHEDULER_OCCURRENCE_SKIPPED`
- **EventEmitter** (`emitter.py`): Application-facing write boundary. Validates, bounds (payload 4KB, metadata 1KB), sanitizes, timestamps, and persists events.
- **EventRepository** (`repository.py`): Append-only event store. Idempotency enforced by `event_id` primary key. `EventIntegrityConflict` raised on conflicting duplicate.
- **HistoryService** (`history.py`): Read-side query layer over EventStore. Does NOT duplicate or reinterpret events.
- **Event Sanitizer** (`sanitizer.py`): Regex-based secret redaction for common credential patterns.

### 1.2 Scheduler

- **SchedulerService** (`scheduler/service.py`): Control-plane business logic for CRUD, idempotency, enable/disable.
- **SchedulerWorker** (`scheduler/worker.py`): Background polling loop. Builds an `EventEmitter` per session.
- **SchedulerRepository** (`scheduler/repository.py`): Emits scheduler lifecycle events with deterministic `event_id` (`sched_evt_<32hex>`).
- **SchedulerHandoffService** (`scheduler/handoff.py`): Durable handoff boundary from scheduler to execution layer.

### 1.3 Autonomous Execution Core

- **AutonomousTaskOrchestrator** (`orchestration/orchestrator.py`): Emits events via `_emit_event` helper:
  - `TASK_STARTED`, `TASK_FAILED`, `VERIFICATION_STARTED`, `VERIFICATION_FAILED`, `VERIFICATION_PASSED`, `CHECKPOINT_CREATED`, `TASK_COMPLETED`
- **AutonomousPlanExecutor** (`services/autonomous_loop.py`): Emits `PLAN_STARTED`, `PLAN_COMPLETED`, `PLAN_FAILED`.
- **AutonomousRunController** (`orchestration/run_controller.py`): No direct event emission; coordinates via orchestrator.
- **BackgroundExecutionManager** (`services/background_execution_manager.py`): In-memory only. No durable event emission.
- **RecoveryManager** (`orchestration/recovery_manager.py`): No event emission. Uses `TaskRepository` and `TaskStateMachine`.

### 1.4 AI Manager & Execution Bridge

- **AIManager** (`ai_manager/ai_manager.py`): Provider-neutral decision engine.
- **ManagerPlanningService** (`ai_manager/bridge.py`): Decision → Plan bridge.
- **ExecutionBridge** (`ai_manager/execution_bridge.py`): Decision → Execution bridge. Enforces `PolicyCapabilityGate` and `CapabilityApprovalService`.
- **CapabilityApprovalService** (`ai_manager/approval_service.py`): Handles approval flow with TTL.

### 1.5 Security & Authorization

- **PermissionEngine** (`tools/permission_engine.py`): Fail-closed permission evaluation. Records audit events.
- **ApprovalService** (`tools/approval_service.py`): ASK_USER approval flow. Creates `PendingApproval` records. Emits audit events.
- **VerificationGate** (`verification/gate.py`): Fail-closed verification. No event emission.

### 1.6 Persistence & Configuration

- **Database** (`db/database.py`): SQLite + async SQLAlchemy. No Alembic. Migrations are inline DDL in `init_db()`.
- **Models** (`db/models/`): ORM models for events, tasks, scheduled jobs, settings, audit, activity, approvals, etc.
- **Settings** (`services/settings_service.py`): Key-value store. `AppSetting` model.
- **ActivityService** (`services/activity_service.py`): Generic activity log. Not suitable for structured notifications.

### 1.7 REST / API Conventions

- FastAPI with `APIRouter`.
- Routes in `app/api/routes/` (activity, ai, approvals, health, history, plans, scheduler, settings, system).
- Thin route layer: routes handle HTTP parsing/errors; business logic lives in services.
- Dependency injection via `fastapi.Depends`.
- Pagination: `limit`/`offset` with hard caps (e.g., limit max 500, offset max 100000).
- Error mapping: service `ValueError` → `HTTPException` with appropriate status codes.
- Response schemas in `app/api/schemas/`.

### 1.8 Lifecycle

- `app/core/lifecycle.py`: `lifespan` context manager. Startup order: logging → DB init → activity log → settings seed → Ollama autostart → tool registration → scheduler worker start. Shutdown reverses this.

---

## 2. Problem Definition

### 2.1 Terminology

| Term | Definition |
|------|------------|
| **Event** | Immutable, append-only record of a system occurrence. Stored in `events` table. Source of truth for "what happened". |
| **History entry** | Read-side projection of an Event via `HistoryService`. Does not duplicate data. |
| **Notification** | Mutable, user-facing record derived from one or more events. Represents "the user should know about this". Has read/unread state. |
| **Delivery attempt** | External transmission of a notification (email, Telegram, mobile push, etc.). **NOT present in V1.** |
| **User acknowledgement/read state** | Mutable state on the Notification record itself (`read_at`, `acknowledged_at`). |

### 2.2 Key Distinction

**Notifications MUST reference events; they must NOT become a second event store or duplicate event payloads.**

Justification:
- The EventStore is already append-only, idempotent, and immutable.
- Duplicating event payloads into notifications creates consistency and retention problems.
- Notifications are projections: they point back to the originating `event_id` (or `correlation_id`).
- Event payloads remain bounded (4KB) and sanitized. Notification bodies should be bounded, application-owned summaries.

### 2.3 What a Notification Means

A Notification in this system is:

- **Application-owned**: Created by explicit application code, not by external providers.
- **Durable**: Persisted in SQLite.
- **User-facing**: Designed for the local desktop UI to consume.
- **Actionable only through explicit API**: V1 does NOT support notification-triggered actions.
- **Provider-neutral**: No external delivery coupling in V1.

---

## 3. Notification Domain Proposal (V1)

### 3.1 Model

```python
class Notification(BaseModel):
    model_config = ConfigDict(frozen=False, arbitrary_types_allowed=False)

    notification_id: str                      # PK, e.g. "notif_<uuid>"
    created_at: datetime                      # When the notification was created
    updated_at: datetime                      # When the notification was last modified
    notification_type: str                    # e.g. "task.failed", "plan.completed", "scheduler.occurrence.failed"
    severity: str                             # "info", "warning", "error", "critical"
    title: str                                # Short summary (bounded, e.g. 200 chars)
    body: str | None                          # Bounded details (bounded, e.g. 2000 chars)
    source_event_id: str | None               # The event_id this notification was derived from
    correlation_id: str | None                # Correlation group (mirrors event correlation_id)
    task_id: str | None                       # Optional task reference
    plan_id: str | None                       # Optional plan reference
    run_id: str | None                        # Optional run reference
    execution_id: str | None                  # Optional execution reference
    read_at: datetime | None                  # When user marked it read
    acknowledged_at: datetime | None          # When user acknowledged it
    expires_at: datetime | None               # Optional expiration (for TTL-based cleanup)
    metadata: dict[str, Any] | None           # Bounded structured metadata (1KB max)
```

### 3.2 Field Authority & Derivation

| Field | Authority | Notes |
|-------|-----------|-------|
| `notification_id` | Application | Generated at creation. |
| `created_at` | System | UTC timestamp. |
| `updated_at` | System | Updated on read/ack. |
| `notification_type` | Application | Must match an allowed vocabulary or be namespaced. |
| `severity` | Application | Derived from event type or explicit policy. |
| `title` | Application | Bounded, sanitized string. |
| `body` | Application | Bounded, sanitized string. |
| `source_event_id` | Application | References `events.event_id`. |
| `correlation_id` | Derived from event | Copied from source event. |
| `task_id`, `plan_id`, `run_id`, `execution_id` | Derived from event | Copied from source event. |
| `read_at`, `acknowledged_at` | User | Mutated via API. |
| `expires_at` | Application | Optional. Derived from policy or explicit setting. |
| `metadata` | Application | Bounded dict. |

### 3.3 Allowed `notification_type` Vocabulary (V1)

Strict enum-like strings. New types require Stage approval.

- `task.started`
- `task.completed`
- `task.failed`
- `plan.completed`
- `plan.failed`
- `verification.failed`
- `scheduler.occurrence.failed`
- `scheduler.occurrence.recovered`
- `approval.requested`
- `recovery.completed`
- `system.error`

---

## 4. Notification Lifecycle

### 4.1 V1 State Machine: Minimal

```
CREATED → READ
```

Justification:
- V1 has no external delivery provider.
- "DELIVERING" / "DELIVERED" states would pretend an external handoff occurred when it did not.
- "ACKNOWLEDGED" is not required for V1; the UI can use `read_at` alone.

### 4.2 Transitions

| From | To | Trigger |
|------|----|---------|
| `CREATED` | `READ` | User (or system) marks notification as read via API. |

**No other transitions in V1.**

### 4.3 Expiration

- `expires_at` is optional.
- When set, a retention/cleanup process (not part of Stage 13 core) can delete expired notifications.
- V1 implementation may simply add a TTL-based delete query.

---

## 5. Event → Notification Policy

### 5.1 Approach: Hybrid (Explicit + Rule-Based)

**Explicit application-owned notification creation** is the primary mechanism. The application creates notifications at well-defined points in the execution lifecycle.

**Rule-based filtering** is NOT a separate event-listener subsystem. Instead, the `NotificationService` exposes a single method:

```python
async def maybe_create_from_event(self, event: Event) -> Notification | None:
    """Evaluate event against notification policy and create notification if warranted."""
```

This keeps the policy localized and avoids a generic event-listener framework that could create loops or noise.

### 5.2 Minimal V1 Policy

Create a notification **only** for the following event types and outcomes:

| Event Type | Condition | Notification Type | Severity | Title Template |
|------------|-----------|-------------------|----------|----------------|
| `task.failed` | Always | `task.failed` | `error` | "Task {task_id} failed" |
| `task.completed` | Always | `task.completed` | `info` | "Task {task_id} completed" |
| `plan.failed` | Always | `plan.failed` | `error` | "Plan {plan_id} failed" |
| `plan.completed` | Always | `plan.completed` | `info` | "Plan {plan_id} completed" |
| `verification.failed` | Always | `verification.failed` | `warning` | "Verification failed for task {task_id}" |
| `scheduler.occurrence.failed` | Always | `scheduler.occurrence.failed` | `error` | "Scheduled job {job_id} occurrence failed" |
| `scheduler.occurrence.recovered` | Always | `scheduler.occurrence.recovered` | `warning` | "Scheduled job {job_id} occurrence recovered" |
| `approval.requested` | Always | `approval.requested` | `info` | "Approval requested for tool {tool_id}" |
| `recovery.completed` | Always | `recovery.completed` | `warning` | "Task {task_id} recovery completed" |
| `system.error` | Always | `system.error` | `error` | "System error" |

**Explicit NOTIFICATION CREATION points** (in addition to the event-based policy):

- When `ApprovalService.create_pending_approval()` is called.
- When `RecoveryManager.recover_task()` completes.
- When `SchedulerWorker._process_job()` finalizes FAILED/SKIPPED.

**Do NOT automatically notify for:**
- Every `task.started` (too noisy).
- Every scheduler claimed/triggered (routine operation).
- Every verification passed (routine success).
- Every checkpoint created (routine).

### 5.3 Integration Points

The following components should invoke the notification policy:

1. **AutonomousTaskOrchestrator** (`_emit_event` calls): After emitting `TASK_FAILED`, `TASK_COMPLETED`, `VERIFICATION_FAILED`.
2. **AutonomousPlanExecutor** (`_finalize_result`): After emitting `PLAN_FAILED`, `PLAN_COMPLETED`.
3. **ApprovalService** (`create_pending_approval`): After creating approval.
4. **RecoveryManager** (`recover_task`): After successful recovery.
5. **SchedulerWorker** / **SchedulerRepository**: After emitting scheduler events (failed, recovered, skipped).
6. **VerificationGate**: Does NOT emit events; orchestrator handles this.

---

## 6. Idempotency Model

### 6.1 Canonical Idempotency Key

**V1 idempotency key: `(source_event_id, notification_type)`**

Justification:
- `event_id` is globally unique and already authoritative.
- A single event may warrant multiple notification types (e.g., a task failure might produce both `task.failed` and `verification.failed`), but in V1 policy each event-to-type mapping is unique.
- If the same event is replayed (e.g., event replay, retry, restart), the notification service must not create duplicates.

### 6.2 Behavior

| Scenario | Behavior |
|----------|----------|
| Exact duplicate event with same `notification_type` | Return existing notification (idempotent). |
| Same event, different `notification_type` | Create new notification (different key). |
| Repeated processing after crash | `source_event_id` + `notification_type` lookup prevents duplicates. |
| Concurrent processing | Database unique constraint on `(source_event_id, notification_type)` prevents races. Loser gets `IntegrityError` → return existing. |
| Event replay | All notifications for replayed events are re-created unless idempotency key matches. |

### 6.3 Guarantee

**At-most-once notification creation per (event, type) pair.** We do NOT claim exactly-once delivery because there is no external delivery in V1.

---

## 7. Delivery Abstraction

### 7.1 V1: Durable In-App Notification Only

- Notifications are persisted in the local SQLite database.
- No external provider integration.
- The UI (frontend) polls or long-polls the `/api/notifications` REST endpoints.

### 7.2 Future Provider-Neutral Interface

Define an abstract interface for future expansion:

```python
class NotificationDeliveryProvider(Protocol):
    async def deliver(self, notification: Notification) -> DeliveryResult: ...
    async def mark_delivered(self, notification_id: str) -> None: ...
```

**V1 implementation:** `InAppNotificationProvider` — no-op delivery, just marks the in-app record as visible.

**Why define it now:**
- The Notification domain model and persistence must not be coupled to Telegram/email/etc.
- Future providers should be pluggable without schema changes to the core `notifications` table.
- The interface is documentation and structural guidance only; no abstract base class is required in V1.

---

## 8. Security & Permissions

### 8.1 Secret Redaction

- Notification `title` and `body` MUST be constructed from bounded, application-owned strings.
- If event payload fields are used to construct notification text, they MUST be passed through the existing `redact_payload()` / `redact_metadata()` pipeline before inclusion.
- Notifications must NEVER contain:
  - Raw API keys, tokens, passwords, private keys
  - Filesystem paths that could leak user identity or project structure
  - Untrusted model output that could contain prompt injection or malicious text

### 8.2 Prompt Injection Propagation

- Event payloads may contain AI-generated content (e.g., agent summaries, failure reasons).
- These are already sanitized by the EventSanitizer before persistence.
- Notification text derived from event payloads must use the same redaction pipeline.
- **Never** include raw `agent_result.error_message` or model rationale in notifications without redaction.

### 8.3 Authorization Boundaries

- Notifications must NOT bypass `PermissionEngine`, `ApprovalService`, or `VerificationGate`.
- Notifications must NOT grant execution authority.
- **V1 explicitly excludes notification-triggered actions.** A notification is informational only.

### 8.4 Read/Write Access

- The REST API for notifications must be protected by the existing application authorization boundary (local desktop UI only; CORS restricted to localhost).
- No new authorization framework is introduced in V1.

---

## 9. REST / API Architecture

### 9.1 Minimal V1 Surface

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/notifications` | List notifications (paginated, recent first). |
| `GET` | `/api/notifications/unread-count` | Return count of unread notifications. |
| `GET` | `/api/notifications/{notification_id}` | Get single notification. |
| `PATCH` | `/api/notifications/{notification_id}/read` | Mark as read. |
| `POST` | `/api/notifications/mark-all-read` | Mark all as read. |

### 9.2 Excluded from V1

- Delete/dismiss (not required for foundation; can be added later).
- Filter by type/severity (can be added later).
- Push/subscribe (no external delivery).

### 9.3 Conventions

- Follow existing pagination: `limit` (max 500), `offset` (max 100000).
- Response schemas in `app/api/schemas/notifications.py`.
- Errors mapped to standard HTTP status codes (404, 422, 500).
- Dependency injection via `fastapi.Depends`.

---

## 10. Persistence Model

### 10.1 Database Table: `notifications`

```sql
CREATE TABLE IF NOT EXISTS notifications (
    notification_id VARCHAR(64) PRIMARY KEY,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    notification_type VARCHAR(64) NOT NULL,
    severity VARCHAR(20) NOT NULL DEFAULT 'info',
    title VARCHAR(200) NOT NULL,
    body TEXT,
    source_event_id VARCHAR(64),
    correlation_id VARCHAR(128),
    task_id VARCHAR(74),
    plan_id VARCHAR(64),
    run_id VARCHAR(64),
    execution_id VARCHAR(64),
    read_at TIMESTAMP WITH TIME ZONE,
    acknowledged_at TIMESTAMP WITH TIME ZONE,
    expires_at TIMESTAMP WITH TIME ZONE,
    metadata TEXT
)
```

### 10.2 Indexes

```sql
CREATE INDEX IF NOT EXISTS ix_notifications_created_at ON notifications(created_at)
CREATE INDEX IF NOT EXISTS ix_notifications_notification_type ON notifications(notification_type)
CREATE INDEX IF NOT EXISTS ix_notifications_severity ON notifications(severity)
CREATE INDEX IF NOT EXISTS ix_notifications_source_event_id ON notifications(source_event_id)
CREATE INDEX IF NOT EXISTS ix_notifications_correlation_id ON notifications(correlation_id)
CREATE INDEX IF NOT EXISTS ix_notifications_task_id ON notifications(task_id)
CREATE INDEX IF NOT EXISTS ix_notifications_plan_id ON notifications(plan_id)
CREATE INDEX IF NOT EXISTS ix_notifications_read_at ON notifications(read_at)
CREATE UNIQUE INDEX IF NOT EXISTS uq_notifications_event_type ON notifications(source_event_id, notification_type)
```

### 10.3 Retention

- **V1 default:** 90 days. `expires_at` is set at creation based on policy.
- A background cleanup task (could be added in a later sub-stage) deletes expired notifications.
- Manual deletion is not exposed in V1 API.

### 10.4 Concurrency

- Unique constraint on `(source_event_id, notification_type)` enforces idempotency at DB level.
- SQLite handles concurrent writes via its own locking; repository should retry on `IntegrityError`.

### 10.5 Migration

- Added to `services/core/app/db/database.py` `init_db()` function.
- No Alembic. Inline DDL as done for `events` and `scheduled_jobs`.

---

## 11. Event / History Integration

### 11.1 Relationship

```
Event ──────(1:N)─────▶ Notification
```

- One event can produce zero or more notifications.
- A notification always references exactly one `source_event_id`.
- The EventStore remains the sole source of truth for "what happened".
- Notification is a derived, mutable projection.

### 11.2 Loop Risk Analysis

**Risk: Event → Notification → Event → Notification**

- If notification creation emits a new event (e.g., `notification.created`), and that event triggers another notification, an infinite loop can occur.
- **V1 decision: Notification creation MUST NOT emit events.**
- The `NotificationService` is a pure application service; it does not call `EventEmitter`.
- If future stages require eventing notification lifecycle, they must use a separate, non-recursive event vocabulary that the notification policy explicitly ignores.

### 11.3 History Queries

- HistoryService remains unchanged.
- Users can query history by `correlation_id`, `task_id`, etc., to see the underlying events.
- Notifications are a separate query surface.

---

## 12. Scheduler Integration

### 12.1 Scheduler → Notification Flow

The scheduler already emits events:

- `SCHEDULER_OCCURRENCE_FAILED`
- `SCHEDULER_OCCURRENCE_RECOVERED`
- `SCHEDULER_OCCURRENCE_SKIPPED`

The notification policy (Section 5.2) already maps these to notifications.

### 12.2 Integration Point

- `SchedulerRepository._emit_scheduler_event()` emits events.
- The event is persisted.
- A post-commit hook or explicit call in `SchedulerWorker._process_job()` and `SchedulerRepository.finalize_*` should invoke the notification policy.
- **Preferred pattern:** Explicit call in the worker/handoff after successful DB commit, rather than a generic event-subscriber framework.

### 12.3 No Automatic Notification for Routine Scheduler Events

- `SCHEDULER_OCCURRENCE_CLAIMED`
- `SCHEDULER_OCCURRENCE_TRIGGERED`

These are routine and MUST NOT produce notifications in V1.

---

## 13. Crash / Recovery Analysis

### 13.1 Crash Windows

| Window | Scenario | V1 Behavior |
|--------|----------|-------------|
| Event exists, notification not created | App crashed after event emit, before notification creation | Notification is lost. Event remains. On next relevant event, notification is created. No duplicate. |
| Notification transaction crashes | DB transaction for notification creation fails | Event remains. Notification not created. Retry/replay will create it. |
| Notification created, read state lost | App crashed after notification created, before UI read | Notification remains in `CREATED` state. UI sees it on next poll. |
| Event replay | Events replayed from backup or external source | Idempotency key prevents duplicate notifications. |

### 13.2 Guarantees

- **At-most-once notification creation** per (event, type).
- **Durable persistence**: Notification survives app restart.
- **No exactly-once delivery guarantee** (not applicable — no external delivery).
- **Event replay safety**: Unique constraint prevents duplicates.

---

## 14. Performance & Resource Safety

### 14.1 Bounds

- `title`: max 200 characters.
- `body`: max 2000 characters (stored as `TEXT`, but bounded in application code).
- `metadata`: max 1KB JSON (same cap as event metadata).

### 14.2 Pagination

- Default limit: 50.
- Max limit: 500.
- Max offset: 100,000.
- Ordering: `created_at DESC, notification_id DESC` (recent first).

### 14.3 Retention & Cleanup

- Default `expires_at`: `created_at + 90 days`.
- A cleanup query can be run at startup or on a schedule:
  ```sql
  DELETE FROM notifications WHERE expires_at IS NOT NULL AND expires_at <= :now
  ```

### 14.4 Storm Protection

- The event-to-notification policy is explicit and minimal. It does not fire on every event.
- If a plan fails with 10 tasks, up to 10 notifications may be created. This is bounded and acceptable.
- No background worker is required for V1.

---

## 15. Architecture Fit

### 15.1 Recommendation: New Bounded Module

**Create a new module: `app/notifications/`**

Rationale:
- Notifications are a distinct bounded context.
- They reference events but are not part of the Event subsystem.
- They are not part of Scheduler, API, or AI Manager.
- They must be cleanly separable for future "User Interaction" subsystems (desktop notifications, mobile push, etc.).

### 15.2 Module Structure

```
app/notifications/
    __init__.py
    models.py          # Notification domain model
    repository.py      # NotificationRepository
    service.py         # NotificationService (policy + CRUD)
    delivery.py        # Future provider interface (V1: no-op)
    exceptions.py      # Notification-specific exceptions
```

### 15.3 Future-Proofing

- The `notification_type` vocabulary can be extended without schema changes.
- The `delivery.py` interface allows plugging in Telegram, email, mobile push, desktop OS notifications without touching the core domain.
- The REST API is versioned by path (`/api/notifications`) and can be extended independently.

---

## 16. Explicit Non-Goals (Stage 13)

The following are **explicitly out of scope** for Stage 13:

1. **External notification providers** (Telegram, Discord, email, SMS, mobile push, desktop OS notifications).
2. **Notification-triggered actions** (e.g., clicking a notification executes a task).
3. **AI-generated notification text** (titles/bodies are application-owned templates).
4. **Complex preference rules** (user-configurable notification filters).
5. **Multi-user notification routing** (single-user desktop app in V1).
6. **Analytics / dashboard** for notifications.
7. **Distributed notification workers** (single-process local app).
8. **Notification editing** (only read state mutation in V1).
9. **Notification deletion / dismiss** (retention-based expiration only).
10. **Event emission from notification lifecycle** (loop prevention).

---

## 17. Concrete Implementation Plan

### 17.1 Current Architecture Summary

The codebase has a robust Event Foundation with `EventEmitter`, `EventRepository`, `HistoryService`, and deterministic event IDs. Scheduler, Orchestrator, and PlanExecutor emit lifecycle events. Persistence is SQLite via async SQLAlchemy with inline migrations. REST is FastAPI with thin routes and DI. No notification domain exists.

### 17.2 Discovered Reusable Components

- `EventRepository` / `EventEmitter`: Source events.
- `EventType`: Vocabulary for event types.
- `redact_payload` / `redact_metadata`: Secret redaction.
- `Base` ORM model: Inherit for `NotificationModel`.
- `init_db()`: Inline migration pattern.
- FastAPI route patterns, response schemas, DI.
- SettingsService: For notification retention policy (future).

### 17.3 Proposed Notification Domain

See Section 3. `Notification` Pydantic model with fields: `notification_id`, `created_at`, `updated_at`, `notification_type`, `severity`, `title`, `body`, `source_event_id`, `correlation_id`, `task_id`, `plan_id`, `run_id`, `execution_id`, `read_at`, `acknowledged_at`, `expires_at`, `metadata`.

### 17.4 Lifecycle

`CREATED → READ`. No richer lifecycle in V1.

### 17.5 Event-to-Notification Policy

Hybrid explicit + minimal rule-based. See Section 5.2 for the exact mapping table. Notification creation happens via explicit application calls at key lifecycle points.

### 17.6 Idempotency Model

Unique constraint on `(source_event_id, notification_type)`. At-most-once creation.

### 17.7 Delivery Abstraction

V1: in-app only. `NotificationDeliveryProvider` protocol defined in `delivery.py` for future expansion.

### 17.8 Persistence Model

`notifications` table with indexes and unique constraint. Inline DDL in `init_db()`. 90-day retention via `expires_at`.

### 17.9 API Surface

- `GET /api/notifications`
- `GET /api/notifications/unread-count`
- `GET /api/notifications/{notification_id}`
- `PATCH /api/notifications/{notification_id}/read`
- `POST /api/notifications/mark-all-read`

### 17.10 Security Model

- Titles/bodies are application-owned, bounded, redacted strings.
- No secrets from event payloads leak into notifications.
- No notification-triggered actions.
- No bypass of PermissionEngine / ApprovalService / VerificationGate.

### 17.11 Crash / Recovery Guarantees

- At-most-once notification creation.
- Durable persistence in SQLite.
- Idempotent replay safe.

### 17.12 Exact Stage 13 Implementation Stages

**Stage 13.1 — Discovery (this document)**

**Stage 13.2 — Domain Model & Repository**
- Create `app/notifications/models.py` (Notification domain model).
- Create `app/notifications/repository.py` (NotificationRepository: create, get, list, mark_read, mark_all_read, count_unread, delete_expired).
- Add `NotificationModel` to `app/db/models/__init__.py`.
- Add inline DDL + indexes + unique constraint to `app/db/database.py` `init_db()`.
- Add unit tests for model validation and repository idempotency.

**Stage 13.3 — Service & Policy**
- Create `app/notifications/service.py` (NotificationService).
- Implement `maybe_create_from_event(event: Event) -> Notification | None`.
- Implement explicit notification creation methods for scheduler, approval, recovery.
- Add unit tests for policy mapping and idempotency.

**Stage 13.4 — REST API**
- Create `app/api/schemas/notifications.py` (request/response models).
- Create `app/api/routes/notifications.py` (5 endpoints).
- Register router in `app/main.py`.
- Add DI dependency `get_notification_service` in `app/dependencies.py`.
- Add integration tests for API.

**Stage 13.5 — Integration**
- Wire `NotificationService` into:
  - `AutonomousTaskOrchestrator._emit_event` (for task/verification outcomes).
  - `AutonomousPlanExecutor._finalize_result` (for plan outcomes).
  - `ApprovalService.create_pending_approval`.
  - `RecoveryManager.recover_task`.
  - `SchedulerRepository.finalize_failed`, `finalize_skipped`, `reset_expired_claim_to_pending`.
- Add end-to-end tests verifying notifications are created for key lifecycle events.

**Stage 13.6 — Hardening & Cleanup**
- Add retention cleanup task (startup or scheduled).
- Verify idempotency under concurrent event processing.
- Add hostile tests for secret leakage in notification text.
- Final regression gate.

### 17.13 Tests Required Per Stage

| Stage | Tests |
|-------|-------|
| 13.2 | Model validation (bounds, required fields), repository CRUD, idempotency (duplicate event+type), unique constraint behavior. |
| 13.3 | Policy mapping table (event type → notification type), explicit creation for scheduler/approval/recovery, redaction of sensitive payload fields. |
| 13.4 | API CRUD, pagination limits, 404 handling, read/unread count, mark-all-read. |
| 13.5 | Integration: task failure creates notification, plan completion creates notification, approval request creates notification, scheduler failure creates notification, recovery creates notification. |
| 13.6 | Concurrent event replay idempotency, secret redaction hostile tests, retention cleanup, full regression. |

### 17.14 Risks & Known Limitations

1. **No external delivery in V1**: Foundation only; user does not receive alerts outside the UI.
2. **Notification text is static/template-based**: No AI-generated content in V1.
3. **Single user**: No multi-user routing or preferences.
4. **No delete API**: Users cannot manually dismiss notifications; only expiration cleans them up.
5. **Event replay creates new notifications for new event IDs**: Replaying old events with new IDs will create new notifications; idempotency only protects exact `(event_id, type)` pairs.
6. **SQLite concurrency**: Relying on SQLite's locking; high concurrency may see `IntegrityError` retries.

### 17.15 Explicit Out-of-Scope Items

- Telegram, Discord, email, SMS, mobile push, desktop OS notifications.
- Notification-triggered actions or command execution.
- AI-generated notification text.
- User notification preferences / filters.
- Multi-user routing.
- Analytics or notification dashboard.
- Distributed workers.
- Notification deletion/dismiss API.
- Event emission from notification lifecycle.

---

## Final Status

**STAGE_13_1_IMPLEMENTATION_READY**

Repository discovery is complete. The existing Event Foundation, Scheduler, Orchestrator, and Approval subsystems provide clear integration points. The proposed Notification domain is minimal, idempotent, secure, and architected for future delivery provider expansion without core schema changes.
