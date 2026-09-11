"""
FakePlanProvider — deterministic provider for tests.

Allows tests to:
  - return predefined proposals
  - raise a predefined provider error
  - verify received UserPlan / PlanningConstraints
"""
from __future__ import annotations

from app.manager.models import ProviderTaskProposal
from app.manager.providers.base import (
    PlanProvider,
    PlanProviderContextLimitError,
    PlanProviderEmptyResponseError,
    PlanProviderError,
    PlanProviderInvalidStructuredOutputError,
    PlanProviderMalformedResponseError,
    PlanProviderRateLimitError,
    PlanProviderTimeoutError,
    PlanProviderUnavailableError,
    PlanningConstraints,
    UserPlan,
)


class FakePlanProvider(PlanProvider):
    """
    Deterministic fake provider for PlanManager tests.
    """

    def __init__(
        self,
        proposals: list[ProviderTaskProposal] | None = None,
        error: PlanProviderError | None = None,
    ) -> None:
        self._proposals = proposals or []
        self._error = error
        self.calls: list[tuple[UserPlan, PlanningConstraints]] = []

    async def generate_plan(
        self,
        user_plan: UserPlan,
        constraints: PlanningConstraints,
    ) -> list[ProviderTaskProposal]:
        self.calls.append((user_plan, constraints))
        if self._error is not None:
            raise self._error
        return list(self._proposals)
