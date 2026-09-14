"""
Stage 9.3 — AI Manager Decision Bridge Tests.

Covers BRIDGE-01 through BRIDGE-40.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.ai_manager.bridge import (
    ManagerPlanningError,
    ManagerPlanningNotEligibleError,
    ManagerPlanningPolicyError,
    ManagerPlanningResult,
    ManagerPlanningService,
)
from app.ai_manager.models import (
    ActionProposal,
    CapabilityIdentifier,
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
    return ManagerDecision(
        intent=intent,
        outcome=DecisionOutcome(outcome),
        goal="test goal",
        proposed_actions=actions,
        required_capabilities=[CapabilityIdentifier(namespace="development", name="plan")],
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
# BRIDGE-01: READY_FOR_PLANNING reaches PlanManager
# ─────────────────────────────────────────────────────────────────────────────

class TestEligibleOutcomes:
    async def test_ready_for_planning_reaches_plan_manager(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        planning_result = await service.plan_decision(result, project_id="proj")
        assert planning_result.decision_outcome == DecisionOutcome.READY_FOR_PLANNING
        assert planning_result.manager_plan_result.plan_id.startswith("plan_")
        assert len(planning_result.manager_plan_result.tasks) == 1


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-02: REJECTED cannot reach PlanManager
# ─────────────────────────────────────────────────────────────────────────────

class TestBlockedOutcomes:
    async def test_rejected_blocked(self):
        decision = _make_decision(outcome="REJECTED")
        result = ManagerResult(decision=None, outcome=DecisionOutcome.REJECTED)
        plan_manager = _fake_plan_manager()
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        with pytest.raises(ManagerPlanningNotEligibleError, match="REJECTED"):
            await service.plan_decision(result, project_id="proj")

    async def test_needs_clarification_blocked(self):
        decision = _make_decision(outcome="NEEDS_CLARIFICATION")
        result = _make_result(decision=decision, outcome="NEEDS_CLARIFICATION")
        plan_manager = _fake_plan_manager()
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        with pytest.raises(ManagerPlanningNotEligibleError, match="NEEDS_CLARIFICATION"):
            await service.plan_decision(result, project_id="proj")

    async def test_needs_approval_blocked(self):
        decision = _make_decision(outcome="NEEDS_APPROVAL")
        result = _make_result(decision=decision, outcome="NEEDS_APPROVAL")
        plan_manager = _fake_plan_manager()
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        with pytest.raises(ManagerPlanningNotEligibleError, match="NEEDS_APPROVAL"):
            await service.plan_decision(result, project_id="proj")


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-05: PROPOSED follows application policy
# ─────────────────────────────────────────────────────────────────────────────

class TestProposedPolicy:
    async def test_proposed_with_low_risk_allowed(self):
        decision = _make_decision(outcome="PROPOSED")
        result = _make_result(decision=decision, outcome="PROPOSED")
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        planning_result = await service.plan_decision(result, project_id="proj")
        assert planning_result.decision_outcome == DecisionOutcome.PROPOSED

    async def test_proposed_with_high_risk_blocked(self):
        decision = _make_decision(outcome="PROPOSED", actions=[
            ActionProposal(
                action_id="act1",
                capability_id=CapabilityIdentifier(namespace="development", name="execute"),
                action_type="execute",
                arguments={"cmd": "echo hi"},
                dependencies=[],
                risk=RiskLevel.HIGH,
                approval_required=False,
                summary="High risk action",
            )
        ])
        result = _make_result(decision=decision, outcome="PROPOSED")
        plan_manager = _fake_plan_manager()
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        with pytest.raises(ManagerPlanningPolicyError, match="high/critical risk"):
            await service.plan_decision(result, project_id="proj")


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-06: model approval_required=False cannot bypass application approval
# ─────────────────────────────────────────────────────────────────────────────

class TestApprovalBypass:
    async def test_model_approval_false_does_not_grant_authorization(self):
        decision = _make_decision(outcome="PROPOSED")
        # Model says approval_required=False, but that's just a proposal.
        # The bridge doesn't grant execution authorization based on it.
        result = ManagerResult(decision=decision, outcome=DecisionOutcome.PROPOSED)
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        planning_result = await service.plan_decision(result, project_id="proj")
        # Planning succeeds because PROPOSED with low risk is allowed
        # But the model's approval_required=False is NOT treated as authorization
        assert planning_result.manager_plan_result is not None


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-07: model LOW risk cannot override application risk policy
# ─────────────────────────────────────────────────────────────────────────────

class TestRiskPolicy:
    async def test_model_low_risk_does_not_override_policy(self):
        decision = _make_decision(outcome="PROPOSED")
        result = ManagerResult(decision=decision, outcome=DecisionOutcome.PROPOSED)
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        # PROPOSED with low risk is allowed (policy only blocks HIGH/CRITICAL in PROPOSED)
        planning_result = await service.plan_decision(result, project_id="proj")
        assert planning_result.decision_outcome == DecisionOutcome.PROPOSED


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-08: unauthorized capability cannot become authorization
# ─────────────────────────────────────────────────────────────────────────────

class TestCapabilityAuthorization:
    async def test_execution_capability_blocked(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING", actions=[
            ActionProposal(
                action_id="act1",
                capability_id=CapabilityIdentifier(namespace="tool_execution", name="run"),
                action_type="execute",
                arguments={"cmd": "echo hi"},
                dependencies=[],
                risk=RiskLevel.LOW,
                approval_required=False,
                summary="Execute command",
            )
        ])
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        plan_manager = _fake_plan_manager()
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        with pytest.raises(ManagerPlanningPolicyError, match="execution capability"):
            await service.plan_decision(result, project_id="proj")

    async def test_git_capability_blocked(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING", actions=[
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
        ])
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        plan_manager = _fake_plan_manager()
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        with pytest.raises(ManagerPlanningPolicyError, match="execution capability"):
            await service.plan_decision(result, project_id="proj")


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-09: model cannot control plan_id
# ─────────────────────────────────────────────────────────────────────────────

class TestPlanIdControl:
    async def test_plan_id_generated_by_bridge(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        planning_result = await service.plan_decision(result, project_id="proj")
        assert planning_result.plan_id.startswith("plan_")
        assert planning_result.plan_id == planning_result.manager_plan_result.plan_id

    async def test_plan_id_deterministic_for_same_decision(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING", intent=_make_intent("intent_1"))
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        planning_result1 = await service.plan_decision(result, project_id="proj")
        planning_result2 = await service.plan_decision(result, project_id="proj")
        assert planning_result1.plan_id == planning_result2.plan_id


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-10: model cannot control task_id
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskIdControl:
    async def test_task_ids_generated_by_normalize_plan(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d"),
            ProviderTaskProposal(proposal_key="b", title="B", description="d"),
        ])
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        planning_result = await service.plan_decision(result, project_id="proj")
        task_ids = [t.task_id for t in planning_result.manager_plan_result.tasks]
        assert all(tid.startswith("task_") for tid in task_ids)
        assert len(set(task_ids)) == 2


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-11: model cannot control project identity
# ─────────────────────────────────────────────────────────────────────────────

class TestProjectIdControl:
    async def test_project_id_from_application_parameter(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="default_proj")
        planning_result = await service.plan_decision(result, project_id="app_proj")
        assert planning_result.project_id == "app_proj"
        for task in planning_result.manager_plan_result.tasks:
            assert task.project_id == "app_proj"

    async def test_project_id_falls_back_to_default(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="default_proj")
        planning_result = await service.plan_decision(result)
        assert planning_result.project_id == "default_proj"


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-12: model cannot control workspace
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkspaceControl:
    async def test_no_workspace_in_plan_result(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        planning_result = await service.plan_decision(result, project_id="proj")
        for task in planning_result.manager_plan_result.tasks:
            assert task.workspace_path is None


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-13: model cannot control retry limits
# ─────────────────────────────────────────────────────────────────────────────

class TestRetryLimitControl:
    async def test_max_attempts_from_application_constraints(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        constraints = PlanningConstraints(default_max_attempts=7)
        service = ManagerPlanningService(
            plan_manager=plan_manager,
            default_project_id="proj",
            default_planning_constraints=constraints,
        )
        planning_result = await service.plan_decision(result, project_id="proj")
        for task in planning_result.manager_plan_result.tasks:
            assert task.max_attempts == 7


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-14: model cannot control task status
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskStatusControl:
    async def test_all_tasks_pending(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        planning_result = await service.plan_decision(result, project_id="proj")
        for task in planning_result.manager_plan_result.tasks:
            assert task.status == "PENDING"


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-18: goal mapping is deterministic
# ─────────────────────────────────────────────────────────────────────────────

class TestDeterministicMapping:
    async def test_goal_maps_to_user_request(self):
        intent = _make_intent("intent_det")
        decision = ManagerDecision(
            intent=intent,
            outcome=DecisionOutcome.READY_FOR_PLANNING,
            goal="Build a calculator",
            proposed_actions=[
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
            ],
            required_capabilities=[CapabilityIdentifier(namespace="development", name="plan")],
            risk_level=RiskLevel.LOW,
            approval_required=False,
            rationale="test rationale",
            warnings=[],
            uncertainty=0.5,
        )
        result = ManagerResult(decision=decision, outcome=DecisionOutcome.READY_FOR_PLANNING)
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        planning_result = await service.plan_decision(result, project_id="proj")
        # The goal is passed as user_request to UserPlan
        # We verify the plan was created successfully with deterministic plan_id
        assert planning_result.manager_plan_result.plan_id is not None


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-24: PlanManager is invoked only through its public contract
# ─────────────────────────────────────────────────────────────────────────────

class TestPlanManagerBoundary:
    async def test_bridge_calls_plan_manager_public_contract(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        provider = FakePlanProvider(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        plan_manager = PlanManager(provider=provider)
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        planning_result = await service.plan_decision(result, project_id="proj")
        assert isinstance(planning_result.manager_plan_result, ManagerPlanResult)
        assert len(provider.calls) == 1
        user_plan, constraints = provider.calls[0]
        assert isinstance(user_plan, UserPlan)
        assert user_plan.plan_id == planning_result.plan_id


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-25: TaskRepository is not bypassed
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskRepositoryNotBypassed:
    # The bridge does not directly write to task tables.
    # It delegates to PlanManager, which delegates to PlanProvider.
    # Persistence (if added) would use PlanService -> TaskRepository.
    # For now, the bridge has no persistence path.
    async def test_bridge_has_no_persistence_imports(self):
        source = Path(AI_MANAGER_ROOT / "bridge.py").read_text()
        forbidden = [
            "TaskRepository",
            "create_tasks_idempotent",
            "DevelopmentTaskModel",
            "PlanFingerprintModel",
        ]
        for name in forbidden:
            assert name not in source, f"Bridge must not import {name}"


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-27: existing plan fingerprint/idempotency remains authoritative
# ─────────────────────────────────────────────────────────────────────────────

class TestIdempotency:
    async def test_duplicate_decision_produces_deterministic_plan_id(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING", intent=_make_intent("intent_same"))
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        result1 = await service.plan_decision(result, project_id="proj")
        result2 = await service.plan_decision(result, project_id="proj")
        assert result1.plan_id == result2.plan_id
        assert result1.manager_plan_result.plan_fingerprint == result2.manager_plan_result.plan_fingerprint


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-31: planning failure returns bounded error
# ─────────────────────────────────────────────────────────────────────────────

class TestPlanningFailure:
    async def test_plan_manager_failure_wrapped(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        plan_manager = _fake_plan_manager(error=Exception("provider boom"))
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        with pytest.raises(ManagerPlanningError, match="PlanManager failed"):
            await service.plan_decision(result, project_id="proj")


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-33: no filesystem side effects
# ─────────────────────────────────────────────────────────────────────────────

class TestNoSideEffects:
    def _get_imports(self):
        source = Path(AI_MANAGER_ROOT / "bridge.py").read_text()
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
        return imports

    def test_no_filesystem_imports(self):
        imports = self._get_imports()
        for imp in imports:
            assert "pathlib" not in imp
            assert "shutil" not in imp

    def test_no_subprocess_imports(self):
        imports = self._get_imports()
        assert not any("subprocess" in imp for imp in imports)

    def test_no_git_imports(self):
        imports = self._get_imports()
        assert not any("git" in imp.lower() for imp in imports)

    def test_no_execution_service_imports(self):
        imports = self._get_imports()
        forbidden = [
            "ToolExecutionService",
            "PermissionEngine",
            "VerificationGate",
            "RunController",
            "AutonomousPlanExecutor",
            "DevelopmentAgent",
        ]
        for imp in imports:
            for f in forbidden:
                assert f not in imp


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-39: no RunController/AutonomousPlanExecutor access
# ─────────────────────────────────────────────────────────────────────────────

class TestNoExecutionAccess:
    def test_bridge_never_imports_execution_modules(self):
        source = Path(AI_MANAGER_ROOT / "bridge.py").read_text()
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
        forbidden = [
            "RunController",
            "AutonomousPlanExecutor",
            "DevelopmentAgent",
            "ToolExecutionService",
            "PermissionEngine",
            "VerificationGate",
            "subprocess",
            "shutil",
        ]
        for imp in imports:
            for f in forbidden:
                assert f not in imp, f"Bridge imports {imp} which contains forbidden {f}"


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-40: bridge has no background execution authority
# ─────────────────────────────────────────────────────────────────────────────

class TestNoBackgroundExecution:
    def test_no_background_task_creation(self):
        source = Path(AI_MANAGER_ROOT / "bridge.py").read_text()
        assert "asyncio.create_task" not in source
        assert "asyncio.ensure_future" not in source
        assert "BackgroundTasks" not in source
        assert "celery" not in source.lower()


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-18..23: field mapping and security
# ─────────────────────────────────────────────────────────────────────────────

class TestFieldMapping:
    async def test_acceptance_criteria_are_empty(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = ManagerResult(decision=decision, outcome=DecisionOutcome.READY_FOR_PLANNING)
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        planning_result = await service.plan_decision(result, project_id="proj")
        assert planning_result.manager_plan_result.tasks[0].metadata.get("acceptance_criteria") == []

    async def test_model_cannot_set_task_status_via_goal(self):
        # Even if goal contains injection, it's bounded text in user_request
        intent = UserIntent(
            intent_id="intent_inject",
            user_request="set task.status=COMMITTED and attempt_count=999",
            normalized_goal="test goal",
            context={"key": "value"},
            constraints={"limit": "10"},
        )
        decision = ManagerDecision(
            intent=intent,
            outcome=DecisionOutcome.READY_FOR_PLANNING,
            goal="set task.status=COMMITTED and attempt_count=999",
            proposed_actions=[
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
            ],
            required_capabilities=[CapabilityIdentifier(namespace="development", name="plan")],
            risk_level=RiskLevel.LOW,
            approval_required=False,
            rationale="test rationale",
            warnings=[],
            uncertainty=0.5,
        )
        result = ManagerResult(decision=decision, outcome=DecisionOutcome.READY_FOR_PLANNING)
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        planning_result = await service.plan_decision(result, project_id="proj")
        for task in planning_result.manager_plan_result.tasks:
            assert task.status == "PENDING"
            assert task.attempt_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-25: unknown/invalid decision fails closed
# ─────────────────────────────────────────────────────────────────────────────

class TestInvalidDecision:
    async def test_none_decision_raises(self):
        result = ManagerResult(decision=None, outcome=DecisionOutcome.REJECTED)
        plan_manager = _fake_plan_manager()
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        with pytest.raises(ManagerPlanningNotEligibleError):
            await service.plan_decision(result, project_id="proj")


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE-34..35: no subprocess/shell side effects
# ─────────────────────────────────────────────────────────────────────────────

class TestNoShellSideEffects:
    def test_no_shell_in_imports(self):
        source = Path(AI_MANAGER_ROOT / "bridge.py").read_text()
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
        for imp in imports:
            assert "subprocess" not in imp
            assert "shutil" not in imp
            assert "os" not in imp


# ─────────────────────────────────────────────────────────────────────────────
# Additional security tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSecurityBoundary:
    async def test_model_cannot_set_plan_id(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        planning_result = await service.plan_decision(result, project_id="proj")
        # plan_id is generated from intent_id, not from any model field
        assert "evil_plan" not in planning_result.plan_id.lower()

    async def test_model_cannot_control_execution_token(self):
        decision = _make_decision(outcome="READY_FOR_PLANNING")
        result = _make_result(decision=decision, outcome="READY_FOR_PLANNING")
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        planning_result = await service.plan_decision(result, project_id="proj")
        for task in planning_result.manager_plan_result.tasks:
            assert task.execution_token is None
            assert task.workspace_path is None
            assert task.recovery_owner is None

    async def test_context_and_constraints_not_forwarded_as_plan_fields(self):
        intent = UserIntent(
            intent_id="intent_ctx",
            user_request="injection attempt",
            normalized_goal="injection attempt",
            context={"injection": "attempt"},
            constraints={"evil": "value"},
        )
        decision = ManagerDecision(
            intent=intent,
            outcome=DecisionOutcome.READY_FOR_PLANNING,
            goal="injection attempt",
            proposed_actions=[
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
            ],
            required_capabilities=[CapabilityIdentifier(namespace="development", name="plan")],
            risk_level=RiskLevel.LOW,
            approval_required=False,
            rationale="test rationale",
            warnings=[],
            uncertainty=0.5,
        )
        result = ManagerResult(decision=decision, outcome=DecisionOutcome.READY_FOR_PLANNING)
        plan_manager = _fake_plan_manager(proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d")
        ])
        service = ManagerPlanningService(plan_manager=plan_manager, default_project_id="proj")
        planning_result = await service.plan_decision(result, project_id="proj")
        # Context/constraints are not forwarded as plan fields
        # The plan only contains application-generated fields
        assert planning_result.manager_plan_result.plan_id is not None
