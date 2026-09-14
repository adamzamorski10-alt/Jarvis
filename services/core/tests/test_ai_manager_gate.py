"""
Stage 9.4 — AI Manager Policy & Capability Decision Gate Tests.

Covers POLICY-01 through POLICY-40.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.ai_manager.bridge import (
    ManagerPlanningAuthorizationError,
    ManagerPlanningError,
    ManagerPlanningNotEligibleError,
    ManagerPlanningPolicyError,
    ManagerPlanningService,
)
from app.ai_manager.capabilities import (
    CapabilityDefinition,
    CapabilityRegistry,
    create_default_registry,
)
from app.ai_manager.errors import (
    AIManagerInvalidDecisionError,
    AIManagerMalformedDecisionError,
    AIManagerPolicyRejectionError,
)
from app.ai_manager.gate import PolicyCapabilityGate
from app.ai_manager.models import (
    ActionProposal,
    CapabilityIdentifier,
    DecisionOutcome,
    ManagerDecision,
    ManagerResult,
    RiskLevel,
    UserIntent,
)
from app.ai_manager.policy import ApplicationPolicy, AuthorizationDecision, PolicyDecision
from app.manager.models import (
    ManagerInvalidPlanError,
    ManagerPlanResult,
    PlanningConstraints,
    ProviderTaskProposal,
    UserPlan,
)
from app.manager.plan_manager import PlanManager
from app.manager.providers.fake import FakePlanProvider

AI_MANAGER_ROOT = Path(__file__).resolve().parent.parent / "app" / "ai_manager"


def _make_intent(intent_id: str = "intent_1") -> UserIntent:
    return UserIntent(
        intent_id=intent_id,
        user_request="test request",
        normalized_goal="test goal",
        context={"key": "value"},
        constraints={"limit": "10"},
    )


def _make_decision(
    outcome: str = "READY_FOR_PLANNING",
    intent: UserIntent | None = None,
    actions: list[ActionProposal] | None = None,
    required_capabilities: list[CapabilityIdentifier] | None = None,
) -> ManagerDecision:
    if intent is None:
        intent = _make_intent()
    if actions is None:
        actions = [
            ActionProposal(
                action_id="act1",
                capability_id=CapabilityIdentifier(namespace="development", name="plan"),
                action_type="plan",
                arguments={"target": "test"},
                dependencies=[],
                risk=RiskLevel.LOW,
                approval_required=False,
                summary="Test action",
            )
        ]
    if required_capabilities is None:
        required_capabilities = [CapabilityIdentifier(namespace="development", name="plan")]
    return ManagerDecision(
        intent=intent,
        outcome=DecisionOutcome(outcome),
        goal="test goal",
        proposed_actions=actions,
        required_capabilities=required_capabilities,
        risk_level=RiskLevel.LOW,
        approval_required=False,
        rationale="test rationale",
        warnings=[],
        uncertainty=0.5,
    )


def _make_result(decision: ManagerDecision | None = None, outcome: str = "READY_FOR_PLANNING") -> ManagerResult:
    if decision is None:
        decision = _make_decision(outcome=outcome)
    return ManagerResult(decision=decision, outcome=DecisionOutcome(outcome))


def _fake_plan_manager(proposals=None, error=None):
    provider = FakePlanProvider(proposals=proposals, error=error)
    return PlanManager(provider=provider)


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-01: Known allowed capability → ALLOWED
# ─────────────────────────────────────────────────────────────────────────────

class TestKnownAllowedCapability:
    def test_development_plan_allowed(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(actions=[])
        result = gate.authorize(decision)
        assert result.authorized is True
        assert result.outcome == PolicyDecision.ALLOWED

    def test_git_commit_requires_approval(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(
            required_capabilities=[CapabilityIdentifier(namespace="git", name="commit")],
            actions=[],
        )
        result = gate.authorize(decision)
        assert result.authorized is False
        assert result.outcome == PolicyDecision.NEEDS_APPROVAL


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-02: Unknown capability → DENIED
# ─────────────────────────────────────────────────────────────────────────────

class TestUnknownCapabilityDenied:
    def test_unknown_capability_denied(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(
            required_capabilities=[CapabilityIdentifier(namespace="unknown", name="cap")],
            actions=[],
        )
        result = gate.authorize(decision)
        assert result.authorized is False
        assert result.outcome == PolicyDecision.DENIED
        assert CapabilityIdentifier(namespace="unknown", name="cap") in result.denied_capabilities

    def test_nonexistent_namespace_denied(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(
            required_capabilities=[CapabilityIdentifier(namespace="does_not_exist", name="anything")],
            actions=[],
        )
        result = gate.authorize(decision)
        assert result.authorized is False
        assert result.outcome == PolicyDecision.DENIED


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-03: Disabled capability → DENIED
# ─────────────────────────────────────────────────────────────────────────────

class TestDisabledCapabilityDenied:
    def test_tool_execution_run_disabled(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(
            required_capabilities=[CapabilityIdentifier(namespace="tool_execution", name="run")],
            actions=[],
        )
        result = gate.authorize(decision)
        assert result.authorized is False
        assert result.outcome == PolicyDecision.DENIED

    def test_shell_execute_disabled(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(
            required_capabilities=[CapabilityIdentifier(namespace="shell", name="execute")],
            actions=[],
        )
        result = gate.authorize(decision)
        assert result.authorized is False
        assert result.outcome == PolicyDecision.DENIED


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-04: Explicitly denied capability → DENIED
# ─────────────────────────────────────────────────────────────────────────────

class TestExplicitlyDeniedCapability:
    def test_capability_in_denied_list(self):
        registry = create_default_registry()
        policy = ApplicationPolicy(
            denied_capabilities=[CapabilityIdentifier(namespace="development", name="plan")]
        )
        gate = PolicyCapabilityGate(registry=registry, policy=policy)
        decision = _make_decision(
            required_capabilities=[CapabilityIdentifier(namespace="development", name="plan")],
            actions=[],
        )
        result = gate.authorize(decision)
        assert result.authorized is False
        assert result.outcome == PolicyDecision.DENIED


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-05: Risk above application maximum → DENIED
# ─────────────────────────────────────────────────────────────────────────────

class TestRiskAboveMaximum:
    def test_high_risk_above_medium_max(self):
        registry = create_default_registry()
        policy = ApplicationPolicy(max_risk_level=RiskLevel.MEDIUM)
        gate = PolicyCapabilityGate(registry=registry, policy=policy)
        decision = _make_decision(
            required_capabilities=[CapabilityIdentifier(namespace="development", name="execute")],
            actions=[],
        )
        result = gate.authorize(decision)
        assert result.authorized is False
        assert result.outcome == PolicyDecision.DENIED

    def test_critical_risk_above_high_max(self):
        registry = create_default_registry()
        policy = ApplicationPolicy(max_risk_level=RiskLevel.HIGH)
        gate = PolicyCapabilityGate(registry=registry, policy=policy)
        decision = _make_decision(
            required_capabilities=[CapabilityIdentifier(namespace="tool_execution", name="run")],
            actions=[],
        )
        result = gate.authorize(decision)
        assert result.authorized is False
        assert result.outcome == PolicyDecision.DENIED


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-06: Model LOW risk cannot downgrade application HIGH risk
# ─────────────────────────────────────────────────────────────────────────────

class TestModelRiskCannotDowngrade:
    def test_model_low_risk_ignored_for_capability_risk(self):
        registry = create_default_registry()
        policy = ApplicationPolicy(max_risk_level=RiskLevel.HIGH)
        gate = PolicyCapabilityGate(registry=registry, policy=policy)
        decision = _make_decision(
            required_capabilities=[CapabilityIdentifier(namespace="development", name="execute")],
            actions=[],
        )
        # Model says risk_level=LOW, but registry says HIGH
        assert decision.risk_level == RiskLevel.LOW
        result = gate.authorize(decision)
        assert result.effective_risk == RiskLevel.HIGH
        assert result.outcome == PolicyDecision.NEEDS_APPROVAL

    def test_model_risk_not_authoritative(self):
        registry = create_default_registry()
        policy = ApplicationPolicy(max_risk_level=RiskLevel.LOW)
        gate = PolicyCapabilityGate(registry=registry, policy=policy)
        decision = _make_decision(
            required_capabilities=[CapabilityIdentifier(namespace="git", name="commit")],
            actions=[],
        )
        # Model says LOW, but registry says MEDIUM which exceeds policy max LOW
        result = gate.authorize(decision)
        assert result.authorized is False
        assert result.outcome == PolicyDecision.DENIED
        assert result.effective_risk == RiskLevel.MEDIUM


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-07: Model HIGH risk cannot change application policy
# ─────────────────────────────────────────────────────────────────────────────

class TestModelHighRiskCannotChangePolicy:
    def test_model_high_risk_on_low_capability(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(
            required_capabilities=[CapabilityIdentifier(namespace="development", name="plan")],
            actions=[],
        )
        # Model says risk_level=HIGH, but registry says LOW
        decision = ManagerDecision(
            intent=decision.intent,
            outcome=decision.outcome,
            goal=decision.goal,
            proposed_actions=decision.proposed_actions,
            required_capabilities=decision.required_capabilities,
            risk_level=RiskLevel.HIGH,
            approval_required=True,
            rationale=decision.rationale,
            warnings=decision.warnings,
            uncertainty=decision.uncertainty,
        )
        result = gate.authorize(decision)
        # Registry risk overrides model risk
        assert result.effective_risk == RiskLevel.LOW
        assert result.outcome == PolicyDecision.ALLOWED


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-08: Capability requiring approval → NEEDS_APPROVAL
# ─────────────────────────────────────────────────────────────────────────────

class TestCapabilityRequiringApproval:
    def test_git_commit_requires_approval(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(
            required_capabilities=[CapabilityIdentifier(namespace="git", name="commit")],
            actions=[],
        )
        result = gate.authorize(decision)
        assert result.authorized is False
        assert result.outcome == PolicyDecision.NEEDS_APPROVAL
        assert CapabilityIdentifier(namespace="git", name="commit") in result.approval_required_capabilities

    def test_development_execute_requires_approval(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(
            required_capabilities=[CapabilityIdentifier(namespace="development", name="execute")],
            actions=[],
        )
        result = gate.authorize(decision)
        assert result.authorized is False
        assert result.outcome == PolicyDecision.DENIED


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-09: Model approval_required=False cannot bypass approval
# ─────────────────────────────────────────────────────────────────────────────

class TestModelApprovalFalseCannotBypass:
    def test_model_approval_false_ignored(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        actions = [
            ActionProposal(
                action_id="act1",
                capability_id=CapabilityIdentifier(namespace="git", name="commit"),
                action_type="commit",
                arguments={"message": "test"},
                dependencies=[],
                risk=RiskLevel.LOW,
                approval_required=False,
                summary="Git commit",
            )
        ]
        decision = _make_decision(
            required_capabilities=[CapabilityIdentifier(namespace="git", name="commit")],
            actions=actions,
        )
        result = gate.authorize(decision)
        assert result.authorized is False
        assert result.outcome == PolicyDecision.NEEDS_APPROVAL
        assert CapabilityIdentifier(namespace="git", name="commit") in result.approval_required_capabilities


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-10: No approval → never ALLOWED for approval-required capability
# ─────────────────────────────────────────────────────────────────────────────

class TestNoApprovalNeverAllowed:
    def test_approval_required_capability_not_allowed_without_approval(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(
            required_capabilities=[CapabilityIdentifier(namespace="git", name="commit")],
            actions=[],
        )
        result = gate.authorize(decision)
        assert result.authorized is False
        assert result.outcome == PolicyDecision.NEEDS_APPROVAL
        assert result.outcome != PolicyDecision.ALLOWED


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-11: Multiple capabilities all allowed → ALLOWED
# ─────────────────────────────────────────────────────────────────────────────

class TestMultipleCapabilitiesAllAllowed:
    def test_multiple_allowed_capabilities(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(
            required_capabilities=[
                CapabilityIdentifier(namespace="development", name="plan"),
                CapabilityIdentifier(namespace="development", name="code_review"),
                CapabilityIdentifier(namespace="verification_gate", name="check"),
            ]
        )
        result = gate.authorize(decision)
        assert result.authorized is True
        assert result.outcome == PolicyDecision.ALLOWED
        assert len(result.denied_capabilities) == 0
        assert len(result.approval_required_capabilities) == 0


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-12: One denied capability among allowed capabilities → DENIED
# ─────────────────────────────────────────────────────────────────────────────

class TestOneDeniedAmongAllowed:
    def test_one_denied_blocks_all(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(
            required_capabilities=[
                CapabilityIdentifier(namespace="development", name="plan"),
                CapabilityIdentifier(namespace="unknown", name="cap"),
            ]
        )
        result = gate.authorize(decision)
        assert result.authorized is False
        assert result.outcome == PolicyDecision.DENIED
        assert CapabilityIdentifier(namespace="unknown", name="cap") in result.denied_capabilities


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-13: One approval-required capability → NEEDS_APPROVAL
# ─────────────────────────────────────────────────────────────────────────────

class TestOneApprovalRequired:
    def test_one_approval_required_blocks_all(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(
            required_capabilities=[
                CapabilityIdentifier(namespace="development", name="plan"),
                CapabilityIdentifier(namespace="git", name="commit"),
            ],
            actions=[],
        )
        result = gate.authorize(decision)
        assert result.authorized is False
        assert result.outcome == PolicyDecision.NEEDS_APPROVAL
        assert CapabilityIdentifier(namespace="git", name="commit") in result.approval_required_capabilities


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-14: Unknown capability among valid capabilities → DENIED
# ─────────────────────────────────────────────────────────────────────────────

class TestUnknownAmongValid:
    def test_unknown_among_valid_denied(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(
            required_capabilities=[
                CapabilityIdentifier(namespace="development", name="plan"),
                CapabilityIdentifier(namespace="development", name="test"),
                CapabilityIdentifier(namespace="evil", name="escalate"),
            ],
            actions=[],
        )
        result = gate.authorize(decision)
        assert result.authorized is False
        assert result.outcome == PolicyDecision.DENIED
        assert CapabilityIdentifier(namespace="evil", name="escalate") in result.denied_capabilities


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-15: Capability registry cannot contain executable payloads
# ─────────────────────────────────────────────────────────────────────────────

class TestRegistryNoExecutablePayloads:
    def test_registry_is_metadata_only(self):
        registry = create_default_registry()
        for key, cap_def in registry._capabilities.items():
            assert isinstance(cap_def.capability_id, CapabilityIdentifier)
            assert isinstance(cap_def.risk, RiskLevel)
            assert isinstance(cap_def.enabled, bool)
            assert isinstance(cap_def.approval_required, bool)
            assert cap_def.description is None or isinstance(cap_def.description, str)
            assert not callable(cap_def.risk)
            assert not callable(cap_def.enabled)


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-16: Model cannot create new registered capability
# ─────────────────────────────────────────────────────────────────────────────

class TestModelCannotCreateCapability:
    def test_unknown_capability_not_dynamically_registered(self):
        registry = create_default_registry()
        initial_count = len(registry._capabilities)
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(
            required_capabilities=[CapabilityIdentifier(namespace="model", name="invented")],
            actions=[],
        )
        result = gate.authorize(decision)
        assert len(registry._capabilities) == initial_count
        assert result.outcome == PolicyDecision.DENIED


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-17: Hostile capability name → DENIED
# ─────────────────────────────────────────────────────────────────────────────

class TestHostileCapabilityNames:
    @pytest.mark.parametrize("name", [
        "admin",
        "system",
        "root",
        "shell",
        "subprocess",
        "terminal",
        "filesystem",
        "git",
        "permission_engine",
        "tool_execution",
        "workspace",
        "verification_gate",
        "approval",
        "capability:*",
        "../../etc/passwd",
        "capability/x",
        "",
        "CAPITAL",
        "with space",
        "hyphen-ed",
        "with.dots.in.namespace",
    ])
    def test_hostile_namespace_denied(self, name):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        try:
            cap_id = CapabilityIdentifier(namespace=name, name="test")
        except ValueError:
            return
        decision = _make_decision(required_capabilities=[cap_id], actions=[])
        result = gate.authorize(decision)
        assert result.authorized is False
        assert result.outcome == PolicyDecision.DENIED


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-18: Oversized capability list → bounded/fail-closed
# ─────────────────────────────────────────────────────────────────────────────

class TestOversizedCapabilityList:
    def test_oversized_capability_list_bounded(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        caps = [CapabilityIdentifier(namespace="c", name=f"cap_{i}") for i in range(20)]
        decision = _make_decision(required_capabilities=caps, actions=[])
        result = gate.authorize(decision)
        assert result.authorized is False
        assert result.outcome == PolicyDecision.DENIED
        assert len(result.evaluated_capabilities) <= 20


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-19: Oversized capability identifier → bounded/fail-closed
# ─────────────────────────────────────────────────────────────────────────────

class TestOversizedCapabilityIdentifier:
    def test_oversized_identifier_bounded(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        long_name = "a" * 200
        try:
            cap_id = CapabilityIdentifier(namespace="dev", name=long_name)
        except ValueError:
            return
        decision = _make_decision(required_capabilities=[cap_id], actions=[])
        result = gate.authorize(decision)
        assert result.authorized is False
        assert result.outcome == PolicyDecision.DENIED


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-20: Malformed decision → fail-closed
# ─────────────────────────────────────────────────────────────────────────────

class TestMalformedDecisionFailClosed:
    def test_empty_required_capabilities_allowed(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(required_capabilities=[], actions=[])
        result = gate.authorize(decision)
        assert result.authorized is True
        assert result.outcome == PolicyDecision.ALLOWED

    def test_none_decision_raises(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        with pytest.raises((AIManagerInvalidDecisionError, AIManagerMalformedDecisionError, AttributeError)):
            gate.authorize(None)  # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-21: Policy evaluation is deterministic
# ─────────────────────────────────────────────────────────────────────────────

class TestPolicyDeterministic:
    def test_same_input_same_output(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(
            required_capabilities=[CapabilityIdentifier(namespace="development", name="plan")],
            actions=[],
        )
        result1 = gate.authorize(decision)
        result2 = gate.authorize(decision)
        assert result1.authorized == result2.authorized
        assert result1.outcome == result2.outcome
        assert result1.effective_risk == result2.effective_risk
        assert result1.denied_capabilities == result2.denied_capabilities
        assert result1.approval_required_capabilities == result2.approval_required_capabilities
        assert result1.policy_snapshot_id == result2.policy_snapshot_id


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-22: Single coherent policy snapshot is used
# ─────────────────────────────────────────────────────────────────────────────

class TestCoherentPolicySnapshot:
    def test_snapshot_id_consistent_within_evaluation(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision()
        result = gate.authorize(decision)
        assert result.policy_snapshot_id is not None
        assert len(result.policy_snapshot_id) == 16

    def test_different_policies_produce_different_snapshots(self):
        registry = create_default_registry()
        gate1 = PolicyCapabilityGate(registry=registry, policy=ApplicationPolicy(max_risk_level=RiskLevel.LOW))
        gate2 = PolicyCapabilityGate(registry=registry, policy=ApplicationPolicy(max_risk_level=RiskLevel.HIGH))
        decision = _make_decision()
        result1 = gate1.authorize(decision)
        result2 = gate2.authorize(decision)
        assert result1.policy_snapshot_id != result2.policy_snapshot_id


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-23: Policy cannot be bypassed by ManagerPlanningService
# ─────────────────────────────────────────────────────────────────────────────

class TestPolicyCannotBeBypassed:
    async def test_gate_blocks_unknown_capability(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(
            required_capabilities=[CapabilityIdentifier(namespace="unknown", name="bypass")],
            actions=[],
        )
        result = _make_result(decision=decision)
        plan_manager = _fake_plan_manager()
        service = ManagerPlanningService(plan_manager=plan_manager, policy_gate=gate)
        with pytest.raises(ManagerPlanningAuthorizationError, match="denied"):
            await service.plan_decision(result, project_id="proj")

    async def test_gate_blocks_disabled_capability(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(
            required_capabilities=[CapabilityIdentifier(namespace="shell", name="execute")],
            actions=[],
        )
        result = _make_result(decision=decision)
        plan_manager = _fake_plan_manager()
        service = ManagerPlanningService(plan_manager=plan_manager, policy_gate=gate)
        with pytest.raises(ManagerPlanningAuthorizationError, match="denied"):
            await service.plan_decision(result, project_id="proj")


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-24: Stage 9.3 outcome eligibility remains enforced
# ─────────────────────────────────────────────────────────────────────────────

class TestStage93EligibilityRemains:
    async def test_rejected_blocked_by_eligibility(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(outcome="REJECTED")
        result = ManagerResult(decision=None, outcome=DecisionOutcome.REJECTED)
        plan_manager = _fake_plan_manager()
        service = ManagerPlanningService(plan_manager=plan_manager, policy_gate=gate)
        with pytest.raises(ManagerPlanningNotEligibleError, match="REJECTED"):
            await service.plan_decision(result, project_id="proj")

    async def test_needs_clarification_blocked_by_eligibility(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(outcome="NEEDS_CLARIFICATION")
        result = _make_result(decision=decision, outcome="NEEDS_CLARIFICATION")
        plan_manager = _fake_plan_manager()
        service = ManagerPlanningService(plan_manager=plan_manager, policy_gate=gate)
        with pytest.raises(ManagerPlanningNotEligibleError, match="NEEDS_CLARIFICATION"):
            await service.plan_decision(result, project_id="proj")


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-25: REJECTED never reaches planning
# ─────────────────────────────────────────────────────────────────────────────

class TestRejectedNeverReachesPlanning:
    async def test_rejected_decision_none_blocks(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        result = ManagerResult(decision=None, outcome=DecisionOutcome.REJECTED)
        plan_manager = _fake_plan_manager()
        service = ManagerPlanningService(plan_manager=plan_manager, policy_gate=gate)
        with pytest.raises(ManagerPlanningNotEligibleError, match="REJECTED"):
            await service.plan_decision(result, project_id="proj")
        assert len(plan_manager._provider.calls) == 0  # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-26: NEEDS_CLARIFICATION never reaches planning
# ─────────────────────────────────────────────────────────────────────────────

class TestNeedsClarificationNeverReachesPlanning:
    async def test_needs_clarification_blocks(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(outcome="NEEDS_CLARIFICATION")
        result = _make_result(decision=decision, outcome="NEEDS_CLARIFICATION")
        plan_manager = _fake_plan_manager()
        service = ManagerPlanningService(plan_manager=plan_manager, policy_gate=gate)
        with pytest.raises(ManagerPlanningNotEligibleError, match="NEEDS_CLARIFICATION"):
            await service.plan_decision(result, project_id="proj")
        assert len(plan_manager._provider.calls) == 0  # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-27: NEEDS_APPROVAL never reaches planning
# ─────────────────────────────────────────────────────────────────────────────

class TestNeedsApprovalNeverReachesPlanning:
    async def test_needs_approval_blocks(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(outcome="NEEDS_APPROVAL")
        result = _make_result(decision=decision, outcome="NEEDS_APPROVAL")
        plan_manager = _fake_plan_manager()
        service = ManagerPlanningService(plan_manager=plan_manager, policy_gate=gate)
        with pytest.raises(ManagerPlanningNotEligibleError, match="NEEDS_APPROVAL"):
            await service.plan_decision(result, project_id="proj")
        assert len(plan_manager._provider.calls) == 0  # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-28: READY_FOR_PLANNING + DENIED capability never reaches planning
# ─────────────────────────────────────────────────────────────────────────────

class TestReadyForPlanningDeniedBlocked:
    async def test_ready_for_planning_with_denied_capability(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(
            outcome="READY_FOR_PLANNING",
            required_capabilities=[CapabilityIdentifier(namespace="unknown", name="evil")],
            actions=[],
        )
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        plan_manager = _fake_plan_manager()
        service = ManagerPlanningService(plan_manager=plan_manager, policy_gate=gate)
        with pytest.raises(ManagerPlanningAuthorizationError, match="denied"):
            await service.plan_decision(result, project_id="proj")
        assert len(plan_manager._provider.calls) == 0  # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-29: READY_FOR_PLANNING + NEEDS_APPROVAL never reaches planning
# ─────────────────────────────────────────────────────────────────────────────

class TestReadyForPlanningNeedsApprovalBlocked:
    async def test_ready_for_planning_with_approval_required(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(
            outcome="READY_FOR_PLANNING",
            required_capabilities=[CapabilityIdentifier(namespace="git", name="commit")],
            actions=[],
        )
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        plan_manager = _fake_plan_manager()
        service = ManagerPlanningService(plan_manager=plan_manager, policy_gate=gate)
        with pytest.raises(ManagerPlanningNotEligibleError, match="approval"):
            await service.plan_decision(result, project_id="proj")
        assert len(plan_manager._provider.calls) == 0  # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-30: READY_FOR_PLANNING + ALLOWED capabilities reaches planning
# ─────────────────────────────────────────────────────────────────────────────

class TestReadyForPlanningAllowedReachesPlanning:
    async def test_ready_for_planning_with_allowed_capabilities(self):
        registry = create_default_registry()
        gate = PolicyCapabilityGate(registry=registry)
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        service = ManagerPlanningService(plan_manager=plan_manager, policy_gate=gate)
        planning_result = await service.plan_decision(result, project_id="proj")
        assert planning_result.decision_outcome == DecisionOutcome.READY_FOR_PLANNING
        assert planning_result.manager_plan_result.plan_id.startswith("plan_")
        assert len(plan_manager._provider.calls) == 1  # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-31: No filesystem side effects
# ─────────────────────────────────────────────────────────────────────────────

class TestNoFilesystemSideEffects:
    def _get_imports(self, filename: str):
        source = Path(AI_MANAGER_ROOT / filename).read_text()
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
        return imports

    def test_gate_no_filesystem_imports(self):
        imports = self._get_imports("gate.py")
        for imp in imports:
            assert "pathlib" not in imp
            assert "shutil" not in imp

    def test_capabilities_no_filesystem_imports(self):
        imports = self._get_imports("capabilities.py")
        for imp in imports:
            assert "pathlib" not in imp
            assert "shutil" not in imp

    def test_policy_no_filesystem_imports(self):
        imports = self._get_imports("policy.py")
        for imp in imports:
            assert "pathlib" not in imp
            assert "shutil" not in imp


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-32: No subprocess/shell side effects
# ─────────────────────────────────────────────────────────────────────────────

class TestNoSubprocessSideEffects:
    def _get_imports(self, filename: str):
        source = Path(AI_MANAGER_ROOT / filename).read_text()
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
        return imports

    def _get_code_body(self, filename: str) -> str:
        source = Path(AI_MANAGER_ROOT / filename).read_text()
        tree = ast.parse(source)
        body_parts = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = ast.get_source_segment(source, node) or ""
                body_parts.append(body)
        return "\n".join(body_parts)

    def test_gate_no_subprocess_imports(self):
        imports = self._get_imports("gate.py")
        assert not any("subprocess" in imp for imp in imports)
        assert not any("shutil" in imp for imp in imports)
        assert not any("os" in imp for imp in imports)

    def test_gate_no_subprocess_in_code(self):
        body = self._get_code_body("gate.py")
        assert "subprocess" not in body
        assert "os.system" not in body
        assert "shell=True" not in body

    def test_capabilities_no_subprocess(self):
        source = Path(AI_MANAGER_ROOT / "capabilities.py").read_text()
        assert "subprocess" not in source

    def test_policy_no_subprocess(self):
        source = Path(AI_MANAGER_ROOT / "policy.py").read_text()
        assert "subprocess" not in source


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-33: No Git side effects
# ─────────────────────────────────────────────────────────────────────────────

class TestNoGitSideEffects:
    def _get_code_body(self, filename: str) -> str:
        source = Path(AI_MANAGER_ROOT / filename).read_text()
        tree = ast.parse(source)
        body_parts = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = ast.get_source_segment(source, node) or ""
                body_parts.append(body)
        return "\n".join(body_parts)

    def test_gate_no_git_imports(self):
        imports = []
        source = Path(AI_MANAGER_ROOT / "gate.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
        for imp in imports:
            assert not any("git" in i.lower() for i in [imp])

    def test_gate_no_git_in_code(self):
        body = self._get_code_body("gate.py")
        assert "git" not in body.lower()


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-34: No tool execution
# ─────────────────────────────────────────────────────────────────────────────

class TestNoToolExecution:
    def _get_code_body(self, filename: str) -> str:
        source = Path(AI_MANAGER_ROOT / filename).read_text()
        tree = ast.parse(source)
        body_parts = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = ast.get_source_segment(source, node) or ""
                body_parts.append(body)
        return "\n".join(body_parts)

    def test_gate_no_tool_execution_service(self):
        body = self._get_code_body("gate.py")
        assert "ToolExecutionService" not in body
        assert "execute_tool" not in body
        assert "execute_action" not in body

    def test_capabilities_no_tool_execution(self):
        source = Path(AI_MANAGER_ROOT / "capabilities.py").read_text()
        assert "ToolExecutionService" not in source


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-35: No PermissionEngine bypass
# ─────────────────────────────────────────────────────────────────────────────

class TestNoPermissionEngineBypass:
    def _get_code_body(self, filename: str) -> str:
        source = Path(AI_MANAGER_ROOT / filename).read_text()
        tree = ast.parse(source)
        body_parts = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = ast.get_source_segment(source, node) or ""
                body_parts.append(body)
        return "\n".join(body_parts)

    def test_gate_no_permission_engine_import(self):
        imports = []
        source = Path(AI_MANAGER_ROOT / "gate.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
        assert "PermissionEngine" not in imports

    def test_gate_no_permission_engine_in_code(self):
        body = self._get_code_body("gate.py")
        assert "PermissionEngine" not in body

    def test_capabilities_no_permission_engine_import(self):
        source = Path(AI_MANAGER_ROOT / "capabilities.py").read_text()
        assert "PermissionEngine" not in source


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-36: No VerificationGate access
# ─────────────────────────────────────────────────────────────────────────────

class TestNoVerificationGateAccess:
    def _get_code_body(self, filename: str) -> str:
        source = Path(AI_MANAGER_ROOT / filename).read_text()
        tree = ast.parse(source)
        body_parts = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = ast.get_source_segment(source, node) or ""
                body_parts.append(body)
        return "\n".join(body_parts)

    def test_gate_no_verification_gate(self):
        body = self._get_code_body("gate.py")
        assert "VerificationGate" not in body
        assert "verification_gate" not in body

    def test_policy_no_verification_gate(self):
        body = self._get_code_body("policy.py")
        assert "VerificationGate" not in body


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-37: No RunController access
# ─────────────────────────────────────────────────────────────────────────────

class TestNoRunControllerAccess:
    def _get_code_body(self, filename: str) -> str:
        source = Path(AI_MANAGER_ROOT / filename).read_text()
        tree = ast.parse(source)
        body_parts = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = ast.get_source_segment(source, node) or ""
                body_parts.append(body)
        return "\n".join(body_parts)

    def test_gate_no_run_controller(self):
        body = self._get_code_body("gate.py")
        assert "RunController" not in body
        assert "run_controller" not in body

    def test_capabilities_no_run_controller(self):
        body = self._get_code_body("capabilities.py")
        assert "RunController" not in body


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-38: No AutonomousPlanExecutor access
# ─────────────────────────────────────────────────────────────────────────────

class TestNoAutonomousPlanExecutorAccess:
    def _get_code_body(self, filename: str) -> str:
        source = Path(AI_MANAGER_ROOT / filename).read_text()
        tree = ast.parse(source)
        body_parts = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = ast.get_source_segment(source, node) or ""
                body_parts.append(body)
        return "\n".join(body_parts)

    def test_gate_no_autonomous_plan_executor(self):
        body = self._get_code_body("gate.py")
        assert "AutonomousPlanExecutor" not in body
        assert "autonomous_plan_executor" not in body

    def test_bridge_no_autonomous_plan_executor(self):
        source = Path(AI_MANAGER_ROOT / "bridge.py").read_text()
        # Remove module and class/function docstrings before checking
        lines = source.splitlines()
        cleaned = []
        in_docstring = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('"""') and not in_docstring:
                in_docstring = True
                continue
            if stripped.startswith('"""') and in_docstring:
                in_docstring = False
                continue
            if in_docstring:
                continue
            cleaned.append(line)
        code_only = "\n".join(cleaned)
        assert "AutonomousPlanExecutor" not in code_only


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-39: No background execution
# ─────────────────────────────────────────────────────────────────────────────

class TestNoBackgroundExecution:
    def test_gate_no_background_tasks(self):
        source = Path(AI_MANAGER_ROOT / "gate.py").read_text()
        assert "asyncio.create_task" not in source
        assert "asyncio.ensure_future" not in source
        assert "BackgroundTasks" not in source
        assert "celery" not in source.lower()


# ─────────────────────────────────────────────────────────────────────────────
# POLICY-40: No secret/credential access
# ─────────────────────────────────────────────────────────────────────────────

class TestNoSecretCredentialAccess:
    def test_gate_no_secret_patterns(self):
        source = Path(AI_MANAGER_ROOT / "gate.py").read_text()
        forbidden_patterns = [
            "api_key",
            "secret",
            "password",
            "credential",
            "token",
            "private_key",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in source.lower(), f"Gate contains forbidden pattern: {pattern}"

    def test_capabilities_no_secret_patterns(self):
        source = Path(AI_MANAGER_ROOT / "capabilities.py").read_text()
        forbidden_patterns = ["api_key", "secret", "password", "credential", "token", "private_key"]
        for pattern in forbidden_patterns:
            assert pattern not in source.lower(), f"Capabilities contains forbidden pattern: {pattern}"
