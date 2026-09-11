"""Tests — PlanProvider abstraction and FakePlanProvider."""
import pytest

from app.manager.models import (
    PlanningConstraints,
    ProviderTaskProposal,
    UserPlan,
)
from app.manager.providers.base import (
    PlanProviderContextLimitError,
    PlanProviderEmptyResponseError,
    PlanProviderError,
    PlanProviderInvalidStructuredOutputError,
    PlanProviderMalformedResponseError,
    PlanProviderRateLimitError,
    PlanProviderTimeoutError,
    PlanProviderUnavailableError,
)
from app.manager.providers.fake import FakePlanProvider


class TestProviderTaskProposal:
    def test_valid_proposal(self):
        p = ProviderTaskProposal(
            proposal_key="task_a",
            title="Task A",
            description="Description A",
            priority=1,
            dependencies=[],
        )
        assert p.proposal_key == "task_a"
        assert p.title == "Task A"
        assert p.priority == 1

    def test_invalid_proposal_key_special_chars(self):
        with pytest.raises(Exception):
            ProviderTaskProposal(
                proposal_key="a/b",
                title="Task",
                description="d",
            )

    def test_invalid_title_empty(self):
        with pytest.raises(Exception):
            ProviderTaskProposal(
                proposal_key="a",
                title="   ",
                description="d",
            )

    def test_invalid_description_empty(self):
        with pytest.raises(Exception):
            ProviderTaskProposal(
                proposal_key="a",
                title="t",
                description="  ",
            )

    def test_invalid_title_too_long(self):
        with pytest.raises(Exception):
            ProviderTaskProposal(
                proposal_key="a",
                title="x" * 201,
                description="d",
            )

    def test_invalid_description_too_long(self):
        with pytest.raises(Exception):
            ProviderTaskProposal(
                proposal_key="a",
                title="t",
                description="x" * 10_241,
            )

    def test_invalid_dependency_key(self):
        with pytest.raises(Exception):
            ProviderTaskProposal(
                proposal_key="a",
                title="A",
                description="d",
                dependencies=["invalid/key"],
            )

    def test_duplicate_proposal_keys_rejected_by_normalize(self):
        from app.manager.models import normalize_plan, ManagerInvalidPlanError

        user_plan = UserPlan(
            plan_id="p1",
            user_request="req",
            project_id="proj",
            acceptance_criteria=[],
        )
        proposals = [
            ProviderTaskProposal(proposal_key="dup", title="T1", description="d"),
            ProviderTaskProposal(proposal_key="dup", title="T2", description="d"),
        ]
        with pytest.raises(ManagerInvalidPlanError, match="Duplicate proposal_key"):
            normalize_plan(user_plan, proposals)

    def test_extra_fields_forbidden(self):
        with pytest.raises(Exception):
            ProviderTaskProposal(
                proposal_key="a",
                title="A",
                description="d",
                acceptance_criteria=["c"],  # type: ignore[call-arg]
            )

    def test_acceptance_criteria_is_not_accepted(self):
        with pytest.raises(Exception):
            ProviderTaskProposal(
                proposal_key="a",
                title="A",
                description="d",
                acceptance_criteria=["should fail"],  # type: ignore[call-arg]
            )


class TestFakePlanProvider:
    @pytest.mark.asyncio
    async def test_returns_predefined_proposals(self):
        proposals = [
            ProviderTaskProposal(proposal_key="a", title="A", description="d"),
        ]
        provider = FakePlanProvider(proposals=proposals)
        user_plan = UserPlan(plan_id="p", user_request="r", project_id="proj")
        result = await provider.generate_plan(user_plan, PlanningConstraints())
        assert result == proposals

    @pytest.mark.asyncio
    async def test_raises_predefined_error(self):
        provider = FakePlanProvider(error=PlanProviderTimeoutError("boom"))
        user_plan = UserPlan(plan_id="p", user_request="r", project_id="proj")
        with pytest.raises(PlanProviderTimeoutError):
            await provider.generate_plan(user_plan, PlanningConstraints())

    @pytest.mark.asyncio
    async def test_records_calls(self):
        proposals = [
            ProviderTaskProposal(proposal_key="a", title="A", description="d"),
        ]
        provider = FakePlanProvider(proposals=proposals)
        user_plan = UserPlan(
            plan_id="p1", user_request="req", project_id="proj", acceptance_criteria=["c"]
        )
        constraints = PlanningConstraints(max_tasks=5)

        await provider.generate_plan(user_plan, constraints)
        assert len(provider.calls) == 1
        assert provider.calls[0][0] == user_plan
        assert provider.calls[0][1] == constraints
