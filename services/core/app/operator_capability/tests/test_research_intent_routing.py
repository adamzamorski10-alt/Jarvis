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
