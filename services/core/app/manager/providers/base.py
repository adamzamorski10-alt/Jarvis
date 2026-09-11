"""
PlanProvider abstraction and provider error hierarchy.

PlanManager depends on this interface only.  Real provider adapters
(Gemini, OpenRouter, Kimi, etc.) are implemented outside this package.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.manager.models import PlanningConstraints, ProviderTaskProposal, UserPlan


class PlanProviderError(Exception):
    """Base for all provider communication failures."""


class PlanProviderTimeoutError(PlanProviderError):
    def __init__(self, reason: str = "Provider request timed out") -> None:
        super().__init__(reason)


class PlanProviderRateLimitError(PlanProviderError):
    def __init__(self, reason: str = "Provider rate limit exceeded") -> None:
        super().__init__(reason)


class PlanProviderContextLimitError(PlanProviderError):
    def __init__(self, reason: str = "Provider context limit exceeded") -> None:
        super().__init__(reason)


class PlanProviderEmptyResponseError(PlanProviderError):
    def __init__(self, reason: str = "Provider returned empty response") -> None:
        super().__init__(reason)


class PlanProviderMalformedResponseError(PlanProviderError):
    def __init__(self, reason: str = "Provider returned malformed response") -> None:
        super().__init__(reason)


class PlanProviderUnavailableError(PlanProviderError):
    def __init__(self, reason: str = "Provider is unavailable") -> None:
        super().__init__(reason)


class PlanProviderInvalidStructuredOutputError(PlanProviderError):
    def __init__(self, reason: str = "Provider returned invalid structured output") -> None:
        super().__init__(reason)


class PlanProvider(ABC):
    """Provider-neutral planning contract."""

    @abstractmethod
    async def generate_plan(
        self,
        user_plan: UserPlan,
        constraints: PlanningConstraints,
    ) -> list[ProviderTaskProposal]:
        """
        Generate structured task proposals for the given user plan.

        Args:
            user_plan: Application-controlled plan input.
            constraints: Planning scope limits.

        Returns:
            List of structured task proposals.

        Raises:
            PlanProviderTimeoutError
            PlanProviderRateLimitError
            PlanProviderContextLimitError
            PlanProviderEmptyResponseError
            PlanProviderMalformedResponseError
            PlanProviderUnavailableError
            PlanProviderInvalidStructuredOutputError
        """
        raise NotImplementedError
