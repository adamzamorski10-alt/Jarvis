import pytest
from pydantic import ValidationError

from app.web_research.models import (
    ResearchCitation,
    ResearchRequest,
    ResearchResponse,
    ResearchSource,
    ResearchSourceStatus,
)


def source(source_id: str = "src_012345abcdef", **overrides) -> ResearchSource:
    values = {
        "source_id": source_id,
        "title": "Example source",
        "url": "https://example.com/article",
        "hostname": "example.com",
        "status": ResearchSourceStatus.RETRIEVED,
        "extracted_text_length": 120,
    }
    values.update(overrides)
    return ResearchSource(**values)


def test_research_request_normalizes_query_and_has_small_source_budget():
    request = ResearchRequest(query="  useful research  ")
    assert request.query == "useful research"
    assert request.max_sources == 3


@pytest.mark.parametrize("query", ["", "   ", "hello\nworld"])
def test_research_request_rejects_blank_or_control_character_query(query):
    with pytest.raises(ValidationError):
        ResearchRequest(query=query)


@pytest.mark.parametrize("count", [0, 6, -1])
def test_research_request_rejects_out_of_range_source_budget(count):
    with pytest.raises(ValidationError):
        ResearchRequest(query="test", max_sources=count)


def test_research_contracts_are_immutable_and_reject_unknown_fields():
    item = source()
    with pytest.raises(ValidationError):
        item.title = "changed"
    with pytest.raises(ValidationError):
        ResearchRequest(query="test", unexpected=True)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://user:secret@example.com/",
        "https://example.com/has space",
        "https://example.com:99999/",
        "//example.com/path",
    ],
)
def test_research_source_rejects_unsafe_url_shapes(url):
    with pytest.raises(ValidationError):
        source(url=url)


def test_research_source_removes_fragment_from_identity_url():
    item = source(url="https://example.com/article#section")
    assert item.url == "https://example.com/article"


def test_research_source_rejects_hostname_that_does_not_match_url():
    with pytest.raises(ValidationError, match="hostname must match"):
        source(url="https://other.example/article", hostname="example.com")


def test_citation_requires_valid_ordered_offsets():
    with pytest.raises(ValidationError):
        ResearchCitation(source_id="src_012345abcdef", start_offset=9, end_offset=9)
    with pytest.raises(ValidationError):
        ResearchCitation(source_id="src_012345abcdef", start_offset=2)


def test_response_rejects_forged_or_unknown_source_ids():
    with pytest.raises(ValidationError, match="unknown source ID"):
        ResearchResponse(
            answer="A sourced answer.",
            sources=[source()],
            citations=[ResearchCitation(source_id="src_aaaaaaaaaaaa")],
            synthesized=True,
        )


def test_response_rejects_duplicate_source_ids():
    with pytest.raises(ValidationError, match="unique"):
        ResearchResponse(sources=[source(), source()])


def test_synthesized_response_requires_answer_and_citations():
    known_source = source()
    with pytest.raises(ValidationError, match="answer"):
        ResearchResponse(
            sources=[known_source],
            synthesized=True,
            citations=[ResearchCitation(source_id=known_source.source_id)],
        )
    with pytest.raises(ValidationError, match="citation"):
        ResearchResponse(answer="Answer", sources=[known_source], synthesized=True)


def test_non_synthesized_partial_result_can_report_source_status_without_answer():
    response = ResearchResponse(
        sources=[source(status=ResearchSourceStatus.SEARCH_ONLY, extracted_text_length=0)],
        limitations=["Source retrieval was unavailable."],
    )
    assert response.answer == ""
    assert response.synthesized is False
    assert response.sources[0].status is ResearchSourceStatus.SEARCH_ONLY
