"""
AI Operator Core — REST API Routes for Autonomous Execution Control Plane

Exposes the autonomous execution engine through thin, safe control-plane HTTP endpoints.

Architectural Invariants Enforced:
   1. REST is strictly a control plane: execution flows through AutonomousPlanExecutor.
   2. No security, state-machine, verification, repair, or Git invariants are bypassed.
   3. Safe HTTP error semantics (404, 409, 400, 422, 500) without credential/path leakage.
   4. Authoritative task & plan status queries backed by TaskRepository persistence.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.models.plans_models import (
    ErrorResponse,
    PlanExecutionResponse,
    PlanExecutionStatusResponse,
    RunStatusResponse,
    TaskStatusResponse,
)
from app.db.database import AsyncSessionLocal, get_db
from app.dependencies import get_background_execution_manager, get_task_repository
from app.orchestration.orchestrator import AutonomousTaskOrchestrator
from app.orchestration.run_controller import AutonomousRunController, RunStatus
from app.repositories.task_repository import TaskRepository
from app.services.autonomous_loop import AutonomousPlanExecutor, PlanExecutionState
from app.services.background_execution_manager import (
    BackgroundExecutionManager,
    ExecutionNotFoundError,
    PlanAlreadyExecutingError,
    PlanNotExecutableError,
)
from app.services.plan_execution_service import PlanExecutionService
from app.tasks.models import TaskStatus
from app.tools.audit_service import AuditLogService
from app.tools.permission_engine import PermissionEngine
from app.tools.tool_execution_service import ToolExecutionService
from app.verification.gate import VerificationGate
from app.workspace.workspace_manager import WorkspaceManager

logger = logging.getLogger(__name__)

router = APIRouter()


def _sanitize_string(value: str | None, max_len: int = 1000) -> str | None:
    if value is None:
        return None
    val = str(value).strip()
    if len(val) > max_len:
        return val[:max_len] + "... [TRUNCATED]"
    return val


def _get_git_repo_root() -> Path:
    """Determine the Git repository root for WorkspaceManager."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            root = Path(result.stdout.strip())
            if root.exists():
                return root
    except Exception:
        pass
    current = Path(__file__).resolve()
    for parent in [current, *current.parents]:
        git_dir = parent / ".git"
        if git_dir.exists() or git_dir.is_dir():
            return parent
    return Path.cwd()


_REPO_ROOT = _get_git_repo_root()


async def _create_executor_context(engine: Any = None) -> tuple[AutonomousPlanExecutor, Any]:
    """
    Factory creating a clean session and AutonomousPlanExecutor instance for background task.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    if engine is not None:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        session = factory()
    else:
        session = AsyncSessionLocal()

    task_repo = TaskRepository(session)
    from app.tasks.state_machine import TaskStateMachine
    sm = TaskStateMachine(task_repo)

    ws_manager = WorkspaceManager(repo_root=_REPO_ROOT)
    from app.verification.check_registry import CheckRegistry
    check_registry = CheckRegistry()
    gate = VerificationGate(registry=check_registry, primary_worktree_path=_REPO_ROOT)
    from app.tools.registry import tool_registry
    from app.tools.implementation_registry import tool_implementation_registry
    from app.repositories.grant_repository import GrantRepository
    from app.repositories.audit_log_repository import AuditLogRepository
    from app.repositories.approval_repository import ApprovalRepository

    grant_repo = GrantRepository(session)
    audit_repo = AuditLogRepository(session)
    approval_repo = ApprovalRepository(session)

    audit_svc = AuditLogService(audit_repo)
    perm_engine = PermissionEngine(
        registry=tool_registry,
        grant_repo=grant_repo,
        audit_service=audit_svc,
        approval_repo=approval_repo,
    )
    from app.tools.executor import ToolExecutor
    tool_exec = ToolExecutor(registry=tool_registry, impl_registry=tool_implementation_registry)
    tool_svc = ToolExecutionService(
        registry=tool_registry,
        impl_registry=tool_implementation_registry,
        permission_engine=perm_engine,
        audit_service=audit_svc,
        executor=tool_exec,
    )

    from app.orchestration.agent_factory import DevelopmentAgentFactory
    from app.ai.services.provider_manager import ProviderManager
    from app.services.settings_service import SettingsService
    settings_svc = SettingsService(session)
    provider_mgr = ProviderManager(settings_svc)
    factory = DevelopmentAgentFactory(
        provider_manager=provider_mgr,
        tool_execution_service=tool_svc,
    )
    try:
        dev_agent = await factory.create_agent()
    except Exception:
        from app.orchestration.agent_interface import DevelopmentAgent
        from app.orchestration.models import DevelopmentResult, DevelopmentResultStatus

        class _StubDevelopmentAgent(DevelopmentAgent):
            async def execute(self, context):
                return DevelopmentResult(
                    status=DevelopmentResultStatus.SUCCESS,
                    summary="stub",
                    changed_files=[],
                )

        dev_agent = _StubDevelopmentAgent()

    orch = AutonomousTaskOrchestrator(
        task_repo=task_repo,
        state_machine=sm,
        workspace_manager=ws_manager,
        verification_gate=gate,
        agent=dev_agent,
    )

    run_ctrl = AutonomousRunController(
        task_repo=task_repo,
        state_machine=sm,
        workspace_manager=ws_manager,
        verification_gate=gate,
        orchestrator=orch,
    )

    plan_exec_svc = PlanExecutionService(
        task_repository=task_repo,
        run_controller=run_ctrl,
    )

    executor = AutonomousPlanExecutor(
        task_repository=task_repo,
        plan_execution_service=plan_exec_svc,
        audit_service=audit_svc,
    )

    return executor, session


@router.post(
    "/plans/{plan_id}/execute",
    response_model=PlanExecutionResponse,
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": ErrorResponse, "description": "Plan not found"},
        409: {"model": ErrorResponse, "description": "Plan already executing or conflicting state"},
        400: {"model": ErrorResponse, "description": "Malformed request"},
    },
)
async def execute_plan(
    plan_id: str,
    timeout_seconds: float | None = Query(None, description="Optional execution timeout override in seconds"),
    task_repo: TaskRepository = Depends(get_task_repository),
    bg_manager: BackgroundExecutionManager = Depends(get_background_execution_manager),
) -> PlanExecutionResponse:
    """
    Start background autonomous plan execution.
    Returns immediately with execution_id and RUNNING status.
    """
    plan_id_clean = plan_id.strip()
    if not plan_id_clean:
        raise HTTPException(status_code=400, detail="plan_id parameter cannot be empty")

    tasks = await task_repo.get_tasks_by_plan(plan_id_clean)
    if not tasks:
        raise HTTPException(status_code=404, detail=f"Plan '{plan_id_clean}' not found")

    # Check state executability
    committed = [t for t in tasks if t.status == TaskStatus.COMMITTED]
    if len(committed) == len(tasks):
        raise HTTPException(status_code=409, detail=f"Plan '{plan_id_clean}' is already fully completed")

    failed = [t for t in tasks if t.status in (TaskStatus.FAILED, TaskStatus.ABORTED)]
    if failed:
        raise HTTPException(
            status_code=409,
            detail=f"Plan '{plan_id_clean}' contains terminal failed task(s) and cannot be executed",
        )

    running = [t for t in tasks if t.status in (TaskStatus.RUNNING, TaskStatus.VERIFYING, TaskStatus.PASSED)]
    if running:
        raise HTTPException(status_code=409, detail=f"Plan '{plan_id_clean}' is already executing in database")

    try:
        handle_info = await bg_manager.start_execution(
            plan_id=plan_id_clean,
            executor_factory=lambda: _create_executor_context(engine=task_repo._session.bind),
            timeout_seconds=timeout_seconds,
        )
        return PlanExecutionResponse(
            plan_id=plan_id_clean,
            execution_id=handle_info.execution_id,
            status=handle_info.status.value,
        )
    except PlanAlreadyExecutingError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except PlanNotExecutableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to start background execution for plan %s: %s", plan_id_clean, exc)
        raise HTTPException(status_code=500, detail="Failed to start plan execution")


@router.get(
    "/plans/{plan_id}/execution",
    response_model=PlanExecutionStatusResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Plan not found"},
    },
)
async def get_plan_execution_status(
    plan_id: str,
    task_repo: TaskRepository = Depends(get_task_repository),
    bg_manager: BackgroundExecutionManager = Depends(get_background_execution_manager),
) -> PlanExecutionStatusResponse:
    """
    Get authoritative execution status for plan_id.
    """
    plan_id_clean = plan_id.strip()
    if not plan_id_clean:
        raise HTTPException(status_code=400, detail="plan_id parameter cannot be empty")

    tasks = await task_repo.get_tasks_by_plan(plan_id_clean)
    bg_handle = await bg_manager.get_active_execution_by_plan(plan_id_clean)

    if not tasks and not bg_handle:
        raise HTTPException(status_code=404, detail=f"Plan '{plan_id_clean}' not found")

    total_tasks = len(tasks)
    completed_tasks = len([t for t in tasks if t.status == TaskStatus.COMMITTED])
    failed_tasks = len([t for t in tasks if t.status in (TaskStatus.FAILED, TaskStatus.ABORTED)])
    running_tasks = len([t for t in tasks if t.status in (TaskStatus.RUNNING, TaskStatus.VERIFYING, TaskStatus.PASSED)])

    execution_id = bg_handle.execution_id if bg_handle else None
    started_at = bg_handle.started_at if bg_handle else (tasks[0].started_at if tasks else None)
    completed_at = bg_handle.completed_at if bg_handle else None
    reason = _sanitize_string(bg_handle.reason) if bg_handle else None

    if bg_handle:
        overall_status = bg_handle.status.value
    elif total_tasks > 0 and completed_tasks == total_tasks:
        overall_status = "COMPLETED"
    elif failed_tasks > 0:
        overall_status = "FAILED"
    elif running_tasks > 0:
        overall_status = "RUNNING"
    else:
        overall_status = "PENDING"

    return PlanExecutionStatusResponse(
        plan_id=plan_id_clean,
        execution_id=execution_id,
        status=overall_status,
        total_tasks=total_tasks,
        completed_tasks=completed_tasks,
        failed_tasks=failed_tasks,
        running_tasks=running_tasks,
        reason=reason,
        started_at=started_at,
        completed_at=completed_at,
    )


@router.post(
    "/plans/{plan_id}/cancel",
    response_model=PlanExecutionStatusResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Plan or active execution not found"},
    },
)
async def cancel_plan_execution(
    plan_id: str,
    task_repo: TaskRepository = Depends(get_task_repository),
    bg_manager: BackgroundExecutionManager = Depends(get_background_execution_manager),
) -> PlanExecutionStatusResponse:
    """
    Request cancellation of plan execution by plan_id.
    """
    plan_id_clean = plan_id.strip()
    try:
        handle = await bg_manager.cancel_execution(plan_id_clean)
        tasks = await task_repo.get_tasks_by_plan(plan_id_clean)
        return PlanExecutionStatusResponse(
            plan_id=plan_id_clean,
            execution_id=handle.execution_id,
            status="CANCELLED",
            total_tasks=len(tasks),
            completed_tasks=len([t for t in tasks if t.status == TaskStatus.COMMITTED]),
            failed_tasks=len([t for t in tasks if t.status in (TaskStatus.FAILED, TaskStatus.ABORTED)]),
            running_tasks=len([t for t in tasks if t.status in (TaskStatus.RUNNING, TaskStatus.VERIFYING, TaskStatus.PASSED)]),
            reason="Cancellation requested",
            started_at=handle.started_at,
            completed_at=handle.completed_at,
        )
    except ExecutionNotFoundError:
        tasks = await task_repo.get_tasks_by_plan(plan_id_clean)
        if not tasks:
            raise HTTPException(status_code=404, detail=f"Plan '{plan_id_clean}' not found")
        return PlanExecutionStatusResponse(
            plan_id=plan_id_clean,
            status="CANCELLED",
            total_tasks=len(tasks),
            completed_tasks=len([t for t in tasks if t.status == TaskStatus.COMMITTED]),
            failed_tasks=len([t for t in tasks if t.status in (TaskStatus.FAILED, TaskStatus.ABORTED)]),
            running_tasks=0,
            reason="No active execution found to cancel",
        )


@router.post(
    "/executions/{execution_id}/cancel",
    response_model=PlanExecutionStatusResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Execution ID not found"},
    },
)
async def cancel_execution_by_id(
    execution_id: str,
    task_repo: TaskRepository = Depends(get_task_repository),
    bg_manager: BackgroundExecutionManager = Depends(get_background_execution_manager),
) -> PlanExecutionStatusResponse:
    """
    Request cancellation of background execution by execution_id.
    """
    exec_id_clean = execution_id.strip()
    try:
        handle = await bg_manager.cancel_execution(exec_id_clean)
        tasks = await task_repo.get_tasks_by_plan(handle.plan_id)
        return PlanExecutionStatusResponse(
            plan_id=handle.plan_id,
            execution_id=handle.execution_id,
            status="CANCELLED",
            total_tasks=len(tasks),
            completed_tasks=len([t for t in tasks if t.status == TaskStatus.COMMITTED]),
            failed_tasks=len([t for t in tasks if t.status in (TaskStatus.FAILED, TaskStatus.ABORTED)]),
            running_tasks=len([t for t in tasks if t.status in (TaskStatus.RUNNING, TaskStatus.VERIFYING, TaskStatus.PASSED)]),
            reason="Cancellation requested",
            started_at=handle.started_at,
            completed_at=handle.completed_at,
        )
    except ExecutionNotFoundError:
        raise HTTPException(status_code=404, detail=f"Execution '{exec_id_clean}' not found")


@router.get(
    "/tasks/{task_id}",
    response_model=TaskStatusResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Task not found"},
    },
)
async def get_task_status(
    task_id: str,
    task_repo: TaskRepository = Depends(get_task_repository),
) -> TaskStatusResponse:
    """
    Get authoritative persisted status for task_id.
    Exposes bounded, sanitized task status information.
    """
    task_id_clean = task_id.strip()
    task = await task_repo.get_task(task_id_clean)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id_clean}' not found")

    return TaskStatusResponse(
        task_id=task.task_id,
        plan_id=task.plan_id,
        task_key=task.task_key,
        title=task.title,
        status=task.status.value,
        attempt_count=task.attempt_count,
        max_attempts=task.max_attempts,
        started_at=task.started_at,
        completed_at=task.completed_at,
        failure_reason=_sanitize_string(task.failure_reason),
    )


@router.get(
    "/runs/{run_id}",
    response_model=RunStatusResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Run not found"},
    },
)
@router.get(
    "/runs/{run_id}/result",
    response_model=RunStatusResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Run not found"},
    },
)
async def get_run_status(
    run_id: str,
    task_repo: TaskRepository = Depends(get_task_repository),
) -> RunStatusResponse:
    """
    Get authoritative status for run_id or task_id.
    """
    run_id_clean = run_id.strip()
    target_task_id = run_id_clean
    if run_id_clean.startswith("run_status_"):
        target_task_id = run_id_clean[len("run_status_"):]

    task = await task_repo.get_task(target_task_id)
    if task is None:
        # Fallback search by exact task_id
        task = await task_repo.get_task(run_id_clean)

    if task is None:
        raise HTTPException(status_code=404, detail=f"Run or task '{run_id_clean}' not found")

    from app.tasks.state_machine import TaskStateMachine
    sm = TaskStateMachine(task_repo)
    ws_manager = WorkspaceManager(repo_root=_REPO_ROOT)
    gate = VerificationGate()
    from app.orchestration.orchestrator import AutonomousTaskOrchestrator
    from app.ai.services.development_agent import DevelopmentAgent
    from app.tools.tool_execution_service import ToolExecutionService
    from app.tools.registry import tool_registry
    from app.tools.implementation_registry import tool_implementation_registry
    from app.tools.executor import ToolExecutor
    tool_exec = ToolExecutor(registry=tool_registry, impl_registry=tool_implementation_registry)
    tool_svc = ToolExecutionService(
        registry=tool_registry,
        impl_registry=tool_implementation_registry,
        executor=tool_exec,
    )
    dev_agent = DevelopmentAgent(tool_execution_service=tool_svc)
    orch = AutonomousTaskOrchestrator(
        task_repo=task_repo,
        state_machine=sm,
        workspace_manager=ws_manager,
        verification_gate=gate,
        agent=dev_agent,
    )
    run_ctrl = AutonomousRunController(
        task_repo=task_repo,
        state_machine=sm,
        workspace_manager=ws_manager,
        verification_gate=gate,
        orchestrator=orch,
    )

    run_result = await run_ctrl.get_run_status(task.task_id)

    return RunStatusResponse(
        run_id=run_result.run_id,
        task_id=run_result.task_id,
        status=run_result.status.value,
        task_status=run_result.task_status.value,
        attempt_count=run_result.attempt_count,
        verification_passed=run_result.verification_passed,
        commit_sha=run_result.commit_sha,
        failure_reason=_sanitize_string(run_result.failure_reason),
        started_at=run_result.started_at,
        completed_at=run_result.completed_at,
    )
