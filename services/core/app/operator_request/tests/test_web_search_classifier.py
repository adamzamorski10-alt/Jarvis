from app.identity.models import ActorType, RequestContext
from app.operator_request.classifier import OperationClassifier
from app.operator_request.models import OperatorRequest


def test_search_request_classifies_as_web_search():
    request = OperatorRequest(
        request_id="req_search",
        user_request="Find information about the latest Python release on the web",
        normalized_request="Find information about the latest Python release on the web",
        correlation_id="corr_search",
        request_context=RequestContext(
            user_id="user_1",
            actor_type=ActorType.USER,
            actor_id="user_1",
            session_id="sess_1",
        ),
    )
    result = OperationClassifier().classify(request)
    assert result.operation_type == "WEB_SEARCH"
