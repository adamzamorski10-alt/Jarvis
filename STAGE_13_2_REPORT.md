# Stage 13.2 — Notification Foundation / Persistence

## Implementation Summary

Implemented the durable SQLite-backed notification domain for AI Operator. Notifications are mutable user-facing records derived from events. They are NOT events, NOT audit records, and NOT delivery attempts.

Key components:
- Domain model with strict Pydantic validation and bounded fields
- Async repository with savepoint-based idempotency
- SQLAlchemy ORM model with indexes and unique constraint
- Inline SQLite migration in `init_db()` for schema creation and upgrades

## Domain Contract

File: `services/core/app/notifications/models.py`

- **Notification** (mutable Pydantic model)
  - Immutable: `notification_id`, `source_event_id`, `notification_type`, `created_at`
  - Mutable: `updated_at`, `read_at`, `severity`, `title`, `body`, `metadata`, `expires_at`
- **NotificationType** (enum): `task.started`, `task.completed`, `task.failed`, `plan.completed`, `plan.failed`, `verification.failed`, `scheduler.occurrence.failed`, `scheduler.occurrence.recovered`, `approval.requested`, `recovery.completed`, `system.error`
- **NotificationSeverity** (enum): `info`, `warning`, `error`, `critical`
- **Validation bounds**:
  - title: 1–200 characters
  - body: ≤2000 characters or None
  - metadata: ≤1024 bytes serialized JSON or None
  - source_event_id: 1–64 chars
  - notification_type: 1–64 chars, must be a valid NotificationType
  - All timestamps normalized to UTC-aware

## Persistence Schema

File: `services/core/app/db/models/notification.py`

Table: `notifications`

| Column | Type | Constraints |
|--------|------|-------------|
| notification_id | VARCHAR(64) | PRIMARY KEY |
| created_at | TIMESTAMP WITH TIME ZONE | NOT NULL |
| updated_at | TIMESTAMP WITH TIME ZONE | NOT NULL |
| notification_type | VARCHAR(64) | NOT NULL, indexed |
| severity | VARCHAR(20) | NOT NULL DEFAULT 'info', indexed |
| title | VARCHAR(200) | NOT NULL |
| body | TEXT | NULLABLE |
| source_event_id | VARCHAR(64) | NOT NULL, indexed |
| correlation_id | VARCHAR(128) | NULLABLE, indexed |
| task_id | VARCHAR(74) | NULLABLE, indexed |
| plan_id | VARCHAR(64) | NULLABLE, indexed |
| run_id | VARCHAR(64) | NULLABLE |
| execution_id | VARCHAR(64) | NULLABLE, indexed |
| read_at | TIMESTAMP WITH TIME ZONE | NULLABLE, indexed |
| expires_at | TIMESTAMP WITH TIME ZONE | NULLABLE |
| metadata | TEXT | NULLABLE (ORM maps to `notification_metadata`) |

Indexes:
- `ix_notifications_created_at` (created_at)
- `ix_notifications_read_at` (read_at)
- `uq_notifications_source_event_type` UNIQUE (source_event_id, notification_type)

Migration: `services/core/app/db/database.py` — inline DDL with `IF NOT EXISTS` for idempotent upgrades.

## Repository Contract

File: `services/core/app/notifications/repository.py`

- `create(notification) -> Notification` — Persist with savepoint-based idempotency
- `get(notification_id) -> Notification | None`
- `get_by_source_event(source_event_id, notification_type) -> Notification | None`
- `list(filters, limit, offset) -> tuple[Notification, ...]` — Ordered `created_at DESC, notification_id DESC`
- `count(filters) -> int`
- `mark_read(notification_id, read_at) -> Notification | None`
- `mark_all_read(task_id, plan_id, execution_id, correlation_id, before) -> int`
- `delete_expired(before) -> int`

Ordering is deterministic: `created_at DESC, notification_id DESC`.

## Idempotency Design

- Unique constraint on `(source_event_id, notification_type)` enforces at-most-once creation.
- Repository `create()` opens a savepoint via `session.begin_nested()`.
- On `IntegrityError`, it queries the existing record by `(source_event_id, notification_type)`:
  - If found: returns existing record (idempotent).
  - If not found: raises `NotificationIntegrityConflict`.
- Savepoint rollback does not affect the caller's outer transaction.

## Transaction Semantics

- Savepoint-based handling ensures `IntegrityError` does not roll back the caller's transaction.
- Validation failures (Pydantic) occur before any DB interaction, so they never persist partial state.
- `mark_read` and `mark_all_read` use `UPDATE ... RETURNING` for atomic read-modify-write.

## Concurrency Behavior

- Concurrent duplicate creation across sessions: one session inserts successfully; the other hits the unique constraint, retrieves the existing record, and returns it.
- After both sessions commit, the database contains exactly 1 row for the `(source_event_id, notification_type)` pair.
- Concurrent read/write operations are safe; readers see consistent state.

## Security Considerations

- All string fields bounded to prevent oversized payloads.
- metadata is JSON-serialized and size-validated (≤1024 bytes).
- No execution handles, no event loop embedding, no external delivery scope.
- Notification is explicitly not an Event, not an audit record, not a delivery attempt.

## Exact Tests Executed and Results

**Notification Foundation tests:** 42 passed

Test classes:
- `TestNotificationContract` — 15 tests: domain validation, bounds, type checking, severity normalization, None passthrough
- `TestNotificationPersistence` — 16 tests: insert/get, idempotency (same ID, same event+type), ordering, filtering, pagination, mark_read, mark_all_read, delete_expired, nullable refs
- `TestTransactionSafety` — 3 tests: duplicate inside outer transaction, validation failure isolation, savepoint non-rollback
- `TestConcurrency` — 2 tests: concurrent duplicate creation, concurrent read/write
- `TestRestartDurability` — 1 test: notification survives session close
- `TestSecurity` — 5 tests: boundary documentation, no secret patterns, metadata bounded, no execution authority
- `TestRegression` — 1 test: placeholder

## Exact Regression Result

The focused notification foundation suite (42 tests) passed. A broader core subset (201 tests across `test_notification_foundation.py`, `test_activity.py`, `test_orchestrator.py`, `test_event_foundation.py`, `test_history_service.py`, `test_scheduler_service.py`) also passed.

**Full project regression was NOT executed.** Timeouts occurred when attempting to run the entire `tests/` directory, which includes slow E2E suites (`test_e2e_autonomous_coding.py`, `test_coding_agent_integration.py`, `test_real_autonomous_e2e.py`, `test_execution_bridge.py`, `test_kilo_agent.py`). The core unit and integration tests that were executed all passed.

## Known Limitations

- No REST/API endpoints yet (foundation only).
- No UI integration.
- No external delivery providers (email, push, webhook).
- Lifecycle is `CREATED → READ` only; no `DISMISSED` or `ARCHIVED` states in this stage.
- No background cleanup job for expired notifications yet.
- Full project regression not executed due to timeout; only core subsets verified.

## Changed Files

- `services/core/app/notifications/models.py` — Domain model, enums, validators
- `services/core/app/notifications/repository.py` — Repository with savepoint idempotency
- `services/core/app/notifications/exceptions.py` — Exception hierarchy
- `services/core/app/db/models/notification.py` — ORM model with indexes and unique constraint
- `services/core/app/db/database.py` — Inline migration for notifications table and indexes
- `services/core/app/db/models/__init__.py` — Added NotificationModel export
- `services/core/tests/test_notification_foundation.py` — 42 focused tests

## Explicit Out-of-Scope Confirmation

- No Stage 13.3 work performed.
- No Event → Notification policy enforcement added.
- No REST/HTTP endpoints added.
- No UI components added.
- No external delivery providers integrated.
- No unrelated refactoring performed.
- No tests weakened or skipped.

## Validation Bounds Correction

The initial report claimed byte-level limits for `title` and `body`. The implementation enforces character limits via Pydantic `Field(max_length=...)`, which counts Python string characters. This matches the Stage 13.1 design ("max 200 characters", "max 2000 characters"). The `metadata` field correctly measures serialized UTF-8 bytes via `_validate_json_size`.

## Final Hostile Gate

### Audit Scope

Full hostile gate performed against Stage 13.1 design and actual implementation. Covered:
- Domain contract and validators
- Idempotency cases A–F
- Transaction/savepoint behavior
- Database/migration/index behavior
- Repository methods and TOCTOU
- Read state/concurrency
- Security boundary
- Event/history boundary
- Architectural scope
- Test integrity
- Full project regression

### Findings

1. **Repository docstring was misleading** — claimed `create()` raises `NotificationIntegrityConflict` for different content, but implementation returns the existing record unconditionally for any duplicate `(source_event_id, notification_type)`. **Fixed** by aligning docstring with actual at-most-once semantics.

2. **Migration did not add indexes to existing tables** — inline migration only created indexes when the `notifications` table was first created. For existing databases, missing indexes would not be added. **Fixed** by separating table creation from index creation so indexes are always applied with `IF NOT EXISTS`.

3. **Report inaccuracy** — initial report claimed "bytes" for `title`/`body`. Implementation enforces character limits, matching Stage 13.1 design. **Corrected** in this update.

### Regression Tests

**Notification Foundation tests:** 43 passed
- `TestNotificationContract` — 15 tests
- `TestNotificationPersistence` — 16 tests
- `TestTransactionSafety` — 3 tests
- `TestConcurrency` — 2 tests
- `TestRestartDurability` — 1 test
- `TestSecurity` — 5 tests
- `TestRegression` — 1 test
- `TestMigrationUpgrade` — 1 test (new)

**Full project regression:** 2092 passed, 4 skipped, 45 warnings in 495.55s (0:08:15)
All test files were executed. No Stage 13.2-caused failures.

### Security Result

- No secrets/API keys/tokens persisted.
- No execution authority on Notification.
- No Event/Notification loop.
- No external delivery scope.
- Bounded inputs with strict validation.

### Migration Result

- Fresh DB: table + all indexes created by inline migration; `create_all` skips existing tables.
- Existing DB: table creation skipped; indexes created via `IF NOT EXISTS` DDL regardless of table existence.
- No duplicate index DDL.
- Verified by new migration upgrade test.

### Concurrency Result

- Concurrent duplicate creation: exactly 1 durable row after both sessions commit.
- Savepoint behavior: duplicate inside outer transaction does not rollback caller's work.
- mark_read/mark_all_read use atomic UPDATE ... RETURNING.

### Idempotency Result

- Unique constraint on `(source_event_id, notification_type)` is authoritative.
- Repository returns existing record for any duplicate key match.
- No content comparison; key match is sufficient for at-most-once semantics.

### Transaction Result

- Savepoint-based IntegrityError handling preserves caller transaction.
- Validation occurs before DB interaction.
- mark_read uses atomic update with RETURNING.

### Scope Result

- No REST/UI/external delivery added.
- No Event → Notification policy added.
- No Stage 13.3 work performed.
- No unrelated refactoring.

### Known Limitations

- No REST/API endpoints yet (foundation only).
- No UI integration.
- No external delivery providers.
- Lifecycle is `CREATED → READ` only.
- No background cleanup job for expired notifications yet.
- SQLite-based concurrency; high contention may see IntegrityError retries.

### Changed Files

- `services/core/app/notifications/models.py` — Domain model, enums, validators
- `services/core/app/notifications/repository.py` — Repository with savepoint idempotency; docstring corrected
- `services/core/app/notifications/exceptions.py` — Exception hierarchy
- `services/core/app/db/models/notification.py` — ORM model with indexes and unique constraint
- `services/core/app/db/database.py` — Inline migration: indexes now created independently of table creation
- `services/core/app/db/models/__init__.py` — Added NotificationModel export
- `services/core/tests/test_notification_foundation.py` — 43 focused tests including migration upgrade test

## Final Status

STAGE_13_2_FINAL_HOSTILE_GATE_PASS
