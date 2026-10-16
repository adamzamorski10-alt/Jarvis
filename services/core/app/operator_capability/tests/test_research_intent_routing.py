"""Focused tests for explicit research intent routing."""
from app.operator_capability.executor import OperatorCapabilityExecutor


def test_explicit_research_phrasing_is_detected():
    assert OperatorCapabilityExecutor._is_explicit_research_request(
        "Please research current public evidence"
    )
    assert OperatorCapabilityExecutor._is_explicit_research_request(
        "Zrób research na temat bezpieczeństwa AI"
    )
    assert OperatorCapabilityExecutor._is_explicit_research_request(
        "Przeprowadź analizę źródeł dotyczących SSRF"
    )


def test_ordinary_web_search_does_not_trigger_deep_research():
    assert not OperatorCapabilityExecutor._is_explicit_research_request(
        "Find the official documentation page"
    )
    assert not OperatorCapabilityExecutor._is_explicit_research_request(
        "Znajdź stronę oficjalnej dokumentacji"
    )



import pytest
from unittest.mock import AsyncMock, MagicMock

from app.operator_capability.models import CapabilityKind, CapabilityResolution
from app.operator_request.models import OperatorRequest, ReadinessState
from app.tools.models import PermissionDecisionType
from app.identity.models import ActorType, RequestContext


@pytest.mark.asyncio
async def test_explicit_research_uses_research_runner_after_permission_check():
    web_search = MagicMock()
    web_search.search = AsyncMock()
    runner = AsyncMock()
    response = MagicMock()
    response.synthesized = True
    response.answer = "Evidence-backed answer"
    response.sources = [object(), object()]
    response.citations = [object()]
    response.limitations = []
    response.model_dump.return_value = {"answer": "Evidence-backed answer", "synthesized": True}
    runner.return_value = response

    permission_engine = MagicMock()
    decision = MagicMock()
    decision.decision = PermissionDecisionType.ALLOW
    decision.rule_id = "test-rule"
    decision.reason = "allowed"
    permission_engine.evaluate = AsyncMock(return_value=decision)

    executor = OperatorCapabilityExecutor(
        web_search_service=web_search,
        web_research_runner=runner,
        permission_engine=permission_engine,
    )
    request = OperatorRequest(
        request_id="req_research",
        user_request="Research public sources about SSRF",
        operation_type="WEB_SEARCH",
        correlation_id="corr_research",
        readiness_state=ReadinessState.SUFFICIENT,
        request_context=RequestContext(
            user_id="test_user",
            actor_type=ActorType.USER,
            actor_id="test_actor",
            session_id="test_session",
        ),
    )
    resolution = CapabilityResolution(
        request_id="req_research",
        operation_type="WEB_SEARCH",
        capability_kind=CapabilityKind.SERVICE,
        capability_id="web.search",
        description="Public web research",
        parameters={"query": "Research public sources about SSRF"},
        requires_permission=True,
    )

    result = await executor._execute_web_search(request, resolution)

    assert result.success is True
    assert result.result_metadata["research"] is True
    runner.assert_awaited_once_with("Research public sources about SSRF", request_id="req_research")
    web_search.search.assert_not_awaited()
    permission_engine.evaluate.assert_awaited_once()

@pytest.mark.asyncio
async def test_research_runner_errors_do_not_expose_internal_exception_details():
    web_search = MagicMock()
    web_search.search = AsyncMock()
    runner = AsyncMock(side_effect=RuntimeError("secret-provider-endpoint and token=example"))
    permission_engine = MagicMock()
    decision = MagicMock()
    decision.decision = PermissionDecisionType.ALLOW
    decision.rule_id = "test-rule"
    decision.reason = "allowed"
    permission_engine.evaluate = AsyncMock(return_value=decision)

    executor = OperatorCapabilityExecutor(
        web_search_service=web_search,
        web_research_runner=runner,
        permission_engine=permission_engine,
    )
    request = OperatorRequest(
        request_id="req_research_failure",
        user_request="Research public sources about SSRF",
        operation_type="WEB_SEARCH",
        correlation_id="corr_research_failure",
        readiness_state=ReadinessState.SUFFICIENT,
        request_context=RequestContext(
            user_id="test_user",
            actor_type=ActorType.USER,
            actor_id="test_actor",
            session_id="test_session",
        ),
    )
    resolution = CapabilityResolution(
        request_id="req_research_failure",
        operation_type="WEB_SEARCH",
        capability_kind=CapabilityKind.SERVICE,
        capability_id="web.search",
        description="Public web research",
        parameters={"query": "Research public sources about SSRF"},
        requires_permission=True,
    )

    result = await executor._execute_web_search(request, resolution)

    assert result.success is False
    assert result.error_category == "RESEARCH_FAILED"
    assert result.error_detail == "Research execution failed."
    assert "secret-provider-endpoint" not in str(result.model_dump())
    runner.assert_awaited_once()
    web_search.search.assert_not_awaited()

