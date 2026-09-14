"""
AI Manager — Policy & Capability Decision Gate.

Application-owned authorization boundary between model proposal and
application authorization.

MODEL PROPOSES.
APPLICATION DECIDES.
AUTHORIZED APPLICATION SERVICES EXECUTE.

This gate:
  - Does NOT execute capabilities
  - Does NOT execute plans
  - Does NOT call subprocess, shell, filesystem, Git, ToolExecutionService
  - Does NOT grant execution authority
  - Only evaluates authorization
"""
from __future__ import annotations

import hashlib
from typing import Any

from app.ai_manager.capabilities import (
    CapabilityDefinition,
    CapabilityRegistry,
    create_default_registry,
)
from app.ai_manager.errors import AIManagerPolicyRejectionError
from app.ai_manager.models import (
    ActionProposal,
    CapabilityIdentifier,
    ManagerDecision,
    RiskLevel,
)
from app.ai_manager.policy import (
    ApplicationPolicy,
    AuthorizationDecision,
    PolicyDecision,
)


_RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}


class PolicyCapabilityGate:
    """
    Application-owned policy and capability decision gate.

    Evaluates a ManagerDecision against the application capability registry
    and policy to produce an authoritative AuthorizationDecision.
    """

    def __init__(
        self,
        registry: CapabilityRegistry | None = None,
        policy: ApplicationPolicy | dict[str, Any] | None = None,
        audit_service: Any = None,
    ) -> None:
        self._registry = registry or create_default_registry()
        self._policy = self._normalize_policy(policy)
        self._audit_service = audit_service

    @staticmethod
    def _normalize_policy(policy: ApplicationPolicy | dict[str, Any] | None) -> ApplicationPolicy:
        if policy is None:
            return ApplicationPolicy()
        if isinstance(policy, ApplicationPolicy):
            return policy
        if isinstance(policy, dict):
            max_risk = policy.get("max_risk_level", RiskLevel.MEDIUM)
            if isinstance(max_risk, str):
                max_risk = RiskLevel(max_risk)
            return ApplicationPolicy(
                max_risk_level=max_risk,
                allowed_capabilities=policy.get("allowed_capabilities"),
                denied_capabilities=policy.get("denied_capabilities"),
                require_approval_for_capabilities=policy.get("require_approval_for_capabilities"),
                global_enabled=policy.get("global_enabled", True),
            )
        raise TypeError(f"policy must be ApplicationPolicy or dict, got {type(policy).__name__}")

    def _compute_snapshot_id(self) -> str:
        """Compute a deterministic snapshot ID for the current policy/registry state."""
        raw = (
            f"registry:{len(self._registry._capabilities)}"
            f":policy:{self._policy.max_risk_level.value}"
            f":{self._policy.global_enabled}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def authorize(self, decision: ManagerDecision) -> AuthorizationDecision:
        """
        Evaluate a ManagerDecision against the application capability registry
        and policy.

        Returns an AuthorizationDecision. The application policy and registry
        are the sole authorities; model-proposed fields (risk, approval_required)
        are never authoritative.
        """
        snapshot_id = self._compute_snapshot_id()
        evaluated: list[CapabilityIdentifier] = []
        denied: list[CapabilityIdentifier] = []
        approval_required: list[CapabilityIdentifier] = []
        max_risk = RiskLevel.LOW

        if not self._policy.global_enabled:
            return AuthorizationDecision(
                authorized=False,
                outcome=PolicyDecision.DISABLED,
                evaluated_capabilities=evaluated,
                effective_risk=max_risk,
                denied_capabilities=[],
                approval_required_capabilities=[],
                reason="Global policy is disabled",
                policy_snapshot_id=snapshot_id,
            )

        all_capabilities: list[CapabilityIdentifier] = []
        for cap in decision.required_capabilities:
            all_capabilities.append(cap)
        for action in decision.proposed_actions:
            all_capabilities.append(action.capability_id)

        for cap_id in all_capabilities:
            if cap_id in evaluated:
                continue
            evaluated.append(cap_id)

            cap_def = self._registry.get(cap_id)

            if cap_def is None:
                denied.append(cap_id)
                continue

            if not cap_def.enabled:
                denied.append(cap_id)
                continue

            if self._is_denied(cap_id):
                denied.append(cap_id)
                continue

            if not self._is_allowed(cap_id):
                denied.append(cap_id)
                continue

            cap_risk_order = _RISK_ORDER.get(cap_def.risk, 0)
            policy_risk_order = _RISK_ORDER.get(self._policy.max_risk_level, 1)
            if cap_risk_order > policy_risk_order:
                if _RISK_ORDER.get(cap_def.risk, 0) > _RISK_ORDER.get(max_risk, 0):
                    max_risk = cap_def.risk
                denied.append(cap_id)
                continue

            if _RISK_ORDER.get(cap_def.risk, 0) > _RISK_ORDER.get(max_risk, 0):
                max_risk = cap_def.risk

            if cap_def.approval_required or self._policy_requires_approval(cap_id):
                if cap_id not in approval_required:
                    approval_required.append(cap_id)

        if denied:
            return AuthorizationDecision(
                authorized=False,
                outcome=PolicyDecision.DENIED,
                evaluated_capabilities=evaluated,
                effective_risk=max_risk,
                denied_capabilities=denied,
                approval_required_capabilities=approval_required,
                reason=f"Denied capabilities: {[str(c) for c in denied]}",
                policy_snapshot_id=snapshot_id,
            )

        if approval_required:
            return AuthorizationDecision(
                authorized=False,
                outcome=PolicyDecision.NEEDS_APPROVAL,
                evaluated_capabilities=evaluated,
                effective_risk=max_risk,
                denied_capabilities=[],
                approval_required_capabilities=approval_required,
                reason=f"Approval required for capabilities: {[str(c) for c in approval_required]}",
                policy_snapshot_id=snapshot_id,
            )

        return AuthorizationDecision(
            authorized=True,
            outcome=PolicyDecision.ALLOWED,
            evaluated_capabilities=evaluated,
            effective_risk=max_risk,
            denied_capabilities=[],
            approval_required_capabilities=[],
            reason=None,
            policy_snapshot_id=snapshot_id,
        )

    def _is_denied(self, cap_id: CapabilityIdentifier) -> bool:
        if self._policy.denied_capabilities is None:
            return False
        return cap_id in self._policy.denied_capabilities

    def _is_allowed(self, cap_id: CapabilityIdentifier) -> bool:
        if self._policy.allowed_capabilities is None:
            return True
        return cap_id in self._policy.allowed_capabilities

    def _policy_requires_approval(self, cap_id: CapabilityIdentifier) -> bool:
        if self._policy.require_approval_for_capabilities is None:
            return False
        return cap_id in self._policy.require_approval_for_capabilities
