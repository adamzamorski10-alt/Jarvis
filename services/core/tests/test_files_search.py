"""
Tests for Stage 18.6 — files.search tool.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.database import Base
from app.repositories.approval_repository import ApprovalRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.grant_repository import GrantRepository
from app.tools.implementations.files_search import FilesSearch, MAX_SEARCH_RESULTS, MAX_SEARCH_DEPTH, MAX_FILES_INSPECTED, MAX_QUERY_LENGTH
from app.tools.base import BaseTool
from app.tools.models import (
    AutonomyMode,
    RiskLevel,
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolCategory,
    WorkspaceContext,
)
from app.tools.tool_execution_service import ToolExecutionService
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.implementation_registry import ToolImplementationRegistry
from app.tools.permission_engine import PermissionEngine


# ===========================================================================
# Helpers
# ===========================================================================

class MockAuditLogService:
    def __init__(self):
        self.events: list[dict[str, Any]] = []

    async def record(self, event_type, **kwargs):
        self.events.append({"event_type": event_type, **kwargs})


def _build_service_with_real_permission():
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesSearch()
    registry.register(impl.definition)
    impl_registry.register(impl)
    audit_service = MockAuditLogService()
    executor = ToolExecutor(registry, impl_registry)

    permission_engine = PermissionEngine(
        registry=registry,
        audit_service=audit_service,
    )

    service = ToolExecutionService(
        registry=registry,
        impl_registry=impl_registry,
        permission_engine=permission_engine,
        audit_service=audit_service,
        executor=executor,
    )
    return service, permission_engine, audit_service


def _build_service_with_grant(grant_repo):
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesSearch()
    registry.register(impl.definition)
    impl_registry.register(impl)
    audit_service = MockAuditLogService()
    executor = ToolExecutor(registry, impl_registry)

    permission_engine = PermissionEngine(
        registry=registry,
        grant_repo=grant_repo,
        audit_service=audit_service,
    )

    service = ToolExecutionService(
        registry=registry,
        impl_registry=impl_registry,
        permission_engine=permission_engine,
        audit_service=audit_service,
        executor=executor,
    )
    return service, permission_engine, audit_service


def _init_test_workspace(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "hello.txt").write_text("Hello, World!", encoding="utf-8")
    (path / "subdir").mkdir(exist_ok=True)
    (path / "subdir" / "nested.txt").write_text("Nested content", encoding="utf-8")
    (path / "src").mkdir(exist_ok=True)
    (path / "src" / "main.py").write_text("print('main')", encoding="utf-8")
    (path / "src" / "utils.py").write_text("def util(): pass", encoding="utf-8")
    (path / "src" / "config").mkdir(exist_ok=True)
    (path / "src" / "config" / "settings.py").write_text("DEBUG = True", encoding="utf-8")
    (path / "tests").mkdir(exist_ok=True)
    (path / "tests" / "test_main.py").write_text("def test_main(): pass", encoding="utf-8")
    if sys.platform != "win32":
        (path / "link.txt").symlink_to(path / "hello.txt")
        (path / "dir_link").symlink_to(path / "subdir")
        (path / "parent_link").symlink_to(path / "subdir")


def _make_grant_repo():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    return engine, _init


# ===========================================================================
# A. Basic behavior
# ===========================================================================

@pytest.mark.asyncio
async def test_search_filename_match(tmp_path: Path):
    _init_test_workspace(tmp_path)
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.search", arguments={"query": "settings", "path": "."})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert res.status == ToolExecutionStatus.SUCCESS
        assert res.result["query"] == "settings"
        assert len(res.result["matches"]) == 1
        assert res.result["matches"][0]["name"] == "settings.py"
        assert res.result["matches"][0]["type"] == "file"
        assert res.result["truncated"] is False

    await engine.dispose()


@pytest.mark.asyncio
async def test_search_nested_match(tmp_path: Path):
    _init_test_workspace(tmp_path)
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.search", arguments={"query": "nested", "path": "."})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert res.status == ToolExecutionStatus.SUCCESS
        assert len(res.result["matches"]) == 1
        assert res.result["matches"][0]["path"] == "subdir/nested.txt"

    await engine.dispose()


@pytest.mark.asyncio
async def test_search_multiple_matches(tmp_path: Path):
    _init_test_workspace(tmp_path)
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.search", arguments={"query": ".py", "path": "."})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert res.status == ToolExecutionStatus.SUCCESS
        assert len(res.result["matches"]) == 4
        names = {m["name"] for m in res.result["matches"]}
        assert names == {"main.py", "utils.py", "settings.py", "test_main.py"}

    await engine.dispose()


@pytest.mark.asyncio
async def test_search_no_match(tmp_path: Path):
    _init_test_workspace(tmp_path)
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.search", arguments={"query": "nonexistent", "path": "."})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert res.status == ToolExecutionStatus.SUCCESS
        assert res.result["matches"] == []
        assert res.result["truncated"] is False

    await engine.dispose()


@pytest.mark.asyncio
async def test_search_deterministic_ordering(tmp_path: Path):
    _init_test_workspace(tmp_path)
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.search", arguments={"query": ".py", "path": "."})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        paths = [m["path"] for m in res.result["matches"]]
        assert paths == sorted(paths, key=lambda p: p.lower())

    await engine.dispose()


@pytest.mark.asyncio
async def test_search_case_insensitive(tmp_path: Path):
    _init_test_workspace(tmp_path)
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.search", arguments={"query": "SETTINGS", "path": "."})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert len(res.result["matches"]) == 1
        assert res.result["matches"][0]["name"] == "settings.py"

    await engine.dispose()


# ===========================================================================
# B. Query validation
# ===========================================================================

@pytest.mark.asyncio
async def test_search_empty_query_fails(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.search", arguments={"query": ""})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_search_query_max_length_accepted(tmp_path: Path):
    _init_test_workspace(tmp_path)
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        service, _, _ = _build_service_with_grant(grant_repo)
        query = "a" * MAX_QUERY_LENGTH
        call = ToolCall(tool_id="files.search", arguments={"query": query, "path": "."})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert res.result["matches"] == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_search_query_over_max_length_fails(tmp_path: Path):
    _init_test_workspace(tmp_path)
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        service, _, _ = _build_service_with_grant(grant_repo)
        query = "a" * (MAX_QUERY_LENGTH + 1)
        call = ToolCall(tool_id="files.search", arguments={"query": query, "path": "."})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is False

    await engine.dispose()


@pytest.mark.asyncio
async def test_search_special_characters_treated_literally(tmp_path: Path):
    _init_test_workspace(tmp_path)
    (tmp_path / "file[1].txt").write_text("data", encoding="utf-8")
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.search", arguments={"query": "[1]", "path": "."})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert len(res.result["matches"]) == 1
        assert res.result["matches"][0]["name"] == "file[1].txt"

    await engine.dispose()


# ===========================================================================
# C. Search root
# ===========================================================================

@pytest.mark.asyncio
async def test_search_root_dot(tmp_path: Path):
    _init_test_workspace(tmp_path)
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.search", arguments={"query": "hello.txt", "path": "."})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert len(res.result["matches"]) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_search_valid_relative_subdirectory(tmp_path: Path):
    _init_test_workspace(tmp_path)
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.search", arguments={"query": "main.py", "path": "src"})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert len(res.result["matches"]) == 1
        assert res.result["matches"][0]["path"] == "src/main.py"

    await engine.dispose()


@pytest.mark.asyncio
async def test_search_nonexistent_subdirectory_fails(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.search", arguments={"query": "hello", "path": "nonexistent"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_search_absolute_path_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.search", arguments={"query": "hello", "path": str(tmp_path)})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive paths")
@pytest.mark.asyncio
async def test_search_windows_drive_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.search", arguments={"query": "hello", "path": "C:\\Windows"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_search_unc_path_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.search", arguments={"query": "hello", "path": "\\\\server\\share"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_search_traversal_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.search", arguments={"query": "hello", "path": ".."})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_search_mixed_separator_escape_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.search", arguments={"query": "hello", "path": "subdir\\..\\.."})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require elevated privileges on Windows")
@pytest.mark.asyncio
async def test_search_symlink_root_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.search", arguments={"query": "hello", "path": "dir_link"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


# ===========================================================================
# D. Bounds
# ===========================================================================

@pytest.mark.asyncio
async def test_search_default_max_results(tmp_path: Path):
    _init_test_workspace(tmp_path)
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.search", arguments={"query": ".py", "path": "."})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert len(res.result["matches"]) <= 50

    await engine.dispose()


@pytest.mark.asyncio
async def test_search_custom_max_results(tmp_path: Path):
    _init_test_workspace(tmp_path)
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.search", arguments={"query": ".py", "path": ".", "max_results": 2})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert len(res.result["matches"]) == 2
        assert res.result["truncated"] is True

    await engine.dispose()


@pytest.mark.asyncio
async def test_search_max_results_capped_at_200(tmp_path: Path):
    _init_test_workspace(tmp_path)
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.search", arguments={"query": ".py", "path": ".", "max_results": MAX_SEARCH_RESULTS})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert len(res.result["matches"]) <= MAX_SEARCH_RESULTS

    await engine.dispose()


@pytest.mark.asyncio
async def test_search_max_depth_respected(tmp_path: Path):
    _init_test_workspace(tmp_path)
    deep = tmp_path / "a" / "b" / "c" / "d" / "e" / "f" / "g" / "h" / "i" / "j" / "k"
    deep.mkdir(parents=True, exist_ok=True)
    (deep / "deep.txt").write_text("deep", encoding="utf-8")
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.search", arguments={"query": "deep.txt", "path": "."})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert res.result["truncated"] is True

    await engine.dispose()


@pytest.mark.asyncio
async def test_search_truncated_when_max_results_reached(tmp_path: Path):
    _init_test_workspace(tmp_path)
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.search", arguments={"query": ".py", "path": ".", "max_results": 1})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert len(res.result["matches"]) == 1
        assert res.result["truncated"] is True

    await engine.dispose()


# ===========================================================================
# E. Symlink security
# ===========================================================================

@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require elevated privileges on Windows")
@pytest.mark.asyncio
async def test_search_symlink_file_not_followed(tmp_path: Path):
    _init_test_workspace(tmp_path)
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.search", arguments={"query": "hello.txt", "path": "."})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        for match in res.result["matches"]:
            if match["name"] == "link.txt":
                assert match["type"] == "symlink"
                break
        else:
            pytest.fail("link.txt should appear as symlink in results")

    await engine.dispose()


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require elevated privileges on Windows")
@pytest.mark.asyncio
async def test_search_symlink_directory_not_traversed(tmp_path: Path):
    _init_test_workspace(tmp_path)
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.search", arguments={"query": "nested.txt", "path": "."})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        for match in res.result["matches"]:
            assert "dir_link" not in match["path"]

    await engine.dispose()


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require elevated privileges on Windows")
@pytest.mark.asyncio
async def test_search_resolved_outside_symlink_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    outside = tmp_path / ".." / "outside_workspace"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "real.txt").write_text("outside", encoding="utf-8")
    (tmp_path / "escape_link").symlink_to(outside / "real.txt")
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.search", arguments={"query": "real.txt", "path": "."})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is True
    for match in res.result["matches"]:
        assert match["name"] != "real.txt"


# ===========================================================================
# F. Permission boundary
# ===========================================================================

@pytest.mark.asyncio
async def test_search_deny_prevents_traversal(tmp_path: Path):
    _init_test_workspace(tmp_path)
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesSearch()
    registry.register(impl.definition)
    impl_registry.register(impl)

    permission_engine = PermissionEngine(
        registry=registry,
        grant_repo=None,
        audit_service=None,
        approval_repo=None,
    )

    audit_service = MockAuditLogService()
    executor = ToolExecutor(registry, impl_registry)
    service = ToolExecutionService(
        registry=registry,
        impl_registry=impl_registry,
        permission_engine=permission_engine,
        audit_service=audit_service,
        executor=executor,
    )

    call = ToolCall(tool_id="files.search", arguments={"query": "hello"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.MANUAL,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False
    assert res.status == ToolExecutionStatus.CANCELLED
    assert res.error_code == "ask_user"


@pytest.mark.asyncio
async def test_search_ask_user_never_traverses(tmp_path: Path):
    _init_test_workspace(tmp_path)
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesSearch()
    registry.register(impl.definition)
    impl_registry.register(impl)

    permission_engine = PermissionEngine(
        registry=registry,
        grant_repo=None,
        audit_service=None,
        approval_repo=None,
    )

    audit_service = MockAuditLogService()
    executor = ToolExecutor(registry, impl_registry)
    service = ToolExecutionService(
        registry=registry,
        impl_registry=impl_registry,
        permission_engine=permission_engine,
        audit_service=audit_service,
        executor=executor,
    )

    call = ToolCall(tool_id="files.search", arguments={"query": "hello"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.status == ToolExecutionStatus.SUCCESS
    assert res.success is True


@pytest.mark.asyncio
async def test_search_allow_with_grant_executes(tmp_path: Path):
    _init_test_workspace(tmp_path)
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.search", arguments={"query": "hello.txt", "path": "."})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert res.status == ToolExecutionStatus.SUCCESS
        assert len(res.result["matches"]) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_search_requires_no_confirmation(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.search", arguments={"query": "hello.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.status == ToolExecutionStatus.SUCCESS
    assert res.success is True


@pytest.mark.asyncio
async def test_search_invalid_approval_context_prevents_execution(tmp_path: Path):
    _init_test_workspace(tmp_path)
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesSearch()
    registry.register(impl.definition)
    impl_registry.register(impl)

    permission_engine = PermissionEngine(
        registry=registry,
        grant_repo=None,
        audit_service=None,
        approval_repo=None,
    )

    audit_service = MockAuditLogService()
    executor = ToolExecutor(registry, impl_registry)
    service = ToolExecutionService(
        registry=registry,
        impl_registry=impl_registry,
        permission_engine=permission_engine,
        audit_service=audit_service,
        executor=executor,
    )

    class FakeApprovalContext:
        approval_id = "fake-approval"

    call = ToolCall(tool_id="files.search", arguments={"query": "hello.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
        approval_context=FakeApprovalContext(),
    )
    assert res.success is False
    assert res.status == ToolExecutionStatus.DENIED


# ===========================================================================
# G. Context authority
# ===========================================================================

@pytest.mark.asyncio
async def test_search_workspace_path_required(tmp_path: Path):
    _init_test_workspace(tmp_path)
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        registry = ToolRegistry()
        impl_registry = ToolImplementationRegistry()
        impl = FilesSearch()
        registry.register(impl.definition)
        impl_registry.register(impl)

        permission_engine = PermissionEngine(
            registry=registry,
            grant_repo=grant_repo,
            audit_service=None,
            approval_repo=None,
        )

        audit_service = MockAuditLogService()
        executor = ToolExecutor(registry, impl_registry)
        service = ToolExecutionService(
            registry=registry,
            impl_registry=impl_registry,
            permission_engine=permission_engine,
            audit_service=audit_service,
            executor=executor,
        )

        call = ToolCall(tool_id="files.search", arguments={"query": "hello.txt"})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={},
        )
        assert res.success is False
        assert res.status == ToolExecutionStatus.ERROR

    await engine.dispose()


# ===========================================================================
# H. Audit
# ===========================================================================

@pytest.mark.asyncio
async def test_search_audit_event_exists(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, audit_service = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.search", arguments={"query": "hello.txt"})
    await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert len(audit_service.events) >= 1
    event_types = [e["event_type"] for e in audit_service.events]
    assert any("PERMISSION" in str(et) for et in event_types)


@pytest.mark.asyncio
async def test_search_result_bounded(tmp_path: Path):
    _init_test_workspace(tmp_path)
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.search",
            action_pattern="search",
            grant_type="persistent",
        )
        await session.commit()

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.search", arguments={"query": ".py", "path": "."})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert "success" in res.result
        assert "query" in res.result
        assert "path" in res.result
        assert "matches" in res.result
        assert "truncated" in res.result
        assert isinstance(res.result["matches"], list)
        assert len(res.result["matches"]) <= MAX_SEARCH_RESULTS

    await engine.dispose()


# ===========================================================================
# I. Failure behavior
# ===========================================================================

@pytest.mark.asyncio
async def test_search_missing_root_returns_error(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.search", arguments={"query": "hello", "path": "nonexistent"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_search_no_filesystem_mutation_on_deny(tmp_path: Path):
    _init_test_workspace(tmp_path)
    original_files = set(p.name for p in tmp_path.iterdir())
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesSearch()
    registry.register(impl.definition)
    impl_registry.register(impl)

    permission_engine = PermissionEngine(
        registry=registry,
        grant_repo=None,
        audit_service=None,
        approval_repo=None,
    )

    audit_service = MockAuditLogService()
    executor = ToolExecutor(registry, impl_registry)
    service = ToolExecutionService(
        registry=registry,
        impl_registry=impl_registry,
        permission_engine=permission_engine,
        audit_service=audit_service,
        executor=executor,
    )

    call = ToolCall(tool_id="files.search", arguments={"query": "hello"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.MANUAL,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False
    assert set(p.name for p in tmp_path.iterdir()) == original_files


# ===========================================================================
# J. Regression
# ===========================================================================

@pytest.mark.asyncio
async def test_read_file_still_works(tmp_path: Path):
    _init_test_workspace(tmp_path)
    from app.tools.implementations.files_read import FilesReadFile
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesReadFile()
    registry.register(impl.definition)
    impl_registry.register(impl)
    audit_service = MockAuditLogService()
    executor = ToolExecutor(registry, impl_registry)
    permission_engine = PermissionEngine(registry=registry, audit_service=audit_service)
    service = ToolExecutionService(
        registry=registry,
        impl_registry=impl_registry,
        permission_engine=permission_engine,
        audit_service=audit_service,
        executor=executor,
    )
    call = ToolCall(tool_id="files.read_file", arguments={"path": "hello.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is True
    assert res.result["content"] == "Hello, World!"


@pytest.mark.asyncio
async def test_list_directory_still_works(tmp_path: Path):
    _init_test_workspace(tmp_path)
    from app.tools.implementations.files_list import FilesListDirectory
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesListDirectory()
    registry.register(impl.definition)
    impl_registry.register(impl)
    audit_service = MockAuditLogService()
    executor = ToolExecutor(registry, impl_registry)
    permission_engine = PermissionEngine(registry=registry, audit_service=audit_service)
    service = ToolExecutionService(
        registry=registry,
        impl_registry=impl_registry,
        permission_engine=permission_engine,
        audit_service=audit_service,
        executor=executor,
    )
    call = ToolCall(tool_id="files.list_directory", arguments={"path": "."})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is True
    names = {e["name"] for e in res.result["entries"]}
    assert "hello.txt" in names


@pytest.mark.asyncio
async def test_write_file_still_works(tmp_path: Path):
    _init_test_workspace(tmp_path)
    from app.tools.implementations.files_write import FilesWriteFile
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.write_file",
            action_pattern="write_file",
            grant_type="persistent",
        )
        await session.commit()

        registry = ToolRegistry()
        impl_registry = ToolImplementationRegistry()
        impl = FilesWriteFile()
        registry.register(impl.definition)
        impl_registry.register(impl)
        audit_service = MockAuditLogService()
        executor = ToolExecutor(registry, impl_registry)
        permission_engine = PermissionEngine(registry=registry, grant_repo=grant_repo, audit_service=audit_service)
        service = ToolExecutionService(
            registry=registry,
            impl_registry=impl_registry,
            permission_engine=permission_engine,
            audit_service=audit_service,
            executor=executor,
        )
        call = ToolCall(tool_id="files.write_file", arguments={"path": "new.txt", "content": "new"})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "new"

    await engine.dispose()


@pytest.mark.asyncio
async def test_delete_file_still_works(tmp_path: Path):
    _init_test_workspace(tmp_path)
    from app.tools.implementations.files_delete import FilesDeleteFile
    engine, init = _make_grant_repo()
    await init()
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        grant_repo = GrantRepository(session)
        grant = await grant_repo.create_grant(
            tool_id="files.delete_file",
            action_pattern="delete_file",
            grant_type="persistent",
        )
        await session.commit()

        registry = ToolRegistry()
        impl_registry = ToolImplementationRegistry()
        impl = FilesDeleteFile()
        registry.register(impl.definition)
        impl_registry.register(impl)
        audit_service = MockAuditLogService()
        executor = ToolExecutor(registry, impl_registry)
        permission_engine = PermissionEngine(registry=registry, grant_repo=grant_repo, audit_service=audit_service)
        service = ToolExecutionService(
            registry=registry,
            impl_registry=impl_registry,
            permission_engine=permission_engine,
            audit_service=audit_service,
            executor=executor,
        )
        call = ToolCall(tool_id="files.delete_file", arguments={"path": "hello.txt"})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert not (tmp_path / "hello.txt").exists()

    await engine.dispose()


@pytest.mark.asyncio
async def test_tool_execution_service_boundary_intact(tmp_path: Path):
    _init_test_workspace(tmp_path)
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesSearch()
    registry.register(impl.definition)
    impl_registry.register(impl)
    audit_service = MockAuditLogService()
    executor = ToolExecutor(registry, impl_registry)
    permission_engine = PermissionEngine(registry=registry, audit_service=audit_service)
    service = ToolExecutionService(
        registry=registry,
        impl_registry=impl_registry,
        permission_engine=permission_engine,
        audit_service=audit_service,
        executor=executor,
    )
    call = ToolCall(tool_id="files.search", arguments={"query": "hello.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.MANUAL,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.status == ToolExecutionStatus.CANCELLED
    assert res.error_code == "ask_user"


@pytest.mark.asyncio
async def test_permission_engine_boundary_intact(tmp_path: Path):
    _init_test_workspace(tmp_path)
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesSearch()
    registry.register(impl.definition)
    impl_registry.register(impl)
    audit_service = MockAuditLogService()
    executor = ToolExecutor(registry, impl_registry)
    permission_engine = PermissionEngine(registry=registry, audit_service=audit_service)
    service = ToolExecutionService(
        registry=registry,
        impl_registry=impl_registry,
        permission_engine=permission_engine,
        audit_service=audit_service,
        executor=executor,
    )
    call = ToolCall(tool_id="files.search", arguments={"query": "hello.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.MANUAL,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.status == ToolExecutionStatus.CANCELLED
    assert res.error_code == "ask_user"


# ===========================================================================
# Windows-specific tests
# ===========================================================================

@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
@pytest.mark.asyncio
async def test_search_windows_mixed_separators(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.search", arguments={"query": "hello.txt", "path": "subdir\\..\\.."})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
@pytest.mark.asyncio
async def test_search_windows_reserved_name_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.search", arguments={"query": "NUL.txt", "path": "."})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is True
    assert res.result["matches"] == []
