"""
Autonomous Task Orchestrator.

Central coordination engine for safe, autonomous AI Operator development tasks.

Architectural Invariants Enforced:
  1. TaskStateMachine is the SOLE authority for task state transitions.
  2. WorkspaceManager is the SOLE authority for autonomous workspace & Git operations.
  3. VerificationGate is the SOLE authority for code verification.
  4. DevelopmentAgent is provider-neutral interface (no ToolExecutor, PermissionEngine, secrets).
  5. Checkpoints/commits occur ONLY after explicit VerificationGate PASS.
  6. FAIL, ERROR, or required SKIPPED verification verdicts NEVER allow commits.
  7. Autonomous workspace must NOT be the primary worktree.
  8. Zero raw Git, raw shell, or raw subprocess calls in orchestrator.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.orchestration.agent_interface import DevelopmentAgent
from app.orchestration.exceptions import (
    CheckpointOrchestrationError,
    MaxAttemptsExceededError,
    OrchestrationError,
    StaleTaskStateError,
    TaskNotStartableError,
    VerificationOrchestrationError,
    WorkspaceOrchestrationError,
)
from app.orchestration.models import (
    DevelopmentAgentContext,
    DevelopmentResult,
    DevelopmentResultStatus,
    OrchestrationOptions,
    OrchestrationResult,
)
from app.tasks.exceptions import StaleStateTransitionError, TaskNotFoundError
from app.tasks.models import TaskStatus
from app.verification.models import VerificationContext

if TYPE_CHECKING:
    from app.orchestration.agent_factory import DevelopmentAgentFactory
    from app.repositories.task_repository import TaskRepository
    from app.tasks.state_machine import TaskStateMachine
    from app.verification.gate import VerificationGate
    from app.workspace.workspace_manager import WorkspaceManager

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AutonomousTaskOrchestrator:
    """
    Coordinator for safe, autonomous development task lifecycles.
    """

    def __init__(
        self,
        task_repo: TaskRepository,
        state_machine: TaskStateMachine,
        workspace_manager: WorkspaceManager,
        verification_gate: VerificationGate,
        agent: DevelopmentAgent | None = None,
        agent_factory: DevelopmentAgentFactory | None = None,
    ) -> None:
        if agent is None and agent_factory is None:
            raise ValueError("Either agent or agent_factory must be provided to AutonomousTaskOrchestrator")

        self._task_repo = task_repo
        self._state_machine = state_machine
        self._workspace_manager = workspace_manager
        self._verification_gate = verification_gate
        self._agent = agent
        self._agent_factory = agent_factory

    async def start_task(
        self,
        task_id: str,
        options: OrchestrationOptions | None = None,
        cancel_event: asyncio.Event | None = None,
        abort_event: asyncio.Event | None = None,
    ) -> OrchestrationResult:
        """
        Execute the complete autonomous task lifecycle for task_id.

        Lifecycle:
          PENDING / REPAIR_REQUIRED -> RUNNING -> VERIFYING -> PASSED -> COMMITTED
          or on failure -> REPAIR_REQUIRED / FAILED / CANCELLED / ABORTED
        """
        started_at = _utc_now()
        opts = options or OrchestrationOptions()

        # 1. Fetch task
        task = await self._task_repo.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id=task_id)

        # 2. Check status startability
        if task.status in (TaskStatus.COMMITTED, TaskStatus.CANCELLED):
            raise TaskNotStartableError(
                task_id=task_id,
                status=task.status.value,
                reason=f"Task is in terminal state '{task.status.value}'",
            )
        if task.status == TaskStatus.RUNNING:
            raise TaskNotStartableError(
                task_id=task_id,
                status=task.status.value,
                reason="Task is already RUNNING",
            )

        max_attempts = task.max_attempts or opts.max_attempts

        # 3. Check attempt count limit before start
        if task.attempt_count >= max_attempts:
            if self._state_machine.is_transition_allowed(task.status, TaskStatus.FAILED):
                await self._state_machine.transition(
                    task_id=task_id,
                    expected_status=task.status,
                    new_status=TaskStatus.FAILED,
                    reason=f"Maximum attempt limit reached ({task.attempt_count}/{max_attempts})",
                )
            raise MaxAttemptsExceededError(
                task_id=task_id,
                attempt_count=task.attempt_count,
                max_attempts=max_attempts,
            )

        # 4. Atomically claim execution attempt via a single SQL UPDATE that simultaneously
        #    asserts (status == expected AND attempt_count < max_attempts) and writes
        #    (status = RUNNING, attempt_count += 1).  No crash window exists between the
        #    two mutations.  A losing concurrent worker whose UPDATE matches zero rows
        #    never touches either column.
        try:
            claim_result = await self._state_machine.claim_attempt(
                task_id=task_id,
                expected_status=task.status,
                max_attempts=max_attempts,
            )
        except StaleStateTransitionError:
            # Claim lost — re-fetch to distinguish concurrent-winner from exhausted limit.
            fresh = await self._task_repo.get_task(task_id)
            if fresh is None:
                raise TaskNotFoundError(task_id=task_id)
            if fresh.attempt_count >= max_attempts:
                # Attempt limit was reached (possibly by a concurrent worker).
                # Only transition to FAILED when no other worker already holds execution.
                if fresh.status not in (TaskStatus.RUNNING, TaskStatus.VERIFYING, TaskStatus.PASSED):
                    if self._state_machine.is_transition_allowed(fresh.status, TaskStatus.FAILED):
                        try:
                            await self._state_machine.transition(
                                task_id=task_id,
                                expected_status=fresh.status,
                                new_status=TaskStatus.FAILED,
                                reason=f"Maximum attempt limit reached ({fresh.attempt_count}/{max_attempts})",
                            )
                        except Exception:
                            pass  # Another worker may have already changed state
                raise MaxAttemptsExceededError(
                    task_id=task_id,
                    attempt_count=fresh.attempt_count,
                    max_attempts=max_attempts,
                )
            # Concurrent winner claimed the task; this worker should not proceed.
            raise

        claimed_task = claim_result.task
        current_attempt = claimed_task.attempt_count

        # Commit the claim so other sessions see the RUNNING state immediately.
        try:
            if hasattr(self._task_repo, "_session") and self._task_repo._session:
                await self._task_repo._session.commit()
        except Exception as exc:
            logger.warning("Failed to commit claim for task %s: %s", task_id, exc)

        # 5. Autonomous workspace setup via WorkspaceManager
        base_commit_sha = (
            opts.base_commit_sha
            or claimed_task.metadata.get("base_commit_sha")
            or self._workspace_manager.get_current_head()
        )

        workspace = None
        try:
            workspace = self._workspace_manager.create_workspace(
                task_id=task_id,
                base_commit_sha=base_commit_sha,
                execution_token=claimed_task.execution_token or task_id,
            )
        except Exception as exc:
            err_msg = f"Workspace creation failed: {exc}"
            logger.error("Task %s workspace creation error: %s", task_id, exc)
            await self._state_machine.transition(
                task_id=task_id,
                expected_status=TaskStatus.RUNNING,
                new_status=TaskStatus.FAILED,
                reason=err_msg,
            )
            raise WorkspaceOrchestrationError(task_id=task_id, reason=str(exc))

        await self._task_repo.update_workspace_path(task_id, workspace.autonomous_worktree_path)
        try:
            if hasattr(self._task_repo, "_session") and self._task_repo._session:
                await self._task_repo._session.commit()
        except Exception as exc:
            logger.warning("Failed to commit workspace path for task %s: %s", task_id, exc)

        execution_token = claimed_task.execution_token or task_id

        # Validate workspace safety
        primary_path = self._workspace_manager.get_primary_worktree_path().resolve()
        auto_path = Path(workspace.autonomous_worktree_path).resolve()
        if auto_path == primary_path or primary_path in auto_path.parents:
            err_msg = f"Target workspace path '{auto_path}' is primary worktree"
            await self._state_machine.transition(
                task_id=task_id,
                expected_status=TaskStatus.RUNNING,
                new_status=TaskStatus.FAILED,
                reason=err_msg,
            )
            if opts.auto_cleanup_workspace:
                self._safe_remove_workspace(task_id, workspace, execution_token)
            raise WorkspaceOrchestrationError(task_id=task_id, reason=err_msg)

        # 6. Execute DevelopmentAgent
        await self._task_repo.update_heartbeat(task_id, execution_token=claimed_task.execution_token)
        try:
            if hasattr(self._task_repo, "_session") and self._task_repo._session:
                await self._task_repo._session.commit()
        except Exception as exc:
            logger.warning("Failed to commit heartbeat for task %s: %s", task_id, exc)

        agent_context = DevelopmentAgentContext(
            task_id=task_id,
            project_id=claimed_task.project_id,
            workspace_path=workspace.autonomous_worktree_path,
            repository_root=str(workspace.repository_root),
            title=claimed_task.title,
            description=claimed_task.description,
            attempt_count=current_attempt,
            expected_base_commit=workspace.base_commit_sha,
            metadata=claimed_task.metadata,
            cancel_event=cancel_event,
            abort_event=abort_event,
        )

        try:
            if self._agent is not None:
                agent = self._agent
            elif self._agent_factory is not None:
                agent = await self._agent_factory.create_agent()
            else:
                raise OrchestrationError("No DevelopmentAgent or DevelopmentAgentFactory available")
            agent_result = await agent.execute(agent_context)
        except Exception as exc:
            logger.error("DevelopmentAgent raised exception for task %s: %s", task_id, exc)
            agent_result = DevelopmentResult(
                status=DevelopmentResultStatus.ERROR,
                error_message=str(exc),
            )

        # Check for cancellation/abort requested during agent execution
        if cancel_event is not None and cancel_event.is_set():
            next_status = TaskStatus.CANCELLED
            reason = "Cancellation requested during agent execution"
            try:
                await self._state_machine.transition(
                    task_id=task_id,
                    expected_status=TaskStatus.RUNNING,
                    new_status=next_status,
                    reason=reason,
                )
            except Exception:
                pass
            if opts.auto_cleanup_workspace:
                try:
                    self._safe_remove_workspace(task_id, workspace, execution_token)
                except Exception:
                    pass
            return OrchestrationResult(
                task_id=task_id,
                final_status=next_status,
                attempt_count=current_attempt,
                committed=False,
                agent_result=agent_result,
                error_message=reason,
                started_at=started_at,
                finished_at=_utc_now(),
            )

        if abort_event is not None and abort_event.is_set():
            next_status = TaskStatus.ABORTED
            reason = "Abort requested during agent execution"
            try:
                await self._state_machine.transition(
                    task_id=task_id,
                    expected_status=TaskStatus.RUNNING,
                    new_status=next_status,
                    reason=reason,
                )
            except Exception:
                pass
            if opts.auto_cleanup_workspace:
                try:
                    self._safe_remove_workspace(task_id, workspace, execution_token)
                except Exception:
                    pass
            return OrchestrationResult(
                task_id=task_id,
                final_status=next_status,
                attempt_count=current_attempt,
                committed=False,
                agent_result=agent_result,
                error_message=reason,
                started_at=started_at,
                finished_at=_utc_now(),
            )

        # 8. Handle non-successful agent result
        if not agent_result.is_success:
            if agent_result.status == DevelopmentResultStatus.CANCELLED:
                next_status = TaskStatus.CANCELLED
            else:
                next_status = TaskStatus.FAILED

            reason = agent_result.error_message or agent_result.summary or f"Agent status: {agent_result.status.value}"
            await self._state_machine.transition(
                task_id=task_id,
                expected_status=TaskStatus.RUNNING,
                new_status=next_status,
                reason=reason,
            )
            if opts.auto_cleanup_workspace:
                self._safe_remove_workspace(task_id, workspace, execution_token)

            return OrchestrationResult(
                task_id=task_id,
                final_status=next_status,
                attempt_count=current_attempt,
                committed=False,
                agent_result=agent_result,
                error_message=reason,
                started_at=started_at,
                finished_at=_utc_now(),
            )

        # 9. Transition RUNNING -> VERIFYING via TaskStateMachine
        await self._state_machine.transition(
            task_id=task_id,
            expected_status=TaskStatus.RUNNING,
            new_status=TaskStatus.VERIFYING,
            reason="Development complete; running verification gate",
        )

        # Check for cancellation/abort before verification
        if cancel_event is not None and cancel_event.is_set():
            next_status = TaskStatus.CANCELLED
            reason = "Cancellation requested before verification"
            await self._state_machine.transition(
                task_id=task_id,
                expected_status=TaskStatus.VERIFYING,
                new_status=next_status,
                reason=reason,
            )
            if opts.auto_cleanup_workspace:
                self._safe_remove_workspace(task_id, workspace, execution_token)
            return OrchestrationResult(
                task_id=task_id,
                final_status=next_status,
                attempt_count=current_attempt,
                committed=False,
                agent_result=agent_result,
                error_message=reason,
                started_at=started_at,
                finished_at=_utc_now(),
            )

        if abort_event is not None and abort_event.is_set():
            next_status = TaskStatus.ABORTED
            reason = "Abort requested before verification"
            await self._state_machine.transition(
                task_id=task_id,
                expected_status=TaskStatus.VERIFYING,
                new_status=next_status,
                reason=reason,
            )
            if opts.auto_cleanup_workspace:
                self._safe_remove_workspace(task_id, workspace, execution_token)
            return OrchestrationResult(
                task_id=task_id,
                final_status=next_status,
                attempt_count=current_attempt,
                committed=False,
                agent_result=agent_result,
                error_message=reason,
                started_at=started_at,
                finished_at=_utc_now(),
            )

        # 10. Execute VerificationGate
        current_head = self._workspace_manager.get_current_head(Path(workspace.autonomous_worktree_path))
        verification_context = VerificationContext(
            task_id=task_id,
            workspace_path=workspace.autonomous_worktree_path,
            repository_root=str(workspace.repository_root),
            expected_base_commit=workspace.base_commit_sha,
            current_commit=current_head,
            changed_files=list(agent_result.changed_files),
        )

        try:
            verification_report = self._verification_gate.run(verification_context)
        except Exception as exc:
            logger.error("VerificationGate raised exception for task %s: %s", task_id, exc)
            next_status = TaskStatus.REPAIR_REQUIRED if current_attempt < max_attempts else TaskStatus.FAILED
            await self._state_machine.transition(
                task_id=task_id,
                expected_status=TaskStatus.VERIFYING,
                new_status=next_status,
                reason=f"Verification gate runtime error: {exc}",
            )
            if opts.auto_cleanup_workspace:
                self._safe_remove_workspace(task_id, workspace, execution_token)
            raise VerificationOrchestrationError(task_id=task_id, reason=str(exc))

        # 11. Interpret Verification Report
        if not verification_report.passed:
            next_status = TaskStatus.REPAIR_REQUIRED if current_attempt < max_attempts else TaskStatus.FAILED
            await self._state_machine.transition(
                task_id=task_id,
                expected_status=TaskStatus.VERIFYING,
                new_status=next_status,
                reason="Verification gate verdict: FAIL",
            )
            if opts.auto_cleanup_workspace:
                self._safe_remove_workspace(task_id, workspace, execution_token)

            return OrchestrationResult(
                task_id=task_id,
                final_status=next_status,
                attempt_count=current_attempt,
                committed=False,
                verification_report=verification_report,
                agent_result=agent_result,
                error_message="Verification gate failed",
                started_at=started_at,
                finished_at=_utc_now(),
            )

        # 12. Verification PASSED -> Transition VERIFYING -> PASSED
        await self._state_machine.transition(
            task_id=task_id,
            expected_status=TaskStatus.VERIFYING,
            new_status=TaskStatus.PASSED,
            reason="Verification gate verdict: PASS",
        )

        # Check for cancellation/abort before committing
        if cancel_event is not None and cancel_event.is_set():
            next_status = TaskStatus.CANCELLED
            reason = "Cancellation requested before commit"
            await self._state_machine.transition(
                task_id=task_id,
                expected_status=TaskStatus.PASSED,
                new_status=next_status,
                reason=reason,
            )
            if opts.auto_cleanup_workspace:
                self._safe_remove_workspace(task_id, workspace, execution_token)
            return OrchestrationResult(
                task_id=task_id,
                final_status=next_status,
                attempt_count=current_attempt,
                committed=False,
                verification_report=verification_report,
                agent_result=agent_result,
                error_message=reason,
                started_at=started_at,
                finished_at=_utc_now(),
            )

        if abort_event is not None and abort_event.is_set():
            next_status = TaskStatus.ABORTED
            reason = "Abort requested before commit"
            await self._state_machine.transition(
                task_id=task_id,
                expected_status=TaskStatus.PASSED,
                new_status=next_status,
                reason=reason,
            )
            if opts.auto_cleanup_workspace:
                self._safe_remove_workspace(task_id, workspace, execution_token)
            return OrchestrationResult(
                task_id=task_id,
                final_status=next_status,
                attempt_count=current_attempt,
                committed=False,
                verification_report=verification_report,
                agent_result=agent_result,
                error_message=reason,
                started_at=started_at,
                finished_at=_utc_now(),
            )

        # 13. Create Git Checkpoint via WorkspaceManager
        intended_paths = list(agent_result.changed_files) if agent_result.changed_files else ["."]
        try:
            cp_info = self._workspace_manager.create_checkpoint(
                task_id=task_id,
                workspace=workspace,
                intended_paths=intended_paths,
                message=f"feat({task_id}): {claimed_task.title}",
            )
        except Exception as exc:
            logger.error("Checkpoint creation failed for task %s: %s", task_id, exc)
            await self._state_machine.transition(
                task_id=task_id,
                expected_status=TaskStatus.PASSED,
                new_status=TaskStatus.FAILED,
                reason=f"Git checkpoint creation failed: {exc}",
            )
            if opts.auto_cleanup_workspace:
                self._safe_remove_workspace(task_id, workspace, execution_token)
            return OrchestrationResult(
                task_id=task_id,
                final_status=TaskStatus.FAILED,
                attempt_count=current_attempt,
                committed=False,
                verification_report=verification_report,
                agent_result=agent_result,
                error_message=f"Git checkpoint failed: {exc}",
                started_at=started_at,
                finished_at=_utc_now(),
            )

        if not cp_info or not cp_info.commit_sha:
            err_msg = "WorkspaceManager returned empty commit SHA"
            await self._state_machine.transition(
                task_id=task_id,
                expected_status=TaskStatus.PASSED,
                new_status=TaskStatus.FAILED,
                reason=err_msg,
            )
            if opts.auto_cleanup_workspace:
                self._safe_remove_workspace(task_id, workspace, execution_token)
            return OrchestrationResult(
                task_id=task_id,
                final_status=TaskStatus.FAILED,
                attempt_count=current_attempt,
                committed=False,
                verification_report=verification_report,
                agent_result=agent_result,
                error_message=err_msg,
                started_at=started_at,
                finished_at=_utc_now(),
            )

        # 14. Transition PASSED -> COMMITTED via TaskStateMachine
        await self._state_machine.transition(
            task_id=task_id,
            expected_status=TaskStatus.PASSED,
            new_status=TaskStatus.COMMITTED,
            reason=f"Successfully committed checkpoint SHA {cp_info.commit_sha}",
        )

        # 15. Cleanup workspace & return success
        if opts.auto_cleanup_workspace:
            self._safe_remove_workspace(task_id, workspace, execution_token)

        return OrchestrationResult(
            task_id=task_id,
            final_status=TaskStatus.COMMITTED,
            attempt_count=current_attempt,
            committed=True,
            checkpoint_sha=cp_info.commit_sha,
            verification_report=verification_report,
            agent_result=agent_result,
            started_at=started_at,
            finished_at=_utc_now(),
        )

    async def cancel_task(
        self,
        task_id: str,
        reason: str = "User requested cancellation",
    ) -> OrchestrationResult:
        """
        Cancel a task if it is in a cancelable state.
        """
        task = await self._task_repo.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id=task_id)

        if task.status in (TaskStatus.COMMITTED, TaskStatus.CANCELLED):
            return OrchestrationResult(
                task_id=task_id,
                final_status=task.status,
                attempt_count=task.attempt_count,
                committed=(task.status == TaskStatus.COMMITTED),
                error_message=f"Task is already in terminal state '{task.status.value}'",
            )

        if self._state_machine.is_transition_allowed(task.status, TaskStatus.CANCELLED):
            await self._state_machine.transition(
                task_id=task_id,
                expected_status=task.status,
                new_status=TaskStatus.CANCELLED,
                reason=reason,
            )

        return OrchestrationResult(
            task_id=task_id,
            final_status=TaskStatus.CANCELLED,
            attempt_count=task.attempt_count,
            committed=False,
            error_message=reason,
        )

    async def abort_task(
        self,
        task_id: str,
        reason: str = "Emergency system abort",
    ) -> OrchestrationResult:
        """
        Abort an active task due to emergency or policy intervention.
        """
        task = await self._task_repo.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id=task_id)

        if task.status in (TaskStatus.COMMITTED, TaskStatus.ABORTED, TaskStatus.CANCELLED):
            return OrchestrationResult(
                task_id=task_id,
                final_status=task.status,
                attempt_count=task.attempt_count,
                committed=(task.status == TaskStatus.COMMITTED),
                error_message=f"Task is already in state '{task.status.value}'",
            )

        if self._state_machine.is_transition_allowed(task.status, TaskStatus.ABORTED):
            await self._state_machine.transition(
                task_id=task_id,
                expected_status=task.status,
                new_status=TaskStatus.ABORTED,
                reason=reason,
            )

        return OrchestrationResult(
            task_id=task_id,
            final_status=TaskStatus.ABORTED,
            attempt_count=task.attempt_count,
            committed=False,
            error_message=reason,
        )

    def _safe_remove_workspace(self, task_id: str, workspace: Any, execution_token: str | None = None) -> None:
        """Safely remove autonomous workspace, swallowing cleanup errors so they do not alter task status."""
        try:
            self._workspace_manager.remove_workspace(
                task_id,
                workspace,
                expected_execution_token=execution_token,
            )
        except Exception as exc:
            logger.warning("Failed to clean up workspace for task %s: %s", task_id, exc)
