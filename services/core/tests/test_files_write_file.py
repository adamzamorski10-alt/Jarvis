"""
Tests for Stage 18.3 — files.write_file tool.
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
from app.tools.implementations.files_write import FilesWriteFile, MAX_WRITE_BYTES
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
    impl = FilesWriteFile()
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
    impl = FilesWriteFile()
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
    (path / "utf8.txt").write_text("Héllo Wörld 😀", encoding="utf-8")
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
# A. Basic write
# ===========================================================================

@pytest.mark.asyncio
async def test_write_new_file(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.write_file", arguments={"path": "new_file.txt", "content": "new content"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False
    assert res.status == ToolExecutionStatus.CANCELLED


@pytest.mark.asyncio
async def test_write_new_file_with_grant(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.write_file", arguments={"path": "new_file.txt", "content": "new content"})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert res.status == ToolExecutionStatus.SUCCESS
        assert res.result["path"] == "new_file.txt"
        assert res.result["size"] == 11
        assert (tmp_path / "new_file.txt").read_text(encoding="utf-8") == "new content"

    await engine.dispose()


@pytest.mark.asyncio
async def test_write_overwrite_existing_file(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.write_file", arguments={"path": "hello.txt", "content": "overwritten"})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert res.status == ToolExecutionStatus.SUCCESS
        assert res.result["path"] == "hello.txt"
        assert res.result["size"] == 11
        assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "overwritten"

    await engine.dispose()


@pytest.mark.asyncio
async def test_write_empty_content(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.write_file", arguments={"path": "empty.txt", "content": ""})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert res.status == ToolExecutionStatus.SUCCESS
        assert res.result["path"] == "empty.txt"
        assert res.result["size"] == 0
        assert (tmp_path / "empty.txt").read_text(encoding="utf-8") == ""

    await engine.dispose()


@pytest.mark.asyncio
async def test_write_valid_utf8_content(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        content = "Héllo Wörld 😀 你好世界"
        call = ToolCall(tool_id="files.write_file", arguments={"path": "utf8_write.txt", "content": content})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert res.status == ToolExecutionStatus.SUCCESS
        assert res.result["path"] == "utf8_write.txt"
        assert (tmp_path / "utf8_write.txt").read_text(encoding="utf-8") == content

    await engine.dispose()


# ===========================================================================
# B. Size limits
# ===========================================================================

@pytest.mark.asyncio
async def test_write_service_allows_exactly_max_size_through_production_path(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        content = "x" * MAX_WRITE_BYTES
        call = ToolCall(tool_id="files.write_file", arguments={"path": "max.txt", "content": content})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert res.status == ToolExecutionStatus.SUCCESS
        assert res.result["size"] == MAX_WRITE_BYTES

    await engine.dispose()


@pytest.mark.asyncio
async def test_write_tool_enforces_max_size_directly(tmp_path: Path):
    _init_test_workspace(tmp_path)
    tool = FilesWriteFile()
    workspace_ctx = ToolExecutionContext(workspace=WorkspaceContext(workspace_path=str(tmp_path)))
    result = await tool.execute(
        {"path": "max_direct.txt", "content": "x" * MAX_WRITE_BYTES},
        context=workspace_ctx,
    )
    assert result["success"] is True
    assert result["size"] == MAX_WRITE_BYTES
    assert (tmp_path / "max_direct.txt").read_text(encoding="utf-8") == "x" * MAX_WRITE_BYTES


@pytest.mark.asyncio
async def test_write_over_max_size_fails(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        content = "x" * (MAX_WRITE_BYTES + 1)
        call = ToolCall(tool_id="files.write_file", arguments={"path": "too_big.txt", "content": content})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is False
        assert res.status == ToolExecutionStatus.ERROR
        assert "exceeds" in (res.error_message or "").lower()

    await engine.dispose()


@pytest.mark.asyncio
async def test_write_over_max_size_no_filesystem_mutation(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        content = "x" * (MAX_WRITE_BYTES + 1)
        call = ToolCall(tool_id="files.write_file", arguments={"path": "hello.txt", "content": content})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is False
        original = (tmp_path / "hello.txt").read_text(encoding="utf-8")
        assert original == "Hello, World!"

    await engine.dispose()


# ===========================================================================
# C. Path security
# ===========================================================================

@pytest.mark.asyncio
async def test_write_traversal_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.write_file", arguments={"path": "../outside.txt", "content": "evil"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_write_traversal_backslash_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.write_file", arguments={"path": "..\\outside.txt", "content": "evil"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_write_absolute_path_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.write_file", arguments={"path": str(tmp_path / "hello.txt"), "content": "evil"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive paths")
@pytest.mark.asyncio
async def test_write_windows_drive_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.write_file", arguments={"path": "C:\\Windows\\evil.txt", "content": "evil"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_write_unc_path_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.write_file", arguments={"path": "\\\\server\\share\\evil.txt", "content": "evil"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_write_mixed_separator_escape_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.write_file", arguments={"path": "subdir\\..\\outside.txt", "content": "evil"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


# ===========================================================================
# D. Target security
# ===========================================================================

@pytest.mark.asyncio
async def test_write_directory_target_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.write_file", arguments={"path": "subdir", "content": "evil"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require elevated privileges on Windows")
@pytest.mark.asyncio
async def test_write_symlink_target_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.write_file", arguments={"path": "link.txt", "content": "evil"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require elevated privileges on Windows")
@pytest.mark.asyncio
async def test_write_parent_symlink_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.write_file", arguments={"path": "parent_link/new.txt", "content": "evil"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require elevated privileges on Windows")
@pytest.mark.asyncio
async def test_write_nested_symlink_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.write_file", arguments={"path": "dir_link/nested.txt", "content": "evil"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_write_resolved_outside_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    outside = tmp_path / ".." / "outside_workspace"
    outside.mkdir(parents=True, exist_ok=True)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.write_file", arguments={"path": str(outside.relative_to(tmp_path)), "content": "evil"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


# ===========================================================================
# E. create_dirs
# ===========================================================================

@pytest.mark.asyncio
async def test_write_create_dirs_false_missing_parent_fails(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.write_file", arguments={"path": "missing/sub/file.txt", "content": "content", "create_dirs": False})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is False
        assert not (tmp_path / "missing" / "sub" / "file.txt").exists()

    await engine.dispose()


@pytest.mark.asyncio
async def test_write_create_dirs_true_missing_parent_succeeds(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.write_file", arguments={"path": "missing/sub/file.txt", "content": "content", "create_dirs": True})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert res.status == ToolExecutionStatus.SUCCESS
        assert (tmp_path / "missing" / "sub" / "file.txt").read_text(encoding="utf-8") == "content"

    await engine.dispose()


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require elevated privileges on Windows")
@pytest.mark.asyncio
async def test_write_create_dirs_true_unsafe_symlink_fails(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.write_file", arguments={"path": "parent_link/deep.txt", "content": "content", "create_dirs": True})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is False

    await engine.dispose()


@pytest.mark.asyncio
async def test_write_create_dirs_nested_directory_creation(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.write_file", arguments={"path": "a/b/c/d/file.txt", "content": "deep", "create_dirs": True})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert (tmp_path / "a" / "b" / "c" / "d" / "file.txt").read_text(encoding="utf-8") == "deep"

    await engine.dispose()


# ===========================================================================
# F. Atomicity
# ===========================================================================

@pytest.mark.asyncio
async def test_write_atomicity_preserves_target_on_replace_failure(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        original_content = (tmp_path / "hello.txt").read_text(encoding="utf-8")
        call = ToolCall(tool_id="files.write_file", arguments={"path": "hello.txt", "content": "new content"})

        original_replace = os.replace
        def failing_replace(src, dst):
            raise OSError("injected replace failure")

        with patch("os.replace", side_effect=failing_replace):
            res = await service.execute_tool_call(
                call,
                autonomy_mode=AutonomyMode.AUTONOMOUS,
                context={"workspace_path": str(tmp_path)},
            )

        assert res.success is False
        assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == original_content
        temp_files = list(tmp_path.glob(".ai_op_tmp_*"))
        assert temp_files == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_write_temp_file_cleanup_on_write_failure(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.write_file", arguments={"path": "new.txt", "content": "content"})

        original_fdopen = os.fdopen
        def failing_fdopen(fd, *args, **kwargs):
            raise OSError("injected fdopen failure")

        with patch("os.fdopen", side_effect=failing_fdopen):
            res = await service.execute_tool_call(
                call,
                autonomy_mode=AutonomyMode.AUTONOMOUS,
                context={"workspace_path": str(tmp_path)},
            )

        assert res.success is False
        temp_files = list(tmp_path.glob(".ai_op_tmp_*"))
        assert temp_files == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_write_no_partially_written_target_on_failure(tmp_path: Path):
    _init_test_workspace(tmp_path)
    original_content = (tmp_path / "hello.txt").read_text(encoding="utf-8")
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

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.write_file", arguments={"path": "hello.txt", "content": "partial"})

        with patch("os.replace", side_effect=OSError("injected replace failure")):
            res = await service.execute_tool_call(
                call,
                autonomy_mode=AutonomyMode.AUTONOMOUS,
                context={"workspace_path": str(tmp_path)},
            )

        assert res.success is False
        assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == original_content

    await engine.dispose()


# ===========================================================================
# G. Permissions / approval
# ===========================================================================

@pytest.mark.asyncio
async def test_write_deny_prevents_execution(tmp_path: Path):
    _init_test_workspace(tmp_path)
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesWriteFile()
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

    call = ToolCall(tool_id="files.write_file", arguments={"path": "hello.txt", "content": "evil"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.MANUAL,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False
    assert res.status == ToolExecutionStatus.CANCELLED
    assert res.error_code == "ask_user"
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "Hello, World!"


@pytest.mark.asyncio
async def test_write_ask_user_never_executes(tmp_path: Path):
    _init_test_workspace(tmp_path)
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesWriteFile()
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

    call = ToolCall(tool_id="files.write_file", arguments={"path": "hello.txt", "content": "evil"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False
    assert res.status == ToolExecutionStatus.CANCELLED
    assert res.error_code == "ask_user"
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "Hello, World!"


@pytest.mark.asyncio
async def test_write_allow_with_grant_executes(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.write_file", arguments={"path": "hello.txt", "content": "granted"})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert res.status == ToolExecutionStatus.SUCCESS
        assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "granted"

    await engine.dispose()


@pytest.mark.asyncio
async def test_write_requires_confirmation_in_autonomous(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.write_file", arguments={"path": "hello.txt", "content": "evil"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False
    assert res.status == ToolExecutionStatus.CANCELLED
    assert res.error_code == "ask_user"


@pytest.mark.asyncio
async def test_write_invalid_approval_context_prevents_execution(tmp_path: Path):
    _init_test_workspace(tmp_path)
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesWriteFile()
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

    call = ToolCall(tool_id="files.write_file", arguments={"path": "hello.txt", "content": "evil"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
        approval_context=FakeApprovalContext(),
    )
    assert res.success is False
    assert res.status == ToolExecutionStatus.DENIED


# ===========================================================================
# H. Context authority
# ===========================================================================

@pytest.mark.asyncio
async def test_write_workspace_path_required(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        call = ToolCall(tool_id="files.write_file", arguments={"path": "hello.txt", "content": "evil"})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={},
        )
        assert res.success is False
        assert res.status == ToolExecutionStatus.ERROR

    await engine.dispose()


# ===========================================================================
# I. Audit
# ===========================================================================

@pytest.mark.asyncio
async def test_write_audit_event_exists(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, audit_service = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.write_file", arguments={"path": "hello.txt", "content": "evil"})
    await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert len(audit_service.events) >= 1
    event_types = [e["event_type"] for e in audit_service.events]
    assert any("APPROVAL_REQUESTED" in str(et) or "TOOL_CALL_DENIED" in str(et) for et in event_types)


@pytest.mark.asyncio
async def test_write_audit_arguments_passed_to_service(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, audit_service = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.write_file", arguments={"path": "hello.txt", "content": "secret"})
    await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert len(audit_service.events) >= 1
    for event in audit_service.events:
        kwargs = event
        assert "arguments" in kwargs


@pytest.mark.asyncio
async def test_write_result_bounded(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.write_file", arguments={"path": "hello.txt", "content": "bounded"})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert "success" in res.result
        assert "path" in res.result
        assert "size" in res.result
        assert isinstance(res.result["size"], int)
        assert "/" not in res.result["path"] or "\\" not in res.result["path"]

    await engine.dispose()


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
async def test_tool_execution_service_boundary_intact(tmp_path: Path):
    _init_test_workspace(tmp_path)
    from app.tools.implementations.files_read import FilesReadFile
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    read_impl = FilesReadFile()
    registry.register(read_impl.definition)
    impl_registry.register(read_impl)
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
    impl = FilesWriteFile()
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
    call = ToolCall(tool_id="files.write_file", arguments={"path": "hello.txt", "content": "evil"})
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
async def test_write_windows_mixed_separators(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.write_file", arguments={"path": "subdir\\..\\outside.txt", "content": "evil"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
@pytest.mark.asyncio
async def test_write_windows_reserved_name_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.write_file", arguments={"path": "CON.txt", "content": "evil"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_global_argument_limit_still_blocks_other_tools(tmp_path: Path):
    _init_test_workspace(tmp_path)
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()

    class BigArgsTool(BaseTool):
        @property
        def tool_id(self) -> str:
            return "test.big_args"

        @property
        def definition(self) -> ToolDefinition:
            return ToolDefinition(
                tool_id="test.big_args",
                action="big_args",
                name="Big Args",
                description="Test tool without per-tool argument limit",
                version="1.0.0",
                category=ToolCategory.SYSTEM,
                risk_level=RiskLevel.SAFE,
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "data": {"type": "string"},
                    },
                    "required": ["data"],
                },
                result_schema={
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                },
            )

        async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext | None = None) -> dict[str, Any]:
            return {"ok": True}

    big_impl = BigArgsTool()
    registry.register(big_impl.definition)
    impl_registry.register(big_impl)

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

    oversized = "x" * (64 * 1024 + 1)
    call = ToolCall(tool_id="test.big_args", arguments={"data": oversized})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False
    assert res.status == ToolExecutionStatus.ERROR
    assert "exceeds" in (res.error_message or "").lower()
