"""
Kilo Session Manager.

Application-owned session lifecycle, recovery eligibility, and orphan detection
for Kilo Code CLI executions.

SECURITY INVARIANTS:
  - Application owns all identity fields (task_id, project_id, execution_id).
  - Kilo session ID is untrusted external metadata.
  - Recovery requires explicit application authorization.
  - Process ownership is verified before termination.
  - No arbitrary PID termination.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import update as sa_update

from app.db.models.kilo_session import KiloSessionModel
from app.orchestration.kilo_session_models import (
    KiloJsonEvent,
    KiloSessionRecoveryEligibility,
    KiloSessionRecord,
    KiloSessionState,
)
from app.repositories.kilo_session_repository import KiloSessionRepository

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class KiloSessionManager:
    """
    Manager for Kilo Code CLI execution sessions.

    Owns:
      - Session creation and state transitions
      - Recovery eligibility checks
      - Persistent orphan detection
      - Minimal JSON event parsing (diagnostics only)
    """

    def __init__(
        self,
        session_repo: KiloSessionRepository,
        recovery_lease_seconds: float = 300.0,
    ) -> None:
        self._session_repo = session_repo
        self._recovery_lease_seconds = float(recovery_lease_seconds)

    async def create_session(
        self,
        task_id: str,
        project_id: str,
        workspace_path: str,
        execution_id: str,
        authorization_version: str | None = None,
        approval_scope_hash: str | None = None,
    ) -> KiloSessionRecord:
        """
        Create a new application-owned Kilo session record.
        """
        app_session_id = f"kilo_{_utc_now().strftime('%Y%m%d%H%M%S')}_{_generate_short_id()}"
        record = KiloSessionRecord(
            app_session_id=app_session_id,
            task_id=task_id,
            project_id=project_id,
            workspace_path=workspace_path,
            execution_id=execution_id,
            state=KiloSessionState.NEW,
            recovery_eligible=True,
            authorization_version=authorization_version,
            approval_scope_hash=approval_scope_hash,
        )
        return await self._session_repo.create_session(record)

    async def activate_session(
        self,
        app_session_id: str,
        kilo_session_id: str | None,
        pid: int | None,
        process_started_at: datetime | None = None,
    ) -> KiloSessionRecord | None:
        """
        Transition session to ACTIVE with Kilo session ID and PID.
        """
        session = await self._session_repo.get_session(app_session_id)
        if session is None:
            return None
        if session.state != KiloSessionState.NEW:
            return None

        now = _utc_now()
        await self._session_repo.update_session_state(app_session_id, KiloSessionState.ACTIVE, now)
        if pid is not None:
            await self._session_repo.update_session_pid(app_session_id, pid, process_started_at or now)
        if kilo_session_id is not None:
            stmt = (
                sa_update(KiloSessionModel)
                .where(KiloSessionModel.app_session_id == app_session_id)
                .values(kilo_session_id=kilo_session_id, last_observed_at=now)
            )
            await self._session_repo._session.execute(stmt)
            await self._session_repo._session.flush()
            await self._session_repo._session.commit()
        return await self._session_repo.get_session(app_session_id)

    async def update_session_kilo_id(
        self,
        app_session_id: str,
        kilo_session_id: str | None,
        kilo_session_state: str | None = None,
    ) -> KiloSessionRecord | None:
        """Update Kilo session ID on an existing session (e.g., after JSON event parsing)."""
        return await self._session_repo.update_session_kilo_id(
            app_session_id, kilo_session_id, kilo_session_state
        )

    async def complete_session(
        self,
        app_session_id: str,
        state: KiloSessionState,
        error_message: str | None = None,
        kilo_session_state: str | None = None,
    ) -> KiloSessionRecord | None:
        """
        Transition session to a terminal state (COMPLETED, FAILED, INVALIDATED).

        process_terminated_at is set only when the session has a recorded PID,
        indicating that a process was actually started. Callers that terminate
        the process before calling this method should ensure the PID is set.
        """
        session = await self._session_repo.get_session(app_session_id)
        if session is None:
            return None
        if session.state in (KiloSessionState.COMPLETED, KiloSessionState.INVALIDATED):
            return session

        now = _utc_now()
        values: dict[str, Any] = {
            "state": state.value,
            "completed_at": now,
            "last_observed_at": now,
            "recovery_eligible": 0 if state in (KiloSessionState.COMPLETED, KiloSessionState.INVALIDATED) else 1,
            "error_message": error_message,
        }
        if session.pid is not None:
            values["process_terminated_at"] = now
        if kilo_session_state is not None:
            values["kilo_session_state"] = kilo_session_state
        stmt = (
            sa_update(KiloSessionModel)
            .where(KiloSessionModel.app_session_id == app_session_id)
            .values(**values)
        )
        await self._session_repo._session.execute(stmt)
        await self._session_repo._session.flush()
        await self._session_repo._session.commit()
        return await self._session_repo.get_session(app_session_id)

    async def check_recovery_eligibility(
        self,
        task_id: str,
        project_id: str,
        workspace_path: str,
        execution_id: str,
        authorization_version: str | None = None,
        approval_scope_hash: str | None = None,
    ) -> KiloSessionRecoveryEligibility:
        """
        Verify whether recovery is permitted for this task/execution.

        Checks:
          1. Task still exists (caller provides task_id)
          2. Task is still eligible (caller verifies)
          3. Project identity matches
          4. Workspace identity matches
          5. Authorization is still valid
          6. Approval scope is still valid
          7. Previous process is actually terminated
          8. No conflicting active Kilo process exists
          9. Workspace is still the expected workspace
          10. Recovery is allowed by application state
        """
        checks: dict[str, bool] = {}
        reasons: list[str] = []

        # Find the most recent session for this task
        sessions = await self._session_repo.list_sessions_for_task(task_id)
        target_session = None
        for s in sessions:
            if s.execution_id == execution_id:
                target_session = s
                break

        if target_session is None:
            checks["session_exists"] = False
            reasons.append("No session found for this execution")
            return KiloSessionRecoveryEligibility(
                eligible=False,
                reason="; ".join(reasons),
                checks=checks,
            )

        checks["session_exists"] = True

        # 2. State check
        if target_session.state in (KiloSessionState.COMPLETED, KiloSessionState.INVALIDATED):
            checks["state_valid"] = False
            reasons.append(f"Session state is {target_session.state.value}")
        else:
            checks["state_valid"] = True

        # 3. Recovery eligible flag
        checks["recovery_eligible"] = target_session.recovery_eligible
        if not target_session.recovery_eligible:
            reasons.append("Session is not recovery-eligible")

        # 4. Project identity matches
        checks["project_matches"] = target_session.project_id == project_id
        if not checks["project_matches"]:
            reasons.append("Project ID mismatch")

        # 5. Workspace identity matches
        checks["workspace_matches"] = target_session.workspace_path == workspace_path
        if not checks["workspace_matches"]:
            reasons.append("Workspace path mismatch")

        # 6. Execution identity matches
        checks["execution_matches"] = target_session.execution_id == execution_id
        if not checks["execution_matches"]:
            reasons.append("Execution ID mismatch")

        # 7. Authorization version matches
        if target_session.authorization_version is not None:
            checks["authorization_valid"] = target_session.authorization_version == authorization_version
            if not checks["authorization_valid"]:
                reasons.append("Authorization version changed")
        else:
            checks["authorization_valid"] = True

        # 8. Approval scope matches
        if target_session.approval_scope_hash is not None:
            checks["approval_valid"] = target_session.approval_scope_hash == approval_scope_hash
            if not checks["approval_valid"]:
                reasons.append("Approval scope changed")
        else:
            checks["approval_valid"] = True

        # 9. Previous process terminated
        if target_session.pid is not None and target_session.process_terminated_at is None:
            alive = _is_process_alive(target_session.pid)
            checks["process_terminated"] = not alive
            if alive:
                reasons.append(f"Previous process PID {target_session.pid} is still alive")
        else:
            checks["process_terminated"] = True

        # 10. No conflicting active session
        active_count = await self._session_repo.count_active_sessions_for_task(task_id)
        checks["no_conflicting_active"] = active_count == 0
        if active_count > 0:
            reasons.append(f"Task has {active_count} active session(s)")

        eligible = all(checks.values())
        return KiloSessionRecoveryEligibility(
            eligible=eligible,
            reason="; ".join(reasons) if reasons else "All checks passed",
            checks=checks,
        )

    async def enforce_recovery_before_execution(
        self,
        task_id: str,
        project_id: str,
        workspace_path: str,
        execution_id: str,
        authorization_version: str | None = None,
        approval_scope_hash: str | None = None,
    ) -> KiloSessionRecoveryEligibility | None:
        """
        Production gate: before starting a new Kilo execution, check whether an
        existing session for this execution requires recovery. If recovery is
        not eligible, fail closed. If eligible, invalidate the old session and
        clean up any orphaned process, then allow the new execution to proceed.

        Returns:
            None if it is safe to proceed with a new execution.
            KiloSessionRecoveryEligibility if recovery was blocked (eligible=False).
        """
        existing = await self._session_repo.get_latest_session_for_execution(task_id, execution_id)
        if existing is None:
            return None

        if existing.state in (KiloSessionState.COMPLETED, KiloSessionState.INVALIDATED):
            return None

        eligibility = await self.check_recovery_eligibility(
            task_id=task_id,
            project_id=project_id,
            workspace_path=workspace_path,
            execution_id=execution_id,
            authorization_version=authorization_version,
            approval_scope_hash=approval_scope_hash,
        )
        if not eligibility.eligible:
            return eligibility

        if existing.state not in (KiloSessionState.COMPLETED, KiloSessionState.INVALIDATED):
            await self.complete_session(
                existing.app_session_id,
                KiloSessionState.INVALIDATED,
                error_message="Invalidated by new execution",
            )

        if existing.pid is not None and existing.process_terminated_at is None:
            if _is_process_alive(existing.pid):
                identity_ok = await self._verify_process_identity(existing)
                if identity_ok:
                    await self._terminate_process(existing.pid)
                    await self.complete_session(
                        existing.app_session_id,
                        KiloSessionState.INVALIDATED,
                        error_message="Terminated as orphaned process",
                    )

        return None

    async def detect_orphaned_processes(self) -> list[dict[str, Any]]:
        """
        Scan for orphaned Kilo processes from previous application runs.

        Only terminates processes provably owned by this application/session.
        Never kills arbitrary PIDs.
        """
        orphans: list[dict[str, Any]] = []
        active_sessions = await self._session_repo.list_active_sessions()
        for session in active_sessions:
            if session.pid is None:
                continue
            if session.process_terminated_at is not None:
                continue
            if not _is_process_alive(session.pid):
                continue

            # Verify process identity before declaring it orphaned
            identity_verified = await self._verify_process_identity(session)
            if not identity_verified:
                logger.warning(
                    "Skipping PID %s for session %s: process identity unverified",
                    session.pid,
                    session.app_session_id,
                )
                continue

            orphans.append({
                "app_session_id": session.app_session_id,
                "task_id": session.task_id,
                "pid": session.pid,
                "started_at": session.process_started_at,
            })
        return orphans

    async def terminate_orphaned_process(self, app_session_id: str, pid: int) -> bool:
        """
        Terminate a provably orphaned Kilo process.

        Returns True if termination was confirmed.
        """
        session = await self._session_repo.get_session(app_session_id)
        if session is None:
            return False

        if session.pid != pid:
            return False

        terminated = await self._terminate_process(pid)
        if terminated:
            now = _utc_now()
            stmt = (
                sa_update(KiloSessionModel)
                .where(KiloSessionModel.app_session_id == app_session_id)
                .values(
                    state=KiloSessionState.INVALIDATED.value,
                    process_terminated_at=now,
                    completed_at=now,
                    last_observed_at=now,
                    recovery_eligible=0,
                    error_message="Terminated as orphaned process",
                )
            )
            await self._session_repo._session.execute(stmt)
            await self._session_repo._session.flush()
            await self._session_repo._session.commit()
        return terminated

    async def _verify_process_identity(self, session: KiloSessionRecord) -> bool:
        """
        Verify that a running process matches the expected session identity.

        On Windows, we can check process start time via wmic.
        On other platforms, we check the process command line.
        """
        if session.pid is None:
            return False
        if session.process_started_at is None:
            return False

        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["wmic", "process", "where", f"ProcessId={session.pid}", "get", "CreationDate", "/format:list"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode != 0:
                    return False
                for line in result.stdout.splitlines():
                    if line.startswith("CreationDate="):
                        creation_str = line.split("=", 1)[1].strip()
                        if creation_str:
                            try:
                                # WMIC date format: 20260915123456.123456+060
                                dt = datetime.strptime(creation_str[:14], "%Y%m%d%H%M%S")
                                dt = dt.replace(tzinfo=timezone.utc)
                                expected = session.process_started_at.replace(tzinfo=timezone.utc)
                                diff = abs((dt - expected).total_seconds())
                                return 0 <= diff < 5.0
                            except (ValueError, IndexError):
                                pass
                        return False
                return False
            else:
                # On non-Windows, check process start time via ps
                result = subprocess.run(
                    ["ps", "-p", str(session.pid), "-o", "lstart="],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode != 0:
                    return False
                lstart = result.stdout.strip()
                try:
                    dt = datetime.strptime(lstart, "%a %b %d %H:%M:%S %Y")
                    dt = dt.replace(tzinfo=timezone.utc)
                    expected = session.process_started_at.replace(tzinfo=timezone.utc)
                    diff = abs((dt - expected).total_seconds())
                    return 0 <= diff < 5.0
                except (ValueError, IndexError):
                    return False
        except Exception as exc:
            logger.warning("Process identity verification failed for PID %s: %s", session.pid, exc)
            return False

    async def _terminate_process(self, pid: int) -> bool:
        """Terminate a process. Returns True if confirmed."""
        if pid <= 0:
            return False
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=10,
                )
                for _ in range(10):
                    if not _is_process_alive(pid):
                        return True
                    await asyncio.sleep(0.3)
                return not _is_process_alive(pid)
            else:
                os.kill(pid, 9)
                await asyncio.sleep(0.1)
                return not _is_process_alive(pid)
        except (ProcessLookupError, OSError):
            return True
        except Exception as exc:
            logger.warning("Process termination failed for PID %s: %s", pid, exc)
            return False

    @staticmethod
    def parse_kilo_json_events(raw_output: str) -> list[KiloJsonEvent]:
        """
        Parse Kilo JSON events from raw output.

        Minimal parsing for diagnostics/session metadata only.
        Never grants authorization, approval, permission, verification,
        Git authority, or task completion authority.
        """
        events: list[KiloJsonEvent] = []
        for line in raw_output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = __import__("json").loads(line)
                if not isinstance(data, dict):
                    continue
                event = KiloJsonEvent(
                    event_type=str(data.get("type", "")),
                    timestamp=data.get("timestamp"),
                    session_id=data.get("sessionID"),
                    error_name=data.get("error", {}).get("name") if isinstance(data.get("error"), dict) else None,
                    error_data=data.get("error", {}).get("data") if isinstance(data.get("error"), dict) else None,
                    raw=data,
                )
                events.append(event)
            except (__import__("json").JSONDecodeError, ValueError, TypeError):
                continue
        return events


def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    if pid <= 0:
        return False
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = result.stdout.strip()
            if not output or "INFO: No tasks" in output:
                return False
            return str(pid) in output
        else:
            os.kill(pid, 0)
            return True
    except (ProcessLookupError, OSError, FileNotFoundError):
        return False
    except Exception:
        return False


def _generate_short_id() -> str:
    """Generate a short unique ID."""
    import secrets
    return secrets.token_hex(4)
