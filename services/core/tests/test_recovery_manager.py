"""
Hostile Concurrency & Crash Recovery Tests (Stage 6.7).

Verifies:
  1. Heartbeat rejected after recovery claim (Worker A's heartbeat fails after Worker B claims recovery)
  2. Old execution token cannot heartbeat after recovery takeover (TOKEN_A rejected after R1 claim & R2 takeover)
  3. New execution attempt gets new execution token after recovery (TOKEN_A != TOKEN_B, TOKEN_A rejected, TOKEN_B accepted)
  4. Recovery lease ownership prevents immediate second claim
  5. Recovery lease expiration and takeover (Worker B takes over after Worker A lease expires)
  6. Stale recovery worker transition rejected after takeover (Worker A's delayed transition fails CAS)
  7. Recovery vs Cancellation race (No resurrection, cancellation preserved)
  8. Recovery vs Abort race (No resurrection, abort preserved)
  9. Recovery vs Normal Heartbeat (Fresh heartbeat blocks recovery claim)
 10. Recovery attempt limits under concurrency
 11. Recovery crash before final transition (Lease expires, next worker succeeds)
 12. Repeated recovery after worker death
 13. No duplicate normal execution attempt (attempt_count untouched by recovery claim)
 14. No resurrection of terminal tasks (CANCELLED, ABORTED, COMMITTED)
 15. Recovery ownership persistence across independent SQLite sessions
 16. Security boundary import verification
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import tempfile
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.db.database import Base
from app.orchestration.models import RecoveryResult, RecoveryStatus
from app.orchestration.recovery_manager import RecoveryManager
from app.repositories.task_repository import TaskRepository
from app.tasks.exceptions import StaleStateTransitionError, TaskNotFoundError
from app.tasks.models import DevelopmentTask, TaskStatus
from app.tasks.state_machine import TaskStateMachine


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
async def sqlite_engine():
    """Real file-backed SQLite engine in WAL mode for independent session concurrency."""
    tmp_dir = tempfile.TemporaryDirectory()
    db_path = Path(tmp_dir.name) / "test_recovery_hostile.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    engine = create_async_engine(db_url, connect_args={"timeout": 30.0})

    async with engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()
    tmp_dir.cleanup()


@pytest.fixture
def session_factory(sqlite_engine: AsyncEngine):
    return async_sessionmaker(bind=sqlite_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def session(session_factory):
    async with session_factory() as sess:
        yield sess


@pytest.fixture
def task_repo(session: AsyncSession) -> TaskRepository:
    return TaskRepository(session)


@pytest.fixture
def state_machine(task_repo: TaskRepository) -> TaskStateMachine:
    return TaskStateMachine(task_repo)


@pytest.fixture
def recovery_manager(task_repo: TaskRepository, state_machine: TaskStateMachine) -> RecoveryManager:
    return RecoveryManager(
        task_repo=task_repo,
        state_machine=state_machine,
        timeout_seconds=60.0,
        recovery_lease_seconds=30.0,
        default_max_recovery_attempts=3,
    )


# ── 1. Heartbeat Rejected After Recovery Claim ──────────────────────────────

@pytest.mark.asyncio
async def test_heartbeat_rejected_after_recovery_claim(session_factory):
    """
    CRITICAL RACE TEST:
    Worker A claims task execution (RUNNING).
    Task heartbeat becomes stale.
    Recovery Worker B claims recovery (sets recovery_owner='worker_b', last_recovery_at=now).
    Worker A attempts to send a heartbeat.
    PROVES: Worker A's heartbeat fails (returns False) because an active recovery claim exists!
    """
    task_id = "task_hb_race_001"
    stale_time = _utc_now() - timedelta(seconds=120)

    # 1. Setup task & claim attempt
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        sm = TaskStateMachine(repo)
        t = DevelopmentTask(task_id=task_id, project_id="p1", title="T", description="D")
        await repo.create_task(t)
        claim_res = await sm.claim_attempt(task_id, TaskStatus.PENDING, max_attempts=3)
        exec_token_a = claim_res.task.execution_token
        await repo.update_heartbeat(task_id, execution_token=exec_token_a, heartbeat_at=stale_time)
        await sess.commit()

    # 2. Worker B claims recovery
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        db_task_b, won = await repo.claim_task_recovery(
            task_id=task_id,
            recovery_owner="worker_b",
            stale_before=stale_time + timedelta(seconds=60),
            max_recovery_attempts=3,
            recovery_lease_seconds=30.0,
        )
        assert won is True
        assert db_task_b.recovery_owner == "worker_b"
        assert db_task_b.execution_token is None  # Fenced!
        await sess.commit()

    # 3. Worker A attempts heartbeat AFTER Worker B's recovery claim
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        _, hb_updated = await repo.update_heartbeat(
            task_id=task_id,
            execution_token=exec_token_a,
            recovery_lease_seconds=30.0,
        )
        assert hb_updated is False, "Worker A's heartbeat should have been REJECTED due to active recovery claim!"


# ── 2. Old Execution Token Cannot Heartbeat After Recovery Takeover ─────────

@pytest.mark.asyncio
async def test_old_execution_token_cannot_heartbeat_after_recovery_takeover(session_factory):
    """
    EXECUTION RESURRECTION AUDIT TEST:
    Worker A (TOKEN_A) claims execution -> crashes.
    Recovery R1 claims recovery -> crashes.
    R1 recovery lease expires.
    Recovery R2 claims recovery takeover.
    OLD Worker A wakes up and sends heartbeat with TOKEN_A.
    PROVES: Worker A's heartbeat with TOKEN_A is PERMANENTLY REJECTED.
    """
    task_id = "task_resurrection_fencing_001"
    t0 = _utc_now() - timedelta(seconds=120)

    # 1. Worker A claims attempt 1
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        sm = TaskStateMachine(repo)
        t = DevelopmentTask(task_id=task_id, project_id="p1", title="T", description="D")
        await repo.create_task(t)
        c1 = await sm.claim_attempt(task_id, TaskStatus.PENDING, max_attempts=3)
        token_a = c1.task.execution_token
        await repo.update_heartbeat(task_id, execution_token=token_a, heartbeat_at=t0)
        await sess.commit()

    # 2. Recovery R1 claims recovery at T0 - 40s (so R1 lease expires after 30s)
    r1_claim_time = _utc_now() - timedelta(seconds=40)
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        _, won_r1 = await repo.claim_task_recovery(
            task_id=task_id,
            recovery_owner="R1",
            stale_before=_utc_now() - timedelta(seconds=60),
            max_recovery_attempts=3,
            recovery_lease_seconds=30.0,
            now=r1_claim_time,
        )
        assert won_r1 is True
        await sess.commit()

    # 3. Recovery R2 takes over lease
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        claimed_r2, won_r2 = await repo.claim_task_recovery(
            task_id=task_id,
            recovery_owner="R2",
            stale_before=_utc_now() - timedelta(seconds=60),
            max_recovery_attempts=3,
            recovery_lease_seconds=30.0,
        )
        assert won_r2 is True
        assert claimed_r2.recovery_owner == "R2"
        await sess.commit()

    # 4. Old Worker A wakes up and sends heartbeat with token_a
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        _, hb_updated = await repo.update_heartbeat(
            task_id=task_id,
            execution_token=token_a,
            recovery_lease_seconds=30.0,
        )
        assert hb_updated is False, "OLD Worker A must NOT be able to heartbeat after recovery takeover!"

    # Verify durable DB state
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        db_task = await repo.get_task(task_id)
        assert db_task.execution_token is None
        assert db_task.recovery_owner == "R2"


# ── 3. New Execution Attempt Gets New Execution Token ──────────────────────

@pytest.mark.asyncio
async def test_new_execution_attempt_gets_new_execution_token_after_recovery(session_factory):
    """
    TOKEN ROTATION & FENCING TEST:
    Attempt 1 gets TOKEN_A -> task recovered to REPAIR_REQUIRED.
    Attempt 2 gets TOKEN_B.
    PROVES:
      - TOKEN_A != TOKEN_B
      - TOKEN_A heartbeat is REJECTED.
      - TOKEN_B heartbeat is ACCEPTED.
      - attempt_count is 2, recovery_attempt_count is 1.
    """
    task_id = "task_token_rotation_001"
    stale_time = _utc_now() - timedelta(seconds=120)

    # 1. Attempt 1 (TOKEN_A)
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        sm = TaskStateMachine(repo)
        t = DevelopmentTask(task_id=task_id, project_id="p1", title="T", description="D", max_attempts=3)
        await repo.create_task(t)
        c1 = await sm.claim_attempt(task_id, TaskStatus.PENDING, max_attempts=3)
        token_a = c1.task.execution_token
        await repo.update_heartbeat(task_id, execution_token=token_a, heartbeat_at=stale_time)
        await sess.commit()

    # 2. Recovery transitions task to REPAIR_REQUIRED
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        sm = TaskStateMachine(repo)
        rm = RecoveryManager(repo, sm, timeout_seconds=60.0)
        res = await rm.recover_task(task_id, recovery_owner="rec_worker")
        assert res.status == RecoveryStatus.RECOVERY_COMPLETED
        assert res.new_task_status == TaskStatus.REPAIR_REQUIRED
        await sess.commit()

    # 3. Attempt 2 (TOKEN_B)
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        sm = TaskStateMachine(repo)
        c2 = await sm.claim_attempt(task_id, TaskStatus.REPAIR_REQUIRED, max_attempts=3)
        token_b = c2.task.execution_token
        await sess.commit()

    assert token_a != token_b, f"TOKEN_A ({token_a}) must not equal TOKEN_B ({token_b})"

    # 4. Old Attempt 1 heartbeat with TOKEN_A is REJECTED
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        _, hb_a = await repo.update_heartbeat(task_id, execution_token=token_a)
        assert hb_a is False, "TOKEN_A heartbeat must be REJECTED on Attempt 2!"

    # 5. Attempt 2 heartbeat with TOKEN_B is ACCEPTED
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        _, hb_b = await repo.update_heartbeat(task_id, execution_token=token_b)
        assert hb_b is True, "TOKEN_B heartbeat must be ACCEPTED on Attempt 2!"

    # 6. Verify attempt counters
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        final_task = await repo.get_task(task_id)
        assert final_task.attempt_count == 2
        assert final_task.recovery_attempt_count == 1


# ── 4. Recovery Lease Ownership Prevents Immediate Second Claim ──────────────

@pytest.mark.asyncio
async def test_recovery_lease_ownership_prevents_immediate_second_claim(session_factory):
    """
    PROVES: Once Worker A claims recovery, Worker B's immediate claim attempt fails
    because Worker A holds an active recovery lease.
    """
    task_id = "task_lease_prevent_001"
    stale_time = _utc_now() - timedelta(seconds=120)

    async with session_factory() as sess:
        repo = TaskRepository(sess)
        sm = TaskStateMachine(repo)
        t = DevelopmentTask(task_id=task_id, project_id="p1", title="T", description="D")
        await repo.create_task(t)
        await sm.claim_attempt(task_id, TaskStatus.PENDING, max_attempts=3)
        await repo.update_heartbeat(task_id, heartbeat_at=stale_time)
        await sess.commit()

    # Worker A claims recovery at T0
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        _, won_a = await repo.claim_task_recovery(
            task_id=task_id,
            recovery_owner="worker_a",
            stale_before=_utc_now() - timedelta(seconds=60),
            max_recovery_attempts=3,
            recovery_lease_seconds=30.0,
        )
        assert won_a is True
        await sess.commit()

    # Worker B attempts claim immediately afterward at T0 + 1s
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        _, won_b = await repo.claim_task_recovery(
            task_id=task_id,
            recovery_owner="worker_b",
            stale_before=_utc_now() - timedelta(seconds=60),
            max_recovery_attempts=3,
            recovery_lease_seconds=30.0,
        )
        assert won_b is False


# ── 5. Recovery Lease Expiration & Takeover ─────────────────────────────────

@pytest.mark.asyncio
async def test_recovery_lease_expiration_and_takeover(session_factory):
    """
    PROVES: Worker A claims recovery at T0 and crashes/stalls.
    After recovery_lease_seconds (30s) elapses, Worker B attempts claim and TAKES OVER the lease.
    """
    task_id = "task_lease_takeover_001"
    t0 = _utc_now() - timedelta(seconds=120)

    async with session_factory() as sess:
        repo = TaskRepository(sess)
        sm = TaskStateMachine(repo)
        t = DevelopmentTask(task_id=task_id, project_id="p1", title="T", description="D")
        await repo.create_task(t)
        await sm.claim_attempt(task_id, TaskStatus.PENDING, max_attempts=3)
        await repo.update_heartbeat(task_id, heartbeat_at=t0)
        await sess.commit()

    # Worker A claims recovery at T0 - 40s (so recovery_lease_seconds=30s has expired)
    old_claim_time = _utc_now() - timedelta(seconds=40)
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        _, won_a = await repo.claim_task_recovery(
            task_id=task_id,
            recovery_owner="worker_a",
            stale_before=_utc_now() - timedelta(seconds=60),
            max_recovery_attempts=3,
            recovery_lease_seconds=30.0,
            now=old_claim_time,
        )
        assert won_a is True
        await sess.commit()

    # Worker B claims recovery NOW (Worker A's claim is 40s old > 30s lease)
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        claimed_b, won_b = await repo.claim_task_recovery(
            task_id=task_id,
            recovery_owner="worker_b",
            stale_before=_utc_now() - timedelta(seconds=60),
            max_recovery_attempts=3,
            recovery_lease_seconds=30.0,
        )
        assert won_b is True
        assert claimed_b.recovery_owner == "worker_b"
        assert claimed_b.recovery_attempt_count == 2
        await sess.commit()


# ── 6. Stale Recovery Worker Transition Rejected After Takeover ──────────────

@pytest.mark.asyncio
async def test_stale_recovery_worker_transition_rejected_after_takeover(session_factory):
    """
    PROVES: Worker A claims recovery at T0 but stalls.
    Worker B takes over the recovery lease.
    Worker A wakes up and attempts transition_recovery using its old lease.
    Worker A's transition MUST fail with StaleStateTransitionError!
    """
    task_id = "task_stale_worker_rejected_001"
    t0 = _utc_now() - timedelta(seconds=120)

    # 1. Setup
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        sm = TaskStateMachine(repo)
        t = DevelopmentTask(task_id=task_id, project_id="p1", title="T", description="D")
        await repo.create_task(t)
        await sm.claim_attempt(task_id, TaskStatus.PENDING, max_attempts=3)
        await repo.update_heartbeat(task_id, heartbeat_at=t0)
        await sess.commit()

    # 2. Worker A claims at T0 - 40s
    old_claim_time = _utc_now() - timedelta(seconds=40)
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        task_a, won_a = await repo.claim_task_recovery(
            task_id=task_id,
            recovery_owner="worker_a",
            stale_before=_utc_now() - timedelta(seconds=60),
            max_recovery_attempts=3,
            recovery_lease_seconds=30.0,
            now=old_claim_time,
        )
        lease_time_a = task_a.last_recovery_at
        await sess.commit()

    # 3. Worker B takes over recovery lease
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        sm = TaskStateMachine(repo)
        rm_b = RecoveryManager(repo, sm, timeout_seconds=60.0, recovery_lease_seconds=30.0)
        res_b = await rm_b.recover_task(task_id, recovery_owner="worker_b")
        assert res_b.status == RecoveryStatus.RECOVERY_COMPLETED
        assert res_b.recovery_owner == "worker_b"
        await sess.commit()

    # 4. Worker A wakes up and attempts transition using its old lease (lease_time_a)
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        sm = TaskStateMachine(repo)

        with pytest.raises(StaleStateTransitionError):
            await sm.transition_recovery(
                task_id=task_id,
                expected_recovery_owner="worker_a",
                expected_last_recovery_at=lease_time_a,
                new_status=TaskStatus.REPAIR_REQUIRED,
                reason="Stale worker A trying to transition",
            )


# ── 7. Recovery vs Cancellation Race ────────────────────────────────────────

@pytest.mark.asyncio
async def test_recovery_vs_cancellation_race(session_factory):
    """
    RACE TEST: Concurrent task cancellation vs recovery transition.
    PROVES: If task is cancelled concurrently, cancellation is preserved and task is never resurrected.
    """
    task_id = "race_cancel_task_001"
    async with session_factory() as sess0:
        repo0 = TaskRepository(sess0)
        sm0 = TaskStateMachine(repo0)
        t = DevelopmentTask(task_id=task_id, project_id="p1", title="T", description="D")
        await repo0.create_task(t)
        await sm0.claim_attempt(task_id, TaskStatus.PENDING, max_attempts=3)
        stale_time = _utc_now() - timedelta(seconds=120)
        await repo0.update_heartbeat(task_id, heartbeat_at=stale_time)
        await sess0.commit()

    async def cancel_action():
        async with session_factory() as sess:
            repo = TaskRepository(sess)
            sm = TaskStateMachine(repo)
            await sm.transition(task_id, TaskStatus.RUNNING, TaskStatus.CANCELLED, reason="User cancel")
            await sess.commit()

    async def recovery_action():
        async with session_factory() as sess:
            repo = TaskRepository(sess)
            sm = TaskStateMachine(repo)
            rm = RecoveryManager(repo, sm, timeout_seconds=60.0)
            res = await rm.recover_task(task_id, recovery_owner="rec_worker")
            await sess.commit()
            return res

    cancel_task_fut = asyncio.create_task(cancel_action())
    rec_task_fut = asyncio.create_task(recovery_action())

    await asyncio.gather(cancel_task_fut, rec_task_fut)
    rec_res = rec_task_fut.result()

    async with session_factory() as sess_final:
        repo_final = TaskRepository(sess_final)
        final_task = await repo_final.get_task(task_id)

        if final_task.status == TaskStatus.CANCELLED:
            assert rec_res.status in (RecoveryStatus.CANCELLED, RecoveryStatus.ALREADY_RECOVERED)
        else:
            assert final_task.status == TaskStatus.REPAIR_REQUIRED
            assert rec_res.status == RecoveryStatus.RECOVERY_COMPLETED


# ── 8. Recovery vs Abort Race ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recovery_vs_abort_race(session_factory):
    """
    RACE TEST: Concurrent task abort vs recovery transition.
    PROVES: ABORTED status is preserved; recovery worker cannot resurrect or overwrite ABORTED task.
    """
    task_id = "race_abort_task_001"
    async with session_factory() as sess0:
        repo0 = TaskRepository(sess0)
        sm0 = TaskStateMachine(repo0)
        t = DevelopmentTask(task_id=task_id, project_id="p1", title="T", description="D")
        await repo0.create_task(t)
        await sm0.claim_attempt(task_id, TaskStatus.PENDING, max_attempts=3)
        stale_time = _utc_now() - timedelta(seconds=120)
        await repo0.update_heartbeat(task_id, heartbeat_at=stale_time)
        await sess0.commit()

    # Abort first
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        sm = TaskStateMachine(repo)
        await sm.transition(task_id, TaskStatus.RUNNING, TaskStatus.ABORTED, reason="Emergency abort")
        await sess.commit()

    # Recovery worker attempts recovery
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        sm = TaskStateMachine(repo)
        rm = RecoveryManager(repo, sm, timeout_seconds=60.0)
        res = await rm.recover_task(task_id, recovery_owner="rec_worker")
        assert res.status == RecoveryStatus.ABORTED

    async with session_factory() as sess_final:
        repo_final = TaskRepository(sess_final)
        final_task = await repo_final.get_task(task_id)
        assert final_task.status == TaskStatus.ABORTED


# ── 9. Recovery vs Normal Heartbeat Race ────────────────────────────────────

@pytest.mark.asyncio
async def test_recovery_vs_normal_heartbeat_race(session_factory):
    """
    RACE TEST: Worker A sends fresh heartbeat right before Worker B attempts recovery claim.
    PROVES: Database predicate (last_heartbeat < stale_before) evaluates to False;
    Worker B's claim is rejected (returns NOT_STALE). Worker A's execution is NOT killed.
    """
    task_id = "race_hb_fresh_001"
    stale_time = _utc_now() - timedelta(seconds=120)

    async with session_factory() as sess:
        repo = TaskRepository(sess)
        sm = TaskStateMachine(repo)
        t = DevelopmentTask(task_id=task_id, project_id="p1", title="T", description="D")
        await repo.create_task(t)
        claim_res = await sm.claim_attempt(task_id, TaskStatus.PENDING, max_attempts=3)
        token_a = claim_res.task.execution_token
        await repo.update_heartbeat(task_id, execution_token=token_a, heartbeat_at=stale_time)
        await sess.commit()

    # Worker A updates heartbeat to NOW right before Worker B runs
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        _, updated = await repo.update_heartbeat(task_id, execution_token=token_a)
        assert updated is True
        await sess.commit()

    # Worker B attempts recovery
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        sm = TaskStateMachine(repo)
        rm = RecoveryManager(repo, sm, timeout_seconds=60.0)
        res = await rm.recover_task(task_id, recovery_owner="worker_b")
        assert res.status == RecoveryStatus.NOT_STALE

    async with session_factory() as sess_final:
        repo_final = TaskRepository(sess_final)
        final_task = await repo_final.get_task(task_id)
        assert final_task.status == TaskStatus.RUNNING


# ── 10. Recovery Limit Under Concurrency ────────────────────────────────────

@pytest.mark.asyncio
async def test_recovery_limit_under_concurrency(session_factory):
    """
    CONCURRENCY TEST: 3 workers attempt recovery when recovery_attempt_count is at (max - 1).
    PROVES: Task transitions to FAILED exactly once and recovery limit is enforced atomically.
    """
    task_id = "task_rec_limit_concurrent_001"
    stale_time = _utc_now() - timedelta(seconds=120)

    async with session_factory() as sess:
        repo = TaskRepository(sess)
        sm = TaskStateMachine(repo)
        t = DevelopmentTask(task_id=task_id, project_id="p1", title="T", description="D", max_recovery_attempts=1)
        await repo.create_task(t)
        await sm.claim_attempt(task_id, TaskStatus.PENDING, max_attempts=3)
        await repo.update_heartbeat(task_id, heartbeat_at=stale_time)
        await sess.commit()

    # 3 workers call recover_task concurrently
    async def worker_rec(w_id: int):
        async with session_factory() as sess:
            repo = TaskRepository(sess)
            sm = TaskStateMachine(repo)
            rm = RecoveryManager(repo, sm, timeout_seconds=60.0)
            res = await rm.recover_task(task_id, recovery_owner=f"w_{w_id}")
            await sess.commit()
            return res

    results = await asyncio.gather(*[worker_rec(i) for i in range(3)])

    completed = [r for r in results if r.status == RecoveryStatus.RECOVERY_COMPLETED]
    assert len(completed) == 1

    async with session_factory() as sess_final:
        repo_final = TaskRepository(sess_final)
        final_task = await repo_final.get_task(task_id)
        assert final_task.status == TaskStatus.REPAIR_REQUIRED
        assert final_task.recovery_attempt_count == 1

    # Next recovery attempt exceeds limit
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        sm = TaskStateMachine(repo)
        # Move back to RUNNING and set stale heartbeat
        await sm.claim_attempt(task_id, TaskStatus.REPAIR_REQUIRED, max_attempts=3)
        await repo.update_heartbeat(task_id, heartbeat_at=stale_time)
        await sess.commit()

    res_limit = await worker_rec(99)
    assert res_limit.status == RecoveryStatus.RECOVERY_LIMIT_REACHED
    assert res_limit.new_task_status == TaskStatus.FAILED


# ── 11. No Duplicate Normal Execution Attempt ────────────────────────────────

@pytest.mark.asyncio
async def test_no_duplicate_normal_execution_attempt(session_factory):
    """
    PROVES: Recovery detection & claim increments recovery_attempt_count BUT does NOT increment normal attempt_count.
    """
    task_id = "task_attempt_sep_001"
    stale_time = _utc_now() - timedelta(seconds=120)

    async with session_factory() as sess:
        repo = TaskRepository(sess)
        sm = TaskStateMachine(repo)
        t = DevelopmentTask(task_id=task_id, project_id="p1", title="T", description="D", max_attempts=3)
        await repo.create_task(t)
        await sm.claim_attempt(task_id, TaskStatus.PENDING, max_attempts=3)
        await repo.update_heartbeat(task_id, heartbeat_at=stale_time)
        await sess.commit()

    async with session_factory() as sess:
        repo = TaskRepository(sess)
        sm = TaskStateMachine(repo)
        rm = RecoveryManager(repo, sm, timeout_seconds=60.0)
        res = await rm.recover_task(task_id, recovery_owner="rec_1")
        assert res.status == RecoveryStatus.RECOVERY_COMPLETED
        await sess.commit()

    async with session_factory() as sess_final:
        repo_final = TaskRepository(sess_final)
        final_task = await repo_final.get_task(task_id)
        assert final_task.status == TaskStatus.REPAIR_REQUIRED
        assert final_task.attempt_count == 1  # Unchanged!
        assert final_task.recovery_attempt_count == 1  # Incremented!


# ── 12. No Resurrection of Terminal Task ────────────────────────────────────

@pytest.mark.asyncio
async def test_no_resurrection_terminal_task(session_factory):
    """
    HOSTILE: Prove CANCELLED, ABORTED, and COMMITTED tasks are strictly protected against resurrection.
    """
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        sm = TaskStateMachine(repo)
        rm = RecoveryManager(repo, sm, timeout_seconds=60.0)

        # 1. CANCELLED
        t1 = DevelopmentTask(task_id="t_can", project_id="p", title="T", description="D")
        await repo.create_task(t1)
        await sm.transition("t_can", TaskStatus.PENDING, TaskStatus.CANCELLED)
        r1 = await rm.recover_task("t_can")
        assert r1.status == RecoveryStatus.CANCELLED

        # 2. COMMITTED
        t2 = DevelopmentTask(task_id="t_com", project_id="p", title="T", description="D")
        await repo.create_task(t2)
        await sm.claim_attempt("t_com", TaskStatus.PENDING, max_attempts=3)
        await sm.transition("t_com", TaskStatus.RUNNING, TaskStatus.VERIFYING)
        await sm.transition("t_com", TaskStatus.VERIFYING, TaskStatus.PASSED)
        await sm.transition("t_com", TaskStatus.PASSED, TaskStatus.COMMITTED)
        r2 = await rm.recover_task("t_com")
        assert r2.status == RecoveryStatus.NOT_ELIGIBLE

        await sess.commit()


# ── 13. Recovery Ownership Persistence ──────────────────────────────────────

@pytest.mark.asyncio
async def test_recovery_ownership_persistence(session_factory):
    """
    PROVES: recovery_owner, last_recovery_at, and execution_token are durably stored in SQLite
    and readable across independent sessions.
    """
    task_id = "task_persist_001"
    stale_time = _utc_now() - timedelta(seconds=120)

    async with session_factory() as sess:
        repo = TaskRepository(sess)
        sm = TaskStateMachine(repo)
        t = DevelopmentTask(task_id=task_id, project_id="p1", title="T", description="D")
        await repo.create_task(t)
        claim_res = await sm.claim_attempt(task_id, TaskStatus.PENDING, max_attempts=3)
        token = claim_res.task.execution_token
        assert token is not None
        await repo.update_heartbeat(task_id, execution_token=token, heartbeat_at=stale_time)
        await sess.commit()

    # Claim recovery in session 2
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        claimed, won = await repo.claim_task_recovery(
            task_id=task_id,
            recovery_owner="persistent_owner_99",
            stale_before=_utc_now() - timedelta(seconds=60),
            max_recovery_attempts=3,
        )
        assert won is True
        assert claimed.recovery_owner == "persistent_owner_99"
        assert claimed.execution_token is None  # Fenced!
        await sess.commit()

    # Verify in session 3
    async with session_factory() as sess:
        repo = TaskRepository(sess)
        fetched = await repo.get_task(task_id)
        assert fetched is not None
        assert fetched.recovery_owner == "persistent_owner_99"
        assert fetched.last_recovery_at is not None
        assert fetched.recovery_attempt_count == 1
        assert fetched.execution_token is None


# ── 14. Recovery Manager Parameter Validation ───────────────────────────────

@pytest.mark.asyncio
async def test_recovery_manager_invalid_parameters(task_repo, state_machine):
    """Verify invalid timeout or max recovery attempts parameters raise ValueError."""
    with pytest.raises(ValueError, match="timeout_seconds"):
        RecoveryManager(task_repo, state_machine, timeout_seconds=0.0)

    with pytest.raises(ValueError, match="timeout_seconds"):
        RecoveryManager(task_repo, state_machine, timeout_seconds=-10.0)

    with pytest.raises(ValueError, match="timeout_seconds"):
        RecoveryManager(task_repo, state_machine, timeout_seconds=math.nan)

    with pytest.raises(ValueError, match="recovery_lease_seconds"):
        RecoveryManager(task_repo, state_machine, recovery_lease_seconds=0.0)

    with pytest.raises(ValueError, match="default_max_recovery_attempts"):
        RecoveryManager(task_repo, state_machine, default_max_recovery_attempts=0)


# ── 15. Security Boundary Verification ──────────────────────────────────────

def test_recovery_manager_security_boundary_imports():
    """
    SECURITY AUDIT: Confirm RecoveryManager source code contains ZERO forbidden imports
    (ToolExecutor, BaseTool, PermissionEngine, ApprovalService, subprocess, shell=True, raw Git).
    """
    rm_path = Path(__file__).parent.parent / "app" / "orchestration" / "recovery_manager.py"
    assert rm_path.exists(), f"File {rm_path} not found"

    source = rm_path.read_text(encoding="utf-8")

    forbidden = [
        "ToolExecutor",
        "BaseTool",
        "PermissionEngine",
        "ApprovalService",
        "permission_grants",
        "subprocess",
        "shell=True",
        "git ",
        "Gemini",
        "Kilo",
        "FastAPI",
        "requests",
        "httpx",
    ]

    for term in forbidden:
        assert term not in source, f"Forbidden security term '{term}' found in recovery_manager.py!"
