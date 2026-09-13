"""
AI Manager — Core Decision Engine.

The AI Manager is a thin orchestration layer:
  1. accepts UserIntent
  2. calls AIManagerProvider.decide(intent)
  3. normalizes the RawManagerDecision
  4. returns ManagerResult

It does NOT:
  - execute actions
  - call subprocess
  - write files
  - modify workspaces
  - invoke Git
  - change task/run/workspace state
  - call ToolExecutionService
  - call PermissionEngine
  - bypass ApprovalService
  - implement retry loops
  - import execution, security, or orchestration modules

MODEL PROPOSES.
APPLICATION DECIDES.
AUTHORIZED APPLICATION SERVICES EXECUTE.
"""
from __future__ import annotations

from app.ai_manager.errors import (
    AIManagerContextLimitError,
    AIManagerEmptyResponseError,
    AIManagerInvalidDecisionError,
    AIManagerMalformedDecisionError,
    AIManagerPolicyRejectionError,
    AIManagerProviderError,
    AIManagerRateLimitedError,
    AIManagerTimeoutError,
    AIManagerUnavailableError,
)
from app.ai_manager.models import (
    DecisionOutcome,
    ManagerDecision,
    ManagerResult,
    RawManagerDecision,
    UserIntent,
)
from app.ai_manager.normalizer import normalize_raw_decision
from app.ai_manager.providers.base import (
    AIManagerProvider,
    AIManagerProviderContextLimitError,
    AIManagerProviderEmptyResponseError,
    AIManagerProviderMalformedResponseError,
    AIManagerProviderRateLimitError,
    AIManagerProviderTimeoutError,
    AIManagerProviderUnavailableError,
)


class AIManager:
    """
    Provider-neutral AI Manager decision engine.

    The caller supplies an AIManagerProvider implementation and optional
    application policy.  AIManager translates provider exceptions into
    AIManagerProviderError subclasses and delegates semantic validation
    to the normalizer.
    """

    def __init__(
        self,
        provider: AIManagerProvider,
        application_policy: dict[str, Any] | None = None,
    ) -> None:
        self._provider = provider
        self._application_policy = application_policy or {}

    async def decide(self, intent: UserIntent) -> ManagerResult:
        """
        Produce a structured manager decision from user intent.

        The model proposes; the application normalizes and validates.

        Args:
            intent: Application-controlled user intent.

        Returns:
            ManagerResult containing the normalized decision or error.
        """
        try:
            raw = await self._provider.decide(intent)
        except AIManagerProviderTimeoutError as exc:
            return ManagerResult(
                outcome=DecisionOutcome.REJECTED,
                error=AIManagerTimeoutError(str(exc)),
            )
        except AIManagerProviderRateLimitError as exc:
            return ManagerResult(
                outcome=DecisionOutcome.REJECTED,
                error=AIManagerRateLimitedError(str(exc)),
            )
        except AIManagerProviderContextLimitError as exc:
            return ManagerResult(
                outcome=DecisionOutcome.REJECTED,
                error=AIManagerContextLimitError(str(exc)),
            )
        except AIManagerProviderEmptyResponseError as exc:
            return ManagerResult(
                outcome=DecisionOutcome.REJECTED,
                error=AIManagerEmptyResponseError(str(exc)),
            )
        except AIManagerProviderMalformedResponseError as exc:
            return ManagerResult(
                outcome=DecisionOutcome.REJECTED,
                error=AIManagerMalformedDecisionError(str(exc)),
            )
        except AIManagerProviderUnavailableError as exc:
            return ManagerResult(
                outcome=DecisionOutcome.REJECTED,
                error=AIManagerUnavailableError(str(exc)),
            )
        except AIManagerProviderError as exc:
            return ManagerResult(
                outcome=DecisionOutcome.REJECTED,
                error=AIManagerProviderError(str(exc)),
            )

        # Normalize and validate
        try:
            decision = normalize_raw_decision(raw, intent, self._application_policy)
        except AIManagerMalformedDecisionError as exc:
            return ManagerResult(
                outcome=DecisionOutcome.REJECTED,
                error=exc,
            )
        except AIManagerInvalidDecisionError as exc:
            return ManagerResult(
                outcome=DecisionOutcome.REJECTED,
                error=exc,
            )
        except AIManagerPolicyRejectionError as exc:
            return ManagerResult(
                outcome=DecisionOutcome.REJECTED,
                error=exc,
            )

        return ManagerResult(
            outcome=decision.outcome,
            decision=decision,
        )
