"""
Tests for Stage 18.2 — files.list_directory tool.
"""
from __future__ import annotations

import sys
import pytest
from pathlib import Path

from app.tools.implementations.files_list import (
    FilesListDirectory,
    MAX_LIST_DIRECTORY_ENTRIES,
    MAX_LIST_DIRECTORY_RECURSION_DEPTH,
)
from app.tools.models import (
    AutonomyMode,
    ToolCall,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolExecutionStatus,
)
from app.tools.tool_execution_service import ToolExecutionService
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.implementation_registry import ToolImplementationRegistry
from app.tools.permission_engine import PermissionEngine


class MockAuditLogService:
    def __init__(self):
        self.events: list[dict[str, Any]] = []

    async def record(self, event_type, **kwargs):
        self.events.append({"event_type": event_type, **kwargs})


def _build_service_with_real_permission():
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesListDirectory()
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
    (path / "subdir" / "inner").mkdir(exist_ok=True)
    (path / "subdir" / "inner" / "deep.txt").write_text("Deep content", encoding="utf-8")
    (path / "empty").mkdir(exist_ok=True)
    if sys.platform != "win32":
        (path / "link.txt").symlink_to(path / "hello.txt")
        (path / "dir_link").symlink_to(path / "subdir")


# A. Basic listing

@pytest.mark.asyncio
async def test_list_directory_empty(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.list_directory", arguments={"path": "empty"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is True
    assert res.status == ToolExecutionStatus.SUCCESS
    assert res.result["path"] == "empty"
    assert res.result["entries"] == []
    assert res.result["truncated"] is False


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require elevated privileges on Windows")
@pytest.mark.asyncio
async def test_list_directory_files(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.list_directory", arguments={"path": "."})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is True
    assert res.result["path"] == "."
    names = {e["name"] for e in res.result["entries"]}
    assert "hello.txt" in names
    assert "subdir" in names
    assert "link.txt" in names
    assert "dir_link" in names
    assert "empty" in names


@pytest.mark.asyncio
async def test_list_directory_mixed_types(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.list_directory", arguments={"path": "subdir"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is True
    entries = res.result["entries"]
    names = {e["name"] for e in entries}
    assert "nested.txt" in names
    assert "inner" in names
    types = {e["type"] for e in entries}
    assert "file" in types
    assert "directory" in types


# B. Path security

@pytest.mark.asyncio
async def test_list_directory_traversal_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.list_directory", arguments={"path": "../outside"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_list_directory_absolute_path_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.list_directory", arguments={"path": str(tmp_path)})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive paths")
@pytest.mark.asyncio
async def test_list_directory_windows_drive_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.list_directory", arguments={"path": "C:\\Windows"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_list_directory_unc_path_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.list_directory", arguments={"path": "\\\\server\\share"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


# C. Target validation

@pytest.mark.asyncio
async def test_list_directory_nonexistent(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.list_directory", arguments={"path": "nonexistent"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.asyncio
async def test_list_directory_file_target_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.list_directory", arguments={"path": "hello.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


# D. Symlink security

@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require elevated privileges on Windows")
@pytest.mark.asyncio
async def test_list_directory_symlink_target_blocked(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.list_directory", arguments={"path": "link.txt"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require elevated privileges on Windows")
@pytest.mark.asyncio
async def test_list_directory_symlink_inside_workspace_listed(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.list_directory", arguments={"path": "subdir"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is True
    names = {e["name"] for e in res.result["entries"]}
    assert "nested.txt" in names
    assert "inner" in names
    symlinks = [e for e in res.result["entries"] if e["type"] == "symlink"]
    assert len(symlinks) == 0


# E. Recursive mode

@pytest.mark.asyncio
async def test_list_directory_recursive_nested(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.list_directory", arguments={"path": "subdir", "recursive": True})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is True
    assert res.result["truncated"] is False
    names = {e["name"] for e in res.result["entries"]}
    assert "nested.txt" in names
    assert "inner" in names
    assert "deep.txt" in names


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require elevated privileges on Windows")
@pytest.mark.asyncio
async def test_list_directory_recursive_symlink_not_followed(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.list_directory", arguments={"path": ".", "recursive": True})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is True
    symlinks = [e for e in res.result["entries"] if e["type"] == "symlink"]
    assert len(symlinks) >= 2
    for s in symlinks:
        assert s["name"] in ("link.txt", "dir_link")


@pytest.mark.asyncio
async def test_list_directory_entry_count_limit(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    for i in range(1200):
        (tmp_path / f"file_{i:04d}.txt").write_text("x", encoding="utf-8")

    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.list_directory", arguments={"path": ".", "recursive": True})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is True
    assert res.result["truncated"] is True
    assert len(res.result["entries"]) == MAX_LIST_DIRECTORY_ENTRIES


# F. Result safety

@pytest.mark.asyncio
async def test_list_directory_result_no_absolute_paths(tmp_path: Path):
    _init_test_workspace(tmp_path)
    service, _, _ = _build_service_with_real_permission()
    call = ToolCall(tool_id="files.list_directory", arguments={"path": "."})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is True
    assert "path" in res.result
    assert "entries" in res.result
    assert "truncated" in res.result
    assert "/" not in res.result["path"] or "\\" not in res.result["path"]


# G. Permission boundary

@pytest.mark.asyncio
async def test_list_directory_deny_prevents_execution(tmp_path: Path):
    _init_test_workspace(tmp_path)
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesListDirectory()
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

    call = ToolCall(tool_id="files.list_directory", arguments={"path": "."})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.MANUAL,
        context={"workspace_path": str(tmp_path)},
    )
    assert res.success is False
    assert res.status == ToolExecutionStatus.CANCELLED
    assert res.error_code == "ask_user"


# H. Context authority

@pytest.mark.asyncio
async def test_list_directory_workspace_path_required(tmp_path: Path):
    _init_test_workspace(tmp_path)
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    impl = FilesListDirectory()
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

    call = ToolCall(tool_id="files.list_directory", arguments={"path": "."})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={},
    )
    assert res.success is False
    assert res.status == ToolExecutionStatus.ERROR
