"""
AI Manager — Controlled Execution Bridge.

Application-owned, narrow execution path from AI Manager decisions to the
existing autonomous development execution core.

MODEL PROPOSES.
APPLICATION DECIDES.
AUTHORIZED APPLICATION SERVICES EXECUTE.

This bridge is the ONLY authorized path from AI Manager decisions to plan
execution. It does NOT grant the model any execution authority.

SECURITY BOUNDARY:
  - This service does NOT execute tasks, tools, or plans directly.
  - It delegates to existing PlanExecutionService / AutonomousPlanExecutor.
  - It does NOT call subprocess, shell, filesystem APIs, or Git directly.
  - It does NOT bypass PermissionEngine, VerificationGate, or RunController.
  - The model never receives executable handles.
  - Model-proposed fields are never treated as trusted authorization.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.ai_manager.approval_models import (
    ApprovalStatus,
    CapabilityApproval,
    CapabilityApprovalRequest,
    compute_scope_fingerprint,
)
from app.ai_manager.approval_service import (
    CapabilityApprovalError,
    CapabilityApprovalService,
)
from app.ai_manager.bridge import (
    ManagerPlanningNotEligibleError,
    ManagerPlanningPolicyError,
    ManagerPlanningResult,
    ManagerPlanningService,
)
from app.ai_manager.gate import PolicyCapabilityGate, PolicyDecision
from app.ai_manager.models import DecisionOutcome, ManagerDecision, ManagerResult
from app.manager.models import ManagerInvalidPlanError
from app.services.autonomous_loop import AutonomousPlanExecutor, PlanExecutionResult
from app.services.plan_execution_service import PlanExecutionService

logger = logging.getLogger(__name__)


# ── Exceptions ────────────────────────────────────────────────────────────────


class ExecutionBridgeError(Exception):
    """Base exception for ExecutionBridge errors."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Execution bridge error: {reason}")


class ExecutionAuthorizationError(ExecutionBridgeError):
    """Execution authorization denied by application policy."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Authorization rejection: {reason}")


class ExecutionNeedsApprovalError(ExecutionBridgeError):
    """Execution requires explicit application approval."""

    def __init__(
        self,
        intent_id: str,
        approval: CapabilityApproval,
        auth_decision: Any,
    ) -> None:
        self.intent_id = intent_id
        self.approval = approval
        self.auth_decision = auth_decision
        super().__init__(
            f"Execution requires approval for intent '{intent_id}': "
            f"approval_id='{approval.approval_id}', "
            f"capabilities={approval.scope_fingerprint}"
        )


# ── Result models ─────────────────────────────────────────────────────────────


class ExecutionPrepareResult:
    """
    Result of checking execution authorization for a decision.

    application-owned result. The model does not control these fields.
    """

    __slots__ = (
        "authorized",
        "approval_needed",
        "approval",
        "planning_result",
        "plan_id",
        "project_id",
        "auth_reason",
    )

    def __init__(
        self,
        *,
        authorized: bool,
        approval_needed: bool = False,
        approval: CapabilityApproval | None = None,
        planning_result: ManagerPlanningResult | None = None,
        auth_reason: str | None = None,
    ) -> None:
        self.authorized = authorized
        self.approval_needed = approval_needed
        self.approval = approval
        self.planning_result = planning_result
        self.plan_id = planning_result.plan_id if planning_result else None
        self.project_id = planning_result.project_id if planning_result else None
        self.auth_reason = auth_reason

    def __repr__(self) -> str:
        if self.authorized:
            return f"ExecutionPrepareResult(authorized=True, plan_id={self.plan_id!r})"
        if self.approval_needed and self.approval:
            return (
                f"ExecutionPrepareResult(authorized=False, approval_needed=True, "
                f"approval_id={self.approval.approval_id!r})"
            )
        return f"ExecutionPrepareResult(authorized=False, approval_needed=False)"


class ExecutionResult:
    """
    Result of executing a decision through the controlled execution path.
    """

    __slots__ = ("plan_id", "project_id", "execution_result")

    def __init__(
        self,
        *,
        plan_id: str,
        project_id: str,
        execution_result: PlanExecutionResult,
    ) -> None:
        self.plan_id = plan_id
        self.project_id = project_id
        self.execution_result = execution_result

    def __repr__(self) -> str:
        return (
            f"ExecutionResult(plan_id={self.plan_id!r}, "
            f"terminal_state={self.execution_result.terminal_state.value})"
        )


# ── Execution bridge ───────────────────────────────────────────────────────────


class ExecutionBridge:
    """
    Application-owned execution bridge from AI Manager decisions to the
    existing autonomous development execution core.

    This is the ONLY authorized path from AI Manager decisions to plan
    execution. It enforces:

      1. Decision is trusted (from AIManager normalization)
      2. Execution authorization via PolicyCapabilityGate
      3. Approval flow via CapabilityApprovalService when required
      4. Planning via existing ManagerPlanningService
      5. Execution via existing PlanExecutionService / AutonomousPlanExecutor

    The model never receives executable handles. Application code constructs
    the authoritative execution request from validated application state.
    """

    def __init__(
        self,
        planning_service: ManagerPlanningService,
        plan_execution_service: PlanExecutionService,
        autonomous_executor: AutonomousPlanExecutor,
        capability_gate: PolicyCapabilityGate,
        approval_service: CapabilityApprovalService,
        plan_service: Any,
        audit_service: Any = None,
    ) -> None:
        self._planning_service = planning_service
        self._plan_execution_service = plan_execution_service
        self._autonomous_executor = autonomous_executor
        self._capability_gate = capability_gate
        self._approval_service = approval_service
        self._plan_service = plan_service
        self._audit_service = audit_service
        self._logger = logging.getLogger(__name__)

    async def prepare(self, result: ManagerResult, project_id: str) -> ExecutionPrepareResult:
        """
        Check execution authorization for a ManagerResult without executing.

        This is the first step in the controlled execution path. It:
        1. Validates the decision exists and is not rejected.
        2. Re-evaluates capabilities via PolicyCapabilityGate.
        3. If approval is needed, creates an approval request and returns
           the approval information for the application to handle.
        4. If authorized, plans the decision and returns the planning result.

        Args:
            result: Trusted ManagerResult from AIManager.
            project_id: Application-controlled project identifier.

        Returns:
            ExecutionPrepareResult indicating whether execution is authorized
            or approval is needed.

        Raises:
            ExecutionAuthorizationError: If execution is denied or policy
                is disabled.
            ExecutionBridgeError: If the decision is rejected or invalid.
        """
        # 1. Validate decision exists
        if result.outcome == DecisionOutcome.REJECTED:
            raise ExecutionBridgeError("Decision is REJECTED; cannot prepare execution.")
        if result.decision is None:
            raise ExecutionBridgeError("Decision is None; cannot prepare execution.")

        decision = result.decision

        # 2. Re-evaluate execution authorization via PolicyCapabilityGate
        auth_decision = self._capability_gate.authorize(decision)

        if auth_decision.outcome == PolicyDecision.DISABLED:
            raise ExecutionAuthorizationError(
                f"Policy gate reports policy disabled: {auth_decision.reason}"
            )

        if auth_decision.outcome == PolicyDecision.DENIED:
            raise ExecutionAuthorizationError(
                f"Execution authorization denied: {auth_decision.reason}"
            )

        if auth_decision.outcome == PolicyDecision.NEEDS_APPROVAL:
            approval = await self._create_approval(
                decision, project_id, auth_decision
            )
            await self._safe_audit(
                "EXECUTION_APPROVAL_REQUIRED",
                intent_id=decision.intent.intent_id,
                approval_id=approval.approval_id,
                capabilities=[str(c) for c in auth_decision.approval_required_capabilities],
                effective_risk=auth_decision.effective_risk.value,
            )
            return ExecutionPrepareResult(
                authorized=False,
                approval_needed=True,
                approval=approval,
                auth_reason=auth_decision.reason,
            )

        # 3. ALLOWED — plan the decision with execution authorization
        try:
            planning_result = await self._planning_service.plan_decision(
                result, project_id=project_id, execution_authorized=True
            )
        except ManagerInvalidPlanError:
            raise
        except Exception as exc:
            raise ExecutionBridgeError(f"Planning failed: {exc}") from exc

        await self._safe_audit(
            "EXECUTION_AUTHORIZED",
            intent_id=decision.intent.intent_id,
            plan_id=planning_result.plan_id,
            project_id=project_id,
        )

        return ExecutionPrepareResult(
            authorized=True,
            approval_needed=False,
            planning_result=planning_result,
            auth_reason=auth_decision.reason,
        )

    async def execute(
        self,
        result: ManagerResult,
        project_id: str,
        approval_id: str | None = None,
    ) -> ExecutionResult:
        """
        Execute a decision through the controlled execution path.

        This method:
        1. Re-checks execution authorization via PolicyCapabilityGate.
        2. If approval_id is provided, consumes the approval.
        3. Plans the decision (idempotent for the same decision).
        4. Executes the plan via AutonomousPlanExecutor.

        Args:
            result: Trusted ManagerResult from AIManager.
            project_id: Application-controlled project identifier.
            approval_id: Optional approval ID to consume before execution.
                Required when the decision's capabilities need approval.

        Returns:
            ExecutionResult containing the plan_id and PlanExecutionResult.

        Raises:
            ExecutionAuthorizationError: If execution is denied or policy
                is disabled.
            ExecutionNeedsApprovalError: If approval is needed but not
                provided.
            CapabilityApprovalStateError: If the approval is invalid or
                already consumed.
            ExecutionBridgeError: If the decision is rejected or invalid.
        """
        # 1. Validate decision exists
        if result.outcome == DecisionOutcome.REJECTED:
            raise ExecutionBridgeError("Decision is REJECTED; cannot execute.")
        if result.decision is None:
            raise ExecutionBridgeError("Decision is None; cannot execute.")

        decision = result.decision

        # 2. Re-evaluate execution authorization at execution time
        auth_decision = self._capability_gate.authorize(decision)

        if auth_decision.outcome == PolicyDecision.DISABLED:
            raise ExecutionAuthorizationError(
                f"Policy gate reports policy disabled: {auth_decision.reason}"
            )

        if auth_decision.outcome == PolicyDecision.DENIED:
            raise ExecutionAuthorizationError(
                f"Execution authorization denied: {auth_decision.reason}"
            )

        if auth_decision.outcome == PolicyDecision.NEEDS_APPROVAL:
            if approval_id is None:
                raise ExecutionNeedsApprovalError(
                    intent_id=decision.intent.intent_id,
                    approval=CapabilityApproval(
                        approval_id="pending",
                        intent_id=decision.intent.intent_id,
                        operation_type="plan_execution",
                        scope_fingerprint=compute_scope_fingerprint(
                            intent_id=decision.intent.intent_id,
                            capabilities=[str(c) for c in auth_decision.approval_required_capabilities],
                            effective_risk=auth_decision.effective_risk,
                            operation_type="plan_execution",
                            project_id=project_id,
                        ),
                        effective_risk=auth_decision.effective_risk,
                        status=ApprovalStatus.PENDING,
                        created_at=datetime.now(timezone.utc),
                        expires_at=datetime.now(timezone.utc),
                    ),
                    auth_decision=auth_decision,
                )
            # Verify approval scope matches current decision before consuming
            await self._verify_approval_scope(
                approval_id=approval_id,
                decision=decision,
                project_id=project_id,
                auth_decision=auth_decision,
            )

        # 3. Plan the decision (idempotent for same decision)
        try:
            planning_result = await self._planning_service.plan_decision(
                result, project_id=project_id, execution_authorized=True
            )
        except ManagerInvalidPlanError:
            raise
        except Exception as exc:
            raise ExecutionBridgeError(f"Planning failed: {exc}") from exc

        # 4. Persist the authoritative plan before execution.
        # Planning produces an in-memory ManagerPlanResult; execution operates
        # only on persisted DevelopmentTask records. Persistence is idempotent,
        # so this is safe when plan.create already ran.
        try:
            await self._plan_service.persist_manager_plan_result(
                planning_result.manager_plan_result
            )
        except ManagerInvalidPlanError:
            raise
        except Exception as exc:
            raise ExecutionBridgeError(f"Plan persistence failed: {exc}") from exc

        # 5. Consume approval only after planning + persistence succeeded.
        # This prevents a failed planning/persistence attempt from burning a
        # valid user approval.
        if auth_decision.outcome == PolicyDecision.NEEDS_APPROVAL:
            try:
                await self._approval_service.consume(approval_id)  # type: ignore[arg-type]
            except CapabilityApprovalError as exc:
                raise ExecutionAuthorizationError(
                    f"Approval '{approval_id}' consumption failed: {exc}"
                ) from exc

        # 6. Execute the persisted plan via existing autonomous execution core
        try:
            execution_result = await self._autonomous_executor.execute_plan(
                planning_result.plan_id
            )
        except Exception as exc:
            raise ExecutionBridgeError(f"Plan execution failed: {exc}") from exc

        await self._safe_audit(
            "EXECUTION_COMPLETED",
            intent_id=decision.intent.intent_id,
            plan_id=planning_result.plan_id,
            project_id=project_id,
            terminal_state=execution_result.terminal_state.value,
            committed_tasks=execution_result.committed_tasks,
            failed_tasks=execution_result.failed_tasks,
        )

        return ExecutionResult(
            plan_id=planning_result.plan_id,
            project_id=planning_result.project_id,
            execution_result=execution_result,
        )

    async def _create_approval(
        self,
        decision: ManagerDecision,
        project_id: str,
        auth_decision: Any,
    ) -> CapabilityApproval:
        """Create a capability approval request for execution."""
        request = CapabilityApprovalRequest(
            intent_id=decision.intent.intent_id,
            capabilities=[str(c) for c in auth_decision.approval_required_capabilities],
            effective_risk=auth_decision.effective_risk,
            operation_type="plan_execution",
            project_id=project_id,
            request_reason=(
                f"AI Manager execution authorization required for intent "
                f"'{decision.intent.intent_id}'"
            ),
            requested_by="system",
        )
        return await self._approval_service.request_approval(request)

    async def _verify_approval_scope(
        self,
        approval_id: str,
        decision: ManagerDecision,
        project_id: str,
        auth_decision: Any,
    ) -> None:
        """
        Verify that the provided approval matches the current decision scope.

        This prevents approval replay across different intents, capabilities,
        projects, risk levels, or operation types.
        """
        try:
            approval = await self._approval_service.get_approval(approval_id)
        except CapabilityApprovalError as exc:
            raise ExecutionAuthorizationError(f"Approval '{approval_id}' lookup failed: {exc}") from exc

        if approval is None:
            raise ExecutionAuthorizationError(
                f"Approval '{approval_id}' not found."
            )

        if approval.status == ApprovalStatus.EXPIRED:
            raise ExecutionAuthorizationError(
                f"Approval '{approval_id}' has expired."
            )

        if approval.status != ApprovalStatus.APPROVED:
            raise ExecutionAuthorizationError(
                f"Approval '{approval_id}' is in state '{approval.status.value}', expected APPROVED."
            )

        expected_fingerprint = compute_scope_fingerprint(
            intent_id=decision.intent.intent_id,
            capabilities=[str(c) for c in auth_decision.approval_required_capabilities],
            effective_risk=auth_decision.effective_risk,
            operation_type="plan_execution",
            project_id=project_id,
        )

        if approval.scope_fingerprint != expected_fingerprint:
            raise ExecutionAuthorizationError(
                f"Approval '{approval_id}' scope does not match current decision. "
                f"Expected fingerprint '{expected_fingerprint}', got '{approval.scope_fingerprint}'. "
                f"Approval cannot be reused for a different operation."
            )

        # Consumption is deliberately performed only after planning and
        # persistence succeed, immediately before execution.
        return

    async def _safe_audit(self, event_type: str, **kwargs: Any) -> None:
        """Write audit event. Fail-safe: never raises."""
        if self._audit_service is None:
            return
        try:
            log_fn = getattr(self._audit_service, "log_event", None)
            if callable(log_fn):
                res = log_fn(event_type=event_type, **kwargs)
                if asyncio.iscoroutine(res):
                    await res
        except Exception as exc:
            self._logger.warning(
                "Failed to emit audit event %s: %s",
                event_type,
                exc,
            )
