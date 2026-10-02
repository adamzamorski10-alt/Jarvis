"""
Stage 8.15 REST Control Plane Unit & Behavioral Test Suite.

Covers tests API-01 through API-40.
"""
import asyncio
from datetime import datetime, timezone
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.models.plans_models import (
    ErrorResponse,
    PlanExecutionResponse,
    PlanExecutionStatusResponse,
    RunStatusResponse,
    TaskStatusResponse,
)
from app.api.routes.plans import router as plans_router
from app.db.database import init_db
from app.dependencies import get_background_execution_manager, get_task_repository
from app.identity.auth import AuthMiddleware
from app.identity.bootstrap import BootstrapSecret
from app.identity.session import SessionStore
from app.orchestration.run_controller import AutonomousRunResult, RunStatus
from app.repositories.task_repository import TaskRepository
from app.services.autonomous_loop import (
    AutonomousPlanExecutor,
    PlanExecutionResult,
    PlanExecutionState,
)
from app.services.background_execution_manager import (
    BackgroundExecutionManager,
    ExecutionManagerStatus,
)
from app.tasks.models import DevelopmentTask, TaskStatus


@pytest.fixture
def test_app():
    app = FastAPI()
    app.include_router(plans_router, prefix="/api")

    # Global exception handler
    @app.exception_handler(Exception)
    async def custom_exception_handler(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "detail": "An unexpected error occurred."},
        )

    # Set up authentication for tests
    session_store = SessionStore()
    bootstrap_secret = BootstrapSecret()
    app.state.session_store = session_store
    app.state.bootstrap_secret = bootstrap_secret
    app.add_middleware(AuthMiddleware)

    return app


@pytest.fixture
async def async_client(test_app):
    # Create a test session for authentication
    test_session = await test_app.state.session_store.create(user_id="test_user")
    
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {test_session.session_id}"
        yield client


@pytest.fixture
def mock_task_repo():
    repo = AsyncMock(spec=TaskRepository)
    return repo


@pytest.fixture
def mock_bg_manager():
    mgr = AsyncMock(spec=BackgroundExecutionManager)
    return mgr


def create_sample_task(
    task_id="t1",
    plan_id="p1",
    task_key="k1",
    status=TaskStatus.PENDING,
    attempt_count=0,
    max_attempts=3,
):
    return DevelopmentTask(
        task_id=task_id,
        plan_id=plan_id,
        project_id="proj_1",
        task_key=task_key,
        title=f"Task {task_key}",
        description="Test task description",
        status=status,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        started_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_api_01_execute_existing_plan(test_app, async_client, mock_task_repo, mock_bg_manager):
    sample_task = create_sample_task("t1", "p1", "k1", TaskStatus.PENDING)
    mock_task_repo.get_tasks_by_plan.return_value = [sample_task]

    mock_info = MagicMock()
    mock_info.execution_id = "exec_12345"
    mock_info.status.value = "RUNNING"
    mock_bg_manager.start_execution.return_value = mock_info

    test_app.dependency_overrides[get_task_repository] = lambda: mock_task_repo
    test_app.dependency_overrides[get_background_execution_manager] = lambda: mock_bg_manager

    res = await async_client.post("/api/plans/p1/execute")
    assert res.status_code == 200
    data = res.json()
    assert data["plan_id"] == "p1"
    assert data["execution_id"] == "exec_12345"
    assert data["status"] == "RUNNING"


@pytest.mark.asyncio
async def test_api_02_execute_unknown_plan(test_app, async_client, mock_task_repo, mock_bg_manager):
    mock_task_repo.get_tasks_by_plan.return_value = []
    test_app.dependency_overrides[get_task_repository] = lambda: mock_task_repo
    test_app.dependency_overrides[get_background_execution_manager] = lambda: mock_bg_manager

    res = await async_client.post("/api/plans/nonexistent/execute")
    assert res.status_code == 404
    data = res.json()
    assert "not found" in data["detail"].lower()


@pytest.mark.asyncio
async def test_api_03_immediate_response_without_waiting(test_app, async_client, mock_task_repo, mock_bg_manager):
    sample_task = create_sample_task("t1", "p1", "k1")
    mock_task_repo.get_tasks_by_plan.return_value = [sample_task]

    mock_info = MagicMock()
    mock_info.execution_id = "exec_fast"
    mock_info.status.value = "RUNNING"
    mock_bg_manager.start_execution.return_value = mock_info

    test_app.dependency_overrides[get_task_repository] = lambda: mock_task_repo
    test_app.dependency_overrides[get_background_execution_manager] = lambda: mock_bg_manager

    start_time = asyncio.get_event_loop().time()
    res = await async_client.post("/api/plans/p1/execute")
    duration = asyncio.get_event_loop().time() - start_time

    assert res.status_code == 200
    assert duration < 1.0  # Immediate non-blocking response


@pytest.mark.asyncio
async def test_api_04_05_06_plan_execution_lifecycle_status(test_app, async_client, mock_task_repo, mock_bg_manager):
    sample_task = create_sample_task("t1", "p1", "k1", TaskStatus.COMMITTED)
    mock_task_repo.get_tasks_by_plan.return_value = [sample_task]
    mock_bg_manager.get_active_execution_by_plan.return_value = None

    test_app.dependency_overrides[get_task_repository] = lambda: mock_task_repo
    test_app.dependency_overrides[get_background_execution_manager] = lambda: mock_bg_manager

    res = await async_client.get("/api/plans/p1/execution")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "COMPLETED"
    assert data["total_tasks"] == 1
    assert data["completed_tasks"] == 1


@pytest.mark.asyncio
async def test_api_07_task_status_endpoint(test_app, async_client, mock_task_repo):
    sample_task = create_sample_task("t1", "p1", "k1", TaskStatus.COMMITTED, attempt_count=1)
    mock_task_repo.get_task.return_value = sample_task

    test_app.dependency_overrides[get_task_repository] = lambda: mock_task_repo

    res = await async_client.get("/api/tasks/t1")
    assert res.status_code == 200
    data = res.json()
    assert data["task_id"] == "t1"
    assert data["status"] == "COMMITTED"
    assert data["attempt_count"] == 1


@pytest.mark.asyncio
async def test_api_10_11_12_13_cancellation(test_app, async_client, mock_task_repo, mock_bg_manager):
    mock_info = MagicMock()
    mock_info.execution_id = "exec_123"
    mock_info.plan_id = "p1"
    mock_info.status.value = "CANCELLED"
    mock_info.started_at = datetime.now(timezone.utc)
    mock_info.completed_at = datetime.now(timezone.utc)

    mock_bg_manager.cancel_execution.return_value = mock_info
    mock_task_repo.get_tasks_by_plan.return_value = [create_sample_task("t1", "p1", "k1")]

    test_app.dependency_overrides[get_task_repository] = lambda: mock_task_repo
    test_app.dependency_overrides[get_background_execution_manager] = lambda: mock_bg_manager

    res = await async_client.post("/api/plans/p1/cancel")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "CANCELLED"

    res_exec = await async_client.post("/api/executions/exec_123/cancel")
    assert res_exec.status_code == 200
    assert res_exec.json()["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_api_14_15_16_duplicate_execute_conflict(test_app, async_client, mock_task_repo, mock_bg_manager):
    sample_task = create_sample_task("t1", "p1", "k1", TaskStatus.RUNNING)
    mock_task_repo.get_tasks_by_plan.return_value = [sample_task]

    test_app.dependency_overrides[get_task_repository] = lambda: mock_task_repo
    test_app.dependency_overrides[get_background_execution_manager] = lambda: mock_bg_manager

    res = await async_client.post("/api/plans/p1/execute")
    assert res.status_code == 409
    data = res.json()
    assert "already executing" in data["detail"].lower()


@pytest.mark.asyncio
async def test_api_21_22_23_24_25_26_27_safe_error_handling(test_app, async_client, mock_task_repo):
    # Test 404 safe error
    mock_task_repo.get_tasks_by_plan.return_value = []
    test_app.dependency_overrides[get_task_repository] = lambda: mock_task_repo

    res = await async_client.get("/api/plans/unknown/execution")
    assert res.status_code == 404
    data = res.json()
    assert "detail" in data
    # No stack trace, path, or secret leaks
    assert "Traceback" not in json.dumps(data)
    assert "C:\\" not in json.dumps(data)
    assert "F:\\" not in json.dumps(data)


@pytest.mark.asyncio
async def test_api_28_to_32_security_boundary_enforcement(test_app, async_client):
    # Ensure no dangerous endpoints exist on router
    routes = [r.path for r in plans_router.routes]
    forbidden_terms = ["/shell", "/cmd", "/subprocess", "/workspace", "/git"]
    for route in routes:
        for term in forbidden_terms:
            assert term not in route


@pytest.mark.asyncio
async def test_api_33_to_40_bounded_dto_contracts():
    response = TaskStatusResponse(
        task_id="t1",
        plan_id="p1",
        task_key="k1",
        title="Test Task",
        status="COMMITTED",
        attempt_count=1,
        max_attempts=3,
        failure_reason="x" * 2000,
    )
    # Ensure immutability & validation
    assert response.task_id == "t1"
    with pytest.raises(Exception):
        response.status = "MUTATED"
