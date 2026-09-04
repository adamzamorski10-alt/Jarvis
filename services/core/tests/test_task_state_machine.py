"""
Unit & Concurrency Tests — TaskStateMachine & Transition Engine
"""
import asyncio
from datetime import datetime, timezone
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.db.database import Base
from app.repositories.task_repository import TaskRepository
from app.tasks.exceptions import (
    InvalidStateTransitionError,
    StaleStateTransitionError,
    TaskNotFoundError,
)
from app.tasks.models import DevelopmentTask, TaskStatus
from app.tasks.state_machine import TaskStateMachine, _ALLOWED_TRANSITIONS


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


# ===========================================================================
# 1. Allowed Transition Matrix Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_all_allowed_transitions_succeed(db_session: AsyncSession):
    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)

    # Test every valid edge in _ALLOWED_TRANSITIONS
    for source_status, target_statuses in _ALLOWED_TRANSITIONS.items():
        for target_status in target_statuses:
            # Seed task in source_status
            task = await repo.create_task(DevelopmentTask(
                project_id="proj_matrix",
                title=f"Test {source_status.value} -> {target_status.value}",
                description="Matrix test",
                status=source_status,
            ))
            await db_session.commit()

            result = await sm.transition(
                task_id=task.task_id,
                expected_status=source_status,
                new_status=target_status,
                reason=f"Transitioning to {target_status.value}",
            )
            await db_session.commit()

            assert result.success is True
            assert result.task_id == task.task_id
            assert result.previous_status == source_status
            assert result.new_status == target_status
            assert result.task is not None
            assert result.task.status == target_status


# ===========================================================================
# 2. Forbidden Transition Matrix Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_forbidden_transitions_fail_closed(db_session: AsyncSession):
    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)

    all_statuses = set(TaskStatus)

    for source_status, target_statuses in _ALLOWED_TRANSITIONS.items():
        forbidden_targets = all_statuses - target_statuses - {source_status}
        for forbidden in forbidden_targets:
            task = await repo.create_task(DevelopmentTask(
                project_id="proj_forbidden",
                title=f"Forbidden {source_status.value} -> {forbidden.value}",
                description="Forbidden matrix test",
                status=source_status,
            ))
            await db_session.commit()

            with pytest.raises(InvalidStateTransitionError) as exc_info:
                await sm.transition(
                    task_id=task.task_id,
                    expected_status=source_status,
                    new_status=forbidden,
                )

            assert exc_info.value.task_id == task.task_id
            assert exc_info.value.current_status == source_status.value
            assert exc_info.value.attempted_status == forbidden.value

            # Verify persisted status remained unchanged
            persisted = await repo.get_task(task.task_id)
            assert persisted.status == source_status


# ===========================================================================
# 3. Terminal State Integrity Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_terminal_state_committed_rejects_all_transitions(db_session: AsyncSession):
    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)

    task = await repo.create_task(DevelopmentTask(
        project_id="proj_terminal",
        title="Committed Task",
        description="Terminal committed test",
        status=TaskStatus.COMMITTED,
    ))
    await db_session.commit()

    for target in TaskStatus:
        with pytest.raises(InvalidStateTransitionError):
            await sm.transition(
                task_id=task.task_id,
                expected_status=TaskStatus.COMMITTED,
                new_status=target,
            )

    persisted = await repo.get_task(task.task_id)
    assert persisted.status == TaskStatus.COMMITTED


@pytest.mark.asyncio
async def test_terminal_state_cancelled_rejects_all_transitions(db_session: AsyncSession):
    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)

    task = await repo.create_task(DevelopmentTask(
        project_id="proj_terminal",
        title="Cancelled Task",
        description="Terminal cancelled test",
        status=TaskStatus.CANCELLED,
    ))
    await db_session.commit()

    for target in TaskStatus:
        with pytest.raises(InvalidStateTransitionError):
            await sm.transition(
                task_id=task.task_id,
                expected_status=TaskStatus.CANCELLED,
                new_status=target,
            )

    persisted = await repo.get_task(task.task_id)
    assert persisted.status == TaskStatus.CANCELLED


# ===========================================================================
# 4. Task Not Found Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_transition_nonexistent_task_raises_task_not_found(db_session: AsyncSession):
    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)

    with pytest.raises(TaskNotFoundError) as exc_info:
        await sm.transition(
            task_id="task_nonexistent_999",
            expected_status=TaskStatus.PENDING,
            new_status=TaskStatus.RUNNING,
        )

    assert exc_info.value.task_id == "task_nonexistent_999"


# ===========================================================================
# 5. Stale Expected State & Concurrency Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_stale_expected_status_raises_stale_exception(db_session: AsyncSession):
    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)

    task = await repo.create_task(DevelopmentTask(
        project_id="proj_stale",
        title="Stale Task",
        description="Testing stale expected status",
        status=TaskStatus.RUNNING,  # Actually RUNNING in DB
    ))
    await db_session.commit()

    # Attempt transition claiming expected_status is PENDING
    with pytest.raises(StaleStateTransitionError) as exc_info:
        await sm.transition(
            task_id=task.task_id,
            expected_status=TaskStatus.PENDING,  # Mismatch with actual DB status RUNNING
            new_status=TaskStatus.RUNNING,
        )

    assert exc_info.value.task_id == task.task_id
    assert exc_info.value.expected_status == TaskStatus.PENDING.value
    assert exc_info.value.actual_status == TaskStatus.RUNNING.value


@pytest.mark.asyncio
async def test_concurrent_state_transitions_atomic_compare_and_set(tmp_path):
    """
    Two concurrent workers attempt to transition the SAME task from PENDING
    simultaneously in a file-backed WAL SQLite database.
    Worker 1 tries PENDING -> RUNNING.
    Worker 2 tries PENDING -> CANCELLED.
    Exactly ONE succeeds; the loser receives StaleStateTransitionError and does NOT overwrite.
    """
    db_path = tmp_path / "test_sm_conc.db"
    url = f"sqlite+aiosqlite:///{db_path.resolve().as_posix()}"
    engine = create_async_engine(url, connect_args={"check_same_thread": False, "timeout": 5})

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA busy_timeout=5000")

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    # Seed task in PENDING
    async with factory() as seed_session:
        seed_repo = TaskRepository(seed_session)
        task = await seed_repo.create_task(DevelopmentTask(
            project_id="proj_conc_sm",
            title="Concurrent SM Task",
            description="Testing atomic CAS race",
            status=TaskStatus.PENDING,
        ))
        await seed_session.commit()
        target_task_id = task.task_id

    # Execute concurrent transitions from independent sessions
    async with factory() as s1:
        async with factory() as s2:
            sm1 = TaskStateMachine(TaskRepository(s1))
            sm2 = TaskStateMachine(TaskRepository(s2))
            await s1.commit()
            await s2.commit()

            async def try_trans(sm, target_status, reason):
                for _ in range(5):
                    try:
                        res = await sm.transition(
                            task_id=target_task_id,
                            expected_status=TaskStatus.PENDING,
                            new_status=target_status,
                            reason=reason,
                        )
                        await sm._repo._session.commit()
                        return res
                    except StaleStateTransitionError as exc:
                        await sm._repo._session.rollback()
                        return exc
                    except Exception:
                        await sm._repo._session.rollback()
                        await asyncio.sleep(0.05)
                # Final attempt
                try:
                    res = await sm.transition(
                        task_id=target_task_id,
                        expected_status=TaskStatus.PENDING,
                        new_status=target_status,
                        reason=reason,
                    )
                    await sm._repo._session.commit()
                    return res
                except Exception as exc:
                    return exc

            res1, res2 = await asyncio.gather(
                try_trans(sm1, TaskStatus.RUNNING, "Worker 1 claiming task"),
                try_trans(sm2, TaskStatus.CANCELLED, "Worker 2 cancelling task"),
            )

    # Re-verify final state in a fresh session
    async with factory() as final_session:
        final_repo = TaskRepository(final_session)
        final_task = await final_repo.get_task(target_task_id)

    await engine.dispose()

    # Assert exactly 1 succeeded and 1 raised StaleStateTransitionError
    successes = [r for r in (res1, res2) if not isinstance(r, Exception) and r.success]
    stales = [r for r in (res1, res2) if isinstance(r, StaleStateTransitionError)]

    assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"
    assert len(stales) == 1, f"Expected exactly 1 StaleStateTransitionError, got {len(stales)}"
    assert final_task is not None
    assert final_task.status in (TaskStatus.RUNNING, TaskStatus.CANCELLED)
    assert final_task.status == successes[0].new_status
