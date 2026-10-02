# Stage 13 Final Scope Reconciliation Report

**Date:** 2026-09-24  
**Auditor:** Kilo (hostile scope reconciliation)  
**Baseline:** `366ddad` — `checkpoint: stages 10-13 accepted working tree`  
**Subject:** Stage 13 Final Scope Reconciliation — Notification Policy + Event Integration + Replay

---

## 1. Stage 13 Scope Reconstruction

### 1.1 Implemented Sub-Stages

| Sub-Stage | Status | Description |
|-----------|--------|-------------|
| **13.1** | COMPLETE | Discovery/design — notification domain design, event mapping, security analysis |
| **13.2** | COMPLETE | Foundation/persistence — Notification model, repository, schema, migration |
| **13.3A** | COMPLETE | Policy contract + mapping — `NotificationPolicy`, `NotificationDraft`, 15 EventType mappings |
| **13.3B** | COMPLETE | Integration with EventEmitter — synchronous policy execution after event persistence |
| **13.3C** | COMPLETE | Replay/idempotency/concurrency — `NotificationReplayService`, savepoint handling |
| **13.3D** | COMPLETE | Hostile final gate — full hostile audit, security review, test integrity |

### 1.2 Implemented Components

| Component | File | Status |
|-----------|------|--------|
| Notification domain model | `app/notifications/models.py` | COMPLETE |
| Notification repository | `app/notifications/repository.py` | COMPLETE |
| Notification policy | `app/notifications/policy.py` | COMPLETE |
| Notification replay | `app/notifications/replay.py` | COMPLETE |
| Notification exceptions | `app/notifications/exceptions.py` | COMPLETE |
| DB ORM model | `app/db/models/notification.py` | COMPLETE |
| EventEmitter integration | `app/events/emitter.py` | COMPLETE |
| Dependency injection | `app/dependencies.py` | COMPLETE |
| Lifecycle wiring | `app/core/lifecycle.py` | COMPLETE |

### 1.3 Deferred Functionality (Intentional)

| Feature | Status | Rationale |
|---------|--------|-----------|
| `verification.failed` notification | DEFERRED | NOTIFICATION_OPTIONAL; `task.failed` covers the same failure |
| Replay UI/API | DEFERRED | V1 focuses on forward notification creation |
| Backfill historical notifications | DEFERRED | Not required for V1 |
| Batch processing | DEFERRED | V1 processes one Event at a time |
| Replay filtering by date/type | DEFERRED | V1 replay is unbounded but safe |
| `approval.requested` notification | DEFERRED | ApprovalService uses AuditEventType, not EventType |
| `recovery.completed` notification | DEFERRED | RecoveryManager does not emit EventType events |
| `system.error` notification | DEFERRED | No producer currently emits this |
| External delivery (email/push/webhook) | DEFERRED | V1 is internal-only |
| Notification REST API | DEFERRED | V1 is internal-only |
| Policy versioning | DEFERRED | V1 uses single deterministic policy |
| User preferences | DEFERRED | V1 has no user-specific routing |

---

## 2. Notification Architecture

### 2.1 Data Flow

```
Event (durable, immutable)
    ↓
EventEmitter.emit()
    ↓
NotificationPolicy.process(event) → NotificationDraft | None
    ↓
NotificationRepository.create(notification) → (Notification, bool)
    ↓
Durable Notification (mutable, user-facing)
```

### 2.2 Authoritative Boundaries

| Boundary | Authority | Mechanism |
|----------|-----------|-----------|
| Notification type/severity | `NotificationPolicy` | Hardcoded per EventType; payload cannot change |
| Source event | `Event.event_id` | `source_event_id` on Notification |
| Idempotency | `UNIQUE(source_event_id, notification_type)` | DB unique constraint + savepoint |
| Transaction ownership | Caller | Policy/repository do not commit/rollback outer transaction |
| Replay | `NotificationReplayService` | Reads Events, applies policy, creates missing Notifications |
| Security/sanitization | `EventEmitter` + `NotificationPolicy` | Payload sanitized at event ingress; policy uses only whitelisted fields |

### 2.3 Non-Authoritative Boundaries

Notification does NOT authority over:
- Event creation/modification
- Scheduler lifecycle
- PermissionEngine
- ApprovalService
- VerificationGate
- Task/Plan state

---

## 3. Current Implementation Verification

### 3.1 Notification Model

- Immutable: `notification_id`, `source_event_id`, `notification_type`, `created_at`
- Mutable: `updated_at`, `read_at`, `severity`, `title`, `body`, `metadata`, `expires_at`
- Validation bounds: title ≤200 chars, body ≤2000 chars, metadata ≤1024 bytes
- Timestamps: UTC-aware

### 3.2 NotificationRepository

- `create()` returns `tuple[Notification, bool]` — bool indicates newly created
- Savepoint-based idempotency via `UNIQUE(source_event_id, notification_type)`
- On `IntegrityError`: queries existing record, returns `(existing, False)`
- Deterministic ordering: `created_at DESC, notification_id DESC`
- `mark_read`, `mark_all_read`, `delete_expired` operations

### 3.3 NotificationPolicy

- Pure function: `Event → NotificationDraft | None`
- No database I/O, no event emission, no tool execution, no external calls
- All 15 EventTypes mapped (6 produce notifications, 9 return None)
- `verification.failed` returns None (deferred)
- Safe extraction methods: `_safe_str`, `_safe_int`, `_safe_reason`, `_safe_metadata`
- Only whitelisted payload keys are used
- Notification type/severity are hardcoded per EventType

### 3.4 EventEmitter Integration

- Policy runs synchronously after event persistence
- Notification creation wrapped in try/except
- On notification failure: raises `EventEmissionError` (caller logs, does not crash)
- Event remains durable; notification gap handled by replay

### 3.5 NotificationReplayService

- Bounded by `limit` and `batch_size`
- Deterministic selection: `occurred_at ASC, event_id ASC`
- All filters forwarded to `EventRepository.list()`
- Uses only historical event payload
- Does not modify Events
- Counters: `events_scanned`, `notifications_created`, `notifications_skipped_existing`, `notifications_skipped_policy_none`, `failures`

### 3.6 Database Schema

- Table: `notifications`
- Unique constraint: `UNIQUE(source_event_id, notification_type)`
- Indexes: `created_at`, `read_at`, `notification_type`, `severity`, `source_event_id`, `correlation_id`, `task_id`, `plan_id`, `execution_id`
- Migration: inline DDL in `init_db()` with `IF NOT EXISTS`

### 3.7 No REST API/Schemas

- No notification-specific routes or schemas exist
- Notification system is internal-only (V1 scope)

---

## 4. Test Coverage

### 4.1 Stage 13 Test Files

| File | Tests | Description |
|------|-------|-------------|
| `test_notification_policy.py` | 36 | Policy mapping, content, security, determinism |
| `test_notification_integration.py` | 26 | Event→Notification flow, idempotency, failure handling |
| `test_notification_replay.py` | 19 | Replay semantics, concurrency, filtering |
| `test_notification_foundation.py` | 33 | Domain model, persistence, transaction safety, concurrency |

**Total: 114 tests, all PASS**

### 4.2 Test Results

```
tests/test_notification_policy.py      36 passed
tests/test_notification_integration.py  26 passed
tests/test_notification_replay.py       19 passed
tests/test_notification_foundation.py   33 passed
-----------------------------------------------
Total:                                 114 passed, 0 failed
```

---

## 5. Deferred Functionality Verification

### 5.1 `verification.failed` Status

**Intentional deferred to V2.**

Evidence:
- `NotificationPolicy._handle_verification_failed()` returns `None`
- `NotificationType.VERIFICATION_FAILED` exists in enum but is unused in V1
- Test `test_verification_failed_deferred` passes
- Stage 13.3 discovery document explicitly classifies as NOTIFICATION_OPTIONAL

Rationale: `task.failed` already covers the same failure with user-facing notification.

---

## 6. Security / Authority Audit

### 6.1 Untrusted Event Payloads

**PASS.** Policy uses `_safe_str`, `_safe_int`, `_safe_reason`, `_safe_metadata` — only whitelisted keys extracted. Arbitrary payload content is ignored.

### 6.2 Prompt-Injection-Like Payloads

**PASS.** Policy is deterministic, no LLM involvement. Payload content cannot change notification type/severity.

### 6.3 Secret Leakage

**PASS.** Only bounded whitelisted fields propagate to notification. Event payload sanitized by `EventEmitter` before reaching policy.

### 6.4 Notification Type/Severity Manipulation

**PASS.** Notification type and severity are hardcoded per EventType in policy mapping. Payload cannot influence them.

### 6.5 Arbitrary Metadata Propagation

**PASS.** `_safe_metadata` only allows specific keys: `attempt_count`, `commit_sha`, `reason_code`, `reason_detail`, `error_message`. All values bounded.

### 6.6 Duplicate Creation

**PASS.** `UNIQUE(source_event_id, notification_type)` is authoritative. Application-level pre-check is not relied upon exclusively.

### 6.7 Concurrent Replay

**PASS.** Same unique constraint protects concurrent replay. Loser gets existing notification via `get_by_source_event()`.

### 6.8 IntegrityError Handling

**PASS.** Savepoint catches `IntegrityError`. Existing notification returned. Non-duplicate `IntegrityError` raises `NotificationIntegrityConflict`.

### 6.9 Savepoint/Outer Transaction Interaction

**PASS.** Savepoint only covers notification creation. Event persistence uses separate savepoint. Caller owns outer transaction.

### 6.10 Policy Bypass

**PASS.** Policy has no side effects. No EventEmitter dependency. No tool execution. No permission/approval/verification bypass.

### 6.11 Unauthorized Side Effects

**PASS.** Policy does not emit Events, modify state, or invoke external systems.

---

## 7. Findings

### 7.1 Critical Findings

None.

### 7.2 High Findings

None.

### 7.3 Medium Findings

None.

### 7.4 Low Findings

| # | Finding | Evidence | Classification |
|---|---------|----------|----------------|
| 1 | `EventEmitter.emit()` raises `EventEmissionError` with misleading message "Event emission failed" when notification creation fails, even though the event was successfully persisted | `app/events/emitter.py:227` | LOW — cosmetic, not a correctness defect |

**Mitigation:** All production callers (`orchestrator.py`, `autonomous_loop.py`, `scheduler/worker.py`) catch the exception and log it as a non-fatal warning. Event durability is preserved.

---

## 8. Final Classification

### A — READY FOR FINAL GATE

All intended Stage 13 functionality is implemented and verified:

- Notification model, repository, policy, replay — COMPLETE
- EventEmitter integration — COMPLETE
- Idempotency via `UNIQUE(source_event_id, notification_type)` — VERIFIED
- Transaction/savepoint semantics — VERIFIED
- Security (prompt injection, secret leakage, policy bypass) — VERIFIED
- Concurrency/race safety — VERIFIED
- Tests: 114 passed, 0 failed
- Deferred functionality intentionally deferred per approved contract

No correctness, security, or race defects found. Only a LOW-severity cosmetic issue in error messaging.

---

## 9. Test Results

| Suite | Tests | Result |
|-------|-------|--------|
| Stage 13 focused | 114 | 114 passed, 0 failed |
| Scheduler | 326 | 326 passed, 0 failed |
| Event/History | 200 | 200 passed, 0 failed |
| Autonomous/Execution | 370 | 370 passed, 2 skipped |
| Full project regression | 2196 | 2196 passed, 4 skipped, 0 failed |

---

## 10. Dependencies

- Stage 10: Autonomous development core (complete)
- Stage 11: Event/History system (complete)
- Stage 12: Scheduler REST Control Plane (complete)
- Stage 13.2: Notification foundation/persistence (complete)
- Stage 13.3: Notification policy + integration + replay (complete)

No missing dependencies.

---

## Final Status

```
STAGE_13_READY_FOR_FINAL_GATE
```

**Next authorized step:** Stage 13 final acceptance gate against this baseline.
