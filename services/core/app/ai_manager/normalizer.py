"""
AI Manager — Normalization and Validation.

Converts RawManagerDecision (untrusted model output) into ManagerDecision
(application-controlled trusted object).

Validation layers:
  1. Structural validation — Pydantic schema (already done by RawManagerDecision)
  2. Semantic validation — application policy checks
  3. Normalization — convert to trusted types, generate application IDs

All model-controlled data is validated before entering trusted application state.
Malformed output fails closed.
"""
from __future__ import annotations

import uuid
from typing import Any

from app.ai_manager.errors import (
    AIManagerInvalidDecisionError,
    AIManagerMalformedDecisionError,
    AIManagerPolicyRejectionError,
)
from app.ai_manager.models import (
    ActionProposal,
    CapabilityIdentifier,
    DecisionOutcome,
    ManagerDecision,
    RawActionProposal,
    RawManagerDecision,
    RiskLevel,
    UserIntent,
)


def _validate_no_execution_state_fields(raw: RawManagerDecision) -> None:
    """
    Ensure the model did not attempt to inject execution state fields.
    These are application-controlled and must never appear in model output.
    """
    forbidden_fields = {
        "task_id",
        "run_id",
        "execution_id",
        "status",
        "attempt_count",
        "max_attempts",
        "workspace_path",
        "commit_sha",
        "verification_passed",
        "execution_token",
        "recovery_owner",
        "last_heartbeat",
        "execution_terminated_at",
        "workspace_cleanup_pending",
        "verification_gate",
        "permissionengine",
        "permission_engine",
        "tool_execution_service",
        "subprocess",
        "shell",
        "git",
        "workspace_manager",
        "run_controller",
        "plan_execution_service",
        "autonomous_plan_executor",
        "development_agent",
    }
    # Check goal
    for field in forbidden_fields:
        if field in raw.goal.lower():
            raise AIManagerMalformedDecisionError(
                f"Model output contains forbidden execution state reference in goal: {field}"
            )
    # Check rationale
    if raw.rationale:
        for field in forbidden_fields:
            if field in raw.rationale.lower():
                raise AIManagerMalformedDecisionError(
                    f"Model output contains forbidden execution state reference in rationale: {field}"
                )
    # Check warnings
    for warning in (raw.warnings or []):
        for field in forbidden_fields:
            if field in warning.lower():
                raise AIManagerMalformedDecisionError(
                    f"Model output contains forbidden execution state reference in warning: {field}"
                )


def _validate_action_dependencies(actions: list[RawActionProposal]) -> None:
    """Validate action dependency graph."""
    action_ids = {a.action_id for a in actions}

    # Check all dependencies reference existing actions
    for action in actions:
        for dep in action.dependencies:
            if dep not in action_ids:
                raise AIManagerInvalidDecisionError(
                    f"Action '{action.action_id}' references unknown dependency '{dep}'"
                )

    # Check no self-dependencies
    for action in actions:
        if action.action_id in action.dependencies:
            raise AIManagerInvalidDecisionError(
                f"Action '{action.action_id}' has self-dependency"
            )

    # Check no duplicate dependencies
    for action in actions:
        seen = set(action.dependencies)
        if len(seen) != len(action.dependencies):
            raise AIManagerInvalidDecisionError(
                f"Action '{action.action_id}' has duplicate dependencies"
            )

    # Cycle detection via Kahn's algorithm
    if actions:
        in_degree: dict[str, int] = {a.action_id: 0 for a in actions}
        adjacency: dict[str, list[str]] = {a.action_id: [] for a in actions}

        for action in actions:
            for dep in action.dependencies:
                adjacency[dep].append(action.action_id)
                in_degree[action.action_id] += 1

        queue = [k for k, d in in_degree.items() if d == 0]
        visited: set[str] = set()

        while queue:
            node = queue.pop(0)
            visited.add(node)
            for neighbor in adjacency[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(visited) != len(actions):
            cycle_members = sorted(a.action_id for a in actions if a.action_id not in visited)
            raise AIManagerInvalidDecisionError(
                f"Cyclic action dependency detected involving: {', '.join(cycle_members)}"
            )


def _validate_unique_action_ids(actions: list[RawActionProposal]) -> None:
    """Ensure all action_ids are unique."""
    ids = [a.action_id for a in actions]
    if len(ids) != len(set(ids)):
        raise AIManagerInvalidDecisionError("duplicate action_id in proposed_actions")


def normalize_raw_decision(
    raw: RawManagerDecision,
    intent: UserIntent,
    application_policy: dict[str, Any] | None = None,
) -> ManagerDecision:
    """
    Normalize and validate a RawManagerDecision into a trusted ManagerDecision.

    Args:
        raw: Untrusted model output (already schema-validated by Pydantic).
        intent: Application-controlled user intent.
        application_policy: Optional application policy overrides.

    Returns:
        ManagerDecision — trusted application object.

    Raises:
        AIManagerMalformedDecisionError: structural validation failure.
        AIManagerInvalidDecisionError: semantic validation failure.
        AIManagerPolicyRejectionError: application policy rejection.
    """
    application_policy = application_policy or {}

    # 1. Check for forbidden execution state fields in model output
    _validate_no_execution_state_fields(raw)

    # 2. Validate unique action IDs (must be first, before dependency graph construction)
    _validate_unique_action_ids(raw.proposed_actions)

    # 3. Validate action dependencies
    _validate_action_dependencies(raw.proposed_actions)

    # 4. Validate uncertainty
    if raw.uncertainty is not None and (raw.uncertainty < 0.0 or raw.uncertainty > 1.0):
        raise AIManagerInvalidDecisionError(
            f"uncertainty must be between 0.0 and 1.0, got {raw.uncertainty}"
        )

    # 5. Convert to trusted types
    def _to_capability_id(cap_str: str) -> CapabilityIdentifier:
        parts = cap_str.split(".")
        if len(parts) < 2:
            raise AIManagerInvalidDecisionError(
                f"capability_id '{cap_str}' must have at least namespace and name"
            )
        namespace = parts[0]
        name = ".".join(parts[1:])
        return CapabilityIdentifier(namespace=namespace, name=name)

    proposed_actions = []
    for raw_action in raw.proposed_actions:
        capability_id = _to_capability_id(raw_action.capability_id)
        proposed_actions.append(
            ActionProposal(
                action_id=raw_action.action_id,
                capability_id=capability_id,
                action_type=raw_action.action_type,
                arguments=dict(raw_action.arguments),
                dependencies=list(raw_action.dependencies),
                risk=raw_action.risk,
                approval_required=raw_action.approval_required,
                summary=raw_action.summary,
            )
        )

    required_capabilities = [_to_capability_id(cap) for cap in raw.required_capabilities]

    # 6. Application policy enforcement (model proposes, application decides)
    # The model cannot lower application risk policy
    _RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}
    max_allowed_risk = application_policy.get("max_allowed_risk", RiskLevel.MEDIUM)
    if _RISK_ORDER.get(raw.risk_level, 0) > _RISK_ORDER.get(max_allowed_risk, 1):
        raise AIManagerPolicyRejectionError(
            f"Proposed risk level {raw.risk_level.value} exceeds policy maximum {max_allowed_risk.value}"
        )

    # The model cannot override application approval requirements
    if application_policy.get("require_approval_for_all", False):
        for action in proposed_actions:
            if not action.approval_required:
                raise AIManagerPolicyRejectionError(
                    f"Application policy requires approval for all actions; action '{action.action_id}' does not require approval"
                )

    # The model cannot propose capabilities outside the allowed set
    allowed_capabilities = application_policy.get("allowed_capabilities")
    if allowed_capabilities is not None:
        allowed_set = {str(c) for c in allowed_capabilities}
        for action in proposed_actions:
            if str(action.capability_id) not in allowed_set:
                raise AIManagerPolicyRejectionError(
                    f"Proposed capability '{action.capability_id}' is not in allowed capabilities: {sorted(allowed_set)}"
                )

    # 7. Build trusted decision
    outcome = DecisionOutcome(raw.outcome)

    decision = ManagerDecision(
        intent=intent,
        outcome=outcome,
        goal=raw.goal,
        proposed_actions=proposed_actions,
        required_capabilities=required_capabilities,
        risk_level=raw.risk_level,
        approval_required=raw.approval_required,
        rationale=raw.rationale,
        warnings=list(raw.warnings),
        uncertainty=raw.uncertainty,
    )

    return decision
