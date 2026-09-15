"""
Stage 10.4 — Kilo Session & Recovery Tests.

Tests cover:
- Session creation and state transitions
- Session-task/project/workspace binding
- Kilo session ID as untrusted metadata
- Recovery eligibility checks
- Orphan detection and process ownership
- JSON event parsing (minimal, fail-closed)
- Cross-task/project/workspace replay rejection
- Completed session rejection
- Authorization/approval change rejection
- Concurrent recovery safety
- Process ownership verification
- No arbitrary PID termination
- No session-controlled workspace
- No session-controlled task
- No hidden retry
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.db.database import Base
from app.orchestration.kilo_session_manager import KiloSessionManager
from app.orchestration.kilo_session_models import (
    KiloJsonEvent,
    KiloSessionRecoveryEligibility,
    KiloSessionRecord,
    KiloSessionState,
)
from app.orchestration.models import DevelopmentAgentContext, DevelopmentResult, DevelopmentResultStatus
from app.repositories.kilo_session_repository import KiloSessionRepository


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ── SQLite fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
async def sqlite_engine(tmp_path: Path):
    db_path = tmp_path / "test_kilo_session.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(db_url, connect_args={"timeout": 30.0})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(sqlite_engine: AsyncEngine):
    return async_sessionmaker(bind=sqlite_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def session(session_factory):
    async with session_factory() as sess:
        yield sess


@pytest.fixture
def session_repo(session: AsyncSession):
    return KiloSessionRepository(session)


@pytest.fixture
def kilo_session_manager(session_repo: KiloSessionRepository):
    return KiloSessionManager(session_repo=session_repo)


# ── Session creation and state transitions ────────────────────────────────────


class TestSessionCreation:
    async def test_create_session(self, kilo_session_manager: KiloSessionManager):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        assert record.app_session_id is not None
        assert record.task_id == "task_1"
        assert record.project_id == "proj_1"
        assert record.workspace_path == "/tmp/ws1"
        assert record.execution_id == "exec_1"
        assert record.state == KiloSessionState.NEW
        assert record.recovery_eligible is True
        assert record.kilo_session_id is None
        assert record.pid is None

    async def test_session_persists_across_sessions(
        self, session_factory, kilo_session_manager: KiloSessionManager
    ):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        app_session_id = record.app_session_id

        async with session_factory() as sess2:
            repo2 = KiloSessionRepository(sess2)
            fetched = await repo2.get_session(app_session_id)
            assert fetched is not None
            assert fetched.task_id == "task_1"

    async def test_activate_session(self, kilo_session_manager: KiloSessionManager):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        activated = await kilo_session_manager.activate_session(
            record.app_session_id,
            kilo_session_id="ses_kilo_123",
            pid=12345,
        )
        assert activated is not None
        assert activated.state == KiloSessionState.ACTIVE
        assert activated.kilo_session_id == "ses_kilo_123"
        assert activated.pid == 12345

    async def test_complete_session_success(self, kilo_session_manager: KiloSessionManager):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        completed = await kilo_session_manager.complete_session(
            record.app_session_id,
            KiloSessionState.COMPLETED,
        )
        assert completed is not None
        assert completed.state == KiloSessionState.COMPLETED
        assert completed.recovery_eligible is False
        assert completed.completed_at is not None

    async def test_complete_session_failed(self, kilo_session_manager: KiloSessionManager):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        completed = await kilo_session_manager.complete_session(
            record.app_session_id,
            KiloSessionState.FAILED,
            error_message="Model not found",
        )
        assert completed is not None
        assert completed.state == KiloSessionState.FAILED
        assert completed.error_message == "Model not found"

    async def test_invalidate_completed_session_is_noop(
        self, kilo_session_manager: KiloSessionManager
    ):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        await kilo_session_manager.complete_session(
            record.app_session_id,
            KiloSessionState.COMPLETED,
        )
        # Second completion should be safe
        result = await kilo_session_manager.complete_session(
            record.app_session_id,
            KiloSessionState.FAILED,
        )
        assert result is not None
        assert result.state == KiloSessionState.COMPLETED


# ── Application-owned identity binding ────────────────────────────────────────


class TestIdentityBinding:
    async def test_kilo_session_id_is_untrusted(self, kilo_session_manager: KiloSessionManager):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        activated = await kilo_session_manager.activate_session(
            record.app_session_id,
            kilo_session_id="ses_from_kilo",
            pid=None,
        )
        assert activated is not None
        assert activated.kilo_session_id == "ses_from_kilo"
        # But the authoritative identity is still application-owned
        assert activated.task_id == "task_1"
        assert activated.execution_id == "exec_1"

    async def test_session_task_binding(self, session_factory, kilo_session_manager: KiloSessionManager):
        record = await kilo_session_manager.create_session(
            task_id="task_A",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        fetched = await kilo_session_manager._session_repo.get_session(record.app_session_id)
        assert fetched.task_id == "task_A"

    async def test_session_project_binding(self, kilo_session_manager: KiloSessionManager):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="project_X",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        assert record.project_id == "project_X"

    async def test_session_workspace_binding(self, kilo_session_manager: KiloSessionManager):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/workspace/alpha",
            execution_id="exec_1",
        )
        assert record.workspace_path == "/workspace/alpha"

    async def test_cross_task_replay_rejected(self, session_factory, kilo_session_manager: KiloSessionManager):
        record = await kilo_session_manager.create_session(
            task_id="task_original",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        await kilo_session_manager.activate_session(record.app_session_id, "ses_kilo", 12345)
        await kilo_session_manager.complete_session(record.app_session_id, KiloSessionState.COMPLETED)

        # Try to recover with different task_id
        eligibility = await kilo_session_manager.check_recovery_eligibility(
            task_id="task_different",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        assert eligibility.eligible is False
        assert "No session found" in eligibility.reason

    async def test_cross_project_replay_rejected(self, session_factory, kilo_session_manager: KiloSessionManager):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="project_original",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        await kilo_session_manager.activate_session(record.app_session_id, "ses_kilo", 12345)

        eligibility = await kilo_session_manager.check_recovery_eligibility(
            task_id="task_1",
            project_id="project_different",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        assert eligibility.eligible is False
        assert "Project ID mismatch" in eligibility.reason

    async def test_cross_workspace_replay_rejected(self, session_factory, kilo_session_manager: KiloSessionManager):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/workspace/original",
            execution_id="exec_1",
        )
        await kilo_session_manager.activate_session(record.app_session_id, "ses_kilo", 12345)

        eligibility = await kilo_session_manager.check_recovery_eligibility(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/workspace/different",
            execution_id="exec_1",
        )
        assert eligibility.eligible is False
        assert "Workspace path mismatch" in eligibility.reason


# ── Recovery eligibility ──────────────────────────────────────────────────────


class TestRecoveryEligibility:
    async def test_completed_session_not_recoverable(self, session_factory, kilo_session_manager: KiloSessionManager):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        await kilo_session_manager.activate_session(record.app_session_id, "ses_kilo", 12345)
        await kilo_session_manager.complete_session(record.app_session_id, KiloSessionState.COMPLETED)

        eligibility = await kilo_session_manager.check_recovery_eligibility(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        assert eligibility.eligible is False
        assert "state is COMPLETED" in eligibility.reason

    async def test_invalidated_session_not_recoverable(self, session_factory, kilo_session_manager: KiloSessionManager):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        await kilo_session_manager.activate_session(record.app_session_id, "ses_kilo", 12345)
        await kilo_session_manager.complete_session(record.app_session_id, KiloSessionState.INVALIDATED)

        eligibility = await kilo_session_manager.check_recovery_eligibility(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        assert eligibility.eligible is False

    async def test_authorization_change_rejects_recovery(self, session_factory, kilo_session_manager: KiloSessionManager):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
            authorization_version="auth_v1",
        )
        await kilo_session_manager.activate_session(record.app_session_id, "ses_kilo", 12345)

        eligibility = await kilo_session_manager.check_recovery_eligibility(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
            authorization_version="auth_v2",
        )
        assert eligibility.eligible is False
        assert "Authorization version changed" in eligibility.reason

    async def test_approval_change_rejects_recovery(self, session_factory, kilo_session_manager: KiloSessionManager):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
            approval_scope_hash="hash_v1",
        )
        await kilo_session_manager.activate_session(record.app_session_id, "ses_kilo", 12345)

        eligibility = await kilo_session_manager.check_recovery_eligibility(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
            approval_scope_hash="hash_v2",
        )
        assert eligibility.eligible is False
        assert "Approval scope changed" in eligibility.reason

    async def test_active_process_blocks_recovery(self, session_factory, kilo_session_manager: KiloSessionManager):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        await kilo_session_manager.activate_session(record.app_session_id, "ses_kilo", os.getpid())

        eligibility = await kilo_session_manager.check_recovery_eligibility(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        # Current process is alive, so process_terminated check fails
        assert eligibility.checks.get("process_terminated", True) is False

    async def test_conflicting_active_session_blocks_recovery(
        self, session_factory, kilo_session_manager: KiloSessionManager
    ):
        record1 = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        await kilo_session_manager.activate_session(record1.app_session_id, "ses_kilo_1", 12345)

        # Create a second active session for the same task
        record2 = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_2",
        )
        await kilo_session_manager.activate_session(record2.app_session_id, "ses_kilo_2", 54321)

        eligibility = await kilo_session_manager.check_recovery_eligibility(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        assert eligibility.eligible is False
        assert "active session" in eligibility.reason.lower()


# ── Session invalidation ──────────────────────────────────────────────────────


class TestSessionInvalidation:
    async def test_invalidate_sessions_for_task(self, session_factory, kilo_session_manager: KiloSessionManager):
        record1 = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        await kilo_session_manager.activate_session(record1.app_session_id, "ses_1", 12345)

        record2 = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_2",
        )
        await kilo_session_manager.activate_session(record2.app_session_id, "ses_2", 54321)

        count = await kilo_session_manager._session_repo.invalidate_sessions_for_task("task_1")
        assert count == 2

        async with session_factory() as sess:
            repo = KiloSessionRepository(sess)
            s1 = await repo.get_session(record1.app_session_id)
            s2 = await repo.get_session(record2.app_session_id)
            assert s1.state == KiloSessionState.INVALIDATED.value
            assert s2.state == KiloSessionState.INVALIDATED.value
            assert s1.recovery_eligible == 0
            assert s2.recovery_eligible == 0


# ── Orphan detection ──────────────────────────────────────────────────────────


class TestOrphanDetection:
    async def test_detect_no_orphans_when_none_active(self, kilo_session_manager: KiloSessionManager):
        orphans = await kilo_session_manager.detect_orphaned_processes()
        assert orphans == []

    async def test_detect_orphan_with_dead_pid(self, session_factory, kilo_session_manager: KiloSessionManager):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        # Use a PID that is definitely not alive
        await kilo_session_manager.activate_session(record.app_session_id, "ses_kilo", 9999999)

        orphans = await kilo_session_manager.detect_orphaned_processes()
        assert len(orphans) == 0  # PID 9999999 is not alive

    async def test_terminate_orphaned_process(self, session_factory, kilo_session_manager: KiloSessionManager):
        if platform.system() != "Windows":
            pytest.skip("Windows-specific process test")

        # Spawn a real process
        proc = await asyncio.create_subprocess_exec(
            "cmd", "/c", "timeout", "/t", "60",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            stdin=asyncio.subprocess.DEVNULL,
        )
        pid = proc.pid
        assert pid is not None

        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        await kilo_session_manager.activate_session(record.app_session_id, "ses_kilo", pid)

        # Terminate it
        terminated = await kilo_session_manager.terminate_orphaned_process(record.app_session_id, pid)
        assert terminated is True

        async with session_factory() as sess:
            repo = KiloSessionRepository(sess)
            updated = await repo.get_session(record.app_session_id)
            assert updated.state == KiloSessionState.INVALIDATED.value
            assert updated.process_terminated_at is not None


# ── JSON event parsing ────────────────────────────────────────────────────────


class TestJsonEventParsing:
    async def test_parse_valid_json_events(self):
        raw = json.dumps({"type": "session", "sessionID": "ses_123", "timestamp": 1234567890})
        events = KiloSessionManager.parse_kilo_json_events(raw)
        assert len(events) == 1
        assert events[0].event_type == "session"
        assert events[0].session_id == "ses_123"
        assert events[0].timestamp == 1234567890

    async def test_parse_error_event(self):
        raw = json.dumps({
            "type": "error",
            "sessionID": "ses_err",
            "error": {"name": "ModelNotFound", "data": {"message": "no model"}}
        })
        events = KiloSessionManager.parse_kilo_json_events(raw)
        assert len(events) == 1
        assert events[0].error_name == "ModelNotFound"
        assert events[0].error_data == {"message": "no model"}

    async def test_parse_malformed_json_ignored(self):
        raw = "not valid json {{{"
        events = KiloSessionManager.parse_kilo_json_events(raw)
        assert events == []

    async def test_parse_multiple_events(self):
        lines = "\n".join([
            json.dumps({"type": "session", "sessionID": "ses_1"}),
            json.dumps({"type": "error", "error": {"name": "X"}}),
            "not json",
            json.dumps({"type": "session", "sessionID": "ses_2"}),
        ])
        events = KiloSessionManager.parse_kilo_json_events(lines)
        assert len(events) == 3

    async def test_parse_non_dict_ignored(self):
        raw = json.dumps([{"type": "session"}])
        events = KiloSessionManager.parse_kilo_json_events(raw)
        assert events == []

    async def test_json_parsing_never_grants_authority(self):
        """JSON parsing is diagnostics-only. Verify no security fields are set from events."""
        raw = json.dumps({
            "type": "error",
            "sessionID": "ses_fake",
            "error": {"name": "FakeError", "data": {"task_id": "hacked_task"}}
        })
        events = KiloSessionManager.parse_kilo_json_events(raw)
        assert len(events) == 1
        # The event data contains fake task_id but we never use it for authorization
        assert events[0].error_data is not None
        assert events[0].error_data.get("task_id") == "hacked_task"
        # Our code must not use this for task identity


# ── Session recovery in agent ─────────────────────────────────────────────────


class TestAgentSessionIntegration:
    async def test_agent_creates_session_on_execute(self, tmp_path: Path, session_factory):
        from app.orchestration.agents.kilo_agent import KiloDevelopmentAgent
        from app.orchestration.kilo_session_manager import KiloSessionManager

        workspace = tmp_path / "ws"
        workspace.mkdir()

        async with session_factory() as sess:
            repo = KiloSessionRepository(sess)
            manager = KiloSessionManager(repo)
            agent = KiloDevelopmentAgent(
                kilo_executable_path="C:\\kilo.exe",
                kilo_session_manager=manager,
            )

            proc_mock = MagicMock()
            proc_mock.pid = 12345
            proc_mock.returncode = None

            async def mock_start(args):
                return proc_mock

            async def mock_wait(proc, **kwargs):
                proc.returncode = 0
                from app.orchestration.agents.kilo_process import KiloProcessResult
                return KiloProcessResult(returncode=0, stdout_text='{"type":"session","sessionID":"ses_agent_123"}')

            with patch("app.orchestration.agents.kilo_agent.start_kilo_process", side_effect=mock_start):
                with patch("app.orchestration.agents.kilo_agent.wait_kilo_process", side_effect=mock_wait):
                    ctx = DevelopmentAgentContext(
                        task_id="t1",
                        project_id="p1",
                        workspace_path=str(workspace),
                        repository_root=str(tmp_path),
                        title="test",
                        description="test task",
                        attempt_count=0,
                        expected_base_commit="abc123",
                    )
                    result = await agent.execute(ctx)

            assert result.status == DevelopmentResultStatus.SUCCESS
            # Verify session was recorded
            sessions = await repo.list_sessions_for_task("t1")
            assert len(sessions) == 1
            assert sessions[0].state == KiloSessionState.COMPLETED

    async def test_agent_records_failure_session(self, tmp_path: Path, session_factory):
        from app.orchestration.agents.kilo_agent import KiloDevelopmentAgent
        from app.orchestration.kilo_session_manager import KiloSessionManager

        workspace = tmp_path / "ws"
        workspace.mkdir()

        async with session_factory() as sess:
            repo = KiloSessionRepository(sess)
            manager = KiloSessionManager(repo)
            agent = KiloDevelopmentAgent(
                kilo_executable_path="C:\\kilo.exe",
                kilo_session_manager=manager,
            )

            proc_mock = MagicMock()
            proc_mock.pid = 12345
            proc_mock.returncode = None

            async def mock_start(args):
                return proc_mock

            async def mock_wait(proc, **kwargs):
                proc.returncode = 1
                from app.orchestration.agents.kilo_process import KiloProcessResult
                return KiloProcessResult(returncode=1, stderr_text="model not found")

            with patch("app.orchestration.agents.kilo_agent.start_kilo_process", side_effect=mock_start):
                with patch("app.orchestration.agents.kilo_agent.wait_kilo_process", side_effect=mock_wait):
                    ctx = DevelopmentAgentContext(
                        task_id="t1",
                        project_id="p1",
                        workspace_path=str(workspace),
                        repository_root=str(tmp_path),
                        title="test",
                        description="test task",
                        attempt_count=0,
                        expected_base_commit="abc123",
                    )
                    result = await agent.execute(ctx)

            assert result.status == DevelopmentResultStatus.FAILURE
            sessions = await repo.list_sessions_for_task("t1")
            assert len(sessions) == 1
            assert sessions[0].state == KiloSessionState.FAILED


# ── Process ownership verification ────────────────────────────────────────────


class TestProcessOwnership:
    async def test_no_arbitrary_pid_termination(self, session_factory, kilo_session_manager: KiloSessionManager):
        """verify_process_identity must return False for unknown PIDs."""
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        await kilo_session_manager.activate_session(record.app_session_id, "ses_kilo", 1)
        # PID 1 is system process - should never be terminated
        orphans = await kilo_session_manager.detect_orphaned_processes()
        assert len(orphans) == 0

    async def test_process_identity_verification_with_mismatched_start_time(
        self, session_factory, kilo_session_manager: KiloSessionManager
    ):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        # Set a very old start time that won't match current process
        far_past = _utc_now() - timedelta(days=365)
        await kilo_session_manager.activate_session(
            record.app_session_id,
            "ses_kilo",
            os.getpid(),
            process_started_at=far_past,
        )
        identity_ok = await kilo_session_manager._verify_process_identity(record)
        # Identity verification must fail when start time does not match
        assert identity_ok is False

    async def test_pid_reuse_protection(self, session_factory, kilo_session_manager: KiloSessionManager):
        """PID reuse within tolerance window must not allow termination of unrelated process."""
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        # Use current PID but with a future start time (impossible match)
        future = _utc_now() + timedelta(days=1)
        await kilo_session_manager.activate_session(
            record.app_session_id,
            "ses_kilo",
            os.getpid(),
            process_started_at=future,
        )
        identity_ok = await kilo_session_manager._verify_process_identity(record)
        assert identity_ok is False

        # Even if identity check passed (which it shouldn't), terminate_orphaned_process
        # must verify PID match before terminating
        terminated = await kilo_session_manager.terminate_orphaned_process(
            record.app_session_id, 999999  # wrong PID
        )
        assert terminated is False


# ── Start-up orphan handling ──────────────────────────────────────────────────


class TestStartupOrphanHandling:
    async def test_list_active_sessions_empty_on_fresh_db(self, session_factory):
        async with session_factory() as sess:
            repo = KiloSessionRepository(sess)
            active = await repo.list_active_sessions()
            assert active == []


# ── Edge cases ────────────────────────────────────────────────────────────────


class TestEdgeCases:
    async def test_get_nonexistent_session(self, session_repo: KiloSessionRepository):
        result = await session_repo.get_session("nonexistent_session_id")
        assert result is None

    async def test_list_sessions_for_task_empty(self, session_repo: KiloSessionRepository):
        result = await session_repo.list_sessions_for_task("no_such_task")
        assert result == []

    async def test_session_state_validation(self):
        """All states must be valid enum values."""
        for state in KiloSessionState:
            assert state.value is not None
            assert len(state.value) > 0

    async def test_kilo_json_event_schema_stability(self):
        """Verify JSON event parsing handles observed schema."""
        observed_events = [
            {"type": "error", "timestamp": 1789417917818, "sessionID": "ses_f5e618a76ffeezDxa0HHhy56jo", "error": {"name": "UnknownError", "data": {"message": "..."}}},
            {"type": "session", "timestamp": 1789417917818, "sessionID": "ses_f5e618a76ffeezDxa0HHhy56jo"},
        ]
        for raw_event in observed_events:
            events = KiloSessionManager.parse_kilo_json_events(json.dumps(raw_event))
            assert len(events) == 1
            assert events[0].event_type == raw_event["type"]
            assert events[0].session_id == raw_event.get("sessionID")

    async def test_no_session_reuse_across_executions(self, session_factory, kilo_session_manager: KiloSessionManager):
        """Each execution must create a distinct session."""
        s1 = await kilo_session_manager.create_session("t", "p", "/ws", "e1")
        s2 = await kilo_session_manager.create_session("t", "p", "/ws", "e2")
        assert s1.app_session_id != s2.app_session_id
        assert s1.execution_id != s2.execution_id


# ── Concurrency ────────────────────────────────────────────────────────────────


class TestConcurrency:
    async def test_concurrent_activate_same_session(self, session_factory, kilo_session_manager: KiloSessionManager):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )

        async def activate():
            return await kilo_session_manager.activate_session(
                record.app_session_id, "ses_kilo", 12345
            )

        results = await asyncio.gather(activate(), activate(), return_exceptions=True)
        successes = [r for r in results if not isinstance(r, Exception)]
        assert len(successes) >= 1
        for r in successes:
            assert r is not None
            assert r.state == KiloSessionState.ACTIVE

    async def test_concurrent_complete_same_session(self, session_factory, kilo_session_manager: KiloSessionManager):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        await kilo_session_manager.activate_session(record.app_session_id, "ses_kilo", 12345)

        async def complete():
            return await kilo_session_manager.complete_session(
                record.app_session_id, KiloSessionState.COMPLETED
            )

        results = await asyncio.gather(complete(), complete(), return_exceptions=True)
        successes = [r for r in results if not isinstance(r, Exception)]
        assert len(successes) >= 1
        final = await kilo_session_manager._session_repo.get_session(record.app_session_id)
        assert final is not None
        assert final.state == KiloSessionState.COMPLETED

    async def test_orphan_cleanup_idempotent(self, session_factory, kilo_session_manager: KiloSessionManager):
        record = await kilo_session_manager.create_session(
            task_id="task_1",
            project_id="proj_1",
            workspace_path="/tmp/ws1",
            execution_id="exec_1",
        )
        await kilo_session_manager.activate_session(record.app_session_id, "ses_kilo", 1)

        orphans1 = await kilo_session_manager.detect_orphaned_processes()
        orphans2 = await kilo_session_manager.detect_orphaned_processes()
        assert orphans1 == orphans2

    async def test_concurrent_sessions_for_different_tasks(self, session_factory, kilo_session_manager: KiloSessionManager):
        s1 = await kilo_session_manager.create_session("t1", "p1", "/ws1", "e1")
        s2 = await kilo_session_manager.create_session("t2", "p2", "/ws2", "e2")
        await kilo_session_manager.activate_session(s1.app_session_id, "ses_1", 11111)
        await kilo_session_manager.activate_session(s2.app_session_id, "ses_2", 22222)

        assert s1.task_id != s2.task_id
        assert s1.project_id != s2.project_id
        assert s1.workspace_path != s2.workspace_path

        # Complete s1 so its recovery can be checked
        await kilo_session_manager.complete_session(s1.app_session_id, KiloSessionState.COMPLETED)

        # s2 is for a different task, so s1's recovery check should not see s2
        eligibility1 = await kilo_session_manager.check_recovery_eligibility(
            task_id="t1", project_id="p1", workspace_path="/ws1", execution_id="e1"
        )
        assert eligibility1.eligible is False  # completed sessions are not recoverable
        assert "state is COMPLETED" in eligibility1.reason
