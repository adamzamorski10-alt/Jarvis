# Stage 12.2E — Scheduler Lifecycle Events Hostile Verification Report

## Final Status

**STAGE_12_2E_FINAL_HOSTILE_PASS**

## Files Changed

1. `services/core/app/db/database.py` — added `claim_sequence` to CREATE TABLE and added ALTER TABLE migration for existing databases
2. `services/core/app/db/models/scheduled_job.py` — added `claim_sequence` column
3. `services/core/app/scheduler/models.py` — added `claim_sequence` field to `ScheduledJob` domain model
4. `services/core/app/scheduler/repository.py` — required `event_emitter`, claim sequence increment, occurrence preservation, event emission, deterministic event IDs, causation links
5. `services/core/app/events/repository.py` — savepoint-scoped rollback for `IntegrityError`
6. `services/core/app/events/schemas.py` — added scheduler event type enum values
7. `services/core/tests/test_scheduler_events.py` — new test file for Stage 12.2E (26 tests)
8. `services/core/tests/test_scheduler_claim.py` — updated fixtures to pass required `event_emitter`, updated recovery assertion
9. `services/core/tests/test_scheduler_handoff.py` — updated fixtures to pass required `event_emitter`
10. `services/core/tests/test_scheduler_repository.py` — updated fixtures to pass required `event_emitter`
11. `services/core/app/scheduler/worker.py` — fixed `_build_event_emitter` to raise on failure instead of returning `None`

## Hostile Verification Results

### 1. Event Emission Enforcement

**VERDICT: PASS (after fix)**

**Original defect:** `SchedulerRepository.__init__` accepted `event_emitter: Any | None = None`. Production code could construct a repository without an emitter, and state mutations would silently occur without corresponding events.

**Fix applied:** Changed `event_emitter` to a required parameter with a `None` guard:
```python
def __init__(self, session: AsyncSession, event_emitter: Any) -> None:
    if event_emitter is None:
        raise ValueError("event_emitter is required for SchedulerRepository")
    self._session = session
    self._event_emitter = event_emitter
```

Also removed the silent `None` guard in `_emit_scheduler_event`.

**Hostile finding — second defect:** `SchedulerWorker._build_event_emitter` at `services/core/app/scheduler/worker.py:114` caught all exceptions and returned `None`. The worker then constructed `SchedulerRepository(session, event_emitter=None)`, allowing production scheduler state mutations without events. The report incorrectly claimed "production construction sites: None found."

**Fix applied:** Removed the `try/except` in `_build_event_emitter` so that emitter construction failures propagate loudly instead of silently dropping events:
```python
def _build_event_emitter(self, session: Any) -> Any:
    from app.events.repository import EventRepository
    from app.events.emitter import EventEmitter
    event_repo = EventRepository(session)
    return EventEmitter(event_repo)
```

**Production construction sites:** Exactly one — `SchedulerWorker._poll_once` at `worker.py:88`. After fix, any failure to construct the emitter aborts the worker iteration rather than proceeding without events.

**Verification:** All 114 scheduler tests pass with the required parameter. Tests that do not exercise event emission pass a no-op `AsyncMock()`.

### 2. Transaction Boundary Verification

**VERDICT: PASS**

Verified exact order in all four methods:

| Method | State mutation | flush | Event emission | Return |
|--------|---------------|-------|----------------|--------|
| `claim_next_occurrence` | UPDATE in `_do_claim` | `await self._session.flush()` | `await self._emit_scheduler_event(...)` | returns claimed job |
| `finalize_triggered` | UPDATE | `await self._session.flush()` | `await self._emit_scheduler_event(...)` | returns triggered job |
| `finalize_failed` | UPDATE | `await self._session.flush()` | `await self._emit_scheduler_event(...)` | returns bool |
| `reset_expired_claim_to_pending` | UPDATE | `await self._session.flush()` | `await self._emit_scheduler_event(...)` | returns recovered job |

- Same `AsyncSession` used throughout.
- No internal `commit()` in any of the four methods.
- `_retry_on_locked` performs `session.rollback()` only on "database is locked" errors; this is a recovery mechanism, not a normal-path transaction boundary.
- Event emission failure raises `EventEmissionError`, which propagates to the caller. The caller's transaction is rolled back by the framework (FastAPI `get_db()` dependency), preventing partial scheduler state from becoming durable.

**Hostile test added:** `test_event_emission_failure_prevents_scheduler_state_commit` verifies that when event emission fails, rolling back the session returns the job to `PENDING`.

### 3. EventRepository Savepoint Semantics

**VERDICT: PASS**

Verified `EventRepository.record()` implementation:

```python
try:
    async with self._session.begin_nested():
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
except IntegrityError as exc:
    existing = await self.get(event.event_id)
    if existing is not None and _events_equivalent(existing, event):
        return existing
    raise EventIntegrityConflict(...) from exc
```

**Case A — normal insert:** Both scheduler state and event become durable on outer commit. Verified by existing tests.

**Case B — exact duplicate event:** `begin_nested()` creates a savepoint. The duplicate `IntegrityError` rolls back only the savepoint. The outer scheduler transaction remains usable. Verified by `test_duplicate_event_shared_session_savepoint`.

**Case C — conflicting duplicate:** Same `event_id` with different content raises `EventIntegrityConflict`. The savepoint is rolled back. The outer scheduler transaction remains usable. Verified by `test_conflicting_event_raises_without_corrupting_outer`.

**Case D — unrelated insert failure:** Any exception inside `begin_nested()` rolls back only the savepoint. The outer transaction is unaffected.

### 4. Claim Sequence Atomicity

**VERDICT: PASS**

**SQL:**
```sql
UPDATE scheduled_jobs
SET
    status = 'CLAIMED',
    claim_sequence = claim_sequence + 1,
    ...
WHERE
    job_id = :job_id
    AND status = 'PENDING'
    AND next_run_at <= :now
    AND enabled = 1
RETURNING *
```

**Verified properties:**
- `claim_sequence` increments only when the claim succeeds (WHERE clause requires `status = 'PENDING'`).
- Two concurrent claimers cannot both receive the same sequence: SQLite serializes the UPDATE; only one matches `status = 'PENDING'`.
- Failed claim attempts do not increment it: `claim_next_occurrence` returns `None` without incrementing.
- Recovery does not increment it: `reset_expired_claim_to_pending` does not modify `claim_sequence`.
- Next successful claim after recovery receives a new sequence: verified by `test_repeated_claim_different_event_ids`.

**Hostile test added:** `TestClaimSequence` verifies increment, persistence, and non-increment on failed claims.

### 5. Repeated Event Identity

**VERDICT: PASS**

**Lifecycle trace:**
```
PENDING
→ CLAIMED(sequence=1) → event_id = sched_evt_<hash(X, 1, "claimed")>
→ lease expires
→ RECOVERED → event_id = sched_evt_<hash(X, 1, "recovered")>
→ PENDING
→ CLAIMED(sequence=2) → event_id = sched_evt_<hash(X, 2, "claimed")>  ← DIFFERENT
```

**Verified by `test_repeated_claim_different_event_ids`:**
- Same `occurrence_id`
- Sequence 1 vs 2
- Two different CLAIMED event IDs
- Both events remain in EventStore

**Duplicate replay:** Same `occurrence_id + claim_sequence + event_type` produces the same event_id and does not create a second event. Verified by `test_duplicate_event_shared_session_savepoint`.

### 6. Event ID Canonicalization

**VERDICT: PASS**

**Implementation:**
```python
def make_scheduler_event_id(occurrence_id: str, claim_sequence: int, event_type: str) -> str:
    message = f"{occurrence_id}\x00{claim_sequence}\x00{event_type}"
    digest = hashlib.sha256(message.encode()).hexdigest()[:32]
    return f"sched_evt_{digest}"
```

- Uses `\x00` (NUL) as explicit delimiter between components.
- `occurrence_id` is a hex string, `claim_sequence` is an integer, `event_type` is an enum string. None can contain `\x00`.
- Same logical identity → same hash.
- Different occurrence → different hash.
- Different claim_sequence → different hash.
- Different event type → different hash.

**Length verification:**
- Prefix `sched_evt_`: 10 characters
- Hex digest: 32 characters
- Total: 42 characters
- DB column: `String(64)`
- 42 <= 64: **VALID**

### 7. Event Payload Completeness

**VERDICT: PASS**

**CLAIMED payload** includes `claim_sequence`:
```json
{
  "job_id": "sched_payload",
  "occurrence_id": "occurrence_sched_payload_30d2e68d6741",
  "claim_sequence": 1,
  "schedule_type": "cron",
  "schedule_expr": "*/5 * * * *",
  "target_type": "task",
  "target_id": "task_abc"
}
```

History consumers can now distinguish CLAIMED sequence 1 from sequence 2 without reconstructing information from the event ID.

**TRIGGERED payload:** Contains `occurrence_id` and `execution_id`.
**FAILED payload:** Contains `reason_code` and bounded `reason_detail` (200 chars max).
**RECOVERED payload:** Contains `reason: "lease_expired"`. No `claim_token` or lease token.

### 8. Failed / Recovered Semantics

**VERDICT: PASS**

**FAILED:**
- Can only be emitted by the current claimant: WHERE clause requires `status = 'CLAIMED'`, `occurrence_id = :occurrence_id`, `claim_token = :claim_token`, `lease_until > :now`.
- Once failed, the job stays in `FAILED` state. No further claims or finalizations are possible without manual intervention.
- Reason codes are bounded strings passed by the caller.
- `reason_detail` is bounded to 200 characters.
- Event identity `(occurrence_id, claim_sequence, "scheduler.occurrence.failed")` is valid because FAILED can only happen once per claim.

**RECOVERED:**
- Can only represent an actual lease-expiration recovery: WHERE clause requires `status = 'CLAIMED'`, `lease_until <= :now`.
- Does not expose `claim_token`: payload only contains `reason: "lease_expired"`.
- Cannot create duplicate lifecycle events: once recovered, status becomes `PENDING`. A second recovery attempt matches zero rows.
- No event falsely implies execution completion.

### 9. Correlation / Causation

**VERDICT: PASS**

| Event | correlation_id | causation_id |
|-------|----------------|--------------|
| CLAIMED | `job_id` | None |
| TRIGGERED | `job_id` | Deterministic event_id of corresponding CLAIMED event |
| FAILED | `job_id` | Deterministic event_id of corresponding CLAIMED event |
| RECOVERED | `job_id` | Deterministic event_id of corresponding CLAIMED event |

- No `claim_token` in correlation or causation.
- Causation IDs fit the `Event` schema (`String(64)`).
- Repeated claims maintain distinguishable history because each claim has a different `claim_sequence` and thus a different CLAIMED event_id.

### 10. Database Migration / Compatibility

**VERDICT: PASS**

**Migration mechanism:** `init_db()` checks if `scheduled_jobs` exists. If it exists, checks for `claim_sequence` column. If missing, executes `ALTER TABLE scheduled_jobs ADD COLUMN claim_sequence INTEGER NOT NULL DEFAULT 0`.

**Verified by `TestDatabaseMigration::test_legacy_db_receives_claim_sequence_column`:**
- Creates a legacy database without `claim_sequence`.
- Runs `init_db()`.
- Verifies `claim_sequence` column exists.
- Verifies existing rows have `claim_sequence = 0` (default).

**Fresh DB:** Still works correctly (CREATE TABLE includes `claim_sequence`).

### 11. Event Type Persistence

**VERDICT: PASS**

New `EventType` enum values:
```python
SCHEDULER_OCCURRENCE_CLAIMED = "scheduler.occurrence.claimed"
SCHEDULER_OCCURRENCE_TRIGGERED = "scheduler.occurrence.triggered"
SCHEDULER_OCCURRENCE_FAILED = "scheduler.occurrence.failed"
SCHEDULER_OCCURRENCE_RECOVERED = "scheduler.occurrence.recovered"
```

Verified end-to-end:
- Python enum → ORM `EventModel` → SQLite `TEXT` column → `EventRepository.get/list` → API serialization.
- Existing Event types remain unaffected.

### 12. Security Hostile Check

**VERDICT: PASS**

Verified no secrets in:
- Event payloads: `claim_token` does not appear in any payload construction.
- Event metadata: no secrets.
- Event IDs: deterministic SHA-256 hash, no secrets.
- Correlation/causation IDs: `job_id` and deterministic event IDs, no secrets.
- Exception/logging output: no secrets.

`claim_token` references in the codebase are limited to:
- `generate_claim_token()` function call
- SQLAlchemy model column mapping
- SQL WHERE clauses for ownership validation
- Setting to `None` on recovery/release

### 13. Full Regression

**VERDICT: PASS**

### Focused 12.2E tests
```bash
python -m pytest tests/test_scheduler_events.py -q
```
Result: **26 passed**

### Scheduler 12.2A–12.2C regression
```bash
python -m pytest tests/test_scheduler_repository.py tests/test_scheduler_claim.py tests/test_scheduler_handoff.py -q
```
Result: **114 passed**

### Scheduler worker tests
```bash
python -m pytest tests/test_scheduler_worker.py -q
```
Result: **10 passed**

### Combined focused suite
```bash
python -m pytest tests/test_scheduler_repository.py tests/test_scheduler_claim.py tests/test_scheduler_handoff.py tests/test_scheduler_events.py tests/test_scheduler_worker.py tests/test_event_foundation.py tests/test_history_integration.py tests/test_history_api.py -q
```
Result: **286 passed**

No pre-existing failures identified in 12.2E-affected tests. All newly introduced tests pass.

### 14. Diff / Scope Audit

**VERDICT: PASS**

Changes are strictly limited to Stage 12.2E requirements:
- Schema/model changes for `claim_sequence`
- Scheduler repository event emission enforcement
- Scheduler worker event emitter construction fix
- EventRepository savepoint fix
- Event type enum additions
- Test updates for required `event_emitter`
- New test file for 12.2E

No unrelated refactors, architecture expansion, scheduler worker implementation, REST implementation, UI changes, or speculative abstractions.

### 15. Final Decision

**STAGE_12_2E_FINAL_HOSTILE_PASS**

All semantic invariants are proven:
1. Event emission is enforced: `event_emitter` is required and `None` is rejected at construction time.
2. Transaction boundaries are correct: `state mutation → flush → event emission → caller commit`.
3. EventRepository savepoint semantics are correct: duplicate/conflicting events do not corrupt outer transaction.
4. Claim sequence is atomic and durable.
5. Repeated claims produce different event IDs.
6. Event IDs use unambiguous canonicalization with NUL delimiters and 128-bit SHA-256.
7. Event payloads include `claim_sequence` where semantically relevant.
8. Failed/recovered semantics are correct and bounded.
9. Correlation/causation links are deterministic.
10. Database migration handles existing databases.
11. Event types persist end-to-end.
12. No secrets in events or event IDs.
13. Full regression passes (286 tests).
14. Scope is limited to 12.2E.
