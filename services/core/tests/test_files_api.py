"""
Backend tests for Files API (GET /api/files/list, read, search).
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.exceptions import HTTPException
from httpx import ASGITransport, AsyncClient

from app.api.routes.files import router as files_router
from app.dependencies import get_task_repository, get_tool_execution_service
from app.identity.auth import AuthMiddleware
from app.identity.bootstrap import BootstrapSecret
from app.identity.session import SessionStore
from app.tools.executor import ToolExecutor
from app.tools.implementation_registry import ToolImplementationRegistry
from app.tools.implementations.files_list import FilesListDirectory
from app.tools.implementations.files_read import FilesReadFile
from app.tools.implementations.files_search import FilesSearch
from app.tools.permission_engine import PermissionEngine
from app.tools.registry import ToolRegistry
from app.tools.tool_execution_service import ToolExecutionService


def _init_test_workspace(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "hello.txt").write_text("Hello, World!", encoding="utf-8")
    (path / "subdir").mkdir(exist_ok=True)
    (path / "subdir" / "nested.txt").write_text("Nested content", encoding="utf-8")


def _build_tool_execution_service(workspace_path: str):
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    for impl in [FilesListDirectory(), FilesReadFile(), FilesSearch()]:
        registry.register(impl.definition)
        impl_registry.register(impl)

    class MockAuditLogService:
        async def record(self, event_type, **kwargs):
            pass

    audit_service = MockAuditLogService()
    executor = ToolExecutor(registry=registry, impl_registry=impl_registry)
    permission_engine = PermissionEngine(
        registry=registry,
        audit_service=audit_service,
    )
    return ToolExecutionService(
        registry=registry,
        impl_registry=impl_registry,
        permission_engine=permission_engine,
        audit_service=audit_service,
        executor=executor,
    )


@pytest.fixture
def test_app(monkeypatch, tmp_path):
    _init_test_workspace(tmp_path)
    monkeypatch.setattr("app.api.routes.files._WORKSPACE_ROOT", str(tmp_path))

    tool_svc = _build_tool_execution_service(str(tmp_path))

    app = FastAPI()
    app.include_router(files_router, prefix="/api")

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

    # Override the tool execution service dependency
    async def get_tool_execution_service_override():
        return tool_svc

    app.dependency_overrides[get_tool_execution_service] = get_tool_execution_service_override

    return app


@pytest.fixture
async def async_client(test_app):
    test_session = await test_app.state.session_store.create(user_id="test_user")
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {test_session.session_id}"
        yield client


class TestFilesList:
    @pytest.mark.asyncio
    async def test_list_requires_auth(self, test_app):
        async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
            with pytest.raises(HTTPException) as exc_info:
                await client.get("/api/files/list")
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_list_default_path(self, test_app, async_client):
        resp = await async_client.get("/api/files/list")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_directory_success(self, test_app, async_client):
        resp = await async_client.get("/api/files/list?path=.")
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == "."
        assert "entries" in data
        names = {e["name"] for e in data["entries"]}
        assert "hello.txt" in names
        assert "subdir" in names

    @pytest.mark.asyncio
    async def test_list_traversal_blocked(self, test_app, async_client):
        resp = await async_client.get("/api/files/list?path=../secret")
        assert resp.status_code in (400, 403, 409)

    @pytest.mark.asyncio
    async def test_list_absolute_path_blocked(self, test_app, async_client):
        resp = await async_client.get("/api/files/list?path=/etc/passwd")
        assert resp.status_code in (400, 403, 409)


class TestFilesRead:
    @pytest.mark.asyncio
    async def test_read_requires_auth(self, test_app):
        async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
            with pytest.raises(HTTPException) as exc_info:
                await client.get("/api/files/read?path=foo")
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_read_missing_path(self, test_app, async_client):
        resp = await async_client.get("/api/files/read")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_read_file_success(self, test_app, async_client):
        resp = await async_client.get("/api/files/read?path=hello.txt")
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "Hello, World!"
        assert data["size"] == 13
        assert data["truncated"] is False

    @pytest.mark.asyncio
    async def test_read_traversal_blocked(self, test_app, async_client):
        resp = await async_client.get("/api/files/read?path=../secret.txt")
        assert resp.status_code in (400, 403, 409)


class TestFilesSearch:
    @pytest.mark.asyncio
    async def test_search_requires_auth(self, test_app):
        async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
            with pytest.raises(HTTPException) as exc_info:
                await client.get("/api/files/search?query=foo")
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_search_missing_query(self, test_app, async_client):
        resp = await async_client.get("/api/files/search")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_search_success(self, test_app, async_client):
        resp = await async_client.get("/api/files/search?query=hello")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["matches"]) == 1
        assert data["matches"][0]["name"] == "hello.txt"

    @pytest.mark.asyncio
    async def test_search_traversal_blocked(self, test_app, async_client):
        resp = await async_client.get("/api/files/search?query=x&path=../secret")
        assert resp.status_code in (400, 403, 409)
