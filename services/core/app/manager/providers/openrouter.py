"""OpenRouter planning adapter implementing PlanProvider.

Wraps the existing OpenRouterProvider for structured plan generation.
Does not modify OpenRouterProvider.
"""
import asyncio
import logging
from typing import List

import httpx
from app.ai.models import AIResponse
from app.ai.normalization import normalize_tool_calls, normalize_tool_definitions
from app.ai.providers.openrouter import OpenRouterProvider, OpenRouterProviderError
from app.manager.models import PlanningConstraints, ProviderTaskProposal, UserPlan
from app.manager.output_parser import PlanningOutputParser
from app.manager.providers.base import (
    PlanProvider,
    PlanProviderContextLimitError,
    PlanProviderEmptyResponseError,
    PlanProviderError,
    PlanProviderMalformedResponseError,
    PlanProviderRateLimitError,
    PlanProviderTimeoutError,
    PlanProviderUnavailableError,
)
from app.manager.prompt_builder import build_messages, build_planning_tool_definition

logger = logging.getLogger(__name__)


class OpenRouterPlanningAdapter(PlanProvider):
    def __init__(
        self,
        base_url: str = "https://openrouter.ai/api/v1",
        api_key: str | None = None,
        model: str = "openrouter/free",
        timeout: float = 60.0,
    ) -> None:
        self._provider = OpenRouterProvider(base_url, api_key, timeout)
        self._model = model

    async def generate_plan(
        self,
        user_plan: UserPlan,
        constraints: PlanningConstraints,
    ) -> List[ProviderTaskProposal]:
        messages = build_messages(user_plan, constraints)
        tool_definition = build_planning_tool_definition()

        last_exc: BaseException | None = None
        for attempt in range(3):
            try:
                raw_response = await self._call_provider(messages, tool_definition)
                proposals = PlanningOutputParser.parse(raw_response)
                return proposals
            except PlanProviderMalformedResponseError:
                raise
            except PlanProviderError as exc:
                last_exc = exc
                if not self._is_retryable(exc):
                    raise
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                    continue
                raise

        if last_exc is not None:
            raise last_exc
        raise PlanProviderError("OpenRouter planning failed after retries")

    async def _call_provider(
        self,
        messages: list[dict[str, str]],
        tool_definition: dict,
    ) -> AIResponse:
        payload = {
            "model": self._model,
            "messages": messages,
            "tools": normalize_tool_definitions([tool_definition]),
            "tool_choice": {"type": "function", "function": {"name": "planning.propose_plan"}},
        }

        try:
            headers = self._provider._headers()
        except OpenRouterProviderError as exc:
            raise PlanProviderError(str(exc)) from exc

        try:
            async with httpx.AsyncClient(timeout=self._provider.timeout) as client:
                response = await client.post(
                    f"{self._provider.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
        except httpx.TimeoutException:
            raise PlanProviderTimeoutError("OpenRouter planning request timed out") from None
        except (httpx.ConnectError, httpx.NetworkError):
            raise PlanProviderUnavailableError("OpenRouter network error") from None

        if response.status_code != 200:
            raise self._map_openrouter_error(
                self._provider._map_http_error(response)
            ) from None

        body = response.json()
        try:
            choice = body["choices"][0]
            message = choice["message"]
            content = message.get("content")
            if not isinstance(content, str) and content is not None:
                raise PlanProviderMalformedResponseError(
                    "Invalid planning response content type"
                )
            tool_calls = None
            raw_tool_calls = message.get("tool_calls")
            if isinstance(raw_tool_calls, list) and raw_tool_calls:
                tool_calls = normalize_tool_calls(raw_tool_calls)
            finish_reason = choice.get("finish_reason") or (
                "tool_calls" if tool_calls else "stop"
            )
            return AIResponse(
                content=content, tool_calls=tool_calls, finish_reason=finish_reason
            )
        except PlanProviderMalformedResponseError:
            raise
        except Exception as exc:
            raise PlanProviderMalformedResponseError(
                f"Invalid planning response: {exc}"
            ) from exc

    @staticmethod
    def _map_openrouter_error(
        exc: OpenRouterProviderError,
    ) -> PlanProviderError:
        status = exc.status
        if status == "timeout":
            return PlanProviderTimeoutError(str(exc))
        if status == "rate_limited":
            return PlanProviderRateLimitError(str(exc))
        if status in ("model_unavailable", "server_error", "network_error"):
            return PlanProviderUnavailableError(str(exc))
        if status == "provider_not_configured":
            return PlanProviderError(str(exc))
        if status in (
            "invalid_api_key",
            "provider_quota_exceeded",
            "unauthorized",
            "model_not_found",
        ):
            return PlanProviderError(str(exc))
        return PlanProviderError(str(exc))

    @staticmethod
    def _is_retryable(error: PlanProviderError) -> bool:
        return isinstance(
            error,
            (
                PlanProviderTimeoutError,
                PlanProviderRateLimitError,
                PlanProviderUnavailableError,
                PlanProviderEmptyResponseError,
            ),
        )
