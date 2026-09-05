"""
Crash & Timeout Recovery Manager.

Detects and safely recovers orphaned task executions caused by orchestrator/process crashes
or execution timeouts.

Architectural Invariants Enforced:
  1. TaskStateMachine is the SOLE authority for task state transitions (RUNNING -> REPAIR_REQUIRED / FAILED).
  2. WorkspaceManager is the SOLE authority for autonomous workspace inspection and cleanup.
  3. Atomic DB-level recovery claims via TaskRepository (no process-local locks).
  4. Fail-closed: CANCELLED, ABORTED, and COMMITTED tasks are strictly protected against resurrection.
  5. Recovery attempt limits are enforced to prevent infinite recovery loops.
  6. Zero imports of unauthorized execution layers, permission engines, or external process calls.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import math
from typing import TYPE_CHECKING
import uuid

from app.orchestration.models import RecoveryResult, RecoveryStatus
from app.tasks.exceptions import StaleStateTransitionError, TaskNotFoundError
from app.tasks.models import TaskStatus, TaskTransitionResult

if TYPE_CHECKING:
    from app.repositories.task_repository import TaskRepository
    from app.tasks.state_machine import TaskStateMachine
    from app.workspace.workspace_manager import WorkspaceManager

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class RecoveryManager:
    """
    Manager for detecting and recovering orphaned or timed-out task executions.
    """

    def __init__(
        self,
        task_repo: TaskRepository,
        state_machine: TaskStateMachine,
        workspace_manager: WorkspaceManager | None = None,
        timeout_seconds: float = 300.0,
        recovery_lease_seconds: float = 60.0,
        default_max_recovery_attempts: int = 3,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be a positive finite float, got {timeout_seconds!r}")
        if not math.isfinite(recovery_lease_seconds) or recovery_lease_seconds <= 0:
            raise ValueError(f"recovery_lease_seconds must be a positive finite float, got {recovery_lease_seconds!r}")
        if default_max_recovery_attempts < 1:
            raise ValueError(f"default_max_recovery_attempts must be >= 1, got {default_max_recovery_attempts!r}")

        self._task_repo = task_repo
        self._state_machine = state_machine
        self._workspace_manager = workspace_manager
        self._timeout_seconds = float(timeout_seconds)
        self._recovery_lease_seconds = float(recovery_lease_seconds)
        self._default_max_recovery_attempts = default_max_recovery_attempts

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    @property
    def recovery_lease_seconds(self) -> float:
        return self._recovery_lease_seconds

    @property
    def default_max_recovery_attempts(self) -> int:
        return self._default_max_recovery_attempts

    async def send_heartbeat(self, task_id: str, execution_token: str | None = None) -> bool:
        """
        Record a durable activity heartbeat for a RUNNING task.

        Returns:
            True if heartbeat was updated successfully.
            False if task is not found, not in RUNNING status, or active recovery claim exists.
        """
        _, updated = await self._task_repo.update_heartbeat(
            task_id,
            execution_token=execution_token,
            recovery_lease_seconds=self._recovery_lease_seconds,
        )
        return updated

    async def recover_task(
        self,
        task_id: str,
        recovery_owner: str | None = None,
        now: datetime | None = None,
    ) -> RecoveryResult:
        """
        Attempt to safely recover a specific task if it is stale or orphaned.
        """
        current_time = _ensure_utc(now) or _utc_now()

        # 1. Fetch task
        task = await self._task_repo.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id=task_id)

        # 2. Guard terminal / protected states against resurrection
        if task.status == TaskStatus.CANCELLED:
            return RecoveryResult(
                task_id=task_id,
                status=RecoveryStatus.CANCELLED,
                previous_task_status=TaskStatus.CANCELLED,
                recovery_owner=task.recovery_owner,
                recovery_attempt_count=task.recovery_attempt_count,
                message="Task is CANCELLED; recovery refused.",
                timestamp=current_time,
            )

        if task.status == TaskStatus.ABORTED:
            return RecoveryResult(
                task_id=task_id,
                status=RecoveryStatus.ABORTED,
                previous_task_status=TaskStatus.ABORTED,
                recovery_owner=task.recovery_owner,
                recovery_attempt_count=task.recovery_attempt_count,
                message="Task is ABORTED; recovery refused.",
                timestamp=current_time,
            )

        if task.status != TaskStatus.RUNNING:
            return RecoveryResult(
                task_id=task_id,
                status=RecoveryStatus.NOT_ELIGIBLE,
                previous_task_status=task.status,
                recovery_owner=task.recovery_owner,
                recovery_attempt_count=task.recovery_attempt_count,
                message=f"Task is in status '{task.status.value}' and not eligible for recovery.",
                timestamp=current_time,
            )

        # 3. Staleness check
        stale_threshold = current_time - timedelta(seconds=self._timeout_seconds)
        last_hb = _ensure_utc(task.last_heartbeat)
        if last_hb is not None and last_hb >= stale_threshold:
            return RecoveryResult(
                task_id=task_id,
                status=RecoveryStatus.NOT_STALE,
                previous_task_status=TaskStatus.RUNNING,
                recovery_owner=task.recovery_owner,
                recovery_attempt_count=task.recovery_attempt_count,
                message="Task heartbeat is fresh; task is actively running.",
                timestamp=current_time,
            )

        # 4. Check recovery attempt limit prior to claim
        max_rec_attempts = task.max_recovery_attempts or self._default_max_recovery_attempts
        if task.recovery_attempt_count >= max_rec_attempts:
            # Transition RUNNING -> FAILED since recovery limit is exhausted
            if self._state_machine.is_transition_allowed(TaskStatus.RUNNING, TaskStatus.FAILED):
                try:
                    await self._state_machine.transition(
                        task_id=task_id,
                        expected_status=TaskStatus.RUNNING,
                        new_status=TaskStatus.FAILED,
                        reason=f"Recovery attempt limit exhausted ({task.recovery_attempt_count}/{max_rec_attempts})",
                    )
                except Exception as exc:
                    logger.warning("Failed to transition task %s to FAILED on recovery limit: %s", task_id, exc)

            return RecoveryResult(
                task_id=task_id,
                status=RecoveryStatus.RECOVERY_LIMIT_REACHED,
                previous_task_status=TaskStatus.RUNNING,
                new_task_status=TaskStatus.FAILED,
                recovery_owner=task.recovery_owner,
                recovery_attempt_count=task.recovery_attempt_count,
                message=f"Maximum recovery attempts reached ({task.recovery_attempt_count}/{max_rec_attempts}).",
                timestamp=current_time,
            )

        # 5. Atomic recovery claim query
        owner = recovery_owner or f"recovery_worker_{uuid.uuid4().hex[:8]}"
        claimed_task, won = await self._task_repo.claim_task_recovery(
            task_id=task_id,
            recovery_owner=owner,
            stale_before=stale_threshold,
            max_recovery_attempts=max_rec_attempts,
            recovery_lease_seconds=self._recovery_lease_seconds,
            now=current_time,
        )

        if not won:
            # Re-fetch task to determine precise reason claim was lost
            fresh = await self._task_repo.get_task(task_id)
            if fresh is None:
                raise TaskNotFoundError(task_id=task_id)

            if fresh.status == TaskStatus.CANCELLED:
                return RecoveryResult(
                    task_id=task_id,
                    status=RecoveryStatus.CANCELLED,
                    previous_task_status=TaskStatus.CANCELLED,
                    message="Recovery claim lost; task became CANCELLED.",
                    timestamp=current_time,
                )
            if fresh.status == TaskStatus.ABORTED:
                return RecoveryResult(
                    task_id=task_id,
                    status=RecoveryStatus.ABORTED,
                    previous_task_status=TaskStatus.ABORTED,
                    message="Recovery claim lost; task became ABORTED.",
                    timestamp=current_time,
                )
            if fresh.recovery_attempt_count >= max_rec_attempts:
                return RecoveryResult(
                    task_id=task_id,
                    status=RecoveryStatus.RECOVERY_LIMIT_REACHED,
                    previous_task_status=fresh.status,
                    recovery_owner=fresh.recovery_owner,
                    recovery_attempt_count=fresh.recovery_attempt_count,
                    message=f"Recovery claim lost; attempt limit reached ({fresh.recovery_attempt_count}/{max_rec_attempts}).",
                    timestamp=current_time,
                )

            return RecoveryResult(
                task_id=task_id,
                status=RecoveryStatus.ALREADY_RECOVERED,
                previous_task_status=fresh.status,
                recovery_owner=fresh.recovery_owner,
                recovery_attempt_count=fresh.recovery_attempt_count,
                message=f"Recovery claim lost; worker '{fresh.recovery_owner}' or state change won.",
                timestamp=current_time,
            )

        # 6. Execute lease-guarded state machine transition for claimed recovery
        if claimed_task.attempt_count < claimed_task.max_attempts:
            target_status = TaskStatus.REPAIR_REQUIRED
            reason = f"Execution timeout / crash recovered by {owner}; marked for retry (attempt {claimed_task.attempt_count}/{claimed_task.max_attempts})"
        else:
            target_status = TaskStatus.FAILED
            reason = f"Execution timeout / crash recovered by {owner}; max execution attempts reached ({claimed_task.attempt_count}/{claimed_task.max_attempts})"

        try:
            transition_res = await self._state_machine.transition_recovery(
                task_id=task_id,
                expected_recovery_owner=owner,
                expected_last_recovery_at=claimed_task.last_recovery_at,  # type: ignore[arg-type]
                new_status=target_status,
                reason=reason,
            )
        except StaleStateTransitionError:
            # Lease expired, another worker took over, or task was cancelled/aborted
            fresh = await self._task_repo.get_task(task_id)
            status_enum = fresh.status if fresh else TaskStatus.RUNNING
            res_status = RecoveryStatus.ALREADY_RECOVERED
            if status_enum == TaskStatus.CANCELLED:
                res_status = RecoveryStatus.CANCELLED
            elif status_enum == TaskStatus.ABORTED:
                res_status = RecoveryStatus.ABORTED

            return RecoveryResult(
                task_id=task_id,
                status=res_status,
                previous_task_status=status_enum,
                recovery_owner=owner,
                recovery_attempt_count=claimed_task.recovery_attempt_count,
                message=f"Recovery transition failed CAS validation: task status is {status_enum.value}",
                timestamp=current_time,
            )

        # 7. Safe Workspace Cleanup
        if self._workspace_manager is not None:
            self._safe_cleanup_workspace(task_id)

        return RecoveryResult(
            task_id=task_id,
            status=RecoveryStatus.RECOVERY_COMPLETED,
            previous_task_status=TaskStatus.RUNNING,
            new_task_status=target_status,
            recovery_owner=owner,
            recovery_attempt_count=claimed_task.recovery_attempt_count,
            message=reason,
            timestamp=current_time,
        )

    async def scan_and_recover_stale_tasks(
        self,
        recovery_owner: str | None = None,
        limit: int = 50,
        now: datetime | None = None,
    ) -> list[RecoveryResult]:
        """
        Scan the database for stale RUNNING tasks and execute recovery on each.
        """
        current_time = now or _utc_now()
        stale_threshold = current_time - timedelta(seconds=self._timeout_seconds)

        stale_tasks = await self._task_repo.list_stale_running_tasks(
            stale_before=stale_threshold,
            limit=limit,
        )

        results: list[RecoveryResult] = []
        for task in stale_tasks:
            try:
                res = await self.recover_task(
                    task_id=task.task_id,
                    recovery_owner=recovery_owner,
                    now=current_time,
                )
                results.append(res)
            except Exception as exc:
                logger.error("Error recovering stale task %s: %s", task.task_id, exc)
                results.append(
                    RecoveryResult(
                        task_id=task.task_id,
                        status=RecoveryStatus.RECOVERY_FAILED,
                        previous_task_status=task.status,
                        message=f"Recovery failed with exception: {exc}",
                        timestamp=current_time,
                    )
                )

        return results

    def _safe_cleanup_workspace(self, task_id: str) -> None:
        """
        Best-effort workspace cleanup for an orphaned task.

        SAFETY INVARIANT:
          Does NOT modify or touch the primary worktree.
          Fails silently on missing workspace or cleanup errors.
        """
        if self._workspace_manager is None:
            return

        try:
            # Derives autonomous worktree path for task_id
            base_dir = self._workspace_manager.get_worktree_base_dir()
            target_path = base_dir / task_id
            if target_path.exists():
                # Construct temporary WorkspaceInfo for safe removal via WorkspaceManager
                from app.workspace.models import WorkspaceInfo, WorkspaceStatus
                dummy_info = WorkspaceInfo(
                    task_id=task_id,
                    repository_root=str(self._workspace_manager.get_repository_root()),
                    primary_worktree_path=str(self._workspace_manager.get_primary_worktree_path()),
                    autonomous_worktree_path=str(target_path),
                    branch_name=f"ai-operator/task/{task_id}",
                    base_commit_sha="0000000000000000000000000000000000000000",
                    created_at=_utc_now(),
                    current_commit_sha="0000000000000000000000000000000000000000",
                    status=WorkspaceStatus.DIRTY,
                )
                self._workspace_manager.remove_workspace(task_id, dummy_info, force=True)
        except Exception as exc:
            logger.warning("RecoveryManager safe workspace cleanup for task %s ignored exception: %s", task_id, exc)
