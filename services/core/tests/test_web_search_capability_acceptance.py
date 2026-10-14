"""Stage 22.4 — Web Search acceptance / capability contract tests."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.identity.models import ActorType, RequestContext
from app.operator_capability.executor import OperatorCapabilityExecutor
from app.operator_capability.models import CapabilityKind
from app.operator_capability.resolver import CapabilityResolver
from app.operator_request.classifier import OperationClassifier
from app.operator_request.models import OperatorRequest, ReadinessState
from app.operator_request.service import OperatorRequestService
from app.tools.models import PermissionDecisionType
from app.web_search.models import WebSearchResponse, WebSearchResult
from app.web_search.service import WebSearchService


def _context() -> RequestContext:
    return RequestContext(
        user_id="acceptance-user",
        actor_type=ActorType.USER,
        actor_id="acceptance-actor",
        session_id="acceptance-session",
    )


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def search(self, request):
        self.calls += 1
        return WebSearchResponse(
            query=request.query,
            provider=self.name,
            results=[
                WebSearchResult(
                    title="Example source",
                    url="https://example.com/source",
                    snippet="Authoritative test result.",
                    source="example.com",
                    rank=1,
                )
            ],
        )


def _permission_engine(decision=PermissionDecisionType.ALLOW):
    engine = AsyncMock()
    engine.evaluate.return_value = SimpleNamespace(
        decision=decision,
        reason="acceptance policy",
        rule_id="acceptance-web-search",
    )
    return engine


@pytest.mark.asyncio
async def test_explicit_web_request_completes_real_capability_flow():
    request_service = OperatorRequestService()
    resolver = CapabilityResolver()
    provider = FakeProvider()
    executor = OperatorCapabilityExecutor(
        web_search_service=WebSearchService(provider=provider),
        permission_engine=_permission_engine(),
    )

    request = await request_service.create_request(
        user_request="Znajdź informacje o najnowszym iPhonie",
        request_context=_context(),
        conversation_id="conv-acceptance",
        message_id="msg-acceptance",
    )
    request = await request_service.classify_request(request)
    assert request.operation_type == "WEB_SEARCH"
    assert request.classification_reason == "deterministic"

    request = await request_service.mark_ready(request)
    resolution = resolver.resolve(request)
    assert resolution.capability_kind == CapabilityKind.SERVICE
    assert resolution.capability_id == "web.search"
    assert resolution.parameters["query"] == request.user_request

    result = await executor.execute(request, resolution)

    assert result.success is True
    assert result.state == "EXECUTION_COMPLETED"
    assert result.capability_id == "web.search"
    assert "https://example.com/source" in result.user_message
    assert result.service_result["results"][0]["url"] == "https://example.com/source"
    assert provider.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "Znajdź informacje o najnowszym iPhonie",
        "Sprawdź w internecie ile kosztuje Python",
        "Wyszukaj najnowsze informacje o Pythonie",
        "Research this topic",
        "Znajdź mi dobre źródła na temat AI",
    ],
)
async def test_explicit_web_intent_is_deterministic(message: str):
    service = OperatorRequestService()
    request = await service.create_request(user_request=message, request_context=_context())
    classified = await service.classify_request(request)
    assert classified.operation_type == "WEB_SEARCH"
    assert classified.classification_reason == "deterministic"
    assert classified.classification_confidence >= 0.8


@pytest.mark.asyncio
async def test_ordinary_information_request_stays_answer_only():
    service = OperatorRequestService()
    resolver = CapabilityResolver()
    request = await service.create_request(
        user_request="Co to jest fotosynteza?",
        request_context=_context(),
    )
    classified = await service.classify_request(request)

    assert classified.operation_type == "INFORMATION_REQUEST"
    resolution = resolver.resolve(classified)
    assert resolution.capability_kind == CapabilityKind.ANSWER_ONLY
    assert resolution.capability_id is None


@pytest.mark.asyncio
async def test_ai_only_web_search_guess_cannot_trigger_network_capability():
    class AIClassifier:
        def __call__(self, **kwargs):
            return {"operation_type": "WEB_SEARCH", "confidence": 0.99, "reason": "model guess"}

    service = OperatorRequestService(classifier=OperationClassifier(ai_classifier=AIClassifier()))
    resolver = CapabilityResolver()
    request = await service.create_request(
        user_request="Artificial intelligence market outlook",
        request_context=_context(),
    )
    classified = await service.classify_request(request)

    assert classified.operation_type != "WEB_SEARCH"
    resolution = resolver.resolve(classified)
    assert resolution.capability_kind == CapabilityKind.ANSWER_ONLY
    assert resolution.capability_id is None


@pytest.mark.asyncio
async def test_permission_denial_blocks_provider_call():
    service = OperatorRequestService()
    resolver = CapabilityResolver()
    provider = FakeProvider()
    executor = OperatorCapabilityExecutor(
        web_search_service=WebSearchService(provider=provider),
        permission_engine=_permission_engine(PermissionDecisionType.DENY),
    )
    request = await service.create_request(
        user_request="Wyszukaj najnowsze informacje o Pythonie",
        request_context=_context(),
    )
    request = await service.classify_request(request)
    request = await service.mark_ready(request)

    result = await executor.execute(request, resolver.resolve(request))

    assert result.success is False
    assert result.state == "BLOCKED"
    assert result.error_category == "PERMISSION_DENIED"
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_readiness_gate_blocks_web_search_before_provider():
    resolver = CapabilityResolver()
    provider = FakeProvider()
    executor = OperatorCapabilityExecutor(
        web_search_service=WebSearchService(provider=provider),
        permission_engine=_permission_engine(),
    )
    request = OperatorRequest(
        request_id="req-not-ready",
        user_request="Wyszukaj informacje o Pythonie",
        operation_type="WEB_SEARCH",
        correlation_id="corr-not-ready",
        request_context=_context(),
        readiness_state=ReadinessState.INSUFFICIENT,
    )

    result = await executor.execute(request, resolver.resolve(request))

    assert result.success is False
    assert result.state == "BLOCKED"
    assert result.error_category == "READINESS_NOT_SUFFICIENT"
    assert provider.calls == 0
