"""
AI Manager — Provider-Neutral Provider Contract.

AIManagerProvider implementations translate model-specific APIs into
the provider-neutral decide() contract.  They must NOT:
  - execute actions,
  - call subprocess or shell,
  - write files,
  - modify workspaces,
  - invoke Git,
  - change task/run/workspace state,
  - call ToolExecutionService,
  - call PermissionEngine,
  - bypass ApprovalService.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.ai_manager.errors import (
    AIManagerContextLimitError,
    AIManagerEmptyResponseError,
    AIManagerError,
    AIManagerMalformedDecisionError,
    AIManagerProviderError,
    AIManagerRateLimitedError,
    AIManagerTimeoutError,
    AIManagerUnavailableError,
)
from app.ai_manager.models import RawManagerDecision, UserIntent


class AIManagerProviderTimeoutError(AIManagerProviderError):
    def __init__(self, reason: str = "Provider request timed out") -> None:
        super().__init__(reason)


class AIManagerProviderRateLimitError(AIManagerProviderError):
    def __init__(self, reason: str = "Provider rate limit exceeded") -> None:
        super().__init__(reason)


class AIManagerProviderContextLimitError(AIManagerProviderError):
    def __init__(self, reason: str = "Provider context limit exceeded") -> None:
        super().__init__(reason)


class AIManagerProviderEmptyResponseError(AIManagerProviderError):
    def __init__(self, reason: str = "Provider returned empty response") -> None:
        super().__init__(reason)


class AIManagerProviderMalformedResponseError(AIManagerProviderError):
    def __init__(self, reason: str = "Provider returned malformed response") -> None:
        super().__init__(reason)


class AIManagerProviderUnavailableError(AIManagerProviderError):
    def __init__(self, reason: str = "Provider is unavailable") -> None:
        super().__init__(reason)


class AIManagerProvider(ABC):
    """
    Provider-neutral AI Manager contract.

    The provider's ONLY job is to produce a structured RawManagerDecision
    from a UserIntent.  The application normalizes and validates the output.
    """

    @abstractmethod
    async def decide(self, intent: UserIntent) -> RawManagerDecision:
        """
        Produce a structured manager decision from user intent.

        Args:
            intent: Application-controlled user intent.

        Returns:
            RawManagerDecision — untrusted model output.

        Raises:
            AIManagerProviderTimeoutError
            AIManagerProviderRateLimitError
            AIManagerProviderContextLimitError
            AIManagerProviderEmptyResponseError
            AIManagerProviderMalformedResponseError
            AIManagerProviderUnavailableError
        """
        raise NotImplementedError
