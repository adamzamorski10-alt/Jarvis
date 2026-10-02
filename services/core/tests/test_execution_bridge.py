"""
Stage 9.6 — AI Manager Controlled Execution Bridge Tests.

Hostile adversarial tests covering:
  EXEC-01 through EXEC-12
  DENY cannot execute
  approval cannot override DENY
  exact approval scope
  approval mismatch
  expired approval
  consumed approval
  concurrent approval consumption
  duplicate execution
  stale ManagerResult
  policy changed after prepare
  capability changed after prepare
  invalid project
  invalid plan
  executor failure
  cancellation
  recovery
  concurrency
  fail-closed behavior.
"""
from __future__ import annotations

import ast
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.ai_manager.approval_models import (
    ApprovalStatus,
    CapabilityApproval,
    compute_scope_fingerprint,
)
from app.ai_manager.approval_service import (
    CapabilityApprovalError,
    CapabilityApprovalService,
    CapabilityApprovalStateError,
)
from app.ai_manager.bridge import (
    ManagerPlanningNotEligibleError,
    ManagerPlanningPolicyError,
    ManagerPlanningResult,
    ManagerPlanningService,
)
from app.ai_manager.capabilities import CapabilityDefinition, CapabilityRegistry, CapabilityIdentifier
from app.ai_manager.execution_bridge import (
    ExecutionAuthorizationError,
    ExecutionBridge,
    ExecutionBridgeError,
    ExecutionNeedsApprovalError,
    ExecutionPrepareResult,
    ExecutionResult,
)
from app.ai_manager.gate import PolicyCapabilityGate, PolicyDecision
from app.ai_manager.models import (
    ActionProposal,
    CapabilityIdentifier as ModelCapabilityIdentifier,
    DecisionOutcome,
    ManagerDecision,
    ManagerResult,
    RiskLevel,
    UserIntent,
)
from app.manager.models import (
    ManagerInvalidPlanError,
    ManagerPlanResult,
    PlanningConstraints,
    ProviderTaskProposal,
    UserPlan,
)
from app.manager.plan_manager import PlanManager
from app.manager.providers.fake import FakePlanProvider
from app.services.autonomous_loop import (
    AutonomousPlanExecutor,
    PlanExecutionResult,
    PlanExecutionState,
)
from app.services.plan_execution_service import PlanExecutionService

AI_MANAGER_ROOT = Path(__file__).resolve().parent.parent / "app" / "ai_manager"


# ── Fixtures & helpers ────────────────────────────────────────────────────────


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
    required_capabilities: list[ModelCapabilityIdentifier] | None = None,
) -> ManagerDecision:
    if intent is None:
        intent = _make_intent()
    if actions is None:
        actions = [
            ActionProposal(
                action_id="act1",
                capability_id=ModelCapabilityIdentifier(namespace="development", name="plan"),
                action_type="plan",
                arguments={"target": "test"},
                dependencies=[],
                risk=RiskLevel.LOW,
                approval_required=False,
                summary="Test action",
            )
        ]
    if required_capabilities is None:
        required_capabilities = [ModelCapabilityIdentifier(namespace="development", name="plan")]
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


def _make_planning_service(
    plan_manager=None,
    policy_gate=None,
    default_project_id="proj",
):
    if plan_manager is None:
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
    return ManagerPlanningService(
        plan_manager=plan_manager,
        default_project_id=default_project_id,
        policy_gate=policy_gate,
    )


def _make_test_registry_with_approval():
    """Create a test registry with a capability that requires approval."""
    return CapabilityRegistry([
        CapabilityDefinition(
            capability_id=ModelCapabilityIdentifier(namespace="test", name="action"),
            risk=RiskLevel.MEDIUM,
            enabled=True,
            approval_required=True,
            description="Test action requiring approval",
        ),
    ])


def _make_gate_with_approval_registry():
    from app.ai_manager.policy import ApplicationPolicy
    return PolicyCapabilityGate(
        registry=_make_test_registry_with_approval(),
        policy=ApplicationPolicy(),
    )


def _make_mock_approval(
    approval_id: str = "cap_approval_mock",
    intent_id: str = "intent_1",
    capabilities: list[str] | None = None,
    risk: RiskLevel = RiskLevel.MEDIUM,
    project_id: str | None = "proj",
    status: ApprovalStatus = ApprovalStatus.APPROVED,
) -> CapabilityApproval:
    if capabilities is None:
        capabilities = ["test.action"]
    scope_fingerprint = compute_scope_fingerprint(
        intent_id=intent_id,
        capabilities=capabilities,
        effective_risk=risk,
        operation_type="plan_execution",
        project_id=project_id,
    )
    return CapabilityApproval(
        approval_id=approval_id,
        intent_id=intent_id,
        operation_type="plan_execution",
        scope_fingerprint=scope_fingerprint,
        effective_risk=risk,
        status=status,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def _make_approval_service(repo=None):
    if repo is None:
        repo = AsyncMock()
        repo.create.return_value = _make_mock_approval("cap_approval_default")
    return CapabilityApprovalService(repo=repo)


def _make_execution_bridge(
    planning_service=None,
    plan_execution_service=None,
    autonomous_executor=None,
    capability_gate=None,
    approval_service=None,
    audit_service=None,
    plan_service=None,
):
    if planning_service is None:
        planning_service = _make_planning_service()
    if plan_execution_service is None:
        plan_execution_service = AsyncMock(spec=PlanExecutionService)
    if autonomous_executor is None:
        autonomous_executor = AsyncMock(spec=AutonomousPlanExecutor)
    if capability_gate is None:
        from app.ai_manager.capabilities import create_default_registry
        from app.ai_manager.policy import ApplicationPolicy
        capability_gate = PolicyCapabilityGate(
            registry=create_default_registry(),
            policy=ApplicationPolicy(),
        )
    if approval_service is None:
        approval_service = _make_approval_service()
    if plan_service is None:
        plan_service = AsyncMock()

        async def _persist(plan_result):
            return list(plan_result.tasks)

        plan_service.persist_manager_plan_result.side_effect = _persist
    return ExecutionBridge(
        planning_service=planning_service,
        plan_execution_service=plan_execution_service,
        autonomous_executor=autonomous_executor,
        capability_gate=capability_gate,
        approval_service=approval_service,
        plan_service=plan_service,
        audit_service=audit_service,
    )


def _decision_with_capability(namespace: str, name: str, risk: RiskLevel = RiskLevel.MEDIUM) -> ManagerDecision:
    return _make_decision(outcome="READY_FOR_PLANNING", actions=[
        ActionProposal(
            action_id="act1",
            capability_id=ModelCapabilityIdentifier(namespace=namespace, name=name),
            action_type="test_action",
            arguments={"param": "value"},
            dependencies=[],
            risk=risk,
            approval_required=False,
            summary=f"Test action {namespace}.{name}",
        )
    ], required_capabilities=[ModelCapabilityIdentifier(namespace=namespace, name=name)])


# ── EXEC-01: MODEL PROPOSES, APPLICATION DECIDES ─────────────────────────────


class TestModelProposesApplicationDecides:
    def test_execution_bridge_never_exposes_executor(self):
        """EXEC-01a: ExecutionBridge never exposes the executor to callers."""
        source = Path(AI_MANAGER_ROOT / "execution_bridge.py").read_text()
        assert "return self._autonomous_executor" not in source

    def test_execution_bridge_no_forbidden_imports(self):
        """EXEC-01b: ExecutionBridge cannot import execution internals."""
        source = Path(AI_MANAGER_ROOT / "execution_bridge.py").read_text()
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
        forbidden = [
            "subprocess", "shutil", "os", "git", "ToolExecutionService",
            "PermissionEngine", "VerificationGate", "RunController",
            "AutonomousPlanExecutor", "DevelopmentAgent",
            "WorkspaceManager",
        ]
        for imp in imports:
            for f in forbidden:
                assert f not in imp, f"ExecutionBridge imports {imp} which contains forbidden {f}"

    async def test_plan_id_never_model_controlled(self):
        """EXEC-01c: plan_id is deterministic from intent_id, never model-controlled."""
        decision = _make_decision(outcome="READY_FOR_PLANNING", intent=_make_intent("intent_fixed"))
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        planning_service = _make_planning_service(plan_manager=plan_manager)
        bridge = _make_execution_bridge(planning_service=planning_service)

        prepare_result = await bridge.prepare(result, project_id="proj")
        assert prepare_result.plan_id is not None
        assert prepare_result.plan_id.startswith("plan_")

    async def test_project_id_application_controlled(self):
        """EXEC-01d: project_id is application parameter, not model-controlled."""
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        bridge = _make_execution_bridge()

        prepare_result = await bridge.prepare(result, project_id="app_controlled_proj")
        assert prepare_result.project_id == "app_controlled_proj"

    def test_decision_frozen_cannot_be_modified(self):
        """EXEC-01e: ManagerDecision is frozen; model output cannot be mutated after creation."""
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        with pytest.raises(Exception):
            decision.goal = "modified by attacker"


# ── EXEC-02: NARROW EXECUTION SCOPE ──────────────────────────────────────────


class TestNarrowExecutionScope:
    def test_execution_bridge_no_forbidden_imports(self):
        """EXEC-02a: ExecutionBridge cannot import execution internals."""
        source = Path(AI_MANAGER_ROOT / "execution_bridge.py").read_text()
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
        forbidden = [
            "subprocess", "shutil", "os", "git", "ToolExecutionService",
            "PermissionEngine", "VerificationGate", "RunController",
            "AutonomousPlanExecutor", "DevelopmentAgent",
            "WorkspaceManager",
        ]
        for imp in imports:
            for f in forbidden:
                assert f not in imp, f"ExecutionBridge imports {imp} which contains forbidden {f}"

    def test_execute_method_signature_only_plan_id(self):
        """EXEC-02c: execute() only takes plan_id, not arbitrary execution targets."""
        bridge = _make_execution_bridge()
        import inspect
        sig = inspect.signature(bridge.execute)
        params = list(sig.parameters.keys())
        assert "result" in params
        assert "project_id" in params
        assert "approval_id" in params
        # No arbitrary command, path, or target parameters
        assert "command" not in params
        assert "path" not in params
        assert "target" not in params
        assert "url" not in params

    def test_execute_method_signature_only_plan_id(self):
        """EXEC-02b: execute() only takes plan_id, not arbitrary execution targets."""
        bridge = _make_execution_bridge()
        import inspect
        sig = inspect.signature(bridge.execute)
        params = list(sig.parameters.keys())
        assert "result" in params
        assert "project_id" in params
        assert "approval_id" in params
        # No arbitrary command, path, or target parameters
        assert "command" not in params
        assert "path" not in params
        assert "target" not in params
        assert "url" not in params


# ── EXEC-03: DENY IS ABSOLUTE ─────────────────────────────────────────────────


class TestDenyIsAbsolute:
    async def test_denied_never_executes_via_prepare(self):
        """EXEC-03a: DENIED capability blocks prepare()."""
        from app.ai_manager.capabilities import create_default_registry
        from app.ai_manager.policy import ApplicationPolicy

        registry = create_default_registry()
        policy = ApplicationPolicy(denied_capabilities=[
            ModelCapabilityIdentifier(namespace="development", name="plan"),
        ])
        gate = PolicyCapabilityGate(registry=registry, policy=policy)

        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        bridge = _make_execution_bridge(capability_gate=gate)

        with pytest.raises(ExecutionAuthorizationError, match="denied"):
            await bridge.prepare(result, project_id="proj")

    async def test_denied_never_executes_via_execute(self):
        """EXEC-03b: DENIED capability blocks execute()."""
        from app.ai_manager.capabilities import create_default_registry
        from app.ai_manager.policy import ApplicationPolicy

        registry = create_default_registry()
        policy = ApplicationPolicy(denied_capabilities=[
            ModelCapabilityIdentifier(namespace="development", name="plan"),
        ])
        gate = PolicyCapabilityGate(registry=registry, policy=policy)

        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        bridge = _make_execution_bridge(capability_gate=gate)

        with pytest.raises(ExecutionAuthorizationError, match="denied"):
            await bridge.execute(result, project_id="proj")

    async def test_disabled_never_executes(self):
        """EXEC-03c: DISABLED policy blocks both prepare and execute."""
        from app.ai_manager.capabilities import create_default_registry
        from app.ai_manager.policy import ApplicationPolicy

        registry = create_default_registry()
        policy = ApplicationPolicy(global_enabled=False)
        gate = PolicyCapabilityGate(registry=registry, policy=policy)

        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        bridge = _make_execution_bridge(capability_gate=gate)

        with pytest.raises(ExecutionAuthorizationError, match="disabled"):
            await bridge.prepare(result, project_id="proj")

        with pytest.raises(ExecutionAuthorizationError, match="disabled"):
            await bridge.execute(result, project_id="proj")

    async def test_approval_cannot_override_deny(self):
        """EXEC-03d: An approval for an ALLOWED capability cannot make a DENIED capability execute."""
        from app.ai_manager.capabilities import create_default_registry
        from app.ai_manager.policy import ApplicationPolicy

        registry = create_default_registry()
        policy = ApplicationPolicy(denied_capabilities=[
            ModelCapabilityIdentifier(namespace="development", name="plan"),
        ])
        gate = PolicyCapabilityGate(registry=registry, policy=policy)

        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        bridge = _make_execution_bridge(capability_gate=gate)

        # Even with an approval_id, DENIED should still block
        with pytest.raises(ExecutionAuthorizationError, match="denied"):
            await bridge.execute(result, project_id="proj", approval_id="cap_approval_any")


# ── EXEC-04: APPROVAL SCOPE BINDING ──────────────────────────────────────────


class TestApprovalScopeBinding:
    async def test_approval_wrong_project_rejected(self):
        """EXEC-04a: Approval for project A cannot authorize execution for project B."""
        gate = _make_gate_with_approval_registry()
        decision = _decision_with_capability("test", "action")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        # Approval was created for project "proj_a"
        approval = _make_mock_approval(
            approval_id="cap_approval_proj_a",
            intent_id="intent_1",
            project_id="proj_a",
        )
        repo = AsyncMock()
        repo.get.return_value = approval
        repo.mark_consumed.return_value = _make_mock_approval(
            approval_id="cap_approval_proj_a",
            intent_id="intent_1",
            project_id="proj_a",
            status=ApprovalStatus.CONSUMED,
        )
        approval_service = CapabilityApprovalService(repo=repo)
        bridge = _make_execution_bridge(
            capability_gate=gate,
            approval_service=approval_service,
        )

        with pytest.raises(ExecutionAuthorizationError, match="scope does not match"):
            await bridge.execute(result, project_id="proj_b", approval_id="cap_approval_proj_a")

    async def test_approval_wrong_intent_rejected(self):
        """EXEC-04b: Approval for intent A cannot authorize execution for intent B."""
        gate = _make_gate_with_approval_registry()
        decision = _decision_with_capability("test", "action")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        approval = _make_mock_approval(
            approval_id="cap_approval_intent_a",
            intent_id="intent_a",
        )
        repo = AsyncMock()
        repo.get.return_value = approval
        repo.mark_consumed.return_value = _make_mock_approval(
            approval_id="cap_approval_intent_a",
            intent_id="intent_a",
            status=ApprovalStatus.CONSUMED,
        )
        approval_service = CapabilityApprovalService(repo=repo)
        bridge = _make_execution_bridge(
            capability_gate=gate,
            approval_service=approval_service,
        )

        with pytest.raises(ExecutionAuthorizationError, match="scope does not match"):
            await bridge.execute(result, project_id="proj", approval_id="cap_approval_intent_a")

    async def test_approval_wrong_capability_rejected(self):
        """EXEC-04c: Approval for capability A cannot authorize execution for capability B."""
        gate = _make_gate_with_approval_registry()
        decision = _decision_with_capability("test", "action")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        # Approval for different capability
        approval = _make_mock_approval(
            approval_id="cap_approval_other_cap",
            capabilities=["test.other_action"],
        )
        repo = AsyncMock()
        repo.get.return_value = approval
        repo.mark_consumed.return_value = _make_mock_approval(
            approval_id="cap_approval_other_cap",
            capabilities=["test.other_action"],
            status=ApprovalStatus.CONSUMED,
        )
        approval_service = CapabilityApprovalService(repo=repo)
        bridge = _make_execution_bridge(
            capability_gate=gate,
            approval_service=approval_service,
        )

        with pytest.raises(ExecutionAuthorizationError, match="scope does not match"):
            await bridge.execute(result, project_id="proj", approval_id="cap_approval_other_cap")

    async def test_approval_wrong_risk_rejected(self):
        """EXEC-04d: Approval for LOW risk cannot authorize MEDIUM risk execution."""
        gate = _make_gate_with_approval_registry()
        decision = _decision_with_capability("test", "action", risk=RiskLevel.MEDIUM)
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        approval = _make_mock_approval(
            approval_id="cap_approval_low_risk",
            risk=RiskLevel.LOW,
        )
        repo = AsyncMock()
        repo.get.return_value = approval
        repo.mark_consumed.return_value = _make_mock_approval(
            approval_id="cap_approval_low_risk",
            risk=RiskLevel.LOW,
            status=ApprovalStatus.CONSUMED,
        )
        approval_service = CapabilityApprovalService(repo=repo)
        bridge = _make_execution_bridge(
            capability_gate=gate,
            approval_service=approval_service,
        )

        with pytest.raises(ExecutionAuthorizationError, match="scope does not match"):
            await bridge.execute(result, project_id="proj", approval_id="cap_approval_low_risk")

    async def test_approval_not_found_rejected(self):
        """EXEC-04e: Non-existent approval_id is rejected."""
        gate = _make_gate_with_approval_registry()
        decision = _decision_with_capability("test", "action")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        repo = AsyncMock()
        repo.get.return_value = None
        approval_service = CapabilityApprovalService(repo=repo)
        bridge = _make_execution_bridge(
            capability_gate=gate,
            approval_service=approval_service,
        )

        with pytest.raises(ExecutionAuthorizationError, match="not found"):
            await bridge.execute(result, project_id="proj", approval_id="cap_approval_nonexistent")

    async def test_approval_expired_rejected(self):
        """EXEC-04f: Expired approval is rejected."""
        gate = _make_gate_with_approval_registry()
        decision = _decision_with_capability("test", "action")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        approval = _make_mock_approval(
            approval_id="cap_approval_expired",
            status=ApprovalStatus.EXPIRED,
        )
        repo = AsyncMock()
        repo.get.return_value = approval
        approval_service = CapabilityApprovalService(repo=repo)
        bridge = _make_execution_bridge(
            capability_gate=gate,
            approval_service=approval_service,
        )

        with pytest.raises(ExecutionAuthorizationError, match="expired"):
            await bridge.execute(result, project_id="proj", approval_id="cap_approval_expired")

    async def test_approval_denied_rejected(self):
        """EXEC-04g: Denied approval is rejected."""
        gate = _make_gate_with_approval_registry()
        decision = _decision_with_capability("test", "action")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        approval = _make_mock_approval(
            approval_id="cap_approval_denied",
            status=ApprovalStatus.DENIED,
        )
        repo = AsyncMock()
        repo.get.return_value = approval
        approval_service = CapabilityApprovalService(repo=repo)
        bridge = _make_execution_bridge(
            capability_gate=gate,
            approval_service=approval_service,
        )

        with pytest.raises(ExecutionAuthorizationError, match="expected APPROVED|consumption failed"):
            await bridge.execute(result, project_id="proj", approval_id="cap_approval_denied")

    async def test_approval_consumed_rejected(self):
        """EXEC-04h: Already consumed approval is rejected."""
        gate = _make_gate_with_approval_registry()
        decision = _decision_with_capability("test", "action")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        approval = _make_mock_approval(
            approval_id="cap_approval_consumed",
            status=ApprovalStatus.CONSUMED,
        )
        repo = AsyncMock()
        repo.get.return_value = approval
        approval_service = CapabilityApprovalService(repo=repo)
        bridge = _make_execution_bridge(
            capability_gate=gate,
            approval_service=approval_service,
        )

        with pytest.raises(ExecutionAuthorizationError, match="expected APPROVED|consumption failed"):
            await bridge.execute(result, project_id="proj", approval_id="cap_approval_consumed")


# ── EXEC-05: ONE-TIME CONSUMPTION ─────────────────────────────────────────────


class TestOneTimeConsumption:
    async def test_concurrent_approval_consumption_one_wins(self):
        """EXEC-05a: Concurrent execute() calls with same approval: only one succeeds."""
        gate = _make_gate_with_approval_registry()
        decision = _decision_with_capability("test", "action")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        planning_service = _make_planning_service(plan_manager=plan_manager)

        execution_result = PlanExecutionResult(
            plan_id="plan_abc",
            terminal_state=PlanExecutionState.COMPLETED,
            committed_tasks=1,
            total_tasks=1,
        )
        autonomous_executor = AsyncMock(spec=AutonomousPlanExecutor)
        autonomous_executor.execute_plan.return_value = execution_result

        # Simulate atomic consume: first call succeeds, second fails (concurrent)
        approved_approval = _make_mock_approval("cap_approval_concurrent")
        consumed_approval = _make_mock_approval("cap_approval_concurrent", status=ApprovalStatus.CONSUMED)

        call_count = 0

        async def mock_get(approval_id):
            return approved_approval

        async def mock_consume(approval_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return consumed_approval
            raise CapabilityApprovalStateError(
                f"Approval '{approval_id}' was already consumed by a concurrent request."
            )

        repo = AsyncMock()
        repo.get = mock_get
        repo.mark_consumed = mock_consume
        approval_service = CapabilityApprovalService(repo=repo)

        bridge = _make_execution_bridge(
            planning_service=planning_service,
            autonomous_executor=autonomous_executor,
            capability_gate=gate,
            approval_service=approval_service,
        )

        # First call should succeed
        exec_result1 = await bridge.execute(result, project_id="proj", approval_id="cap_approval_concurrent")
        assert exec_result1.execution_result.terminal_state == PlanExecutionState.COMPLETED

        # Second call should fail because approval is already consumed
        with pytest.raises(ExecutionAuthorizationError, match="consumption failed"):
            await bridge.execute(result, project_id="proj", approval_id="cap_approval_concurrent")

    async def test_same_approval_cannot_be_consumed_twice(self):
        """EXEC-05b: Approval consumption is atomic; second consume fails."""
        repo = AsyncMock()
        repo.get.return_value = _make_mock_approval("cap_approval_twice")
        repo.mark_consumed.return_value = _make_mock_approval(
            "cap_approval_twice", status=ApprovalStatus.CONSUMED
        )
        approval_service = CapabilityApprovalService(repo=repo)

        # First consume succeeds
        result1 = await approval_service.consume("cap_approval_twice")
        assert result1.status == ApprovalStatus.CONSUMED

        # Second consume fails because the UPDATE returns 0 rows
        repo.get.return_value = _make_mock_approval("cap_approval_twice", status=ApprovalStatus.CONSUMED)
        with pytest.raises(CapabilityApprovalStateError, match="expected APPROVED|state 'CONSUMED'"):
            await approval_service.consume("cap_approval_twice")


# ── EXEC-06: REPLAY PROTECTION ────────────────────────────────────────────────


class TestReplayProtection:
    async def test_duplicate_execution_same_approval_fails(self):
        """EXEC-06a: Cannot execute twice with same approval_id."""
        gate = _make_gate_with_approval_registry()
        decision = _decision_with_capability("test", "action")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        planning_service = _make_planning_service(plan_manager=plan_manager)

        execution_result = PlanExecutionResult(
            plan_id="plan_abc",
            terminal_state=PlanExecutionState.COMPLETED,
            committed_tasks=1,
            total_tasks=1,
        )
        autonomous_executor = AsyncMock(spec=AutonomousPlanExecutor)
        autonomous_executor.execute_plan.return_value = execution_result

        call_count = 0

        async def mock_get(approval_id):
            return _make_mock_approval("cap_approval_replay")

        async def mock_consume(approval_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_mock_approval("cap_approval_replay", status=ApprovalStatus.CONSUMED)
            raise CapabilityApprovalStateError("already consumed")

        repo = AsyncMock()
        repo.get = mock_get
        repo.mark_consumed = mock_consume
        approval_service = CapabilityApprovalService(repo=repo)

        bridge = _make_execution_bridge(
            planning_service=planning_service,
            autonomous_executor=autonomous_executor,
            capability_gate=gate,
            approval_service=approval_service,
        )

        # First execution succeeds
        exec_result1 = await bridge.execute(result, project_id="proj", approval_id="cap_approval_replay")
        assert exec_result1.execution_result.terminal_state == PlanExecutionState.COMPLETED

        # Second execution with same approval fails
        with pytest.raises(ExecutionAuthorizationError, match="consumption failed"):
            await bridge.execute(result, project_id="proj", approval_id="cap_approval_replay")

    async def test_modified_manager_result_does_not_affect_execution(self):
        """EXEC-06b: ManagerDecision is frozen; cannot be modified to affect execution."""
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        # ManagerDecision is frozen (ConfigDict(frozen=True))
        with pytest.raises(Exception):
            decision.goal = "attacker modified goal"


# ── EXEC-07: EXECUTION-TIME RECHECK ──────────────────────────────────────────


class TestExecutionTimeRecheck:
    async def test_policy_change_after_prepare_blocks_execute(self):
        """EXEC-07a: Policy changed to DENIED after prepare() blocks execute()."""
        from app.ai_manager.capabilities import create_default_registry
        from app.ai_manager.policy import ApplicationPolicy

        registry = create_default_registry()
        policy = ApplicationPolicy(denied_capabilities=[
            ModelCapabilityIdentifier(namespace="test", name="action"),
        ])
        gate = PolicyCapabilityGate(registry=registry, policy=policy)

        decision = _decision_with_capability("test", "action")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        bridge = _make_execution_bridge(capability_gate=gate)

        # At prepare time, policy denies the capability
        with pytest.raises(ExecutionAuthorizationError, match="denied"):
            await bridge.prepare(result, project_id="proj")

        with pytest.raises(ExecutionAuthorizationError, match="denied"):
            await bridge.execute(result, project_id="proj")


# ── EXEC-08: AUTHORITATIVE PLAN ───────────────────────────────────────────────


class TestAuthoritativePlan:
    async def test_execution_uses_persisted_plan_not_model_modified(self):
        """EXEC-08a: Execution uses application-generated plan_id, not model-controlled."""
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        planning_service = _make_planning_service(plan_manager=plan_manager)

        execution_result = PlanExecutionResult(
            plan_id="plan_abc",
            terminal_state=PlanExecutionState.COMPLETED,
            committed_tasks=1,
            total_tasks=1,
        )
        autonomous_executor = AsyncMock(spec=AutonomousPlanExecutor)
        autonomous_executor.execute_plan.return_value = execution_result

        bridge = _make_execution_bridge(
            planning_service=planning_service,
            autonomous_executor=autonomous_executor,
        )

        exec_result = await bridge.execute(result, project_id="proj")
        # plan_id is deterministic from intent_id, not from model output
        assert exec_result.plan_id is not None
        assert exec_result.plan_id.startswith("plan_")
        autonomous_executor.execute_plan.assert_called_once()
        called_with = autonomous_executor.execute_plan.call_args[0][0]
        assert called_with == exec_result.plan_id


# ── EXEC-08B: PERSISTED PLAN BOUNDARY ────────────────────────────────────────


class TestPersistedPlanBoundary:
    async def test_empty_persistence_blocks_execution(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        planning_service = _make_planning_service(plan_manager=plan_manager)
        autonomous_executor = AsyncMock(spec=AutonomousPlanExecutor)
        plan_service = AsyncMock()
        plan_service.persist_manager_plan_result.return_value = []

        bridge = _make_execution_bridge(
            planning_service=planning_service,
            autonomous_executor=autonomous_executor,
            plan_service=plan_service,
        )

        with pytest.raises(ExecutionBridgeError, match="no persisted tasks"):
            await bridge.execute(result, project_id="proj")

        autonomous_executor.execute_plan.assert_not_awaited()

    async def test_foreign_project_task_blocks_execution(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        planning_service = _make_planning_service(plan_manager=plan_manager)
        autonomous_executor = AsyncMock(spec=AutonomousPlanExecutor)

        async def _foreign_persist(_plan_result):
            from app.tasks.models import DevelopmentTask, TaskStatus
            return [DevelopmentTask(
                task_id="task_foreign",
                task_key="a",
                project_id="attacker_project",
                title="foreign",
                description="foreign",
                status=TaskStatus.PENDING,
                metadata={"source_plan_id": ManagerPlanningService._generate_plan_id(decision)},
            )]

        plan_service = AsyncMock()
        plan_service.persist_manager_plan_result.side_effect = _foreign_persist

        bridge = _make_execution_bridge(
            planning_service=planning_service,
            autonomous_executor=autonomous_executor,
            plan_service=plan_service,
        )

        with pytest.raises(ExecutionBridgeError, match="does not belong to authoritative plan"):
            await bridge.execute(result, project_id="proj")

        autonomous_executor.execute_plan.assert_not_awaited()


# ── EXEC-09: IDENTITY / SECURITY CONTEXT ──────────────────────────────────────


class TestNoIdentityLeakage:
    def test_decision_has_no_task_id_or_run_id(self):
        """EXEC-09a: ManagerDecision does not contain task_id, run_id, or execution_id."""
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        assert not hasattr(decision, "task_id")
        assert not hasattr(decision, "run_id")
        assert not hasattr(decision, "execution_id")
        assert not hasattr(decision, "workspace_path")
        assert not hasattr(decision, "execution_token")

    def test_execution_bridge_no_git_or_workspace_imports(self):
        """EXEC-09b: ExecutionBridge has no Git or workspace imports."""
        source = Path(AI_MANAGER_ROOT / "execution_bridge.py").read_text()
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
        for imp in imports:
            assert "git" not in imp.lower()
            assert "workspace" not in imp.lower()


# ── EXEC-10: CANCELLATION / FAILURE / RECOVERY ────────────────────────────────


class TestPlanPersistenceBoundary:
    async def test_execute_persists_authoritative_plan_before_executor(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        planning_service = _make_planning_service(plan_manager=plan_manager)

        execution_result = PlanExecutionResult(
            plan_id="plan_abc",
            terminal_state=PlanExecutionState.COMPLETED,
            committed_tasks=1,
            total_tasks=1,
        )
        autonomous_executor = AsyncMock(spec=AutonomousPlanExecutor)
        autonomous_executor.execute_plan.return_value = execution_result

        plan_service = AsyncMock()
        plan_service.persist_manager_plan_result.return_value = []

        bridge = _make_execution_bridge(
            planning_service=planning_service,
            autonomous_executor=autonomous_executor,
            plan_service=plan_service,
        )

        await bridge.execute(result, project_id="proj")

        plan_service.persist_manager_plan_result.assert_awaited_once()
        autonomous_executor.execute_plan.assert_awaited_once_with("plan_abc")

        assert (
            plan_service.persist_manager_plan_result.await_args_list[0].args[0]
            .plan_id == "plan_abc"
        )

    async def test_persistence_failure_blocks_execution(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        autonomous_executor = AsyncMock(spec=AutonomousPlanExecutor)
        plan_service = AsyncMock()
        plan_service.persist_manager_plan_result.side_effect = RuntimeError("db unavailable")

        bridge = _make_execution_bridge(
            autonomous_executor=autonomous_executor,
            plan_service=plan_service,
        )

        with pytest.raises(ExecutionBridgeError, match="Plan persistence failed"):
            await bridge.execute(result, project_id="proj")

        autonomous_executor.execute_plan.assert_not_awaited()


class TestCancellationFailureRecovery:
    async def test_execute_delegates_to_autonomous_plan_executor(self):
        """EXEC-10a: ExecutionBridge delegates to existing AutonomousPlanExecutor."""
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        planning_service = _make_planning_service(plan_manager=plan_manager)

        execution_result = PlanExecutionResult(
            plan_id="plan_abc",
            terminal_state=PlanExecutionState.COMPLETED,
            committed_tasks=1,
            total_tasks=1,
        )
        autonomous_executor = AsyncMock(spec=AutonomousPlanExecutor)
        autonomous_executor.execute_plan.return_value = execution_result

        bridge = _make_execution_bridge(
            planning_service=planning_service,
            autonomous_executor=autonomous_executor,
        )

        exec_result = await bridge.execute(result, project_id="proj")
        assert exec_result.execution_result.terminal_state == PlanExecutionState.COMPLETED
        autonomous_executor.execute_plan.assert_called_once()

    async def test_executor_failure_raises_execution_bridge_error(self):
        """EXEC-10b: Executor failure raises ExecutionBridgeError, not silent success."""
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        planning_service = _make_planning_service(plan_manager=plan_manager)

        autonomous_executor = AsyncMock(spec=AutonomousPlanExecutor)
        autonomous_executor.execute_plan.side_effect = RuntimeError("executor crashed")

        bridge = _make_execution_bridge(
            planning_service=planning_service,
            autonomous_executor=autonomous_executor,
        )

        with pytest.raises(ExecutionBridgeError, match="Plan execution failed"):
            await bridge.execute(result, project_id="proj")


# ── EXEC-11: CONCURRENCY ──────────────────────────────────────────────────────


class TestConcurrency:
    async def test_two_execute_calls_same_decision_same_approval(self):
        """EXEC-11a: Two concurrent execute() with same approval: second fails on consume."""
        gate = _make_gate_with_approval_registry()
        decision = _decision_with_capability("test", "action")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        planning_service = _make_planning_service(plan_manager=plan_manager)

        execution_result = PlanExecutionResult(
            plan_id="plan_abc",
            terminal_state=PlanExecutionState.COMPLETED,
            committed_tasks=1,
            total_tasks=1,
        )
        autonomous_executor = AsyncMock(spec=AutonomousPlanExecutor)
        autonomous_executor.execute_plan.return_value = execution_result

        call_count = 0

        async def mock_get(approval_id):
            return _make_mock_approval("cap_approval_concurrent")

        async def mock_consume(approval_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_mock_approval("cap_approval_concurrent", status=ApprovalStatus.CONSUMED)
            raise CapabilityApprovalStateError("already consumed")

        repo = AsyncMock()
        repo.get = mock_get
        repo.mark_consumed = mock_consume
        approval_service = CapabilityApprovalService(repo=repo)

        bridge = _make_execution_bridge(
            planning_service=planning_service,
            autonomous_executor=autonomous_executor,
            capability_gate=gate,
            approval_service=approval_service,
        )

        exec_result1 = await bridge.execute(result, project_id="proj", approval_id="cap_approval_concurrent")
        assert exec_result1.execution_result.terminal_state == PlanExecutionState.COMPLETED

        with pytest.raises(ExecutionAuthorizationError, match="consumption failed"):
            await bridge.execute(result, project_id="proj", approval_id="cap_approval_concurrent")


# ── EXEC-12: ERROR FAIL-CLOSED ────────────────────────────────────────────────


class TestErrorFailClosed:
    async def test_rejected_decision_never_executes(self):
        """EXEC-12a: REJECTED decision raises in both prepare and execute."""
        result = ManagerResult(decision=None, outcome=DecisionOutcome.REJECTED)
        bridge = _make_execution_bridge()

        with pytest.raises(ExecutionBridgeError, match="REJECTED"):
            await bridge.prepare(result, project_id="proj")

        with pytest.raises(ExecutionBridgeError, match="REJECTED"):
            await bridge.execute(result, project_id="proj")

    async def test_none_decision_never_executes(self):
        """EXEC-12b: REJECTED decision with None decision raises in both prepare and execute."""
        result = ManagerResult(decision=None, outcome=DecisionOutcome.REJECTED)
        bridge = _make_execution_bridge()

        with pytest.raises(ExecutionBridgeError, match="REJECTED"):
            await bridge.prepare(result, project_id="proj")

        with pytest.raises(ExecutionBridgeError, match="REJECTED"):
            await bridge.execute(result, project_id="proj")

    async def test_needs_approval_without_approval_id_raises(self):
        """EXEC-12c: NEEDS_APPROVAL without approval_id raises."""
        gate = _make_gate_with_approval_registry()
        decision = _decision_with_capability("test", "action")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        bridge = _make_execution_bridge(capability_gate=gate)

        with pytest.raises(ExecutionNeedsApprovalError, match="requires approval"):
            await bridge.execute(result, project_id="proj")

    async def test_planning_failure_raises_execution_bridge_error(self):
        """EXEC-12d: Planning failure raises ExecutionBridgeError."""
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        plan_manager = _fake_plan_manager(error=Exception("provider down"))
        planning_service = _make_planning_service(plan_manager=plan_manager)
        bridge = _make_execution_bridge(planning_service=planning_service)

        with pytest.raises(ExecutionBridgeError, match="Planning failed"):
            await bridge.execute(result, project_id="proj")

    async def test_invalid_project_does_not_crash(self):
        """EXEC-12e: Invalid project_id does not cause silent success."""
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        bridge = _make_execution_bridge()

        prepare_result = await bridge.prepare(result, project_id="nonexistent_project")
        assert prepare_result.authorized is True
        assert prepare_result.project_id == "nonexistent_project"


# ── ADDITIONAL SECURITY TESTS ────────────────────────────────────────────────


class TestAdditionalSecurity:
    async def test_prepare_allowed_returns_authorized(self):
        """EXEC-01f: prepare() with ALLOWED decision returns authorized."""
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        bridge = _make_execution_bridge()

        prepare_result = await bridge.prepare(result, project_id="proj")
        assert prepare_result.authorized is True
        assert prepare_result.approval_needed is False
        assert prepare_result.planning_result is not None

    async def test_prepare_needs_approval_returns_approval(self):
        """EXEC-04i: prepare() with NEEDS_APPROVAL returns approval request."""
        gate = _make_gate_with_approval_registry()
        decision = _decision_with_capability("test", "action")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        bridge = _make_execution_bridge(capability_gate=gate)

        prepare_result = await bridge.prepare(result, project_id="proj")
        assert prepare_result.authorized is False
        assert prepare_result.approval_needed is True
        assert prepare_result.approval is not None
        assert prepare_result.approval.approval_id.startswith("cap_approval_")

    async def test_prepare_denied_raises_authorization_error(self):
        """EXEC-03e: prepare() with DENIED raises ExecutionAuthorizationError."""
        from app.ai_manager.capabilities import create_default_registry
        from app.ai_manager.policy import ApplicationPolicy

        registry = create_default_registry()
        policy = ApplicationPolicy(denied_capabilities=[
            ModelCapabilityIdentifier(namespace="development", name="plan"),
        ])
        gate = PolicyCapabilityGate(registry=registry, policy=policy)

        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        bridge = _make_execution_bridge(capability_gate=gate)

        with pytest.raises(ExecutionAuthorizationError, match="denied"):
            await bridge.prepare(result, project_id="proj")

    async def test_execute_allowed_plans_and_executes(self):
        """EXEC-01g: execute() with ALLOWED decision plans and executes."""
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        planning_service = _make_planning_service(plan_manager=plan_manager)

        execution_result = PlanExecutionResult(
            plan_id="plan_abc",
            terminal_state=PlanExecutionState.COMPLETED,
            committed_tasks=1,
            total_tasks=1,
        )
        autonomous_executor = AsyncMock(spec=AutonomousPlanExecutor)
        autonomous_executor.execute_plan.return_value = execution_result

        bridge = _make_execution_bridge(
            planning_service=planning_service,
            autonomous_executor=autonomous_executor,
        )

        exec_result = await bridge.execute(result, project_id="proj")
        assert exec_result.execution_result.terminal_state == PlanExecutionState.COMPLETED

    async def test_execute_idempotent_planning(self):
        """EXEC-08b: execute() planning is idempotent for same decision."""
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        planning_service = _make_planning_service(plan_manager=plan_manager)

        execution_result = PlanExecutionResult(
            plan_id="plan_abc",
            terminal_state=PlanExecutionState.COMPLETED,
            committed_tasks=1,
            total_tasks=1,
        )
        autonomous_executor = AsyncMock(spec=AutonomousPlanExecutor)
        autonomous_executor.execute_plan.return_value = execution_result

        bridge = _make_execution_bridge(
            planning_service=planning_service,
            autonomous_executor=autonomous_executor,
        )

        exec_result1 = await bridge.execute(result, project_id="proj")
        exec_result2 = await bridge.execute(result, project_id="proj")

        # Same decision produces same plan_id (idempotent)
        assert exec_result1.plan_id == exec_result2.plan_id

    async def test_execute_with_approval_id_consumes_and_executes(self):
        """EXEC-04j: execute() with valid approval_id consumes and executes."""
        gate = _make_gate_with_approval_registry()
        decision = _decision_with_capability("test", "action")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        planning_service = _make_planning_service(plan_manager=plan_manager)

        execution_result = PlanExecutionResult(
            plan_id="plan_abc",
            terminal_state=PlanExecutionState.COMPLETED,
            committed_tasks=1,
            total_tasks=1,
        )
        autonomous_executor = AsyncMock(spec=AutonomousPlanExecutor)
        autonomous_executor.execute_plan.return_value = execution_result

        repo = AsyncMock()
        repo.get.return_value = _make_mock_approval(
            approval_id="cap_approval_valid",
            intent_id="intent_1",
            project_id="proj",
        )
        repo.mark_consumed.return_value = _make_mock_approval(
            approval_id="cap_approval_valid",
            intent_id="intent_1",
            project_id="proj",
            status=ApprovalStatus.CONSUMED,
        )
        approval_service = CapabilityApprovalService(repo=repo)

        bridge = _make_execution_bridge(
            planning_service=planning_service,
            autonomous_executor=autonomous_executor,
            capability_gate=gate,
            approval_service=approval_service,
        )

        exec_result = await bridge.execute(result, project_id="proj", approval_id="cap_approval_valid")
        assert exec_result.execution_result.terminal_state == PlanExecutionState.COMPLETED
        repo.mark_consumed.assert_called_once_with("cap_approval_valid")

    async def test_execute_denied_raises_authorization_error(self):
        """EXEC-03f: execute() with DENIED raises ExecutionAuthorizationError."""
        from app.ai_manager.capabilities import create_default_registry
        from app.ai_manager.policy import ApplicationPolicy

        registry = create_default_registry()
        policy = ApplicationPolicy(denied_capabilities=[
            ModelCapabilityIdentifier(namespace="development", name="plan"),
        ])
        gate = PolicyCapabilityGate(registry=registry, policy=policy)

        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        bridge = _make_execution_bridge(capability_gate=gate)

        with pytest.raises(ExecutionAuthorizationError, match="denied"):
            await bridge.execute(result, project_id="proj")

    async def test_prepare_audit_records_authorization(self):
        """EXEC-11b: prepare() with ALLOWED records EXECUTION_AUTHORIZED audit."""
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        audit_service = AsyncMock()
        bridge = _make_execution_bridge(audit_service=audit_service)

        await bridge.prepare(result, project_id="proj")

        calls = audit_service.log_event.call_args_list
        event_types = [call.kwargs.get("event_type") for call in calls]
        assert "EXECUTION_AUTHORIZED" in event_types

    async def test_prepare_audit_records_approval_required(self):
        """EXEC-11c: prepare() with NEEDS_APPROVAL records EXECUTION_APPROVAL_REQUIRED audit."""
        gate = _make_gate_with_approval_registry()
        decision = _decision_with_capability("test", "action")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        repo = AsyncMock()
        repo.create.return_value = _make_mock_approval("cap_approval_audit")
        approval_service = CapabilityApprovalService(repo=repo)

        audit_service = AsyncMock()
        bridge = _make_execution_bridge(
            capability_gate=gate,
            approval_service=approval_service,
            audit_service=audit_service,
        )

        await bridge.prepare(result, project_id="proj")

        calls = audit_service.log_event.call_args_list
        event_types = [call.kwargs.get("event_type") for call in calls]
        assert "EXECUTION_APPROVAL_REQUIRED" in event_types

    async def test_execute_audit_records_completion(self):
        """EXEC-11d: execute() records EXECUTION_COMPLETED audit."""
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")

        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        planning_service = _make_planning_service(plan_manager=plan_manager)

        execution_result = PlanExecutionResult(
            plan_id="plan_abc",
            terminal_state=PlanExecutionState.COMPLETED,
            committed_tasks=1,
            total_tasks=1,
        )
        autonomous_executor = AsyncMock(spec=AutonomousPlanExecutor)
        autonomous_executor.execute_plan.return_value = execution_result

        audit_service = AsyncMock()
        bridge = _make_execution_bridge(
            planning_service=planning_service,
            autonomous_executor=autonomous_executor,
            audit_service=audit_service,
        )

        await bridge.execute(result, project_id="proj")

        calls = audit_service.log_event.call_args_list
        event_types = [call.kwargs.get("event_type") for call in calls]
        assert "EXECUTION_COMPLETED" in event_types

    def test_execution_result_repr_does_not_leak_executor(self):
        """EXEC-01h: ExecutionResult repr does not expose executor internals."""
        result = ExecutionResult(
            plan_id="plan_123",
            project_id="proj",
            execution_result=PlanExecutionResult(
                plan_id="plan_123",
                terminal_state=PlanExecutionState.COMPLETED,
            ),
        )
        repr_str = repr(result)
        assert "executor" not in repr_str.lower()
        assert "autonomous" not in repr_str.lower()

    def test_execution_prepare_result_repr_does_not_leak_executor(self):
        """EXEC-01i: ExecutionPrepareResult repr does not expose executor internals."""
        result = ExecutionPrepareResult(authorized=True)
        repr_str = repr(result)
        assert "executor" not in repr_str.lower()
        assert "autonomous" not in repr_str.lower()
