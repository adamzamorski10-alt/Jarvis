"""Tests — PlanManager orchestrator."""
from __future__ import annotations

import pytest

from app.manager.models import (
    ManagerContextLimitError,
    ManagerEmptyResponseError,
    ManagerInvalidPlanError,
    ManagerMalformedResponseError,
    ManagerPlanResult,
    ManagerProviderError,
    ManagerRateLimitedError,
    ManagerTimeoutError,
    PlanningConstraints,
    ProviderTaskProposal,
    UserPlan,
)
from app.manager.plan_manager import PlanManager
from app.manager.providers.base import (
    PlanProviderContextLimitError,
    PlanProviderEmptyResponseError,
    PlanProviderMalformedResponseError,
    PlanProviderRateLimitError,
    PlanProviderTimeoutError,
    PlanProviderUnavailableError,
    PlanProviderInvalidStructuredOutputError,
)
from app.manager.providers.fake import FakePlanProvider


@pytest.mark.asyncio
async def test_successful_planning():
    proposals = [
        ProviderTaskProposal(proposal_key="a", title="A", description="d", priority=1),
        ProviderTaskProposal(proposal_key="b", title="B", description="d", priority=2, dependencies=["a"]),
    ]
    provider = FakePlanProvider(proposals=proposals)
    manager = PlanManager(provider=provider)

    user_plan = UserPlan(
        plan_id="plan_ok",
        user_request="req",
        project_id="proj",
        acceptance_criteria=["c1"],
        planning_constraints=PlanningConstraints(max_tasks=10),
    )

    result = await manager.plan(user_plan)
    assert isinstance(result, ManagerPlanResult)
    assert result.plan_id == "plan_ok"
    assert len(result.tasks) == 2
    assert result.tasks[0].task_key == "a"
    assert result.tasks[1].task_key == "b"
    assert result.plan_fingerprint is not None


@pytest.mark.asyncio
async def test_provider_receives_exact_user_plan():
    proposals = [
        ProviderTaskProposal(proposal_key="a", title="A", description="d"),
    ]
    provider = FakePlanProvider(proposals=proposals)
    manager = PlanManager(provider=provider)

    user_plan = UserPlan(
        plan_id="plan_exact",
        user_request="exact request",
        project_id="proj_exact",
        acceptance_criteria=["crit"],
        strict=True,
    )

    await manager.plan(user_plan)
    assert len(provider.calls) == 1
    assert provider.calls[0][0] == user_plan


@pytest.mark.asyncio
async def test_provider_receives_constraints():
    proposals = [
        ProviderTaskProposal(proposal_key="a", title="A", description="d"),
    ]
    provider = FakePlanProvider(proposals=proposals)
    constraints = PlanningConstraints(max_tasks=7, max_dependencies_per_task=3, default_max_attempts=5)
    manager = PlanManager(provider=provider, constraints=constraints)

    user_plan = UserPlan(
        plan_id="plan_constraints",
        user_request="req",
        project_id="proj",
    )

    await manager.plan(user_plan)
    assert len(provider.calls) == 1
    assert provider.calls[0][1] == constraints


@pytest.mark.asyncio
async def test_normalize_plan_is_called_through_path():
    proposals = [
        ProviderTaskProposal(proposal_key="a", title="A", description="d"),
    ]
    provider = FakePlanProvider(proposals=proposals)
    manager = PlanManager(provider=provider)

    user_plan = UserPlan(
        plan_id="plan_norm",
        user_request="req",
        project_id="proj",
        acceptance_criteria=["app_controlled_criterion"],
    )

    result = await manager.plan(user_plan)
    task_metadata = result.tasks[0].metadata
    assert "acceptance_criteria" in task_metadata
    assert task_metadata["acceptance_criteria"] == ["app_controlled_criterion"]


@pytest.mark.asyncio
async def test_invalid_proposals_produce_manager_invalid_plan_error():
    provider = FakePlanProvider(
        proposals=[
            ProviderTaskProposal(proposal_key="a", title="A", description="d"),
            ProviderTaskProposal(proposal_key="a", title="B", description="d"),
        ]
    )
    manager = PlanManager(provider=provider)

    user_plan = UserPlan(
        plan_id="plan_invalid",
        user_request="req",
        project_id="proj",
    )

    with pytest.raises(ManagerInvalidPlanError, match="Duplicate proposal_key"):
        await manager.plan(user_plan)


@pytest.mark.asyncio
async def test_provider_timeout_surfaced():
    provider = FakePlanProvider(error=PlanProviderTimeoutError("boom"))
    manager = PlanManager(provider=provider)

    user_plan = UserPlan(plan_id="p", user_request="r", project_id="proj")
    with pytest.raises(ManagerTimeoutError):
        await manager.plan(user_plan)


@pytest.mark.asyncio
async def test_provider_rate_limit_surfaced():
    provider = FakePlanProvider(error=PlanProviderRateLimitError("boom"))
    manager = PlanManager(provider=provider)

    user_plan = UserPlan(plan_id="p", user_request="r", project_id="proj")
    with pytest.raises(ManagerRateLimitedError):
        await manager.plan(user_plan)


@pytest.mark.asyncio
async def test_provider_context_limit_surfaced():
    provider = FakePlanProvider(error=PlanProviderContextLimitError("boom"))
    manager = PlanManager(provider=provider)

    user_plan = UserPlan(plan_id="p", user_request="r", project_id="proj")
    with pytest.raises(ManagerContextLimitError):
        await manager.plan(user_plan)


@pytest.mark.asyncio
async def test_provider_empty_response_surfaced():
    provider = FakePlanProvider(error=PlanProviderEmptyResponseError("boom"))
    manager = PlanManager(provider=provider)

    user_plan = UserPlan(plan_id="p", user_request="r", project_id="proj")
    with pytest.raises(ManagerEmptyResponseError):
        await manager.plan(user_plan)


@pytest.mark.asyncio
async def test_provider_malformed_response_surfaced():
    provider = FakePlanProvider(error=PlanProviderMalformedResponseError("boom"))
    manager = PlanManager(provider=provider)

    user_plan = UserPlan(plan_id="p", user_request="r", project_id="proj")
    with pytest.raises(ManagerMalformedResponseError):
        await manager.plan(user_plan)


@pytest.mark.asyncio
async def test_provider_unavailable_surfaced():
    provider = FakePlanProvider(error=PlanProviderUnavailableError("boom"))
    manager = PlanManager(provider=provider)

    user_plan = UserPlan(plan_id="p", user_request="r", project_id="proj")
    with pytest.raises(ManagerProviderError):
        await manager.plan(user_plan)


@pytest.mark.asyncio
async def test_provider_invalid_structured_output_surfaced():
    provider = FakePlanProvider(error=PlanProviderInvalidStructuredOutputError("boom"))
    manager = PlanManager(provider=provider)

    user_plan = UserPlan(plan_id="p", user_request="r", project_id="proj")
    with pytest.raises(ManagerInvalidPlanError):
        await manager.plan(user_plan)


@pytest.mark.asyncio
async def test_plan_manager_does_not_persist():
    proposals = [
        ProviderTaskProposal(proposal_key="a", title="A", description="d"),
    ]
    provider = FakePlanProvider(proposals=proposals)
    manager = PlanManager(provider=provider)

    user_plan = UserPlan(plan_id="plan_no_persist", user_request="r", project_id="proj")
    result = await manager.plan(user_plan)
    assert isinstance(result, ManagerPlanResult)
    assert not hasattr(manager, "_session")
    assert not hasattr(manager, "_engine")


@pytest.mark.asyncio
async def test_fingerprint_unchanged_for_identical_semantic_input():
    proposals = [
        ProviderTaskProposal(proposal_key="a", title="A", description="d", priority=1),
    ]
    provider = FakePlanProvider(proposals=proposals)
    manager = PlanManager(provider=provider)

    user_plan = UserPlan(
        plan_id="plan_fp",
        user_request="req",
        project_id="proj",
        acceptance_criteria=["c1"],
        planning_constraints=PlanningConstraints(default_max_attempts=3),
    )

    result1 = await manager.plan(user_plan)
    result2 = await manager.plan(user_plan)
    assert result1.plan_fingerprint == result2.plan_fingerprint


@pytest.mark.asyncio
async def test_user_plan_acceptance_criteria_controls_fingerprint():
    proposals = [
        ProviderTaskProposal(proposal_key="a", title="A", description="d"),
    ]
    provider = FakePlanProvider(proposals=proposals)
    manager = PlanManager(provider=provider)

    user_plan1 = UserPlan(
        plan_id="plan_ac",
        user_request="req",
        project_id="proj",
        acceptance_criteria=["c1"],
    )
    user_plan2 = UserPlan(
        plan_id="plan_ac",
        user_request="req",
        project_id="proj",
        acceptance_criteria=["c2"],
    )

    result1 = await manager.plan(user_plan1)
    result2 = await manager.plan(user_plan2)
    assert result1.plan_fingerprint != result2.plan_fingerprint


@pytest.mark.asyncio
async def test_plan_manager_security_no_execution_imports():
    import app.manager.plan_manager as pm
    source = open(pm.__file__).read()
    forbidden = [
        "ToolExecutionService",
        "PermissionEngine",
        "VerificationGate",
        "RunController",
        "WorkspaceManager",
    ]
    for name in forbidden:
        assert name not in source, f"PlanManager must not import {name}"
