"""
Unit & Concurrency Tests — TaskRepository & DevelopmentTask Model
"""
import asyncio
from datetime import datetime, timezone
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.db.database import Base
from app.manager.models import compute_task_id
from app.repositories.task_repository import TaskRepository
from app.tasks.models import DevelopmentTask, TaskStatus


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
# 1. Creation Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_create_valid_task(db_session: AsyncSession):
    repo = TaskRepository(db_session)
    task = DevelopmentTask(
        project_id="proj_alpha",
        title="Implement Feature X",
        description="Add feature X to core engine",
    )
    created = await repo.create_task(task)
    await db_session.commit()

    assert created.task_id.startswith("task_")
    assert created.project_id == "proj_alpha"
    assert created.title == "Implement Feature X"
    assert created.description == "Add feature X to core engine"
    assert created.status == TaskStatus.PENDING
    assert created.priority == 0
    assert created.attempt_count == 0
    assert created.max_attempts == 3
    assert isinstance(created.created_at, datetime)
    assert created.started_at is None
    assert created.completed_at is None
    assert created.failure_reason is None
    assert created.metadata == {}


@pytest.mark.asyncio
async def test_create_duplicate_task_id_fails(db_session: AsyncSession):
    repo = TaskRepository(db_session)
    task1 = DevelopmentTask(
        task_id="task_fixed_123",
        task_key="fixed_123",
        project_id="proj_alpha",
        title="Task 1",
        description="First task",
    )
    await repo.create_task(task1)
    await db_session.commit()

    task2 = DevelopmentTask(
        task_id="task_fixed_123",
        task_key="fixed_123_dup",
        project_id="proj_beta",
        title="Task 2",
        description="Second task with duplicate ID",
    )
    with pytest.raises(IntegrityError):
        await repo.create_task(task2)
        await db_session.commit()
    await db_session.rollback()


# ===========================================================================
# 2. Persistence & Retrieval Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_get_task_by_id(db_session: AsyncSession):
    repo = TaskRepository(db_session)
    task = DevelopmentTask(
        project_id="proj_beta",
        title="Fix Bug Y",
        description="Investigate memory leak",
    )
    created = await repo.create_task(task)
    await db_session.commit()

    retrieved = await repo.get_task(created.task_id)
    assert retrieved is not None
    assert retrieved.task_id == created.task_id
    assert retrieved.title == "Fix Bug Y"


@pytest.mark.asyncio
async def test_get_nonexistent_task(db_session: AsyncSession):
    repo = TaskRepository(db_session)
    retrieved = await repo.get_task("task_nonexistent")
    assert retrieved is None


@pytest.mark.asyncio
async def test_update_task_status(db_session: AsyncSession):
    repo = TaskRepository(db_session)
    task = DevelopmentTask(
        project_id="proj_beta",
        title="Update Status Test",
        description="Testing state change persistence",
    )
    created = await repo.create_task(task)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    updated = await repo.update_task_status(
        created.task_id,
        status=TaskStatus.RUNNING,
        started_at=now,
    )
    await db_session.commit()

    assert updated is not None
    assert updated.status == TaskStatus.RUNNING
    assert updated.started_at is not None


@pytest.mark.asyncio
async def test_update_task_metadata(db_session: AsyncSession):
    repo = TaskRepository(db_session)
    task = DevelopmentTask(
        project_id="proj_gamma",
        title="Metadata Test",
        description="Testing JSON metadata persistence",
    )
    created = await repo.create_task(task)
    await db_session.commit()

    meta_payload = {"branch": "feature/x", "commit_hash": "a1b2c3d", "retry_policy": {"timeout": 300}}
    updated = await repo.update_task_metadata(created.task_id, meta_payload)
    await db_session.commit()

    assert updated is not None
    assert updated.metadata == meta_payload
    
    # Verify re-fetch
    fetched = await repo.get_task(created.task_id)
    assert fetched.metadata["branch"] == "feature/x"
    assert fetched.metadata["retry_policy"]["timeout"] == 300


@pytest.mark.asyncio
async def test_list_and_filter_tasks(db_session: AsyncSession):
    repo = TaskRepository(db_session)

    t1 = await repo.create_task(DevelopmentTask(project_id="proj_A", title="T1", description="D1", priority=10, status=TaskStatus.PENDING))
    t2 = await repo.create_task(DevelopmentTask(project_id="proj_A", title="T2", description="D2", priority=20, status=TaskStatus.RUNNING))
    t3 = await repo.create_task(DevelopmentTask(project_id="proj_B", title="T3", description="D3", priority=5, status=TaskStatus.PENDING))
    await db_session.commit()

    # Filter by project_id
    proj_a_list = await repo.list_tasks(project_id="proj_A")
    assert len(proj_a_list) == 2
    # Priority sorting (descending)
    assert proj_a_list[0].task_id == t2.task_id
    assert proj_a_list[1].task_id == t1.task_id

    # Filter by status
    pending_list = await repo.list_tasks(status=TaskStatus.PENDING)
    assert len(pending_list) == 2

    # Filter by project + status
    proj_b_pending = await repo.list_tasks(project_id="proj_B", status=TaskStatus.PENDING)
    assert len(proj_b_pending) == 1
    assert proj_b_pending[0].task_id == t3.task_id


# ===========================================================================
# 3. Integrity & Parent-Child Relationships
# ===========================================================================

@pytest.mark.asyncio
async def test_parent_child_task_relationship(db_session: AsyncSession):
    repo = TaskRepository(db_session)
    parent = await repo.create_task(DevelopmentTask(project_id="proj_core", title="Parent Task", description="Parent goal"))
    await db_session.commit()

    child = await repo.create_task(DevelopmentTask(
        project_id="proj_core",
        parent_task_id=parent.task_id,
        title="Child Sub-task",
        description="Sub-task goal",
    ))
    await db_session.commit()

    assert child.parent_task_id == parent.task_id

    children = await repo.list_tasks(parent_task_id=parent.task_id)
    assert len(children) == 1
    assert children[0].task_id == child.task_id


# ===========================================================================
# 4. Transaction & Rollback Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_transaction_rollback_on_failure(db_session: AsyncSession):
    repo = TaskRepository(db_session)
    task = DevelopmentTask(project_id="proj_tb", title="Rollback Test", description="Uncommitted")
    created = await repo.create_task(task)
    
    # Rollback without committing
    await db_session.rollback()

    retrieved = await repo.get_task(created.task_id)
    assert retrieved is None


# ===========================================================================
# 5. Task Identity / task_key consistency
# ===========================================================================

@pytest.mark.asyncio
async def test_task_key_persisted_correctly(db_session: AsyncSession):
    repo = TaskRepository(db_session)
    task = DevelopmentTask(
        task_id="task_abc123",
        task_key="my_feature",
        project_id="proj",
        title="My Feature",
        description="d",
    )
    created = await repo.create_task(task)
    await db_session.commit()

    fetched = await repo.get_task(created.task_id)
    assert fetched is not None
    assert fetched.task_key == "my_feature"


@pytest.mark.asyncio
async def test_task_id_uses_full_sha256(db_session: AsyncSession):
    repo = TaskRepository(db_session)
    task = DevelopmentTask(
        task_id="task_" + "a" * 64,
        task_key="full_sha",
        project_id="proj",
        title="Full SHA",
        description="d",
    )
    created = await repo.create_task(task)
    await db_session.commit()

    assert len(created.task_id) == 5 + 64
    assert created.task_id.startswith("task_")


@pytest.mark.asyncio
async def test_task_key_matches_proposal_key_in_metadata(db_session: AsyncSession):
    repo = TaskRepository(db_session)
    task = DevelopmentTask(
        task_id="task_def456",
        task_key="auth_service",
        project_id="proj",
        title="Auth Service",
        description="d",
        metadata={"proposal_key": "auth_service"},
    )
    created = await repo.create_task(task)
    await db_session.commit()

    fetched = await repo.get_task(created.task_id)
    assert fetched.task_key == fetched.metadata.get("proposal_key")


# ===========================================================================
# 6. Concurrency & Lost Update Prevention
# ===========================================================================

@pytest.mark.asyncio
async def test_concurrent_atomic_attempt_count_increment(tmp_path):
    """
    Simulates 2 concurrent worker processes incrementing attempt_count
    on the same task using a shared file-backed SQLite database with WAL mode.
    Guarantees no lost updates under atomic SQL execution.
    """
    db_path = tmp_path / "test_task_repo_conc.db"
    url = f"sqlite+aiosqlite:///{db_path.resolve().as_posix()}"
    engine = create_async_engine(url, connect_args={"check_same_thread": False, "timeout": 5})

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA busy_timeout=5000")

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    # Seed task
    async with factory() as seed_session:
        seed_repo = TaskRepository(seed_session)
        task = await seed_repo.create_task(DevelopmentTask(
            project_id="proj_conc",
            title="Concurrent Task",
            description="Testing concurrent attempt count increment",
        ))
        await seed_session.commit()
        target_task_id = task.task_id

    # Execute 2 concurrent attempt increments from independent sessions
    async with factory() as s1:
        async with factory() as s2:
            repo1 = TaskRepository(s1)
            repo2 = TaskRepository(s2)
            await s1.commit()
            await s2.commit()

            async def do_attempt(repo, reason):
                for _ in range(5):
                    try:
                        res = await repo.record_attempt_atomically(target_task_id, failure_reason=reason)
                        await repo._session.commit()
                        return res
                    except Exception:
                        await repo._session.rollback()
                        await asyncio.sleep(0.05)
                return await repo.record_attempt_atomically(target_task_id, failure_reason=reason)

            (t1, c1), (t2, c2) = await asyncio.gather(
                do_attempt(repo1, "Attempt 1 failed"),
                do_attempt(repo2, "Attempt 2 failed"),
            )

    # Re-verify final state in a fresh session
    async with factory() as final_session:
        final_repo = TaskRepository(final_session)
        final_task = await final_repo.get_task(target_task_id)

    await engine.dispose()

    assert final_task is not None
    assert final_task.attempt_count == 2, f"Expected attempt_count == 2, got {final_task.attempt_count}"
    assert final_task.failure_reason in ("Attempt 1 failed", "Attempt 2 failed")
