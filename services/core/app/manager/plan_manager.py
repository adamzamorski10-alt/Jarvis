"""
PlanManager — orchestrates provider-neutral plan generation.

PlanManager is a thin orchestration layer:
  1. accepts UserPlan
  2. calls PlanProvider.generate_plan(...)
  3. calls normalize_plan(...)
  4. returns ManagerPlanResult

It does NOT:
  - persist plans
  - execute tasks
  - select providers/models
  - implement retry loops
  - import execution, security, or orchestration modules
"""
from __future__ import annotations

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
    normalize_plan,
)
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
)


class PlanManager:
    """
    Provider-neutral plan generation orchestrator.

    The caller supplies a PlanProvider implementation.  PlanManager translates
    provider exceptions into ManagerProviderError subclasses and delegates
    semantic validation to normalize_plan().
    """

    def __init__(
        self,
        provider: PlanProvider,
        constraints: PlanningConstraints | None = None,
    ) -> None:
        self._provider = provider
        self._constraints = constraints or PlanningConstraints()

    async def plan(self, user_plan: UserPlan) -> ManagerPlanResult:
        """
        Generate a normalized plan from user input via the configured provider.

        Raises:
            ManagerProviderError: provider communication failure.
            ManagerInvalidPlanError: normalized plan is invalid.
        """
        try:
            proposals = await self._provider.generate_plan(user_plan, self._constraints)
        except PlanProviderTimeoutError as exc:
            raise ManagerTimeoutError(str(exc)) from exc
        except PlanProviderRateLimitError as exc:
            raise ManagerRateLimitedError(str(exc)) from exc
        except PlanProviderContextLimitError as exc:
            raise ManagerContextLimitError(str(exc)) from exc
        except PlanProviderEmptyResponseError as exc:
            raise ManagerEmptyResponseError(str(exc)) from exc
        except PlanProviderMalformedResponseError as exc:
            raise ManagerMalformedResponseError(str(exc)) from exc
        except PlanProviderUnavailableError as exc:
            raise ManagerProviderError(str(exc)) from exc
        except PlanProviderInvalidStructuredOutputError as exc:
            raise ManagerInvalidPlanError(str(exc)) from exc
        except PlanProviderError as exc:
            raise ManagerProviderError(str(exc)) from exc
        return normalize_plan(user_plan, proposals)
