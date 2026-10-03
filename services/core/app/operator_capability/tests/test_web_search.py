from app.operator_capability.models import CapabilityKind
from app.operator_capability.resolver import CapabilityResolver
from app.operator_request.models import OperatorRequest, ReadinessState
from app.identity.models import ActorType, RequestContext


def make_request(text="search the web for Python 3.13 news"):
    return OperatorRequest(
        request_id="req_web",
        user_request=text,
        normalized_request=text,
        operation_type="WEB_SEARCH",
        readiness_state=ReadinessState.SUFFICIENT,
        correlation_id="corr_web",
        request_context=RequestContext(
            user_id="user_1",
            actor_type=ActorType.USER,
            actor_id="user_1",
            session_id="sess_1",
        ),
    )


def test_web_search_resolves_to_service():
    resolution = CapabilityResolver().resolve(make_request())
    assert resolution.capability_kind == CapabilityKind.SERVICE
    assert resolution.capability_id == "web.search"
    assert resolution.requires_approval is False
    assert resolution.requires_permission is True


def test_web_search_is_not_answer_only():
    resolution = CapabilityResolver().resolve(make_request())
    assert resolution.capability_kind != CapabilityKind.ANSWER_ONLY


import pytest
from app.operator_capability.executor import OperatorCapabilityExecutor


@pytest.mark.asyncio
async def test_web_search_executes_through_service():
    class FakeSearch:
        async def search(self, request, request_id=None):
            from app.web_search.models import WebSearchResponse, WebSearchResult
            return WebSearchResponse(
                query=request.query,
                provider="fake",
                results=[WebSearchResult(title="Example", url="https://example.com", rank=1)],
            )

    request = make_request()
    resolution = CapabilityResolver().resolve(request)
    executor = OperatorCapabilityExecutor(web_search_service=FakeSearch())
    result = await executor.execute(request, resolution)
    assert result.success is True
    assert result.service_result["results"][0]["url"] == "https://example.com"
