"""
Tests for Stage 18.4 — files.delete_file tool.
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
from app.tools.implementations.files_delete import FilesDeleteFile
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
    impl = FilesDeleteFile()
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
    impl = FilesDeleteFile()
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
# A. Basic behavior
# ===========================================================================

@pytest.mark.asyncio
async def test_delete_existing_file(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.delete_file", arguments={"path": "hello.txt"})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert res.status == ToolExecutionStatus.SUCCESS
        assert res.result["path"] == "hello.txt"
        assert res.result["deleted"] is True
        assert not (tmp_path / "hello.txt").exists()

    await engine.dispose()


@pytest.mark.asyncio
async def test_delete_nested_file(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.delete_file", arguments={"path": "subdir/nested.txt"})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert res.status == ToolExecutionStatus.SUCCESS
        assert res.result["path"] == "subdir/nested.txt"
        assert res.result["deleted"] is True
        assert not (tmp_path / "subdir" / "nested.txt").exists()

    await engine.dispose()


@pytest.mark.asyncio
async def test_delete_returns_correct_result(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.delete_file", arguments={"path": "hello.txt"})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert res.result["success"] is True
        assert res.result["deleted"] is True
        assert isinstance(res.result["path"], str)
        assert res.result["path"] == "hello.txt"

    await engine.dispose()


# ===========================================================================
# B. Missing / invalid targets
# ===========================================================================

@pytest.mark.asyncio
async def test_delete_missing_file_fails(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.delete_file", arguments={"path": "nonexistent.txt"})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is False
        assert res.status == ToolExecutionStatus.ERROR
        assert "not found" in (res.error_message or "").lower()

    await engine.dispose()


@pytest.mark.asyncio
async def test_delete_directory_target_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.delete_file", arguments={"path": "subdir"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False
    assert (tmp_path / "subdir").exists()


@pytest.mark.asyncio
async def test_delete_workspace_root_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.delete_file", arguments={"path": "."})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_delete_invalid_path_empty_fails(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.delete_file", arguments={"path": ""})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


# ===========================================================================
# C. Traversal
# ===========================================================================

@pytest.mark.asyncio
async def test_delete_traversal_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.delete_file", arguments={"path": "../outside.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_delete_traversal_backslash_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.delete_file", arguments={"path": "..\\outside.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_delete_mixed_separator_escape_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.delete_file", arguments={"path": "subdir\\..\\outside.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_delete_resolved_outside_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    outside = tmp_path / ".." / "outside_workspace"
    outside.mkdir(parents=True, exist_ok=True)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.delete_file", arguments={"path": str(outside.relative_to(tmp_path)), "content": "evil"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


# ===========================================================================
# D. Absolute / platform paths
# ===========================================================================

@pytest.mark.asyncio
async def test_delete_absolute_path_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.delete_file", arguments={"path": str(tmp_path / "hello.txt")})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive paths")
@pytest.mark.asyncio
async def test_delete_windows_drive_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.delete_file", arguments={"path": "C:\\Windows\\evil.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_delete_unc_path_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.delete_file", arguments={"path": "\\\\server\\share\\evil.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


# ===========================================================================
# E. Symlink security
# ===========================================================================

@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require elevated privileges on Windows")
@pytest.mark.asyncio
async def test_delete_symlink_target_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.delete_file", arguments={"path": "link.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False
    assert (tmp_path / "link.txt").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require elevated privileges on Windows")
@pytest.mark.asyncio
async def test_delete_parent_symlink_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.delete_file", arguments={"path": "parent_link/nested.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require elevated privileges on Windows")
@pytest.mark.asyncio
async def test_delete_nested_symlink_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.delete_file", arguments={"path": "dir_link/nested.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require elevated privileges on Windows")
@pytest.mark.asyncio
async def test_delete_resolved_outside_symlink_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    outside = tmp_path / ".." / "outside_workspace"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "real.txt").write_text("outside", encoding="utf-8")
    (tmp_path / "escape_link").symlink_to(outside / "real.txt")
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.delete_file", arguments={"path": "escape_link"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False
    assert (outside / "real.txt").exists()


# ===========================================================================
# F. Permission boundary
# ===========================================================================

@pytest.mark.asyncio
async def test_delete_deny_prevents_execution(tmp_path: Path):
    _init_test_workspace(tmp_path)
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesDeleteFile()
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

    call = ToolCall(tool_id="files.delete_file", arguments={"path": "hello.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.MANUAL,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False
    assert res.status == ToolExecutionStatus.CANCELLED
    assert res.error_code == "ask_user"
    assert (tmp_path / "hello.txt").exists()


@pytest.mark.asyncio
async def test_delete_ask_user_never_executes(tmp_path: Path):
    _init_test_workspace(tmp_path)
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesDeleteFile()
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

    call = ToolCall(tool_id="files.delete_file", arguments={"path": "hello.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False
    assert res.status == ToolExecutionStatus.CANCELLED
    assert res.error_code == "ask_user"
    assert (tmp_path / "hello.txt").exists()


@pytest.mark.asyncio
async def test_delete_allow_with_grant_executes(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.delete_file", arguments={"path": "hello.txt"})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert res.status == ToolExecutionStatus.SUCCESS
        assert not (tmp_path / "hello.txt").exists()

    await engine.dispose()


@pytest.mark.asyncio
async def test_delete_requires_confirmation_in_autonomous(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.delete_file", arguments={"path": "hello.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False
    assert res.status == ToolExecutionStatus.CANCELLED
    assert res.error_code == "ask_user"


@pytest.mark.asyncio
async def test_delete_invalid_approval_context_prevents_execution(tmp_path: Path):
    _init_test_workspace(tmp_path)
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesDeleteFile()
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

    call = ToolCall(tool_id="files.delete_file", arguments={"path": "hello.txt"})
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
async def test_delete_workspace_path_required(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        call = ToolCall(tool_id="files.delete_file", arguments={"path": "hello.txt"})
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
async def test_delete_audit_event_exists(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, audit_service = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.delete_file", arguments={"path": "hello.txt"})
    await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert len(audit_service.events) >= 1
    event_types = [e["event_type"] for e in audit_service.events]
    assert any("APPROVAL_REQUESTED" in str(et) or "TOOL_CALL_DENIED" in str(et) for et in event_types)


@pytest.mark.asyncio
async def test_delete_result_bounded(tmp_path: Path):
    _init_test_workspace(tmp_path)
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

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.delete_file", arguments={"path": "hello.txt"})
        res = await service.execute_tool_call(
            call,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            context={"workspace_path": str(tmp_path)},
        )
        assert res.success is True
        assert "success" in res.result
        assert "path" in res.result
        assert "deleted" in res.result
        assert isinstance(res.result["deleted"], bool)

    await engine.dispose()


# ===========================================================================
# I. Failure behavior
# ===========================================================================

@pytest.mark.asyncio
async def test_delete_atomicity_preserves_target_on_failure(tmp_path: Path):
    _init_test_workspace(tmp_path)
    original_content = (tmp_path / "hello.txt").read_text(encoding="utf-8")
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

        service, _, _ = _build_service_with_grant(grant_repo)
        call = ToolCall(tool_id="files.delete_file", arguments={"path": "hello.txt"})

        original_unlink = Path.unlink
        def failing_unlink(self, *args, **kwargs):
            raise OSError("injected unlink failure")

        with patch.object(Path, "unlink", failing_unlink):
            res = await service.execute_tool_call(
                call,
                autonomy_mode=AutonomyMode.AUTONOMOUS,
                context={"workspace_path": str(tmp_path)},
            )

        assert res.success is False
        assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == original_content

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
async def test_tool_execution_service_boundary_intact(tmp_path: Path):
    _init_test_workspace(tmp_path)
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesDeleteFile()
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
    call = ToolCall(tool_id="files.delete_file", arguments={"path": "hello.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.MANUAL,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.status == ToolExecutionStatus.CANCELLED
    assert res.error_code == "ask_user"
    assert (tmp_path / "hello.txt").exists()


@pytest.mark.asyncio
async def test_permission_engine_boundary_intact(tmp_path: Path):
    _init_test_workspace(tmp_path)
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesDeleteFile()
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
    call = ToolCall(tool_id="files.delete_file", arguments={"path": "hello.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.MANUAL,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.status == ToolExecutionStatus.CANCELLED
    assert res.error_code == "ask_user"
    assert (tmp_path / "hello.txt").exists()


# ===========================================================================
# Windows-specific tests
# ===========================================================================

@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
@pytest.mark.asyncio
async def test_delete_windows_mixed_separators(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.delete_file", arguments={"path": "subdir\\..\\outside.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
@pytest.mark.asyncio
async def test_delete_windows_reserved_name_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.delete_file", arguments={"path": "CON.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False
