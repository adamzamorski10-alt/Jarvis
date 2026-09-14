"""
FakeAIManagerProvider — deterministic provider for AI Manager tests.

Allows tests to:
  - return predefined RawManagerDecision
  - raise a predefined provider error
  - verify received UserIntent
"""
from __future__ import annotations

from app.ai_manager.models import RawManagerDecision, UserIntent
from app.ai_manager.providers.base import (
    AIManagerProvider,
    AIManagerProviderContextLimitError,
    AIManagerProviderEmptyResponseError,
    AIManagerProviderError,
    AIManagerProviderMalformedResponseError,
    AIManagerProviderRateLimitError,
    AIManagerProviderTimeoutError,
    AIManagerProviderUnavailableError,
)


class FakeAIManagerProvider(AIManagerProvider):
    """
    Deterministic fake provider for AIManager tests.
    """

    def __init__(
        self,
        decision: RawManagerDecision | None = None,
        error: AIManagerProviderError | None = None,
    ) -> None:
        self._decision = decision
        self._error = error
        self.calls: list[UserIntent] = []

    async def decide(self, intent: UserIntent) -> RawManagerDecision:
        self.calls.append(intent)
        if self._error is not None:
            raise self._error
        if self._decision is None:
            raise AIManagerProviderError("Fake provider has no decision configured")
        return self._decision
