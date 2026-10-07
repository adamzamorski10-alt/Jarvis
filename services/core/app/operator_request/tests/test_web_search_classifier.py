from app.identity.models import ActorType, RequestContext
from app.operator_request.classifier import OperationClassifier
from app.operator_request.models import OperatorRequest


def make_request(text: str) -> OperatorRequest:
    return OperatorRequest(
        request_id="req_search",
        user_request=text,
        normalized_request=text,
        correlation_id="corr_search",
        request_context=RequestContext(
            user_id="user_1",
            actor_type=ActorType.USER,
            actor_id="user_1",
            session_id="sess_1",
        ),
    )


def test_search_request_classifies_as_web_search():
    result = OperationClassifier().classify(
        make_request("Find information about the latest Python release on the web")
    )
    assert result.operation_type == "WEB_SEARCH"


def test_online_check_classifies_as_web_search():
    result = OperationClassifier().classify(make_request("Check online how much X costs"))
    assert result.operation_type == "WEB_SEARCH"


def test_polish_search_request_classifies_as_web_search():
    result = OperationClassifier().classify(make_request("Znajdź mi informacje o najnowszym iPhonie"))
    assert result.operation_type == "WEB_SEARCH"


def test_polish_online_check_classifies_as_web_search():
    result = OperationClassifier().classify(make_request("Sprawdź w internecie ile kosztuje X"))
    assert result.operation_type == "WEB_SEARCH"


def test_ordinary_information_question_does_not_force_web_search():
    result = OperationClassifier().classify(make_request("What is photosynthesis?"))
    assert result.operation_type == "INFORMATION_REQUEST"


def test_polish_ordinary_information_question_does_not_force_web_search():
    result = OperationClassifier().classify(make_request("Co to jest fotosynteza?"))
    assert result.operation_type == "INFORMATION_REQUEST"


def test_ai_classifier_cannot_force_web_search_without_explicit_web_intent():
    classifier = OperationClassifier(
        ai_classifier=lambda **_: {
            "operation_type": "WEB_SEARCH",
            "confidence": 0.99,
            "reason": "model guess",
        }
    )
    result = classifier.classify(make_request("What is photosynthesis?"))
    assert result.operation_type != "WEB_SEARCH"


def test_generic_find_request_does_not_force_web_search():
    result = OperationClassifier().classify(make_request("Find my task 123"))
    assert result.operation_type != "WEB_SEARCH"


def test_generic_polish_find_request_does_not_force_web_search():
    result = OperationClassifier().classify(make_request("Znajdź moje zadanie 123"))
    assert result.operation_type != "WEB_SEARCH"
