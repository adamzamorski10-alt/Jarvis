"""
Stage 8.7 — DevelopmentAgent Foundation Tests.

Tests cover:
  - WorkspaceWriter: containment, reads, writes, edits, changed_files, limits
  - BaseDevelopmentAgent: coding loop, cancellation, abort, finish, limits, errors
  - FakeCodingProvider: deterministic provider for agent tests
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from app.ai.providers.coding_base import (
    CodingConstraints,
    CodingOperation,
    CodingOperationType,
    CodingProvider,
    CodingStepResult,
)
from app.orchestration.agents.base_agent import BaseDevelopmentAgent
from app.orchestration.agents.workspace_writer import WorkspaceWriter
from app.orchestration.models import (
    DevelopmentAgentContext,
    DevelopmentResult,
    DevelopmentResultStatus,
)


# ── WorkspaceWriter tests ─────────────────────────────────────────────────────


class TestWorkspaceWriter:
    async def test_write_file_inside_workspace(self, tmp_path: Path):
        writer = WorkspaceWriter(str(tmp_path))
        await writer.write_file("src/foo.py", "print('hello')")
        assert (tmp_path / "src" / "foo.py").read_text(encoding="utf-8") == "print('hello')"
        assert writer.take_changed_files() == ("src/foo.py",)

    async def test_read_file_inside_workspace(self, tmp_path: Path):
        target = tmp_path / "README.md"
        target.write_text("# hello", encoding="utf-8")
        writer = WorkspaceWriter(str(tmp_path))
        assert await writer.read_file("README.md") == "# hello"

    async def test_exact_edit(self, tmp_path: Path):
        target = tmp_path / "app.py"
        target.write_text("def foo(): pass\n", encoding="utf-8")
        writer = WorkspaceWriter(str(tmp_path))
        await writer.edit_file("app.py", "def foo(): pass", "def foo(): return 1")
        assert target.read_text(encoding="utf-8") == "def foo(): return 1\n"
        assert writer.take_changed_files() == ("app.py",)

    async def test_missing_edit_target_raises(self, tmp_path: Path):
        target = tmp_path / "app.py"
        target.write_text("def foo(): pass\n", encoding="utf-8")
        writer = WorkspaceWriter(str(tmp_path))
        with pytest.raises(ValueError, match="old_string not found"):
            await writer.edit_file("app.py", "nonexistent", "replacement")

    async def test_absolute_path_rejection(self, tmp_path: Path):
        writer = WorkspaceWriter(str(tmp_path))
        with pytest.raises(ValueError):
            await writer.write_file("/etc/passwd", "evil")

    async def test_traversal_rejection(self, tmp_path: Path):
        writer = WorkspaceWriter(str(tmp_path))
        with pytest.raises(ValueError, match="Path traversal not allowed"):
            await writer.write_file("../../etc/passwd", "evil")

    async def test_symlink_escape_rejection(self, tmp_path: Path):
        if os.name == "nt":
            pytest.skip("Symlinks require elevated privileges on Windows")
        outside = tmp_path / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        link = tmp_path / "link_to_outside"
        link.symlink_to(outside)
        writer = WorkspaceWriter(str(tmp_path))
        with pytest.raises(ValueError, match="escapes workspace"):
            await writer.write_file("link_to_outside", "hacked")

    async def test_duplicate_changed_files_deduped(self, tmp_path: Path):
        writer = WorkspaceWriter(str(tmp_path))
        await writer.write_file("a.py", "v1")
        await writer.write_file("a.py", "v2")
        assert writer.take_changed_files() == ("a.py",)

    async def test_content_size_limit(self, tmp_path: Path):
        writer = WorkspaceWriter(str(tmp_path))
        big = "x" * 1025  # > 1024 min
        with pytest.raises(ValueError, match="exceeds max_content_bytes"):
            await writer.write_file("big.py", big, max_content_bytes=1024)

    async def test_failed_write_not_in_changed_files(self, tmp_path: Path):
        writer = WorkspaceWriter(str(tmp_path))
        with pytest.raises(ValueError):
            await writer.write_file("/etc/passwd", "evil")
        assert writer.take_changed_files() == ()

    async def test_read_nonexistent_file_raises(self, tmp_path: Path):
        writer = WorkspaceWriter(str(tmp_path))
        with pytest.raises(ValueError, match="File not found"):
            await writer.read_file("nonexistent.txt")


# ── Fake CodingProvider for agent tests ───────────────────────────────────────


class FakeCodingProvider(CodingProvider):
    """Deterministic fake CodingProvider for testing."""

    def __init__(self, steps: list[CodingStepResult] | None = None) -> None:
        self._steps = steps or []
        self.calls: list[dict] = []
        self.name = "fake-coding-provider"

    async def plan_next_step(
        self,
        model: str,
        task_description: str,
        workspace_snapshot: dict[str, str],
        operation_history: tuple[CodingOperation, ...],
        constraints: CodingConstraints | None = None,
    ) -> CodingStepResult:
        self.calls.append({
            "model": model,
            "snapshot_keys": list(workspace_snapshot.keys()),
            "history_len": len(operation_history),
        })
        if not self._steps:
            return CodingStepResult(
                operations=(CodingOperation(type=CodingOperationType.FINISH, path=""),),
                finished=True,
            )
        return self._steps.pop(0)


# ── BaseDevelopmentAgent tests ────────────────────────────────────────────────


class TestBaseDevelopmentAgent:
    async def test_finish_with_no_writes(self, tmp_path: Path):
        provider = FakeCodingProvider()
        agent = BaseDevelopmentAgent(provider=provider, model="test-model")
        ctx = DevelopmentAgentContext(
            task_id="task_1",
            project_id="proj",
            workspace_path=str(tmp_path),
            repository_root=str(tmp_path),
            title="Test",
            description="do nothing",
            attempt_count=0,
            expected_base_commit="abc123",
        )
        result = await agent.execute(ctx)
        assert result.status == DevelopmentResultStatus.SUCCESS
        assert result.changed_files == ()
        assert provider.calls[0]["history_len"] == 0

    async def test_multi_turn_read_write_edit_finish(self, tmp_path: Path):
        target = tmp_path / "app.py"
        target.write_text("x = 1\n", encoding="utf-8")

        steps = [
            CodingStepResult(
                operations=(CodingOperation(type=CodingOperationType.READ, path="app.py"),),
                finished=False,
            ),
            CodingStepResult(
                operations=(
                    CodingOperation(type=CodingOperationType.WRITE, path="app.py", content="x = 2\n"),
                ),
                finished=False,
            ),
            CodingStepResult(
                operations=(
                    CodingOperation(type=CodingOperationType.EDIT, path="app.py", old_string="x = 2", new_string="x = 3"),
                ),
                finished=False,
            ),
            CodingStepResult(
                operations=(CodingOperation(type=CodingOperationType.FINISH, path=""),),
                finished=True,
                thought="done",
            ),
        ]
        provider = FakeCodingProvider(steps=steps)
        (tmp_path / "outside.py").write_text("outside", encoding="utf-8")
        (tmp_path / "link").symlink_to(tmp_path / "outside.py")
        agent = BaseDevelopmentAgent(provider=provider, model="test-model")
        ctx = DevelopmentAgentContext(
            task_id="task_2",
            project_id="proj",
            workspace_path=str(tmp_path),
            repository_root=str(tmp_path),
            title="Test",
            description="multi-turn",
            attempt_count=0,
            expected_base_commit="abc123",
        )
        result = await agent.execute(ctx)
        assert result.status == DevelopmentResultStatus.SUCCESS
        assert target.read_text(encoding="utf-8") == "x = 3\n"
        assert result.changed_files == ("app.py",)
        assert result.summary == "done"
        assert len(provider.calls) == 4

    async def test_empty_operations_failure(self, tmp_path: Path):
        steps = [
            CodingStepResult(operations=(), finished=False),
        ]
        provider = FakeCodingProvider(steps=steps)
        agent = BaseDevelopmentAgent(provider=provider, model="test-model")
        ctx = DevelopmentAgentContext(
            task_id="task_3",
            project_id="proj",
            workspace_path=str(tmp_path),
            repository_root=str(tmp_path),
            title="Test",
            description="empty",
            attempt_count=0,
            expected_base_commit="abc123",
        )
        result = await agent.execute(ctx)
        assert result.status == DevelopmentResultStatus.FAILURE
        assert "empty operations" in result.error_message

    async def test_provider_exception_error(self, tmp_path: Path):
        class ExplodingProvider(CodingProvider):
            name = "exploding"

            async def plan_next_step(self, *args, **kwargs):
                raise RuntimeError("provider boom")

        agent = BaseDevelopmentAgent(provider=ExplodingProvider(), model="test-model")
        ctx = DevelopmentAgentContext(
            task_id="task_4",
            project_id="proj",
            workspace_path=str(tmp_path),
            repository_root=str(tmp_path),
            title="Test",
            description="explode",
            attempt_count=0,
            expected_base_commit="abc123",
        )
        result = await agent.execute(ctx)
        assert result.status == DevelopmentResultStatus.ERROR
        assert "provider boom" in result.error_message

    async def test_cancellation_between_turns(self, tmp_path: Path):
        steps = [
            CodingStepResult(
                operations=(CodingOperation(type=CodingOperationType.FINISH, path=""),),
                finished=True,
            ),
        ]
        provider = FakeCodingProvider(steps=steps)
        agent = BaseDevelopmentAgent(provider=provider, model="test-model")
        cancel_event = asyncio.Event()
        cancel_event.set()
        ctx = DevelopmentAgentContext(
            task_id="task_5",
            project_id="proj",
            workspace_path=str(tmp_path),
            repository_root=str(tmp_path),
            title="Test",
            description="cancel",
            attempt_count=0,
            expected_base_commit="abc123",
            cancel_event=cancel_event,
        )
        result = await agent.execute(ctx)
        assert result.status == DevelopmentResultStatus.CANCELLED
        assert provider.calls == []  # no provider call before cancel check

    async def test_abort_between_turns(self, tmp_path: Path):
        steps = [
            CodingStepResult(
                operations=(CodingOperation(type=CodingOperationType.FINISH, path=""),),
                finished=True,
            ),
        ]
        provider = FakeCodingProvider(steps=steps)
        agent = BaseDevelopmentAgent(provider=provider, model="test-model")
        abort_event = asyncio.Event()
        abort_event.set()
        ctx = DevelopmentAgentContext(
            task_id="task_6",
            project_id="proj",
            workspace_path=str(tmp_path),
            repository_root=str(tmp_path),
            title="Test",
            description="abort",
            attempt_count=0,
            expected_base_commit="abc123",
            abort_event=abort_event,
        )
        result = await agent.execute(ctx)
        assert result.status == DevelopmentResultStatus.ERROR
        assert "Aborted" in result.error_message
        assert provider.calls == []

    async def test_operation_limit_exceeded(self, tmp_path: Path):
        ops = tuple(
            CodingOperation(type=CodingOperationType.WRITE, path=f"f{i}.py", content="x")
            for i in range(5)
        )
        steps = [
            CodingStepResult(operations=ops, finished=False),
        ]
        provider = FakeCodingProvider(steps=steps)
        constraints = CodingConstraints(max_operations=3)
        agent = BaseDevelopmentAgent(provider=provider, model="test-model", constraints=constraints)
        ctx = DevelopmentAgentContext(
            task_id="task_7",
            project_id="proj",
            workspace_path=str(tmp_path),
            repository_root=str(tmp_path),
            title="Test",
            description="limit",
            attempt_count=0,
            expected_base_commit="abc123",
        )
        result = await agent.execute(ctx)
        assert result.status == DevelopmentResultStatus.FAILURE
        assert "Operation limit" in result.error_message

    async def test_write_limit_exceeded(self, tmp_path: Path):
        ops = tuple(
            CodingOperation(type=CodingOperationType.WRITE, path=f"f{i}.py", content="x")
            for i in range(3)
        )
        steps = [
            CodingStepResult(operations=ops, finished=False),
        ]
        provider = FakeCodingProvider(steps=steps)
        constraints = CodingConstraints(max_total_writes=2)
        agent = BaseDevelopmentAgent(provider=provider, model="test-model", constraints=constraints)
        ctx = DevelopmentAgentContext(
            task_id="task_8",
            project_id="proj",
            workspace_path=str(tmp_path),
            repository_root=str(tmp_path),
            title="Test",
            description="write limit",
            attempt_count=0,
            expected_base_commit="abc123",
        )
        result = await agent.execute(ctx)
        assert result.status == DevelopmentResultStatus.FAILURE
        assert "Write limit" in result.error_message
        # Only 2 files should exist
        assert (tmp_path / "f0.py").exists()
        assert (tmp_path / "f1.py").exists()
        assert not (tmp_path / "f2.py").exists()

    async def test_failed_operation_not_in_changed_files(self, tmp_path: Path):
        steps = [
            CodingStepResult(
                operations=(
                    CodingOperation(type=CodingOperationType.WRITE, path="good.py", content="ok"),
                    CodingOperation(type=CodingOperationType.WRITE, path="link/bad.py", content="evil"),
                ),
                finished=False,
            ),
            CodingStepResult(
                operations=(CodingOperation(type=CodingOperationType.FINISH, path=""),),
                finished=True,
            ),
        ]
        provider = FakeCodingProvider(steps=steps)
        outside = tmp_path / "outside.py"
        outside.write_text("outside", encoding="utf-8")
        (tmp_path / "link").symlink_to(outside)
        agent = BaseDevelopmentAgent(provider=provider, model="test-model")
        ctx = DevelopmentAgentContext(
            task_id="task_9",
            project_id="proj",
            workspace_path=str(tmp_path),
            repository_root=str(tmp_path),
            title="Test",
            description="partial",
            attempt_count=0,
            expected_base_commit="abc123",
        )
        result = await agent.execute(ctx)
        assert result.status == DevelopmentResultStatus.FAILURE
        assert result.changed_files == ("good.py",)
        assert (tmp_path / "good.py").exists()

    async def test_changed_files_deduplicated(self, tmp_path: Path):
        steps = [
            CodingStepResult(
                operations=(
                    CodingOperation(type=CodingOperationType.WRITE, path="a.py", content="v1"),
                    CodingOperation(type=CodingOperationType.WRITE, path="a.py", content="v2"),
                ),
                finished=False,
            ),
            CodingStepResult(
                operations=(CodingOperation(type=CodingOperationType.FINISH, path=""),),
                finished=True,
            ),
        ]
        provider = FakeCodingProvider(steps=steps)
        agent = BaseDevelopmentAgent(provider=provider, model="test-model")
        ctx = DevelopmentAgentContext(
            task_id="task_10",
            project_id="proj",
            workspace_path=str(tmp_path),
            repository_root=str(tmp_path),
            title="Test",
            description="dedup",
            attempt_count=0,
            expected_base_commit="abc123",
        )
        result = await agent.execute(ctx)
        assert result.status == DevelopmentResultStatus.SUCCESS
        assert result.changed_files == ("a.py",)

    async def test_content_size_limit_enforced(self, tmp_path: Path):
        big = "x" * 1025
        steps = [
            CodingStepResult(
                operations=(CodingOperation(type=CodingOperationType.WRITE, path="big.py", content=big),),
                finished=False,
            ),
            CodingStepResult(
                operations=(CodingOperation(type=CodingOperationType.FINISH, path=""),),
                finished=True,
            ),
        ]
        provider = FakeCodingProvider(steps=steps)
        constraints = CodingConstraints(max_content_bytes=1024)
        agent = BaseDevelopmentAgent(provider=provider, model="test-model", constraints=constraints)
        ctx = DevelopmentAgentContext(
            task_id="task_11",
            project_id="proj",
            workspace_path=str(tmp_path),
            repository_root=str(tmp_path),
            title="Test",
            description="size",
            attempt_count=0,
            expected_base_commit="abc123",
        )
        result = await agent.execute(ctx)
        assert result.status == DevelopmentResultStatus.FAILURE
        assert "exceeds max_content_bytes" in result.error_message

    async def test_drive_relative_path_rejection(self, tmp_path: Path):
        writer = WorkspaceWriter(str(tmp_path))
        with pytest.raises(ValueError, match="drive specifiers not allowed"):
            await writer.write_file("C:foo.txt", "bar")
        with pytest.raises(ValueError, match="drive specifiers not allowed"):
            CodingOperation(type=CodingOperationType.WRITE, path="C:foo.txt", content="bar")

    async def test_oversized_payload_rejection(self):
        big_content = "x" * (1_048_576 + 1)
        with pytest.raises(ValueError):
            CodingOperation(type=CodingOperationType.WRITE, path="a.py", content=big_content)

        big_edit = "x" * (65_536 + 1)
        with pytest.raises(ValueError):
            CodingOperation(type=CodingOperationType.EDIT, path="a.py", old_string=big_edit, new_string="y")
        with pytest.raises(ValueError):
            CodingOperation(type=CodingOperationType.EDIT, path="a.py", old_string="x", new_string=big_edit)

    async def test_edit_unread_file_updates_snapshot(self, tmp_path: Path):
        target = tmp_path / "app.py"
        target.write_text("old = 1\n", encoding="utf-8")

        steps = [
            CodingStepResult(
                operations=(
                    CodingOperation(type=CodingOperationType.EDIT, path="app.py", old_string="old = 1", new_string="new = 2"),
                ),
                finished=False,
            ),
            CodingStepResult(
                operations=(CodingOperation(type=CodingOperationType.FINISH, path=""),),
                finished=True,
            ),
        ]
        provider = FakeCodingProvider(steps=steps)
        agent = BaseDevelopmentAgent(provider=provider, model="test-model")
        ctx = DevelopmentAgentContext(
            task_id="t_edit_snapshot",
            project_id="p",
            workspace_path=str(tmp_path),
            repository_root=str(tmp_path),
            title="Edit Snapshot Test",
            description="edit test",
            attempt_count=0,
            expected_base_commit="sha",
        )
        res = await agent.execute(ctx)
        assert res.status == DevelopmentResultStatus.SUCCESS
        assert len(provider.calls) == 2
        assert "app.py" in provider.calls[1]["snapshot_keys"]

    async def test_single_turn_operation_overflow(self, tmp_path: Path):
        ops = tuple(
            CodingOperation(type=CodingOperationType.WRITE, path=f"f{i}.py", content="x")
            for i in range(5)
        )
        steps = [
            CodingStepResult(operations=ops, finished=False),
        ]
        provider = FakeCodingProvider(steps=steps)
        constraints = CodingConstraints(max_operations=3)
        agent = BaseDevelopmentAgent(provider=provider, model="test-model", constraints=constraints)
        ctx = DevelopmentAgentContext(
            task_id="t_overflow",
            project_id="p",
            workspace_path=str(tmp_path),
            repository_root=str(tmp_path),
            title="Overflow",
            description="single turn overflow",
            attempt_count=0,
            expected_base_commit="sha",
        )
        res = await agent.execute(ctx)
        assert res.status == DevelopmentResultStatus.FAILURE
        assert "Operation limit of 3 exceeded" in res.error_message
        assert len(res.changed_files) == 3

    async def test_read_size_limits(self, tmp_path: Path):
        writer = WorkspaceWriter(str(tmp_path))
        f_small = tmp_path / "small.txt"
        f_small.write_text("hello", encoding="utf-8")
        assert await writer.read_file("small.txt", max_content_bytes=10) == "hello"

        f_big = tmp_path / "big.txt"
        f_big.write_text("12345678901", encoding="utf-8")
        with pytest.raises(ValueError, match="exceeds max_content_bytes"):
            await writer.read_file("big.txt", max_content_bytes=10)

    async def test_allowed_extensions_enforcement(self, tmp_path: Path):
        writer = WorkspaceWriter(str(tmp_path), allowed_extensions=(".py", ".md"))
        await writer.write_file("app.py", "x = 1")
        assert writer.take_changed_files() == ("app.py",)

        with pytest.raises(ValueError, match="not allowed by constraints"):
            await writer.write_file("script.exe", "binary")

    async def test_extension_case_handling(self, tmp_path: Path):
        writer = WorkspaceWriter(str(tmp_path), allowed_extensions=(".PY",))
        await writer.write_file("app.py", "x = 1")
        assert writer.take_changed_files() == ("app.py",)

    def test_strict_operation_field_validation(self):
        with pytest.raises(ValueError, match="read does not accept"):
            CodingOperation(type=CodingOperationType.READ, path="a.py", content="err")
        with pytest.raises(ValueError, match="write does not accept"):
            CodingOperation(type=CodingOperationType.WRITE, path="a.py", content="ok", old_string="bad")
        with pytest.raises(ValueError, match="edit does not accept"):
            CodingOperation(type=CodingOperationType.EDIT, path="a.py", old_string="a", new_string="b", content="err")
        with pytest.raises(ValueError, match="finish does not accept"):
            CodingOperation(type=CodingOperationType.FINISH, path="a.py")

    def test_reconcile_finished_flag(self):
        with pytest.raises(ValueError, match="finished=True requires a FINISH operation"):
            CodingStepResult(
                operations=(CodingOperation(type=CodingOperationType.READ, path="a.py"),),
                finished=True,
            )
        with pytest.raises(ValueError, match="FINISH operation requires finished=True"):
            CodingStepResult(
                operations=(CodingOperation(type=CodingOperationType.FINISH, path=""),),
                finished=False,
            )

    async def test_normalized_changed_files(self, tmp_path: Path):
        writer = WorkspaceWriter(str(tmp_path))
        await writer.write_file("src/./foo.py", "content")
        assert writer.take_changed_files() == ("src/foo.py",)

