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
