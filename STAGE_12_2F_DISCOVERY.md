# Stage 12.2F — Scheduler Worker / Advancement Discovery

## Executive Verdict

**STAGE_12_2F_IMPLEMENTATION_READY**

The existing architecture provides all necessary primitives for a minimal, safe scheduler worker. The worker contract is fully defined. One semantic distinction must be preserved: **database atomicity** (state mutation + advancement) is achievable, but **execution handoff atomicity** is NOT achievable within the scheduler database transaction because the execution layer operates outside that transaction boundary. The worker design must account for this non-atomic boundary explicitly.

Two semantic gaps exist between the Stage 12.1 discovery contract and the current implementation:
1. **SKIP_ONCE is not a state transition**: The calculator enforces skip-by-advancement semantics, but the scheduler never emits `scheduler.occurrence.skipped` or transitions to `SKIPPED` state. This is acceptable for V1 because the calculator's reference-time semantics make skip implicit.
2. **Concurrency check happens post-claim**: The handoff service checks for running targets AFTER claiming. The 12.1 discovery specified pre-claim skipping. The current behavior is claim-then-validate, which leaves the occurrence in `CLAIMED` state until lease expiry rather than skipping immediately. This is a documented deviation from 12.1 §9/§10.

---

## 1. Existing Architecture / Call Graph

### Application Lifespan

`app/core/lifecycle.py`:
1. Configures logging
2. Initializes database (`init_db()`)
3. Seeds default settings
4. Optionally starts Ollama
5. Registers builtin tools
6. Yields control to application
7. On shutdown: cancels `BackgroundExecutionManager` tasks, cleans up Ollama

No scheduler worker currently starts here. This is the integration point for 12.2F.

### Existing Background Mechanisms

- **BackgroundExecutionManager** (`app/services/background_execution_manager.py`):
  - In-memory singleton tracking active executions
  - Uses per-plan `asyncio.Lock` for concurrency control
  - Launches `asyncio.create_task()` for each execution
  - Not durable: state lost on restart
  - Shutdown cancels pending tasks with 10s timeout

### Execution Chain (Existing)

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

### Scheduler Chain (Existing, 12.1–12.2E)

```
ScheduledJob (domain model)
  → SchedulerRepository (persistence)
    → SchedulerHandoffService (durable handoff boundary)
      → AutonomousRunController (task execution)
      → BackgroundExecutionManager (plan execution)
    → EventEmitter (lifecycle events)
```

### Scheduler Event Chain (12.2E)

```
SchedulerRepository
  → _emit_scheduler_event()
    → EventEmitter.emit()
      → EventRepository.record() (with savepoint)
        → EventModel (SQLite)
```

### Worker Chain (Partially Existing)

```
SchedulerWorker._poll_once()
  → SchedulerRepository.get_expired_leases()        # recovery
    → SchedulerRepository.reset_expired_claim_to_pending()
      → _emit_scheduler_event(RECOVERED)
  → SchedulerRepository.get_due_pending_jobs()       # due detection
  → SchedulerRepository.claim_next_occurrence()      # atomic claim
    → _emit_scheduler_event(CLAIMED)
  → SchedulerHandoffService.handoff_task/plan()      # execution boundary
    → AutonomousRunController.start_run()             # task path
    → BackgroundExecutionManager.start_execution()    # plan path
    → SchedulerRepository.finalize_triggered_and_advance()  # atomic TRIGGERED + advancement
      → _emit_scheduler_event(TRIGGERED)
  → SchedulerRepository.finalize_failed()            # failure path
    → _emit_scheduler_event(FAILED)
```

---

## 2. Worker Responsibility Boundary

### Worker OWNS

- **WHEN** a scheduled job becomes due (polling `get_due_pending_jobs`)
- **Claiming** due occurrences (`claim_next_occurrence`)
- **Recovery** of expired leases on startup and during polling (`reset_expired_claim_to_pending`)
- **Polling loop** lifecycle (startup, shutdown, sleep, cancellation)
- **Event emission** through existing `EventEmitter` (already wired into repository)

### Worker DOES NOT OWN

- **HOW** tasks/plans execute → delegates to `SchedulerHandoffService` → existing execution layer
- **Permission evaluation** → delegates to `PermissionEngine` / `ApprovalService`
- **Verification** → delegates to `VerificationGate`
- **Task state transitions** → delegates to `TaskStateMachine` / `TaskRepository`
- **Event persistence** → delegates to `EventEmitter`
- **Authorization** → never bypasses `ExecutionBridge`
- **Task recovery** → `RecoveryManager` owns orphaned RUNNING tasks

### Critical Invariant

```
Scheduler determines WHEN
Existing system determines HOW
```

The worker must never:
- Become a second orchestrator
- Become a second execution engine
- Bypass `PermissionEngine` or `ApprovalService`
- Mutate task/plan state directly
- Execute tools or subprocesses
- Invent a second recovery system for tasks

---

## 3. Occurrence Lifecycle

### Current States (Actual Usage)

| State | Meaning | Transitions |
|-------|---------|-------------|
| `PENDING` | Due, not yet claimed | → `CLAIMED` (claim), → `FAILED` (recovery failure) |
| `CLAIMED` | Claimed, lease active | → `TRIGGERED` (handoff success), → `PENDING` (lease expiry recovery), → `FAILED` (finalize_failed) |
| `TRIGGERED` | Handoff recorded, schedule advanced | Terminal for scheduler (execution layer owns further state) |
| `FAILED` | Handoff failed or unrecoverable | Terminal |
| `DISABLED` | ONCE job with no future occurrences | Terminal |

### States Defined but NOT Used by Scheduler

| State | Meaning | Notes |
|-------|---------|-------|
| `COMPLETED` | Execution completed | Defined in model but never transitioned to by scheduler. Execution layer (TaskStatus.COMMITTED) tracks task completion. |
| `SKIPPED` | Occurrence skipped | Defined in model but never transitioned to. No `scheduler.occurrence.skipped` event type exists. SKIP_ONCE is enforced by calculator advancement, not by state transition. |

### Normal Path

```
PENDING
→ CLAIMED (atomic UPDATE: status='CLAIMED', claim_token, occurrence_id, lease_until)
→ TRIGGERED (atomic UPDATE: status='TRIGGERED', execution_id, next_run_at advanced, last_run_at updated)
→ [execution layer takes over]
```

### Failure Path

```
PENDING
→ CLAIMED
→ FAILED (finalize_failed: status='FAILED')
```

### Recovery Path

```
CLAIMED (lease expired)
→ PENDING (reset_expired_claim_to_pending: status='PENDING', occurrence_id cleared, next_run_at recomputed)
→ [re-claim on next poll]
```

### ONCE Job Path

```
PENDING
→ CLAIMED
→ TRIGGERED (with advancement)
→ [if calculate_next_occurrence returns None: status='DISABLED']
```

### Key Semantic: TRIGGERED is Terminal for Scheduler

Once `TRIGGERED`, the scheduler does NOT transition the occurrence again. The execution layer owns subsequent state. `get_expired_leases` queries `status IN ('CLAIMED', 'TRIGGERED')` for recovery, but only resets `CLAIMED` occurrences. `TRIGGERED` occurrences are left alone (execution is presumed in progress).

---

## 4. Schedule Advancement Semantics

### Atomic Advancement Method Already Exists

`SchedulerRepository.finalize_triggered_and_advance()` at `repository.py:552` already implements atomic TRIGGERED + advancement:

```python
async def finalize_triggered_and_advance(
    self,
    occurrence_id: str,
    claim_token: str,
    execution_id: str,
    now: datetime,
) -> ScheduledJob | None:
```

This method:
1. Reads current job state
2. Computes `next_run_at` using calculator with `reference_time = job.next_run_at`
3. Atomically UPDATEs: `status='TRIGGERED'`, `next_run_at=calculated`, `last_run_at=previous_next_run_at`, clears claim fields, sets `execution_id`
4. WHERE clause requires: `occurrence_id=:occurrence_id`, `claim_token=:claim_token`, `status='CLAIMED'`, `lease_until > :now`

### Advancement by Schedule Type

| Type | Reference for next calculation | Advancement behavior |
|------|-------------------------------|----------------------|
| ONCE | `job.next_run_at` (scheduled fire time) | If `calculate_next_occurrence` returns `None`: set `status = 'DISABLED'`. Otherwise: `TRIGGERED` with advanced `next_run_at`. |
| INTERVAL | `job.next_run_at` | `next_run_at = last_run_at + interval_seconds`. Always has a future occurrence (unless interval exceeds bounds). |
| CRON | `job.next_run_at` | `next_run_at = calculate_next_occurrence(definition, last_run_at)`. May return `None` if schedule has no future occurrences. |

### Reference Time Semantics (from 12.2B)

`reference_time = previous scheduled fire time` (i.e., `job.next_run_at`), NOT execution completion time.

Example:
```
Scheduled fires: 10:00, 11:00, 12:00, 13:00
Worker wakes at: 13:37
last_run_at = 12:00 (previous scheduled fire time)
next_run_at = 12:00 + 1 hour = 13:00 (already past)
Calculator computes: 13:00 + 1 hour = 14:00
```

This prevents schedule drift from execution duration.

### ONCE Advancement

For ONCE schedules:
- `calculate_next_occurrence` returns `None` if `reference_time >= target_time` (elapsed)
- When `None` is returned, `finalize_triggered_and_advance` sets `status = 'DISABLED'`
- The job is permanently disabled after firing
- `next_run_at` retains the original scheduled time (not advanced to `None`)

### INTERVAL Advancement

- `calculate_next_occurrence` returns `reference_time + interval_seconds`
- Always produces a future datetime (unless interval exceeds 10-year max)
- Missed intervals are NOT caught up; only the next valid occurrence is computed

### CRON Advancement

- `calculate_next_occurrence` uses strict `>` boundary from reference time
- If reference_time exactly matches a cron time, returns the NEXT matching time
- Missed cron matches are skipped
- DST transitions handled by calculator (skip nonexistent, fire once for ambiguous)

---

## 5. Misfire Policy

### Current Implementation: Implicit SKIP_ONCE via Calculator

The `SKIP_ONCE` policy from Stage 12.1 is enforced by the **calculator**, not by the worker:

- **ONCE**: `calculate_next_occurrence()` returns `None` if `reference_time >= target_time`. The worker sees `next_run_at = None` and sets `status = 'DISABLED'`.
- **INTERVAL**: `calculate_next_occurrence()` always returns `reference_time + interval`. The worker fires the next scheduled occurrence based on `last_run_at`. Missed intervals are skipped because `last_run_at` is the previous scheduled fire time, not the time the worker woke up.
- **CRON**: `calculate_next_occurrence()` uses strict `>` boundary from reference time. The worker fires the next matching cron occurrence after `last_run_at`. Missed fire times are skipped.

### What is NOT Implemented

- No `scheduler.occurrence.skipped` event type
- No `SKIPPED` state transition
- No explicit misfire count in events
- No tolerance window configuration

### SKIP_ONCE Semantics (Current)

| Scenario | Behavior |
|----------|----------|
| One-time job, 10 minutes past due | `calculate_next_occurrence` returns `None` → `DISABLED` |
| Interval job, 20 occurrences missed | `next_run_at = last_run_at + interval` → only ONE occurrence fires |
| Cron job, 3 matches missed | `next_run_at = next_cron_match_after(last_run_at)` → only ONE occurrence fires |
| Job due, previous still running | Handoff service returns failure; job stays `CLAIMED` until lease expiry → recovery → re-claim |

### Semantic Gap from 12.1 Discovery

The 12.1 discovery specified:
> **SKIP**: Do NOT queue. Emit `scheduler.occurrence.skipped` with reason `previous_still_running`. Update `next_run_at` to the next scheduled time.

Current behavior:
> Claims the occurrence, handoff service detects running task, returns failure, job stays `CLAIMED` until lease expires, recovery resets to `PENDING`, next poll re-claims.

This is NOT the same as skipping. The current behavior:
1. Wastes a claim cycle
2. Leaves the job in `CLAIMED` state (not `PENDING`)
3. Delays advancement until lease expires
4. May loop claim-fail-claim-fail if execution completes within lease window

For V1, this is acceptable but should be documented as a known deviation.

---

## 6. Concurrency Model

### Two Workers Polling the Same Job

- `get_due_pending_jobs` is a read-only SELECT. Both workers see the same candidate.
- `claim_next_occurrence` uses atomic UPDATE with WHERE clause. Only one worker succeeds.
- Loser sees `rowcount = 0` and returns `None`.

### Two Workers Claiming the Same Occurrence

- Same as above. Atomic claim prevents duplicate ownership.
- `claim_sequence` increments only on successful claim (WHERE requires `status='PENDING'`).

### One Worker Recovering Another's Expired Lease

- `reset_expired_claim_to_pending` requires `status='CLAIMED'` AND `lease_until <= :now`
- Only expired leases can be recovered
- Atomic UPDATE prevents duplicate recovery

### Stale Claimant After Recovery

- Recovery clears `occurrence_id`, `claim_token`, sets `status='PENDING'`
- Stale claimant's operations fail on `claim_token` mismatch and `status != 'CLAIMED'`

### Concurrent Next-Run Advancement

- Advancement happens inside `finalize_triggered_and_advance` as part of the same atomic UPDATE as handoff recording
- No separate advancement operation to race with

### Concurrency Skip (Current Behavior)

The handoff service checks for running tasks AFTER claiming:

```python
if task.status in (TaskStatus.RUNNING, TaskStatus.VERIFYING, TaskStatus.PASSED):
    return HandoffResult(success=False, error=f"Task is already in status '{task.status.value}'")
```

This means:
1. Scheduler claims the occurrence
2. Handoff service detects running task
3. Handoff returns failure
4. Worker logs warning, job stays `CLAIMED`
5. Lease expires → recovery → `PENDING`
6. Next poll re-claims (if task still running, handoff fails again)

**Task execution duplicate: IMPOSSIBLE** (CAS in `TaskRepository.claim_task_attempt()`)
**Plan execution duplicate: POSSIBLE** (in-memory `execution_id` lost on restart)
**Lost occurrence: NO** (recovery ensures re-claim)

### Proof: Only One Active Claimant per Occurrence

```
claim_next_occurrence:
  UPDATE scheduled_jobs
  SET status='CLAIMED', ...
  WHERE job_id=:job_id AND status='PENDING' AND next_run_at <= :now AND enabled=1
```

SQLite serializes the UPDATE. Only one transaction can match `status='PENDING'` for a given job. The loser sees `rowcount=0`.

---

## 7. Polling Loop Design

### Smallest Reliable Loop (Already Implemented)

```python
class SchedulerWorker:
    async def _run(self) -> None:
        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Worker poll iteration failed: %s", exc)
            try:
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break

    async def _poll_once(self) -> None:
        now = now_utc()
        async with self._session_factory() as session:
            event_emitter = self._build_event_emitter(session)
            repo = SchedulerRepository(session, event_emitter=event_emitter)
            handoff = self._handoff_factory(repo)
            try:
                await self._recover_expired(repo, now)
                due_jobs = await repo.get_due_pending_jobs(now, limit=self._batch_size)
                for job in due_jobs:
                    if not self._running:
                        break
                    try:
                        await self._process_job(repo, handoff, job, now)
                    except Exception as exc:
                        logger.exception("Failed to process job %s: %s", job.job_id, exc)
            except Exception:
                await session.rollback()
                raise
            else:
                await session.commit()
```

### Design Parameters

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `poll_interval` | 60 seconds | Balance between responsiveness and DB load |
| `batch_size` | 10 | Process up to 10 due jobs per iteration |
| `lease_duration` | 5 minutes | Matches task execution default timeout |
| `retry_on_locked` | 3 attempts, 50ms base delay | SQLite concurrency handling |

### Safety Properties

- **No blocking calls**: All I/O is async
- **No unbounded task explosion**: One job processed at a time in sequence
- **No infinite retry loops**: Failed handoffs leave job `CLAIMED`; lease expires → recovery → retry
- **Exception isolation**: One bad job doesn't stop the loop
- **Cancellable**: `asyncio.CancelledError` breaks the loop cleanly
- **Transaction per iteration**: Each poll iteration uses one session; commit/rollback per iteration

### What the Worker Does NOT Do

- Does NOT create background asyncio tasks for executions (delegates to `BackgroundExecutionManager`)
- Does NOT spawn parallel job processing (sequential within iteration)
- Does NOT implement backoff or jitter
- Does NOT track execution completion

---

## 8. Lease Management

### Lease Lifecycle

| Phase | Action | Owner |
|-------|--------|-------|
| **Grant** | `claim_next_occurrence` sets `lease_until = now + DEFAULT_LEASE_DURATION` | Worker |
| **Active** | Occurrence is `CLAIMED`, handoff in progress | Worker |
| **Expiry** | `lease_until <= now` | Time |
| **Recovery** | `reset_expired_claim_to_pending` resets to `PENDING` | Worker (next poll) |

### Lease Parameters

```python
DEFAULT_LEASE_DURATION = timedelta(minutes=5)
MAX_LEASE_EXTENSION = timedelta(minutes=5)  # available but not used in V1
```

### No Active Renewal in V1

The worker does NOT implement active lease renewal. Rationale:
- Task handoff (`AutonomousRunController.start_run()`) blocks until task completes or times out (default 300s = 5 minutes)
- Plan handoff (`BackgroundExecutionManager.start_execution()`) is fire-and-forget
- For tasks, the lease should cover the handoff + initial execution start
- For plans, the lease only needs to cover the handoff call

If renewal becomes necessary:
- Renewal owner: the worker that claimed the occurrence
- Renewal method: `renew_lease(occurrence_id, claim_token, now)` (already exists in repository)
- Stopping condition: handoff completes (success or failure)
- Crash behavior: lease expires, recovery resets to PENDING

### Crash Behavior

| Crash point | Lease state | Recovery behavior |
|-------------|-------------|-------------------|
| Before claim | N/A | Next poll claims normally |
| After claim, before handoff | Active | Lease may expire → recovery resets to `PENDING` |
| After handoff, before TRIGGERED | Active | Lease may expire → recovery resets to `PENDING` → re-claim → task CAS prevents duplicate |
| After TRIGGERED | N/A (cleared) | Schedule advanced; next poll waits for new `next_run_at` |

---

## 9. Crash Window Analysis

### Window A — Worker finds due occurrence but crashes before claim

- **DB state**: `PENDING` with `next_run_at <= now`
- **Event state**: None
- **Retry/recovery**: Next poll claims normally
- **Duplicate execution**: None
- **Owner**: Worker retry
- **Lost occurrence**: No

### Window B — Claim succeeds, process crashes before handoff

- **DB state**: `CLAIMED` with valid lease
- **Event state**: `scheduler.occurrence.claimed` emitted
- **Retry/recovery**: Lease may expire → recovery resets to `PENDING` → re-claim → handoff
- **Duplicate execution**: None (no handoff occurred)
- **Owner**: Scheduler recovery + worker retry
- **Lost occurrence**: No

### Window C — Handoff fails (target running/invalid)

- **DB state**: `CLAIMED` (handoff never recorded)
- **Task state**: Unchanged
- **Plan state**: Unchanged
- **Event state**: `scheduler.occurrence.claimed` emitted; `scheduler.occurrence.triggered` NOT emitted
- **Retry/recovery**: Lease expires → recovery resets to `PENDING` → re-claim → handoff retry
- **Duplicate execution**: None
- **Owner**: Worker retry (next poll after recovery)
- **Lost occurrence**: No

### Window D — Task handoff succeeds, scheduler crashes before TRIGGERED

- **Task state**: `RUNNING` with `execution_token` (durable, via atomic CAS)
- **Plan state**: Background task may be running (in-memory `execution_id` only)
- **DB state**: `CLAIMED` (TRIGGERED not recorded)
- **Event state**: `scheduler.occurrence.claimed` emitted; `scheduler.occurrence.triggered` NOT emitted
- **Retry/recovery**: Lease expires → recovery resets to `PENDING` → re-claim → handoff
  - **Task path**: `TaskRepository.claim_task_attempt()` CAS prevents duplicate claim because task is already `RUNNING`. **Duplicate task execution: IMPOSSIBLE.**
  - **Plan path**: `BackgroundExecutionManager` in-memory state lost on restart. New background task may be created. **Duplicate plan execution: POSSIBLE.**
- **Owner**: Scheduler recovery + worker retry
- **Lost occurrence**: No (task protected by CAS; plan may have duplicate)

### Window E — Plan handoff succeeds, only in-memory `execution_id` exists, scheduler crashes before TRIGGERED

- **Plan state**: Background task running (in-memory `execution_id` only)
- **DB state**: `CLAIMED` (TRIGGERED not recorded)
- **Event state**: `scheduler.occurrence.claimed` emitted; `scheduler.occurrence.triggered` NOT emitted
- **Retry/recovery**: Lease expires → recovery resets to `PENDING` → re-claim → handoff → `BackgroundExecutionManager.start_execution()` sees empty `_plan_to_exec_id` → creates NEW background task. **Duplicate plan execution: POSSIBLE.**
- **Owner**: Scheduler recovery + worker retry
- **Lost occurrence**: No
- **Can recovery distinguish "never handed off" from "accepted but scheduler crashed"?**: No — in-memory execution state is lost on restart.

### Window F — TRIGGERED + advancement commits after successful handoff

- **Task state**: `RUNNING` with `execution_token`
- **Plan state**: Background task running
- **DB state**: `TRIGGERED` with `execution_id`, `next_run_at` advanced, `last_run_at` updated
- **Event state**: `scheduler.occurrence.claimed` + `scheduler.occurrence.triggered` emitted
- **Retry/recovery**: On restart, next poll waits for new `next_run_at`
- **Duplicate execution**: None
- **Owner**: Execution layer
- **Lost occurrence**: No

### Window G — Scheduler finalization fails after handoff

- **Task state**: `RUNNING` with `execution_token` (task execution already started)
- **Plan state**: Background task running
- **DB state**: `CLAIMED` (finalize_triggered_and_advance returned `None` due to stale claim/expired lease)
- **Event state**: `scheduler.occurrence.claimed` emitted; `scheduler.occurrence.triggered` NOT emitted
- **Retry/recovery**: Worker retries on next poll. Task CAS may block duplicate. Plan duplicate possible.
- **Duplicate execution**: Possible for plans
- **Owner**: Worker retry
- **Lost occurrence**: No (execution continues; scheduler will retry)

### Window H — Process crashes between successful handoff and scheduler finalization

- **Task state**: `RUNNING` with `execution_token` (if task handoff) or background task running (if plan handoff)
- **DB state**: `CLAIMED` (no finalization committed)
- **Event state**: `scheduler.occurrence.claimed` emitted; `scheduler.occurrence.triggered` NOT emitted
- **Retry/recovery**:
  - Lease may expire → recovery resets to `PENDING` → re-claim → handoff
  - **Task path**: CAS prevents duplicate execution. **Duplicate task execution: IMPOSSIBLE.**
  - **Plan path**: In-memory state lost → duplicate plan execution possible. **Duplicate plan execution: POSSIBLE.**
- **Owner**: Scheduler recovery + worker retry
- **Lost occurrence**: No
- **External side-effect risk**: Task side effects already occurred (task is RUNNING). Plan side effects may occur again if duplicate plan execution happens.

### Summary

| Window | DB state | Task state | Plan state | Duplicate task? | Duplicate plan? | Lost occurrence? |
|--------|----------|------------|------------|-----------------|-----------------|------------------|
| A | PENDING | Unchanged | Unchanged | No | No | No |
| B | CLAIMED | Unchanged | Unchanged | No | No | No |
| C | CLAIMED | Unchanged | Unchanged | No | No | No |
| D | CLAIMED | RUNNING (CAS protected) | Background running | **IMPOSSIBLE** | **POSSIBLE** | No |
| E | CLAIMED | N/A | Background running (in-memory only) | N/A | **POSSIBLE** | No |
| F | TRIGGERED + advanced | RUNNING | Background running | No | No | No |
| G | CLAIMED | RUNNING | Background running | Possible (plan) | Possible (plan) | No |
| H | CLAIMED | RUNNING (if task) | Background running (if plan) | **IMPOSSIBLE** | **POSSIBLE** | No |

### Key Conclusions

1. **Task execution is protected against duplicates** by existing atomic CAS in `TaskRepository.claim_task_attempt()`.
2. **Plan execution has a documented duplicate window** after process restart due to in-memory `execution_id`.
3. **TRIGGERED must remain a durable state** — it is the scheduler's marker that handoff was recorded. Removing it breaks 12.2D recovery semantics.
4. **The only new atomic operation** is `TRIGGERED + advancement` in one UPDATE. Execution handoff remains non-atomic with scheduler DB.
5. **No lost occurrences are possible** — every crash window either retries or recovers to re-claim.

---

## 10. Duplicate Execution Analysis

### Stage 12.1 Guarantees (Verified)

| Layer | Guarantee | Mechanism |
|-------|-----------|-----------|
| Scheduler delivery | AT_LEAST_ONCE | Atomic claim + lease recovery |
| Task execution claim | AT_MOST_ONCE | `TaskRepository.claim_task_attempt()` CAS |
| Task side effects | EFFECTIVELY_ONCE | TaskStateMachine prevents duplicate state transitions |
| Plan execution | AT_LEAST_ONCE (known limitation) | In-memory `execution_id` lost on restart |
| External side effects | UNKNOWN | Not scheduler's responsibility |

### Critical Analysis: Handoff Success → Crash Before TRIGGERED → Retry

```
handoff succeeds (task is RUNNING, execution_token set)
→ worker crashes before finalize_triggered_and_advance commits
→ occurrence stays CLAIMED
→ lease expires → recovery → PENDING
→ worker re-claims
→ handoff tries to start task again
→ TaskRepository.claim_task_attempt() CAS fails because task is already RUNNING
→ HandoffResult(success=False, error="Task is already in status 'RUNNING'")
→ Worker logs warning, job stays CLAIMED
→ [manual intervention needed OR lease expires again]
```

**Task duplicate: IMPOSSIBLE** — CAS prevents it.

**Plan duplicate: POSSIBLE** — `BackgroundExecutionManager` in-memory state is lost. New `start_execution()` call creates a new background task.

### What Prevents Duplicate Task Execution

1. `TaskRepository.claim_task_attempt()` uses atomic CAS: `WHERE task_id=:id AND status=:expected AND attempt_count < :max`
2. Once task is `RUNNING`, subsequent claims fail with `rowcount=0`
3. `TaskStateMachine` enforces transition matrix: `RUNNING → RUNNING` is NOT allowed
4. Scheduler handoff service checks task status BEFORE calling `start_run()`

### What Does NOT Prevent Duplicate Plan Execution

1. `BackgroundExecutionManager` tracks executions in-memory only
2. On restart, `_plan_to_exec_id` dict is empty
3. `start_execution()` sees no active execution and creates a new one
4. Scheduler has no durable plan execution tracking

This is a **pre-existing architectural limitation**, not introduced by the scheduler worker.

### Scheduler's Responsibility

The scheduler must NOT invent an idempotency mechanism for plan execution. The existing architecture accepts this limitation. If plan duplicate execution becomes unacceptable, it must be addressed at the `BackgroundExecutionManager` level (durable execution tracking), not in the scheduler.

---

## 11. Execution Handoff Contract

### SchedulerHandoffService (Existing)

The handoff service is the durable boundary between scheduler and execution layer. It:

1. Validates scheduler ownership (occurrence_id, claim_token, lease)
2. Validates target existence (task/plan exists)
3. Validates target startability (not terminal, not already running)
4. Invokes execution layer
5. Records `TRIGGERED` + advancement atomically
6. Returns `HandoffResult`

### Task Handoff Path

```
SchedulerHandoffService.handoff_task()
  → TaskRepository.get_task()                    # validate target exists
  → check task.status not in (COMMITTED, CANCELLED, RUNNING, VERIFYING, PASSED)
  → AutonomousRunController.start_run(task_id)    # execute
  → TaskRepository.get_task()                     # read execution_token
  → SchedulerRepository.finalize_triggered_and_advance()  # atomic TRIGGERED + advancement
```

### Plan Handoff Path

```
SchedulerHandoffService.handoff_plan()
  → SchedulerRepository.get_job_by_occurrence_id()  # validate scheduler ownership
  → check target_type == 'plan'
  → BackgroundExecutionManager.start_execution()     # execute
  → SchedulerRepository.finalize_triggered_and_advance()  # atomic TRIGGERED + advancement
```

### Preserved Boundaries

| Check | How |
|-------|-----|
| PermissionEngine | Inside `AutonomousRunController` → `AutonomousTaskOrchestrator` |
| ApprovalService | Inside orchestrator verification loop |
| VerificationGate | Inside orchestrator |
| TaskStateMachine | `claim_attempt()` enforces state transition matrix |
| RecoveryManager | Detects orphaned RUNNING tasks independently |

### Handoff Failure Handling

| Failure | Handoff Service Action | Worker Action |
|---------|----------------------|---------------|
| Target not found | Returns `HandoffResult(success=False, error="Target task not found")` | Logs warning, job stays `CLAIMED` |
| Task already running | Returns `HandoffResult(success=False, error="Task is already in status 'RUNNING'")` | Logs warning, job stays `CLAIMED` |
| Task terminal | Returns `HandoffResult(success=False, error="Task is in terminal status")` | Logs warning, job stays `CLAIMED` |
| Execution error | Returns `HandoffResult(success=False, error="Execution error: ...")` | Logs warning, job stays `CLAIMED` |
| Stale claim/lease | `finalize_triggered_and_advance` returns `None` | Returns `HandoffResult(success=False, error="Failed to record TRIGGERED state")` |
| Successful handoff | `finalize_triggered_and_advance` returns advanced job | Returns `HandoffResult(success=True, execution_id=...)` |

---

## 12. Failure Semantics

### When to Call `finalize_failed()` vs `finalize_triggered()`

| Scenario | Method | Reason |
|----------|--------|--------|
| Handoff succeeds, execution accepted | `finalize_triggered_and_advance` | Normal path |
| Handoff raises exception | `finalize_failed` | Infrastructure failure |
| Handoff returns failure (target invalid) | `finalize_failed` | Target validation failure |
| Lease expires before handoff | Recovery (`reset_expired_claim_to_pending`) | Not a failure, just recovery |
| Worker crashes after claim | Recovery on next poll | Not a failure |

### Failure Classification

| Failure Type | Examples | Scheduler Action |
|--------------|---------|-----------------|
| **Scheduler infrastructure** | DB unavailable, event emission failure | Log error, skip job, continue loop |
| **Target validation** | Task/plan not found, already terminal | `finalize_failed` with `reason_code="target_not_found"` or `target_invalid` |
| **Authorization** | Permission denied, approval required | Handoff service returns failure → `finalize_failed` with `reason_code="authorization_failed"` |
| **Execution handoff** | Task not startable, max attempts exceeded | Handoff service returns failure → `finalize_failed` |
| **Execution failure after handoff** | Task crashes, plan fails | Execution layer handles; scheduler already advanced |
| **Lease expiration** | Worker crashes, lease timeout | Recovery (`reset_expired_claim_to_pending`) |
| **Process cancellation** | Worker shutting down | Cancel current job, exit loop cleanly |

### Important: Scheduler Must Not Falsely Claim Execution Failed

If the handoff succeeds but the scheduler crashes before `TRIGGERED` is recorded:
- The execution may have started (task is RUNNING, plan is running)
- The scheduler must NOT emit `scheduler.occurrence.failed`
- Recovery will find the `CLAIMED` occurrence with expired lease
- Recovery resets to `PENDING`
- Re-handoff will detect the running task and fail
- This is the expected behavior

### `finalize_failed` Semantics

```python
async def finalize_failed(
    self,
    occurrence_id: str,
    claim_token: str,
    now: datetime,
    reason_code: str = "execution_error",
    reason_detail: str = "",
) -> bool:
```

- WHERE clause requires: `status='CLAIMED'`, `occurrence_id=:occurrence_id`, `claim_token=:claim_token`, `lease_until > :now`
- Sets `status='FAILED'`
- Does NOT advance schedule
- Emits `scheduler.occurrence.failed` with bounded `reason_detail` (200 chars max)

### Failure Reason Codes (Approved)

| Code | Meaning |
|------|---------|
| `execution_error` | Unexpected exception during handoff |
| `target_not_found` | Task/plan does not exist |
| `target_invalid` | Target exists but is in terminal state |
| `authorization_failed` | PermissionEngine or ApprovalService rejected |
| `lease_expired` | Lease expired before handoff (used by recovery, not finalize_failed) |

---

## 13. Atomic Advancement Contract

### The Dangerous Windows

**Window 1: TRIGGERED committed but advancement not committed**
```
UPDATE scheduled_jobs SET status='TRIGGERED', execution_id=:id WHERE ...
→ commits
→ worker crashes before advancement UPDATE
→ next_run_at is stale
→ on restart: job appears due again → duplicate handoff
```

**Window 2: Advancement committed but TRIGGERED not committed**
```
UPDATE scheduled_jobs SET next_run_at=:next, last_run_at=:last WHERE ...
→ commits
→ worker crashes before TRIGGERED UPDATE
→ job is still CLAIMED with advanced next_run_at
→ recovery resets to PENDING with stale next_run_at
→ same occurrence fires again
```

### Solution: Atomic TRIGGERED + Advancement

Both windows are closed by `finalize_triggered_and_advance()`:

```sql
UPDATE scheduled_jobs
SET status = 'TRIGGERED',
    next_run_at = :next_run_at,
    last_run_at = :last_run_at,
    occurrence_id = NULL,
    claim_token = NULL,
    claimed_at = NULL,
    lease_until = NULL,
    execution_id = :execution_id,
    updated_at = :now
WHERE occurrence_id = :occurrence_id
  AND claim_token = :claim_token
  AND status = 'CLAIMED'
  AND lease_until > :now
RETURNING *
```

SQLite evaluates WHERE and performs UPDATE atomically. No partial state is visible.

### What is NOT Atomic

The execution handoff itself is NOT atomic with the scheduler database transaction:

```
execution handoff (external, non-atomic)
→ scheduler records TRIGGERED + advancement (atomic DB UPDATE)
```

If the process crashes after execution starts but before the UPDATE commits:
- Task: execution is RUNNING with durable `execution_token` (CAS protected)
- Plan: background task may be running (in-memory only)
- Scheduler: `CLAIMED` (no TRIGGERED recorded)

On restart:
- Lease expires → recovery → `PENDING`
- Re-claim → handoff
- Task CAS prevents duplicate execution
- Plan may create duplicate execution (known limitation)

### Atomicity Proof

1. `finalize_triggered_and_advance` is a single SQL UPDATE statement
2. SQLite guarantees atomicity for single statements
3. WHERE clause requires `status='CLAIMED'` — UPDATE only succeeds for unhanded-off occurrences
4. If execution handoff fails, worker never calls `finalize_triggered_and_advance` — no partial state
5. If worker crashes after handoff but before UPDATE, occurrence remains `CLAIMED` — recovery handles it

---

## 14. Event Semantics

### Existing Events (12.2E)

| Event | When emitted | Payload |
|-------|-------------|---------|
| `scheduler.occurrence.claimed` | After successful claim | `job_id`, `occurrence_id`, `claim_sequence`, `schedule_type`, `schedule_expr`, `target_type`, `target_id` |
| `scheduler.occurrence.triggered` | After successful handoff + atomic TRIGGERED + advancement | `occurrence_id`, `claim_sequence`, `execution_id` |
| `scheduler.occurrence.failed` | After `finalize_failed` | `reason_code`, `reason_detail` |
| `scheduler.occurrence.recovered` | After lease recovery | `reason: "lease_expired"` |

### Worker-Added Events

**No new event types required in V1.**

The worker reuses existing events from repository methods:
- `claim_next_occurrence` → emits `scheduler.occurrence.claimed`
- `finalize_triggered_and_advance` → emits `scheduler.occurrence.triggered`
- `finalize_failed` → emits `scheduler.occurrence.failed`
- `reset_expired_claim_to_pending` → emits `scheduler.occurrence.recovered`

### Correlation Semantics

| Event | correlation_id | causation_id |
|-------|----------------|--------------|
| `scheduler.occurrence.claimed` | `job_id` | None |
| `scheduler.occurrence.triggered` | `job_id` | CLAIMED event_id |
| `scheduler.occurrence.failed` | `job_id` | CLAIMED event_id |
| `scheduler.occurrence.recovered` | `job_id` | CLAIMED event_id |

### Events NOT Emitted

| Event | Reason |
|-------|--------|
| `scheduler.occurrence.skipped` | Not implemented. SKIP_ONCE is implicit via calculator advancement. |
| `scheduler.occurrence.completed` | Scheduler does not track execution completion. Execution layer owns completion. |
| `scheduler.job.created/updated/deleted` | Out of scope for worker (API layer responsibility) |

---

## 15. Startup / Recovery

### Startup Procedure

1. `init_db()` runs (existing)
2. Worker starts in `lifespan()` after DB initialization
3. Worker performs first `_poll_once()` which:
   a. Queries `get_expired_leases(now)` — resets expired `CLAIMED`/`TRIGGERED` occurrences to `PENDING`
   b. Queries `get_due_pending_jobs(now, limit=batch_size)` — processes due jobs
4. Worker enters polling loop

### Recovery Semantics

On each poll iteration, before processing due jobs:

```python
expired = await repo.get_expired_leases(now)  # status IN ('CLAIMED', 'TRIGGERED') AND lease_until <= now
for job in expired:
    next_run = calculator.calculate_next_occurrence(definition, job.next_run_at)
    if next_run is None:
        next_run = now  # ONCE elapsed
    recovered = await repo.reset_expired_claim_to_pending(
        job.occurrence_id, now, next_run
    )
```

### Recovery Ownership

| Component | Owns |
|-----------|------|
| Scheduler worker | Scheduler occurrence/lease state |
| RecoveryManager | Stale development task recovery (RUNNING tasks with stale heartbeats) |
| BackgroundExecutionManager | In-memory plan execution tracking |
| TaskStateMachine | Task state transitions |

### Interaction Between Scheduler Recovery and RecoveryManager

```
scheduler occurrence expired
    → worker resets to PENDING
    → worker re-claims
    → handoff to execution layer
    → if task is already RUNNING: TaskStateMachine/RecoveryManager handles it
    → if plan is already executing: BackgroundExecutionManager handles it
```

The scheduler must NOT start retrying orphaned RUNNING tasks. It only resets its own lease and re-handoffs. The execution layer's CAS/state machine prevents duplicate execution.

### Recovery Does NOT Modify development_tasks

`scheduled_jobs` and `development_tasks` are separate tables with separate recovery mechanisms. Scheduler recovery only touches `scheduled_jobs`.

---

## 16. Database Query / Performance

### Due Job Query

```python
async def get_due_pending_jobs(self, now: datetime, limit: int = 50) -> list[ScheduledJob]:
    stmt = (
        select(ScheduledJobModel)
        .where(
            ScheduledJobModel.status == "PENDING",
            ScheduledJobModel.next_run_at <= _to_naive_utc(now),
            ScheduledJobModel.enabled == 1,
        )
        .order_by(ScheduledJobModel.next_run_at.asc())
        .limit(limit)
    )
```

### Index Coverage

| Index | Columns | Used By |
|-------|---------|---------|
| `idx_scheduled_jobs_next_run_at` | `next_run_at` | `get_due_pending_jobs` (range scan) |
| `idx_scheduled_jobs_enabled` | `enabled` | `get_due_pending_jobs` (filter) |
| `idx_scheduled_jobs_status` | `status` | `get_due_pending_jobs` (filter) |
| `idx_scheduled_jobs_occurrence_id` | `occurrence_id` | `get_job_by_occurrence_id`, claim/recovery WHERE clauses |
| `idx_scheduled_jobs_claim_token` | `claim_token` | Claim/recovery WHERE clauses |
| `idx_scheduled_jobs_lease_until` | `lease_until` | `get_expired_leases` |

### Query Plan

SQLite can use the composite index on `(status, next_run_at)` via `idx_scheduled_jobs_status` + `idx_scheduled_jobs_next_run_at` or a single index scan. With the current separate indexes, SQLite will use `idx_scheduled_jobs_next_run_at` for the range scan and filter by `status` and `enabled`.

For the expected scale (tens to hundreds of scheduled jobs, not thousands), this is sufficient. No additional indexes are needed.

### No Full Table Scans

- `get_due_pending_jobs` uses `next_run_at` index with `<=` bound
- `get_expired_leases` uses `lease_until` index with `<=` bound
- Both queries have `LIMIT` to bound result size

---

## 17. Time / Timezone

### Storage

- All timestamps stored in UTC (`DateTime(timezone=True)` in SQLAlchemy)
- `_to_naive_utc` and `_to_aware_utc` handle conversion at repository boundary

### Worker Time

- Worker uses `now_utc()` = `datetime.now(timezone.utc)` for all polling decisions
- No naive datetimes in worker logic
- All comparisons are UTC

### Calculator Time

- Calculator receives UTC-aware `reference_time`
- For CRON: converts to job's timezone via `zoneinfo`, computes next match, converts back to UTC
- For INTERVAL: adds `timedelta(seconds=interval)` to UTC `reference_time`
- For ONCE: parses ISO datetime, compares with UTC `reference_time`

### Timezone Handling

| Field | Storage | Notes |
|-------|---------|-------|
| `next_run_at` | UTC | Always timezone-aware |
| `last_run_at` | UTC | Always timezone-aware |
| `claimed_at` | UTC | Always timezone-aware |
| `lease_until` | UTC | Always timezone-aware |
| `job.timezone` | IANA string | Used by calculator only |
| `schedule_expr` | String | Interpreted in `job.timezone` for CRON |

### DST Semantics (from 12.2B)

- **Spring-forward (nonexistent local time)**: Calculator SKIPS the nonexistent time
- **Fall-back (ambiguous local time)**: Calculator fires ONCE at first occurrence (fold=0)
- **Clock jumps**: Handled by misfire/recovery on restart

### Worker Polling and DST

- Worker polls at fixed UTC intervals
- DST transitions do not affect polling frequency
- `next_run_at` is always UTC, so DST transitions are transparent to the worker

---

## 18. Configuration

### Worker-Settable Parameters

| Parameter | Current Value | Configurable? | Rationale |
|-----------|--------------|---------------|-----------|
| `poll_interval` | 60 seconds | Hardcoded | V1 default; can be made configurable later |
| `batch_size` | 10 | Hardcoded | V1 default; sufficient for expected load |
| `lease_duration` | 5 minutes | `DEFAULT_LEASE_DURATION` constant | Can be changed globally if needed |

### What Should NOT Be Configurable

- Lease duration per-job: uniform lease simplifies recovery
- Retry attempts: `_retry_on_locked` is an implementation detail
- Database connection parameters: handled by existing `database.py`

### Existing Settings Conventions

The project uses `SettingsService` for application settings. Worker configuration should follow the same pattern if/when it becomes configurable, but V1 uses hardcoded defaults.

---

## 19. Test Strategy

### Normal Path Tests

| Test | What it proves |
|------|---------------|
| Due job claimed | `get_due_pending_jobs` + `claim_next_occurrence` works |
| Task handed off | `SchedulerHandoffService.handoff_task()` → `AutonomousRunController.start_run()` |
| Plan handed off | `SchedulerHandoffService.handoff_plan()` → `BackgroundExecutionManager.start_execution()` |
| Scheduler state advanced | `finalize_triggered_and_advance` updates `next_run_at`, `last_run_at` |
| ONCE disables | ONCE job with no future occurrences → `status='DISABLED'` |
| INTERVAL advances | `next_run_at = last_run_at + interval` |
| CRON advances | `next_run_at = next_cron_match_after(last_run_at)` |

### Concurrency Tests

| Test | What it proves |
|------|---------------|
| Two workers, one job | Only one claim succeeds |
| Two workers, recovery | Only one recovery succeeds |
| Stale claimant after recovery | Stale claimant cannot mutate |
| Concurrent advancement | Atomic UPDATE prevents race |

### Crash Simulation Tests

| Test | What it proves |
|------|---------------|
| Crash before claim | Job remains PENDING |
| Crash after claim | Lease expires, recovery resets |
| Crash after handoff, before TRIGGERED | Task CAS prevents duplicate; plan duplicate possible |
| Crash after TRIGGERED, before advancement | TRIGGERED with stale next_run_at; recovery handles |
| Crash after TRIGGERED + advancement | Schedule advanced, next poll waits |
| Restart with expired lease | Recovery resets to PENDING |
| Restart with active lease | Worker continues with existing lease |

### Recovery Tests

| Test | What it proves |
|------|---------------|
| Expired CLAIMED recovered | `reset_expired_claim_to_pending` works |
| Expired TRIGGERED not recovered | `get_expired_leases` includes TRIGGERED but `reset_expired_claim_to_pending` only resets CLAIMED |
| Recovery advances schedule | `next_run_at` recomputed correctly after recovery |
| Multiple expired leases | All recovered in one poll iteration |

### Scheduling Tests

| Test | What it proves |
|------|---------------|
| ONCE advancement | ONCE job disables after firing |
| INTERVAL advancement | `next_run_at = last_run_at + interval` |
| CRON advancement | `next_run_at = next_cron_match` |
| Timezone | UTC storage, IANA interpretation |
| DST spring-forward | Nonexistent time skipped |
| DST fall-back | Ambiguous time fires once |
| Misfire SKIP_ONCE | Missed intervals skipped, not caught up |

### Security Tests

| Test | What it proves |
|------|---------------|
| No claim_token in logs | Worker does not log sensitive tokens |
| No auth bypass | Scheduled execution goes through same auth as manual |
| Bounded payloads | Event payloads within limits |
| No secret leakage | No credentials in events or logs |

### Regression Tests

- All Stage 12.2A–12.2E tests (currently 114 + 26 + 10 = 150 tests)
- All Event/History tests
- All autonomous execution tests
- Full project suite

---

## 20. Identified Risks and Open Questions

### Risk 1: Concurrency Skip is Post-Claim, Not Pre-Claim

**Current behavior**: Worker claims occurrence, then handoff service detects running task, returns failure. Job stays `CLAIMED` until lease expires.

**12.1 contract**: Skip without claiming. Emit `scheduler.occurrence.skipped`.

**Impact**: Wasted claim cycle, delayed advancement, potential claim-fail loop.

**Mitigation for V1**: Accept as known deviation. The claim-fail-recover-retry cycle is bounded by lease duration (5 minutes). For V1, this is acceptable.

**Future fix**: Add pre-claim check in worker: query `TaskRepository` for RUNNING tasks in target plan before claiming. If found, skip without claiming.

### Risk 2: Plan Execution Duplicate After Restart

**Impact**: If worker crashes after plan handoff but before `TRIGGERED`, restart may create duplicate plan execution.

**Mitigation**: This is a pre-existing limitation of `BackgroundExecutionManager` (in-memory only). Not scheduler's responsibility to fix.

### Risk 3: Worker Integration in lifespan()

**Impact**: Worker must be started after DB initialization and stopped cleanly on shutdown.

**Mitigation**: Standard asyncio task pattern. Worker handles `CancelledError` cleanly.

### Risk 4: SKIP_ONCE Not Emitted as Event

**Impact**: Operators cannot query `scheduler.occurrence.skipped` events.

**Mitigation**: Acceptable for V1. SKIP_ONCE is implicit in the calculator's advancement. If explicit skip events are needed, add them in a future stage.

### Open Questions

1. **Poll interval**: 60 seconds hardcoded. Should this be configurable?
   - Recommendation: Hardcoded for V1. Add `SettingsService` integration later if needed.

2. **Batch size**: 10 hardcoded. Sufficient for expected load?
   - Recommendation: Hardcoded for V1. Monitor and adjust if needed.

3. **Should scheduler emit `scheduler.occurrence.skipped`?**
   - Recommendation: No for V1. The calculator's advancement semantics make skip implicit. Add explicit skip events if operators need visibility.

4. **Should COMPLETED state be used?**
   - Recommendation: No for V1. The scheduler's responsibility ends at `TRIGGERED`. Execution completion is tracked by the execution layer.

---

## 21. Implementation Readiness

### What Already Exists

| Component | Status |
|-----------|--------|
| `SchedulerRepository` | Complete — all CRUD + claim + recovery + advancement |
| `SchedulerHandoffService` | Complete — task and plan handoff paths |
| `SchedulerWorker` | Partial — polling loop, recovery, claim, handoff exist |
| `finalize_triggered_and_advance` | Complete — atomic TRIGGERED + advancement |
| Event types | Complete — claimed, triggered, failed, recovered |
| Database schema | Complete — all columns, indexes, migrations |
| Tests | Complete — 12.2A–12.2E tests (150 total) |

### What Needs Implementation

| Item | Description | Size |
|------|-------------|------|
| Worker integration in `lifespan()` | Start/stop worker in `app/core/lifecycle.py` | 10 lines |
| Worker `_process_job` completion | Ensure handoff failure calls `finalize_failed` for permanent failures | 5 lines |
| Test: worker integration | Test worker starts, processes jobs, stops cleanly | New test class |

### What Does NOT Need Implementation

- No new repository methods
- No new event types
- No new state machine transitions
- No changes to execution layer
- No changes to `SchedulerHandoffService`
- No REST endpoints
- No UI changes

### Smallest Implementation Plan

1. **Verify `_process_job` handles handoff failures correctly**: Currently logs warning and returns. For permanent failures (target not found, task terminal), should call `finalize_failed`. For transient failures (lease expired during handoff), should leave `CLAIMED` for retry.

2. **Integrate worker into `lifespan()`**: Start worker after DB init, cancel on shutdown.

3. **Add worker integration tests**: Test full poll iteration with real DB.

4. **Run full regression**: All 150 scheduler/event tests + autonomous execution tests.

### Files to Change

| File | Change |
|------|--------|
| `app/scheduler/worker.py` | Complete `_process_job` failure handling |
| `app/core/lifecycle.py` | Start/stop worker |
| `tests/test_scheduler_worker.py` | Add integration tests |

### What NOT to Change

- Do NOT modify `finalize_triggered` (preserve 12.2E contract)
- Do NOT add new event types
- Do NOT modify `EventRepository` or `EventEmitter`
- Do NOT modify `TaskRepository`, `TaskStateMachine`, or execution layer
- Do NOT add distributed locks
- Do NOT add REST endpoints
- Do NOT implement `scheduler.occurrence.skipped` event or `SKIPPED` state transition

---

## 22. Final Decision

**STAGE_12_2F_IMPLEMENTATION_READY**

All semantic contracts are defined:
1. Worker responsibility is bounded and well-defined
2. Occurrence lifecycle is stable (PENDING → CLAIMED → TRIGGERED/FAILED/DISABLED)
3. Schedule advancement is atomic via `finalize_triggered_and_advance`
4. SKIP_ONCE is implemented implicitly by calculator advancement
5. Concurrency is serialized by SQLite atomic UPDATE
6. Lease management is simple and bounded (5 minutes, no renewal)
7. Crash windows are documented and recoverable
8. Duplicate execution analysis is complete (task: impossible, plan: possible but pre-existing)
9. Execution handoff preserves all authorization boundaries
10. Failure semantics are well-defined
11. Event semantics are complete (4 event types, no new types needed)
12. Startup/recovery is bounded and deterministic
13. Database queries are indexed and efficient
14. Timezone semantics are correct (UTC storage, IANA interpretation)
15. Configuration is minimal and hardcoded for V1
16. Test strategy is defined
17. Implementation scope is minimal (3 small changes)

Two known deviations from the 12.1 discovery are documented and accepted for V1:
1. Concurrency skip happens post-claim with atomic skip transition (not pre-claim; pre-claim check is not safe)
2. Explicit `scheduler.occurrence.skipped` event and `SKIPPED` state are required for SKIP semantics

---

## 23. Final Semantic Correction

### 1. Concurrency = SKIP

**Critical finding: Pre-claim target-state detection is NOT safe.**

A non-atomic pre-claim check followed by an atomic claim creates a race window:

```
Worker A: check target idle
Worker B: check target idle
Worker A: claim (succeeds)
Worker B: claim (succeeds? NO — atomic UPDATE serializes, only one succeeds)
```

The atomic scheduler claim (`UPDATE ... WHERE status='PENDING' ...`) IS the sole concurrency boundary. It guarantees `only one worker claims an occurrence`. A non-atomic pre-check cannot strengthen this guarantee.

However, a pre-check can become STALE:

```
Worker A: check target idle
[target becomes RUNNING — manual trigger, another worker, etc.]
Worker A: claim (succeeds — claim does not check target state)
Worker A: handoff → target is RUNNING → failure
```

This is the current behavior. The user explicitly rejected "post-claim failure" because it leaves the occurrence in `CLAIMED` state, causing lifecycle churn: `CLAIMED → lease expiry → RECOVERED → CLAIMED → FAILED → ...`

**Correct design: atomic claim + atomic skip transition**

```
Worker A:
  1. claim_next_occurrence()        → PENDING → CLAIMED (atomic)
  2. check target state             → target is RUNNING
  3. finalize_skipped()             → CLAIMED → SKIPPED (atomic)
```

```
Worker B:
  1. claim_next_occurrence()        → rowcount=0 (already CLAIMED)
  2. done
```

The atomic claim prevents Worker B from claiming. The atomic skip prevents the occurrence from being re-claimed after Worker A skips it.

**Why this is safe:**
- The scheduler claim remains the sole concurrency boundary (atomic UPDATE with `status='PENDING'` precondition)
- The skip transition is atomic (single UPDATE with `status='CLAIMED'` precondition)
- After skip, the occurrence is terminal (`SKIPPED` is not `PENDING`), so no worker can re-claim it
- Pre-claim checks are optimizations only; they do not provide concurrency guarantees

**Race analysis:**

| Race | Safe? | Reason |
|------|-------|--------|
| A checks idle, B checks idle, A claims, B claims | Safe | Atomic claim serializes; B gets rowcount=0 |
| A checks idle, target becomes RUNNING, A claims | Safe | A claims successfully; A detects running target after claim and skips |
| A claims, B claims (B fails), A checks target → running, A skips | Safe | B never claimed; A's skip is atomic |
| A claims, A checks target → idle, A handoffs | Safe | Normal path |

**Conclusion:** Pre-claim detection is not safely possible. Post-claim skip with atomic transition is the smallest correct design.

### 2. SKIP_ONCE / Misfire Semantics

**Current implementation (INCORRECT):**

`finalize_triggered_and_advance` computes `next_run_at` using `job.next_run_at` as reference:

```python
last_run_at = current.next_run_at
next_run_at = calculate_next_occurrence(definition, last_run_at)
```

For an INTERVAL job with interval=3600s, `next_run_at` = `last_run_at + 3600s`.

If the worker is unavailable from 10:00 to 13:37:
- `last_run_at` = 10:00 (previous scheduled fire time)
- `next_run_at` = 10:00 + 3600 = 11:00 (STILL IN THE PAST)

On the next poll at 13:38, the job is due again (`next_run_at`=11:00 <= 13:38). It will be claimed, and `next_run_at` will advance to 12:00. This continues until `next_run_at` catches up to the current time.

**This is catch-up behavior, NOT SKIP_ONCE.**

**Correct SKIP_ONCE semantics (from Stage 12.1):**

> SKIP_ONCE: skip exactly one occurrence, advance by exactly one interval/cron cycle from the CURRENT time.

For INTERVAL:
```
reference = max(now, last_run_at)
next_run_at = reference + interval_seconds
```

For CRON:
```
reference = max(now, last_run_at)
next_run_at = next_cron_match_after(reference)
```

For ONCE:
```
reference = max(now, last_run_at)
if reference >= target_time:
    status = DISABLED  (elapsed)
else:
    next_run_at = target_time
```

**Concrete example — CRON every 5 minutes, worker down 10:00→10:17:**

| Step | `now` | `last_run_at` | reference | `next_run_at` | Action |
|------|-------|---------------|-----------|---------------|--------|
| Worker returns | 10:17 | 10:00 | 10:17 | 10:20 | SKIP 10:00/10:05/10:10/10:15 |
| Next poll | 10:20 | 10:17 | 10:20 | 10:25 | Fire at 10:20 |
| Next poll | 10:25 | 10:20 | 10:25 | 10:30 | Fire at 10:25 |

All missed occurrences (10:00, 10:05, 10:10, 10:15) are skipped. Only one occurrence (10:20) fires. The next occurrence is 10:25.

**Concrete example — INTERVAL 3600s, worker down 10:00→13:37:**

| Step | `now` | `last_run_at` | reference | `next_run_at` | Action |
|------|-------|---------------|-----------|---------------|--------|
| Worker returns | 13:37 | 10:00 | 13:37 | 14:37 | SKIP 10:00/11:00/12:00/13:00 |
| Next poll | 14:37 | 13:37 | 14:37 | 15:37 | Fire at 14:37 |

**Current implementation behavior (INCORRECT):**

| Step | `now` | `last_run_at` | reference | `next_run_at` | Action |
|------|-------|---------------|-----------|---------------|--------|
| Worker returns | 13:37 | 10:00 | 10:00 | 11:00 | Catch-up: still in past |
| Next poll | 13:38 | 11:00 | 11:00 | 12:00 | Catch-up: still in past |
| Next poll | 13:39 | 12:00 | 12:00 | 13:00 | Catch-up: still in past |
| Next poll | 13:40 | 13:00 | 13:00 | 14:00 | Fire at 14:00 |

**The current implementation implements catch-up, not SKIP_ONCE.**

**Required fix:** `finalize_triggered_and_advance` must use `max(now, current.next_run_at)` as the calculator reference, not `current.next_run_at`.

```python
reference = max(now, current.next_run_at)
next_run_at = calculate_next_occurrence(definition, reference)
```

This ensures `next_run_at` is always in the future (unless the schedule has no future occurrences, e.g., ONCE elapsed).

### 3. Explicit SKIPPED State / Event

**Decision: REQUIRED for SKIP semantics.**

The `concurrency=SKIP` contract requires that when the target is already running, the occurrence is SKIPPED, not left in `CLAIMED` state. This requires:

1. **`SKIPPED` state transition:** Atomic transition from `CLAIMED` to `SKIPPED`
2. **`scheduler.occurrence.skipped` event:** For observability and audit

**Why Option B (explicit) is required, not Option A (implicit):**

Option A (implicit skip via advancement) is insufficient because:
- The occurrence must be claimed before the target state can be checked (atomic claim is the concurrency boundary)
- After claiming, if the target is running, the occurrence MUST transition to a terminal state to prevent re-claim
- Without an explicit `SKIPPED` state, the occurrence would remain `CLAIMED` (current incorrect behavior) or transition to `FAILED` (wrong semantics — running target is not a failure)

**Minimal schema/event change:**

No schema change required — `SKIPPED` already exists in `SchedulerJobStatus` and the `events` table accepts any `EventType` string.

Required additions:
1. `SchedulerRepository.finalize_skipped(occurrence_id, claim_token, now, reason)` — atomic transition `CLAIMED → SKIPPED`
2. `EventType.SCHEDULER_OCCURRENCE_SKIPPED = "scheduler.occurrence.skipped"` — new event type
3. Worker calls `finalize_skipped` when target is running

**Event payload:**
```json
{
  "job_id": "...",
  "occurrence_id": "...",
  "claim_sequence": 1,
  "reason": "target_already_running",
  "target_type": "task",
  "target_id": "task_abc"
}
```

### 4. Final Lifecycle Table

| Target state | Scheduler action | Occurrence result | Why safe under concurrency |
|-------------|-----------------|-------------------|---------------------------|
| target idle | claim + handoff | TRIGGERED | Atomic claim ensures single owner; handoff succeeds |
| target already running | claim + skip | SKIPPED | Atomic claim ensures single owner; atomic skip prevents re-claim |
| target missing | claim + fail | FAILED | Atomic claim ensures single owner; atomic fail prevents re-claim |
| target unauthorized | claim + fail | FAILED | Atomic claim ensures single owner; execution layer handles auth; atomic fail prevents re-claim |
| target disabled/terminal | claim + skip | SKIPPED | Atomic claim ensures single owner; atomic skip prevents re-claim |

**Why pre-claim skip is NOT used:**
- Pre-claim check is non-atomic and can become stale
- Target state can change between check and another worker's claim
- The atomic claim is the only safe concurrency boundary
- Post-claim skip with atomic transition achieves the same result without race conditions

### 5. Crash / Restart Implications

| Crash point | DB state | Recovery behavior | Duplicate? |
|-------------|----------|-------------------|------------|
| Before claim | PENDING | Next poll claims normally | No |
| After claim, before skip check | CLAIMED | Lease expires → recovery → PENDING → re-claim → skip | No |
| After skip check, before skip | CLAIMED | Lease expires → recovery → PENDING → re-claim → skip | No |
| After skip | SKIPPED | Terminal; no recovery needed | No |
| After claim, before handoff | CLAIMED | Lease expires → recovery → PENDING → re-claim → handoff | Task: impossible; Plan: possible |

**SKIP_ONCE after crash/restart:**
- If `next_run_at` is correctly computed with `max(now, last_run_at)`, the job will not be due again until the next scheduled time
- No catch-up loop
- No duplicate firings

### 6. Smallest Implementation Change Required

| Component | Change |
|-----------|--------|
| `SchedulerRepository` | Add `finalize_skipped(occurrence_id, claim_token, now, reason)` — atomic `CLAIMED → SKIPPED` transition |
| `SchedulerRepository.finalize_triggered_and_advance` | Change reference from `current.next_run_at` to `max(now, current.next_run_at)` |
| `EventType` | Add `SCHEDULER_OCCURRENCE_SKIPPED = "scheduler.occurrence.skipped"` |
| `SchedulerWorker._process_job` | After claim, check target state via handoff service (or dedicated check); call `finalize_skipped` or `finalize_failed` based on result |
| `SchedulerHandoffService` | Optionally extract target-state validation into a separate method for pre-handoff checking |

**What does NOT need to change:**
- `finalize_triggered` (preserve 12.2E contract)
- `claim_next_occurrence` (atomic claim boundary unchanged)
- `reset_expired_claim_to_pending` (recovery unchanged)
- EventRepository / EventEmitter (no new savepoint semantics)
- TaskRepository / TaskStateMachine / execution layer

---

## 24. Final Decision

**STAGE_12_2F_IMPLEMENTATION_READY**

All semantic contracts are now fully defined with corrections:

1. **Concurrency = SKIP**: Achieved via atomic claim + atomic skip transition. Pre-claim detection is NOT safe; post-claim skip with atomic transition is the smallest correct design.
2. **SKIP_ONCE**: Correctly defined as `reference = max(now, last_run_at)`, not `last_run_at`. Current implementation uses `last_run_at` and must be corrected.
3. **Explicit SKIPPED state/event**: Required for SKIP semantics. `SchedulerRepository.finalize_skipped()` and `EventType.SCHEDULER_OCCURRENCE_SKIPPED` must be added.
4. **Occurrence lifecycle**: `PENDING → CLAIMED → TRIGGERED/FAILED/SKIPPED`. SKIPPED is terminal.
5. **Schedule advancement**: Atomic via `finalize_triggered_and_advance` with corrected reference time.
6. **Worker responsibility**: Bounded — claim, skip/fail/handoff, advance, recover.
7. **Crash windows**: All documented with safe recovery paths.
8. **Duplicate execution**: Task duplicate IMPOSSIBLE (existing CAS); plan duplicate POSSIBLE (pre-existing limitation).
9. **Event semantics**: 5 event types (claimed, triggered, failed, recovered, skipped).
10. **Implementation scope**: Minimal — 4 small additions to existing code.

**Corrected deviations from 12.1:**
1. Concurrency skip is post-claim with atomic skip (not pre-claim; pre-claim is not safe)
2. Explicit `scheduler.occurrence.skipped` event and `SKIPPED` state ARE required
3. SKIP_ONCE uses `max(now, last_run_at)` as reference (current implementation is incorrect and must be fixed)

No semantic blockers remain.
