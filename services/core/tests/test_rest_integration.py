"""
Stage 8.15 REST Control Plane Application Integration Test Suite.

Demonstrates end-to-end flow:
HTTP POST /api/plans/{plan_id}/execute
-> BackgroundExecutionManager
-> AutonomousPlanExecutor
-> Task DAG
-> Task Execution & Verification
-> Commit
-> HTTP GET status query.
"""
import asyncio
from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes.plans import router as plans_router
from app.db.database import Base, get_db, init_db
from app.db.models.development_task import DevelopmentTaskModel
from app.dependencies import get_background_execution_manager
from app.manager.models import compute_task_id
from app.orchestration.run_controller import AutonomousRunResult, RunStatus
from app.repositories.task_repository import TaskRepository
from app.services.autonomous_loop import (
    AutonomousPlanExecutor,
    PlanExecutionResult,
    PlanExecutionState,
)
from app.services.background_execution_manager import (
    BackgroundExecutionManager,
    background_execution_manager,
)
from app.tasks.models import DevelopmentTask, TaskStatus


@pytest.fixture
async def sqlite_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(sqlite_engine):
    return async_sessionmaker(sqlite_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def async_client(sqlite_engine, session_factory):
    app = FastAPI()
    app.include_router(plans_router, prefix="/api")

    fresh_mgr = BackgroundExecutionManager()
    app.dependency_overrides[get_background_execution_manager] = lambda: fresh_mgr

    async def _get_test_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_test_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_rest_integration_full_plan_execution_flow(async_client, session_factory):
    """
    Test complete integration:
    HTTP execute plan -> Background execution -> Task DAG execution -> Commit -> HTTP GET execution status.
    """
    plan_id = "plan_integration_01"
    task_id_1 = compute_task_id(plan_id, "task_1")
    task_id_2 = compute_task_id(plan_id, "task_2")

    # Seed 2 tasks in memory DB
    async with session_factory() as session:
        repo = TaskRepository(session)
        t1 = DevelopmentTask(
            task_id=task_id_1,
            plan_id=plan_id,
            project_id="proj_int",
            task_key="task_1",
            title="First task",
            description="First integration test task",
            status=TaskStatus.PENDING,
            attempt_count=0,
            max_attempts=3,
        )
        t2 = DevelopmentTask(
            task_id=task_id_2,
            plan_id=plan_id,
            project_id="proj_int",
            task_key="task_2",
            title="Second task",
            description="Second integration test task",
            status=TaskStatus.PENDING,
            attempt_count=0,
            max_attempts=3,
        )
        await repo.create_tasks_idempotent(
            plan_id=plan_id,
            tasks=[t1, t2],
            dependencies=[(task_id_2, task_id_1)], # task_2 depends on task_1
            plan_fingerprint="fp_integration_01",
        )
        await session.commit()

    # Mock the plan execution service bridge to simulate successful execution
    mock_run_result_1 = AutonomousRunResult(
        run_id="run_1",
        task_id=task_id_1,
        status=RunStatus.SUCCEEDED,
        task_status=TaskStatus.COMMITTED,
        verification_passed=True,
        commit_sha="sha_1111",
    )
    mock_run_result_2 = AutonomousRunResult(
        run_id="run_2",
        task_id=task_id_2,
        status=RunStatus.SUCCEEDED,
        task_status=TaskStatus.COMMITTED,
        verification_passed=True,
        commit_sha="sha_2222",
    )

    async def mock_execute_planned_task(task_id, timeout_seconds=None, options=None):
        async with session_factory() as s:
            r = TaskRepository(s)
            if task_id == task_id_1:
                await r.update_task_status(task_id_1, TaskStatus.COMMITTED, failure_reason="Mock test success")
                await s.commit()
                return mock_run_result_1
            else:
                await r.update_task_status(task_id_2, TaskStatus.COMMITTED, failure_reason="Mock test success")
                await s.commit()
                return mock_run_result_2

    # Patch PlanExecutionService within the factory context
    with patch("app.services.plan_execution_service.PlanExecutionService.execute_planned_task", side_effect=mock_execute_planned_task):
        # 1. Trigger HTTP POST execute
        res_start = await async_client.post(f"/api/plans/{plan_id}/execute")
        assert res_start.status_code == 200
        start_data = res_start.json()
        assert start_data["plan_id"] == plan_id
        exec_id = start_data["execution_id"]
        assert exec_id.startswith("exec_")

        # 2. Wait for background execution to complete
        for _ in range(20):
            await asyncio.sleep(0.1)
            res_status = await async_client.get(f"/api/plans/{plan_id}/execution")
            assert res_status.status_code == 200
            status_data = res_status.json()
            if status_data["status"] == "COMPLETED":
                break

        # 3. Assert final status
        assert status_data["status"] == "COMPLETED"
        assert status_data["total_tasks"] == 2
        assert status_data["completed_tasks"] == 2
        assert status_data["failed_tasks"] == 0


@pytest.mark.asyncio
async def test_rest_integration_repair_loop_flow(async_client, session_factory):
    """
    Test repair loop integration:
    HTTP execute plan -> Task fails verification -> Repair -> Retry succeeds -> Plan completed.
    """
    plan_id = "plan_integration_repair"
    task_id_1 = compute_task_id(plan_id, "task_repair")

    async with session_factory() as session:
        repo = TaskRepository(session)
        t1 = DevelopmentTask(
            task_id=task_id_1,
            plan_id=plan_id,
            project_id="proj_int",
            task_key="task_repair",
            title="Repair task",
            description="Repair test task",
            status=TaskStatus.PENDING,
            attempt_count=0,
            max_attempts=3,
        )
        await repo.create_tasks_idempotent(
            plan_id=plan_id,
            tasks=[t1],
            dependencies=[],
            plan_fingerprint="fp_repair_01",
        )
        await session.commit()

    attempt_counter = 0

    async def mock_execute_planned_task_repair(task_id, timeout_seconds=None, options=None):
        nonlocal attempt_counter
        attempt_counter += 1
        async with session_factory() as s:
            r = TaskRepository(s)
            if attempt_counter == 1:
                # First attempt fails verification -> Stage 8.13 REPAIR_REQUIRED
                await r.update_task_status(task_id_1, TaskStatus.REPAIR_REQUIRED, failure_reason="Verification failed")
                await s.commit()
                return AutonomousRunResult(
                    run_id="run_rep_1",
                    task_id=task_id_1,
                    status=RunStatus.FAILED,
                    task_status=TaskStatus.REPAIR_REQUIRED,
                    verification_passed=False,
                    failure_reason="Verification failed",
                )
            else:
                # Second attempt after repair -> COMMITTED
                await r.update_task_status(task_id_1, TaskStatus.COMMITTED, failure_reason="Repair succeeded")
                await s.commit()
                return AutonomousRunResult(
                    run_id="run_rep_2",
                    task_id=task_id_1,
                    status=RunStatus.SUCCEEDED,
                    task_status=TaskStatus.COMMITTED,
                    verification_passed=True,
                    commit_sha="sha_repair_fixed",
                )

    with patch("app.services.plan_execution_service.PlanExecutionService.execute_planned_task", side_effect=mock_execute_planned_task_repair):
        res_start = await async_client.post(f"/api/plans/{plan_id}/execute")
        assert res_start.status_code == 200

        for _ in range(20):
            await asyncio.sleep(0.1)
            res_status = await async_client.get(f"/api/plans/{plan_id}/execution")
            if res_status.json()["status"] == "COMPLETED":
                break

        assert res_status.json()["status"] == "COMPLETED"
        assert attempt_counter == 2
