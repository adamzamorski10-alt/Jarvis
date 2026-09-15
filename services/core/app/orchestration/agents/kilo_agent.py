"""
Kilo Development Agent — subprocess-backed DevelopmentAgent implementation.

Wraps the Kilo Code CLI as a managed subprocess within the application's
authorized workspace. Application services remain authoritative for all
security-sensitive state.

SECURITY:
- Does NOT own task state, permissions, approvals, verification, or Git.
- Does NOT perform shell execution or filesystem mutations directly.
- Kilo output is treated as untrusted.
- Changed files are derived from authoritative filesystem state, not Kilo claims.
- Session IDs from Kilo are untrusted external metadata.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from app.orchestration.agent_interface import DevelopmentAgent
from app.orchestration.agents.kilo_process import (
    KiloLifecycleState,
    KiloProcessError,
    KiloProcessResult,
    build_kilo_args,
    start_kilo_process,
    wait_kilo_process,
)
from app.orchestration.kilo_session_manager import KiloSessionManager
from app.orchestration.kilo_session_models import KiloSessionRecord, KiloSessionState
from app.orchestration.models import (
    DevelopmentAgentContext,
    DevelopmentResult,
    DevelopmentResultStatus,
)

logger = logging.getLogger(__name__)


class KiloDevelopmentAgent(DevelopmentAgent):
    """
    Minimal DevelopmentAgent that delegates to Kilo Code CLI subprocess.

    Kilo is NOT a CodingProvider. This adapter:
    - launches kilo.exe with --dir constrained to the authorized workspace
    - monitors process completion
    - derives changed files from filesystem state, not Kilo claims
    - never trusts Kilo output for security decisions
    - tracks application-owned Kilo sessions for recovery
    """

    def __init__(
        self,
        *,
        kilo_executable_path: str,
        default_model: str | None = None,
        pure_mode: bool = True,
        kill_timeout_seconds: float = 15.0,
        kilo_session_manager: KiloSessionManager | None = None,
    ) -> None:
        if not kilo_executable_path or not isinstance(kilo_executable_path, str):
            raise ValueError("kilo_executable_path must be a non-empty string")
        self._kilo_executable = kilo_executable_path
        self._default_model = default_model
        self._pure_mode = pure_mode
        self._kill_timeout_seconds = kill_timeout_seconds
        self._kilo_session_manager = kilo_session_manager

    async def execute(self, context: DevelopmentAgentContext) -> DevelopmentResult:
        workspace = Path(context.workspace_path).resolve()
        if not workspace.is_dir():
            return DevelopmentResult(
                status=DevelopmentResultStatus.ERROR,
                error_message=f"Workspace does not exist or is not a directory: {workspace}",
            )

        task_description = context.description or context.title
        if not task_description:
            return DevelopmentResult(
                status=DevelopmentResultStatus.ERROR,
                error_message="Task description is empty",
            )

        # Enforce Kilo session recovery eligibility before creating a new session
        if self._kilo_session_manager is not None:
            execution_id = f"exec_{context.task_id}_{context.attempt_count}"
            eligibility = await self._kilo_session_manager.enforce_recovery_before_execution(
                task_id=context.task_id,
                project_id=context.project_id,
                workspace_path=str(workspace),
                execution_id=execution_id,
                authorization_version=self._compute_auth_version(context),
                approval_scope_hash=self._compute_approval_hash(context),
            )
            if eligibility is not None and not eligibility.eligible:
                return DevelopmentResult(
                    status=DevelopmentResultStatus.ERROR,
                    error_message=f"Kilo session recovery not eligible: {eligibility.reason}",
                )

        # Create application-owned session record
        app_session: KiloSessionRecord | None = None
        if self._kilo_session_manager is not None:
            try:
                app_session = await self._kilo_session_manager.create_session(
                    task_id=context.task_id,
                    project_id=context.project_id,
                    workspace_path=str(workspace),
                    execution_id=f"exec_{context.task_id}_{context.attempt_count}",
                    authorization_version=self._compute_auth_version(context),
                    approval_scope_hash=self._compute_approval_hash(context),
                )
            except Exception as exc:
                logger.warning("Failed to create Kilo session record: %s", exc)

        before_snapshot = self._snapshot_workspace(workspace)

        try:
            args = build_kilo_args(
                executable=self._kilo_executable,
                workspace_path=str(workspace),
                task_description=task_description,
                pure=self._pure_mode,
                json_format=True,
                model=self._default_model,
            )
        except KiloProcessError as exc:
            if app_session and self._kilo_session_manager:
                await self._kilo_session_manager.complete_session(
                    app_session.app_session_id,
                    KiloSessionState.FAILED,
                    error_message=f"Failed to build Kilo invocation: {exc.reason}",
                )
            return DevelopmentResult(
                status=DevelopmentResultStatus.ERROR,
                error_message=f"Failed to build Kilo invocation: {exc.reason}",
            )

        proc = None
        kilo_session_id = None
        try:
            proc = await start_kilo_process(args)
            if proc.pid and app_session and self._kilo_session_manager:
                await self._kilo_session_manager.activate_session(
                    app_session.app_session_id,
                    kilo_session_id=None,
                    pid=proc.pid,
                )
        except KiloProcessError as exc:
            if app_session and self._kilo_session_manager:
                await self._kilo_session_manager.complete_session(
                    app_session.app_session_id,
                    KiloSessionState.FAILED,
                    error_message=f"Failed to start Kilo: {exc.reason}",
                )
            return DevelopmentResult(
                status=DevelopmentResultStatus.ERROR,
                error_message=f"Failed to start Kilo: {exc.reason}",
            )

        try:
            process_result = await wait_kilo_process(
                proc,
                cancel_event=context.cancel_event,
                kill_timeout=self._kill_timeout_seconds,
            )
        except asyncio.CancelledError:
            if proc is not None and proc.returncode is None:
                proc.kill()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(proc.wait()), timeout=self._kill_timeout_seconds
                    )
                except asyncio.TimeoutError:
                    pass
            raise
        finally:
            if proc is not None and proc.returncode is None:
                try:
                    proc.kill()
                    await asyncio.wait_for(
                        asyncio.shield(proc.wait()), timeout=2.0
                    )
                except (asyncio.TimeoutError, ProcessLookupError, OSError):
                    pass

        # Parse JSON events for session metadata (diagnostics only)
        if process_result.stdout_text and app_session and self._kilo_session_manager:
            try:
                events = KiloSessionManager.parse_kilo_json_events(process_result.stdout_text)
                for event in events:
                    if event.session_id and kilo_session_id is None:
                        kilo_session_id = event.session_id
                    if event.event_type == "session" and event.session_id:
                        kilo_session_id = event.session_id
            except Exception as exc:
                logger.debug("JSON event parsing failed: %s", exc)

        # Update session with Kilo session ID if discovered
        if kilo_session_id and app_session and self._kilo_session_manager:
            try:
                await self._kilo_session_manager.update_session_kilo_id(
                    app_session.app_session_id,
                    kilo_session_id=kilo_session_id,
                    kilo_session_state="session",
                )
            except Exception as exc:
                logger.debug("Session update failed: %s", exc)

        after_snapshot = self._snapshot_workspace(workspace)
        changed_files = self._diff_snapshots(before_snapshot, after_snapshot)

        # Determine terminal state
        if process_result.cancelled:
            terminal_state = KiloSessionState.INTERRUPTED
            if app_session and self._kilo_session_manager:
                await self._kilo_session_manager.complete_session(
                    app_session.app_session_id,
                    terminal_state,
                    error_message="Cancellation requested",
                )
            return DevelopmentResult(
                status=DevelopmentResultStatus.CANCELLED,
                summary="Kilo process was cancelled",
                error_message="Cancellation requested",
                changed_files=changed_files,
                details=self._safe_details(process_result),
            )

        if process_result.returncode != 0:
            terminal_state = KiloSessionState.FAILED
            if app_session and self._kilo_session_manager:
                await self._kilo_session_manager.complete_session(
                    app_session.app_session_id,
                    terminal_state,
                    error_message=process_result.stderr_text[-1024:] if process_result.stderr_text else f"Exit code {process_result.returncode}",
                )
            return DevelopmentResult(
                status=DevelopmentResultStatus.FAILURE,
                summary="Kilo process exited with non-zero status",
                error_message=(
                    process_result.stderr_text[-1024:]
                    if process_result.stderr_text
                    else f"Exit code {process_result.returncode}"
                ),
                changed_files=changed_files,
                details=self._safe_details(process_result),
            )

        terminal_state = KiloSessionState.COMPLETED
        if app_session and self._kilo_session_manager:
            await self._kilo_session_manager.complete_session(
                app_session.app_session_id,
                terminal_state,
            )
        return DevelopmentResult(
            status=DevelopmentResultStatus.SUCCESS,
            summary="Kilo process completed",
            changed_files=changed_files,
            details=self._safe_details(process_result),
        )

    @staticmethod
    def _compute_auth_version(context: DevelopmentAgentContext) -> str | None:
        """Compute a fingerprint of the authorization context for this execution."""
        parts = [context.task_id, context.project_id, context.workspace_path]
        if context.metadata:
            auth_info = context.metadata.get("authorization")
            if auth_info:
                parts.append(str(auth_info))
        import hashlib
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @staticmethod
    def _compute_approval_hash(context: DevelopmentAgentContext) -> str | None:
        """Compute a fingerprint of the approval scope for this execution."""
        if context.previous_verification_result is not None:
            import hashlib
            raw = f"{context.task_id}:{context.attempt_count}:{context.previous_verification_result.gate_decision}"
            return hashlib.sha256(raw.encode()).hexdigest()[:16]
        return None

    def _snapshot_workspace(
        self, workspace: Path
    ) -> dict[str, tuple[float, int]]:
        """Snapshot current workspace file mtimes and sizes."""
        snapshot: dict[str, tuple[float, int]] = {}
        try:
            for item in workspace.rglob("*"):
                if item.is_file() and not self._is_hidden(item, workspace):
                    try:
                        stat = item.stat()
                        snapshot[item.relative_to(workspace).as_posix()] = (
                            stat.st_mtime,
                            stat.st_size,
                        )
                    except OSError:
                        pass
        except OSError:
            pass
        return snapshot

    def _diff_snapshots(
        self,
        before: dict[str, tuple[float, int]],
        after: dict[str, tuple[float, int]],
    ) -> tuple[str, ...]:
        """Return files that are new or modified between snapshots."""
        changed: list[str] = []
        all_files = set(before) | set(after)
        for f in sorted(all_files):
            if f not in before:
                changed.append(f)
            elif f in after:
                b_mtime, b_size = before[f]
                a_mtime, a_size = after[f]
                if b_mtime != a_mtime or b_size != a_size:
                    changed.append(f)
        return tuple(changed)

    def _is_hidden(self, path: Path, workspace: Path) -> bool:
        """Return True if path is hidden (starts with .) relative to workspace."""
        try:
            rel = path.relative_to(workspace)
            return any(part.startswith(".") for part in rel.parts)
        except ValueError:
            return True

    def _safe_details(self, result: KiloProcessResult) -> dict[str, Any]:
        """Build bounded details dict from process result."""
        details: dict[str, Any] = {
            "returncode": result.returncode,
            "cancelled": result.cancelled,
            "termination_confirmed": result.termination_confirmed,
            "lifecycle_state": result.lifecycle_state,
            "started_at": result.started_at,
            "terminated_at": result.terminated_at,
            "duration_seconds": result.duration_seconds,
        }

        stdout = result.stdout_text or ""
        stderr = result.stderr_text or ""

        max_output = 4096
        if len(stdout) > max_output:
            stdout = (
                stdout[:max_output]
                + f"\n... [truncated {len(stdout) - max_output} chars]"
            )
        if len(stderr) > max_output:
            stderr = (
                stderr[:max_output]
                + f"\n... [truncated {len(stderr) - max_output} chars]"
            )

        details["stdout_tail"] = stdout[-2048:]
        details["stderr_tail"] = stderr[-2048:]

        return details
