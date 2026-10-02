"""
Backend tests for Tasks API (GET /api/tasks, GET /api/tasks/{id}).
"""
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.exceptions import HTTPException
from httpx import ASGITransport, AsyncClient

from app.api.routes.tasks import router as tasks_router
from app.dependencies import get_task_repository
from app.identity.auth import AuthMiddleware
from app.identity.bootstrap import BootstrapSecret
from app.identity.session import SessionStore
from app.tasks.models import DevelopmentTask, TaskStatus


class FakeTaskRepository:
    def __init__(self, tasks: list[DevelopmentTask]):
        self._tasks = tasks

    async def list_tasks(self, **kwargs):
        return self._tasks

    async def get_task(self, task_id: str):
        for t in self._tasks:
            if t.task_id == task_id:
                return t
        return None


@pytest.fixture
def test_app():
    app = FastAPI()
    app.include_router(tasks_router, prefix="/api")

    @app.exception_handler(Exception)
    async def custom_exception_handler(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "detail": "An unexpected error occurred."},
        )

    session_store = SessionStore()
    bootstrap_secret = BootstrapSecret()
    app.state.session_store = session_store
    app.state.bootstrap_secret = bootstrap_secret
    app.add_middleware(AuthMiddleware)

    return app


@pytest.fixture
async def async_client(test_app):
    test_session = await test_app.state.session_store.create(user_id="test_user")
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {test_session.session_id}"
        yield client


def _make_tasks():
    return [
        DevelopmentTask(
            task_id="task_list_1",
            title="Task One",
            description="Description for task one",
            project_id="proj_1",
            status=TaskStatus.PENDING,
            priority=10,
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ),
        DevelopmentTask(
            task_id="task_list_2",
            title="Task Two",
            description="Description for task two",
            project_id="proj_1",
            status=TaskStatus.COMMITTED,
            priority=20,
            created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        ),
        DevelopmentTask(
            task_id="task_list_3",
            title="Task Three",
            description="Description for task three",
            project_id="proj_1",
            status=TaskStatus.FAILED,
            priority=5,
            created_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
        ),
    ]


class TestTasksList:
    @pytest.mark.asyncio
    async def test_list_requires_auth(self, test_app):
        async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
            with pytest.raises(HTTPException) as exc_info:
                await client.get("/api/tasks")
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_list_with_data(self, test_app, async_client):
        fake_repo = FakeTaskRepository(_make_tasks())
        test_app.dependency_overrides[get_task_repository] = lambda: fake_repo

        resp = await async_client.get("/api/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert data["limit"] == 50
        assert len(data["tasks"]) == 3
        test_app.dependency_overrides.pop(get_task_repository, None)

    @pytest.mark.asyncio
    async def test_list_status_filter(self, test_app, async_client):
        tasks = [t for t in _make_tasks() if t.status == TaskStatus.FAILED]
        fake_repo = FakeTaskRepository(tasks)
        test_app.dependency_overrides[get_task_repository] = lambda: fake_repo

        resp = await async_client.get("/api/tasks?status=FAILED")
        assert resp.status_code == 200
        data = resp.json()
        assert all(t["status"] == "FAILED" for t in data["tasks"])
        test_app.dependency_overrides.pop(get_task_repository, None)

    @pytest.mark.asyncio
    async def test_list_bounded(self, test_app, async_client):
        fake_repo = FakeTaskRepository(_make_tasks())
        test_app.dependency_overrides[get_task_repository] = lambda: fake_repo

        resp = await async_client.get("/api/tasks?limit=50&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] <= 50
        test_app.dependency_overrides.pop(get_task_repository, None)


class TestTaskDetail:
    @pytest.mark.asyncio
    async def test_get_task_requires_auth(self, test_app):
        async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
            with pytest.raises(HTTPException) as exc_info:
                await client.get("/api/tasks/task_abc")
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, test_app, async_client):
        fake_repo = FakeTaskRepository([])
        test_app.dependency_overrides[get_task_repository] = lambda: fake_repo

        resp = await async_client.get("/api/tasks/task_does_not_exist")
        assert resp.status_code == 404
        test_app.dependency_overrides.pop(get_task_repository, None)

    @pytest.mark.asyncio
    async def test_get_task_detail(self, test_app, async_client):
        task = DevelopmentTask(
            task_id="task_detail_1",
            title="Detail Task",
            description="Description for detail task",
            project_id="proj_1",
            plan_id="plan_1",
            status=TaskStatus.RUNNING,
            priority=10,
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        fake_repo = FakeTaskRepository([task])
        test_app.dependency_overrides[get_task_repository] = lambda: fake_repo

        resp = await async_client.get("/api/tasks/task_detail_1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "task_detail_1"
        assert data["title"] == "Detail Task"
        assert data["status"] == "RUNNING"
        assert data["plan_id"] == "plan_1"
        test_app.dependency_overrides.pop(get_task_repository, None)

    @pytest.mark.asyncio
    async def test_get_task_no_secret_exposure(self, test_app, async_client):
        task = DevelopmentTask(
            task_id="task_detail_1",
            title="Detail Task",
            description="Description for detail task",
            project_id="proj_1",
            status=TaskStatus.PENDING,
            priority=10,
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        fake_repo = FakeTaskRepository([task])
        test_app.dependency_overrides[get_task_repository] = lambda: fake_repo

        resp = await async_client.get("/api/tasks/task_detail_1")
        assert resp.status_code == 200
        text = resp.text
        assert "password" not in text.lower()
        assert "secret" not in text.lower()
        test_app.dependency_overrides.pop(get_task_repository, None)
