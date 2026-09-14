"""
AI Manager — Decision to Plan Bridge.

Application-controlled service that converts a validated ManagerDecision
into a UserPlan and delegates to the existing PlanManager.

SECURITY & BOUNDARIES:
- This is an APPLICATION component, not an AI provider.
- It does NOT execute tasks, tools, or plans.
- It does NOT access filesystem, shell, Git, or network directly.
- It does NOT call ToolExecutionService, PermissionEngine, VerificationGate,
  RunController, AutonomousPlanExecutor, or DevelopmentAgent.
- It does NOT invent an approval service.
- It does NOT grant execution authorization based on model output.
- The model cannot control plan_id, project_id, acceptance_criteria,
  task identity, execution state, or any authoritative field.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.ai_manager.models import (
    DecisionOutcome,
    ManagerDecision,
    ManagerResult,
    RiskLevel,
)
from app.ai_manager.gate import PolicyCapabilityGate, PolicyDecision
from app.manager.models import (
    ManagerInvalidPlanError,
    ManagerPlanResult,
    PlanningConstraints,
    UserPlan,
)
from app.manager.plan_manager import PlanManager

logger = logging.getLogger(__name__)

# ── Exceptions ────────────────────────────────────────────────────────────────


class ManagerPlanningError(Exception):
    """Base exception for ManagerPlanningService errors."""


class ManagerPlanningNotEligibleError(ManagerPlanningError):
    """Decision outcome is not eligible for planning."""

    def __init__(self, outcome: DecisionOutcome, reason: str) -> None:
        self.outcome = outcome
        self.reason = reason
        super().__init__(f"Decision outcome {outcome.value!r} is not eligible for planning: {reason}")


class ManagerPlanningPolicyError(ManagerPlanningError):
    """Decision was rejected by application policy."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Planning policy rejection: {reason}")


class ManagerPlanningAuthorizationError(ManagerPlanningError):
    """Capability authorization denied by application policy gate."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Authorization rejection: {reason}")


# ── Planning result ───────────────────────────────────────────────────────────


class ManagerPlanningResult(BaseModel):
    """
    Result of converting a ManagerDecision into a plan.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str
    project_id: str
    manager_plan_result: ManagerPlanResult
    decision_outcome: DecisionOutcome


# ── Bridge service ────────────────────────────────────────────────────────────


class ManagerPlanningService:
    """
    Application-controlled bridge from AI Manager decisions to PlanManager.

    Responsibilities:
      1. Receive a trusted ManagerResult / ManagerDecision.
      2. Determine whether the decision is eligible for planning.
      3. Apply application-controlled policy.
      4. Convert an eligible decision into a UserPlan.
      5. Delegate to the EXISTING PlanManager.
      6. Return a bounded application result.

    It MUST NOT:
      - call an AI provider directly,
      - execute tasks,
      - access filesystem,
      - use subprocess,
      - manipulate Git,
      - call VerificationGate,
      - call RunController,
      - call AutonomousPlanExecutor,
      - call DevelopmentAgent,
      - mutate task execution state,
      - bypass PermissionEngine,
      - bypass approval mechanisms.
    """

    def __init__(
        self,
        plan_manager: PlanManager,
        default_project_id: str = "default",
        default_planning_constraints: PlanningConstraints | None = None,
        policy_gate: PolicyCapabilityGate | None = None,
    ) -> None:
        self._plan_manager = plan_manager
        self._default_project_id = default_project_id
        self._default_constraints = default_planning_constraints or PlanningConstraints(
            max_tasks=20,
            default_max_attempts=3,
        )
        self._policy_gate = policy_gate

    async def plan_decision(
        self,
        result: ManagerResult,
        project_id: str | None = None,
    ) -> ManagerPlanningResult:
        """
        Convert a ManagerResult into a planning request.

        Args:
            result: Trusted ManagerResult from AIManager.
            project_id: Application-controlled project identifier.
                Defaults to the service's default_project_id.

        Returns:
            ManagerPlanningResult containing the plan_id and ManagerPlanResult.

        Raises:
            ManagerPlanningNotEligibleError: If the decision outcome is not
                eligible for planning.
            ManagerPlanningPolicyError: If the decision violates application
                policy.
            ManagerInvalidPlanError: If the resulting plan is invalid.
            ManagerProviderError: If the PlanManager provider fails.
        """
        # 1. Validate decision exists
        if result.decision is None:
            raise ManagerPlanningNotEligibleError(
                result.outcome,
                "Decision is None; cannot plan a rejected or errored decision.",
            )

        decision = result.decision

        # 2. Application capability/policy authorization gate
        if self._policy_gate is not None:
            auth_decision = self._policy_gate.authorize(decision)
            if auth_decision.outcome == PolicyDecision.DISABLED:
                raise ManagerPlanningPolicyError(
                    f"Policy gate reports policy disabled: {auth_decision.reason}"
                )
            if auth_decision.outcome == PolicyDecision.DENIED:
                raise ManagerPlanningAuthorizationError(
                    f"Capability authorization denied: {auth_decision.reason}"
                )
            if auth_decision.outcome == PolicyDecision.NEEDS_APPROVAL:
                raise ManagerPlanningNotEligibleError(
                    DecisionOutcome.NEEDS_APPROVAL,
                    f"Capabilities require application approval: {auth_decision.reason}",
                )

        # 3. Outcome eligibility gate
        self._check_eligibility(result.outcome)

        # 4. Application policy enforcement
        self._enforce_policy(decision)

        # 4. Convert to UserPlan (application-controlled fields only)
        user_plan = self._decision_to_user_plan(decision, project_id=project_id)

        # 5. Delegate to existing PlanManager
        try:
            manager_plan_result = await self._plan_manager.plan(user_plan)
        except ManagerInvalidPlanError:
            raise
        except Exception as exc:
            raise ManagerPlanningError(f"PlanManager failed: {exc}") from exc

        return ManagerPlanningResult(
            plan_id=user_plan.plan_id,
            project_id=user_plan.project_id,
            manager_plan_result=manager_plan_result,
            decision_outcome=result.outcome,
        )

    def _check_eligibility(self, outcome: DecisionOutcome) -> None:
        """
        Determine whether the decision outcome may enter planning.

        Eligible:
          - READY_FOR_PLANNING: model explicitly signaled readiness

        Conditionally eligible (application policy):
          - PROPOSED: only if no risky/execution-type actions are proposed
            (enforced in _enforce_policy)

        Blocked:
          - REJECTED: never enters planning
          - NEEDS_CLARIFICATION: requires human input first
          - NEEDS_APPROVAL: no plan-level approval service exists yet
        """
        if outcome == DecisionOutcome.READY_FOR_PLANNING:
            return

        if outcome == DecisionOutcome.PROPOSED:
            return

        if outcome == DecisionOutcome.NEEDS_APPROVAL:
            raise ManagerPlanningNotEligibleError(
                outcome,
                "NEEDS_APPROVAL decisions require an application approval mechanism. "
                "No plan-level approval service is currently available.",
            )

        if outcome == DecisionOutcome.NEEDS_CLARIFICATION:
            raise ManagerPlanningNotEligibleError(
                outcome,
                "NEEDS_CLARIFICATION decisions require additional user input before planning.",
            )

        if outcome == DecisionOutcome.REJECTED:
            raise ManagerPlanningNotEligibleError(
                outcome,
                "REJECTED decisions cannot enter planning.",
            )

        raise ManagerPlanningNotEligibleError(
            outcome,
            f"Unknown outcome {outcome.value!r} is not eligible for planning.",
        )

    def _enforce_policy(self, decision: ManagerDecision) -> None:
        """
        Apply application-controlled policy to the decision.

        The model cannot override these checks.
        """
        # Application policy: PROPOSED outcomes with HIGH/CRITICAL risk are blocked
        if decision.outcome == DecisionOutcome.PROPOSED:
            risky_actions = [
                a for a in decision.proposed_actions
                if a.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
            ]
            if risky_actions:
                action_ids = [a.action_id for a in risky_actions]
                raise ManagerPlanningPolicyError(
                    f"PROPOSED decision contains high/critical risk actions: {action_ids}. "
                    f"Only READY_FOR_PLANNING may contain risky actions."
                )

        # Application policy: execution-type capabilities are not authorized for planning
        # The model may propose any capability ID, but planning authorization
        # is application-controlled. We block execution-oriented capability namespaces
        # from entering planning.
        _EXECUTION_CAPABILITY_PREFIXES = (
            "tool_execution.",
            "shell.",
            "subprocess.",
            "git.",
            "workspace.",
            "verification_gate.",
            "permission_engine.",
        )
        for action in decision.proposed_actions:
            cap_str = str(action.capability_id)
            for prefix in _EXECUTION_CAPABILITY_PREFIXES:
                if cap_str.startswith(prefix):
                    raise ManagerPlanningPolicyError(
                        f"Proposed capability '{cap_str}' is an execution capability. "
                        f"Capability proposal does not grant authorization."
                    )

    def _decision_to_user_plan(
        self,
        decision: ManagerDecision,
        project_id: str | None = None,
    ) -> UserPlan:
        """
        Deterministically convert a ManagerDecision into a UserPlan.

        Application-controlled fields:
          - plan_id: generated from intent_id (never model-controlled)
          - project_id: from application parameter
          - acceptance_criteria: empty (model cannot control)
          - planning_constraints: application defaults
          - strict: True (application default)

        Model-proposed fields used as untrusted planning context only:
          - goal → user_request (bounded untrusted text)
          - proposed_actions → not directly forwarded
          - required_capabilities → not directly forwarded
          - rationale/warnings → not forwarded
        """
        # plan_id is application-generated, deterministic, never model-controlled
        plan_id = self._generate_plan_id(decision)

        # project_id is application-controlled
        resolved_project_id = project_id or self._default_project_id

        # acceptance_criteria are application-controlled requirements
        # The model must not be able to set them.
        acceptance_criteria: list[str] = []

        return UserPlan(
            plan_id=plan_id,
            user_request=decision.goal,
            project_id=resolved_project_id,
            acceptance_criteria=acceptance_criteria,
            planning_constraints=self._default_constraints,
            strict=True,
        )

    @staticmethod
    def _generate_plan_id(decision: ManagerDecision) -> str:
        """
        Generate a deterministic plan_id from the decision.

        The model never controls this value.
        """
        raw = f"plan:{decision.intent.intent_id}:{decision.outcome.value}:{decision.goal}".encode()
        digest = hashlib.sha256(raw).hexdigest()[:12]
        return f"plan_{digest}"
