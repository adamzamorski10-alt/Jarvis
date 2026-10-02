"""
Tests for Stage 17.3A — files.read_file tool.
"""
from __future__ import annotations

import sys
import pytest
from pathlib import Path

from app.tools.implementations.files_read import FilesReadFile, MAX_READ_BYTES
from app.tools.models import (
    AutonomyMode,
    PermissionDecisionType,
    ToolCall,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolExecutionStatus,
    PermissionDecision,
    RiskLevel,
)
from app.tools.tool_execution_service import ToolExecutionService
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.implementation_registry import ToolImplementationRegistry
from app.tools.permission_engine import PermissionEngine
from app.tools.audit_service import AuditLogService


class MockAuditLogService:
    def __init__(self):
        self.events: list[dict[str, Any]] = []

    async def record(self, event_type, **kwargs):
        self.events.append({"event_type": event_type, **kwargs})


def _build_service_with_real_permission(permission_decision=PermissionDecisionType.ALLOW):
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesReadFile()
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


def _init_test_workspace(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "hello.txt").write_text("Hello, World!", encoding="utf-8")
    (path / "subdir").mkdir(exist_ok=True)
    (path / "subdir" / "nested.txt").write_text("Nested content", encoding="utf-8")
    (path / "large.txt").write_text("x" * 5000, encoding="utf-8")
    (path / "zero.txt").write_text("", encoding="utf-8")
    (path / "utf8.txt").write_text("Héllo Wörld 😀", encoding="utf-8")
    (path / "invalid_utf8.bin").write_bytes(b"\xff\xfe\x00\x00")


@pytest.mark.asyncio
async def test_files_read_file_valid(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.read_file", arguments={"path": "hello.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is True
    assert res.status == ToolExecutionStatus.SUCCESS
    assert res.result["content"] == "Hello, World!"
    assert res.result["size"] == 13
    assert res.result["encoding"] == "utf-8"
    assert res.result["truncated"] is False


@pytest.mark.asyncio
async def test_files_read_file_nested(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.read_file", arguments={"path": "subdir/nested.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is True
    assert res.result["content"] == "Nested content"


@pytest.mark.asyncio
async def test_files_read_file_traversal_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.read_file", arguments={"path": "../secret.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_files_read_file_absolute_path_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.read_file", arguments={"path": str(tmp_path / "hello.txt")})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_files_read_file_directory_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.read_file", arguments={"path": "subdir"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require elevated privileges on Windows")
@pytest.mark.asyncio
async def test_files_read_file_symlink_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    (tmp_path / "link.txt").symlink_to(tmp_path / "hello.txt")
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.read_file", arguments={"path": "link.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_files_read_file_max_bytes(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.read_file", arguments={"path": "large.txt", "max_bytes": 100})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is True
    assert len(res.result["content"]) == 100
    assert res.result["truncated"] is True


@pytest.mark.asyncio
async def test_files_read_file_missing_file(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.read_file", arguments={"path": "nonexistent.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_files_read_file_zero_byte_file(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.read_file", arguments={"path": "zero.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is True
    assert res.result["content"] == ""
    assert res.result["size"] == 0
    assert res.result["truncated"] is False


@pytest.mark.asyncio
async def test_files_read_file_valid_utf8(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.read_file", arguments={"path": "utf8.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is True
    assert res.result["content"] == "Héllo Wörld 😀"
    assert res.result["encoding"] == "utf-8"


@pytest.mark.asyncio
async def test_files_read_file_invalid_utf8_replacement(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.read_file", arguments={"path": "invalid_utf8.bin"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is True
    assert res.result["encoding"] == "utf-8"
    assert "\ufffd" in res.result["content"]


@pytest.mark.asyncio
async def test_files_read_file_deny_prevents_execution(tmp_path: Path):
    _init_test_workspace(tmp_path)
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesReadFile()
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

    call = ToolCall(tool_id="files.read_file", arguments={"path": "hello.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.MANUAL,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False
    assert res.status == ToolExecutionStatus.CANCELLED
    assert res.error_code == "ask_user"


@pytest.mark.asyncio
async def test_files_read_file_approval_cannot_upgrade_deny(tmp_path: Path):
    _init_test_workspace(tmp_path)
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesReadFile()
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

    call = ToolCall(tool_id="files.read_file", arguments={"path": "hello.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
        approval_context=FakeApprovalContext(),
    )
    assert res.success is False
    assert res.status == ToolExecutionStatus.DENIED


@pytest.mark.asyncio
async def test_files_read_file_ask_user_never_executes(tmp_path: Path):
    _init_test_workspace(tmp_path)
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesReadFile()
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

    call = ToolCall(tool_id="files.read_file", arguments={"path": "hello.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.MANUAL,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False
    assert res.status == ToolExecutionStatus.CANCELLED
    assert res.error_code == "ask_user"


@pytest.mark.asyncio
async def test_files_read_file_workspace_path_required(tmp_path: Path):
    _init_test_workspace(tmp_path)
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesReadFile()
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

    call = ToolCall(tool_id="files.read_file", arguments={"path": "hello.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={},
    )
    assert res.success is False
    assert res.status == ToolExecutionStatus.ERROR


@pytest.mark.asyncio
async def test_files_read_file_no_filesystem_mutation(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    original_mtime = (tmp_path / "hello.txt").stat().st_mtime
    call = ToolCall(tool_id="files.read_file", arguments={"path": "hello.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is True
    assert (tmp_path / "hello.txt").stat().st_mtime == original_mtime
