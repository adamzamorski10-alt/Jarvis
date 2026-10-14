from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.research.citation_validator import CitationValidationError, validate_claim_citations
from app.research.models import (
    ResearchClaim,
    ResearchClaimStatus,
    ResearchRequest,
    ResearchSource,
    ResearchSourceStatus,
)


def make_source(source_id: str = "src_1") -> ResearchSource:
    return ResearchSource(
        source_id=source_id,
        title="Example source",
        url="https://example.com/article",
        host="example.com",
        retrieved_at=datetime.now(timezone.utc),
        status=ResearchSourceStatus.FETCHED,
        excerpt="A bounded piece of source evidence.",
    )


def test_research_request_normalizes_bounded_fields() -> None:
    request = ResearchRequest(query="  latest research  ", country="pl", search_lang="PL")

    assert request.query == "latest research"
    assert request.country == "PL"
    assert request.search_lang == "pl"
    assert request.max_sources == 5


@pytest.mark.parametrize("query", ["", "   ", "x" * 601])
def test_research_request_rejects_blank_or_oversized_query(query: str) -> None:
    with pytest.raises(ValidationError):
        ResearchRequest(query=query)


@pytest.mark.parametrize("country", ["P", "POL", "éL", "1!"])
def test_research_request_rejects_invalid_country(country: str) -> None:
    with pytest.raises(ValidationError):
        ResearchRequest(query="research", country=country)


def test_research_request_rejects_extra_fields_and_excess_source_budget() -> None:
    with pytest.raises(ValidationError):
        ResearchRequest(query="research", source_urls=["https://example.com"])
    with pytest.raises(ValidationError):
        ResearchRequest(query="research", max_sources=6)


def test_supported_claim_requires_a_known_source() -> None:
    claim = ResearchClaim(
        text="A sourced claim",
        source_ids=["src_1"],
        evidence_status=ResearchClaimStatus.SUPPORTED,
    )
    validate_claim_citations([claim], [make_source()])


def test_unknown_source_id_is_rejected() -> None:
    claim = ResearchClaim(
        text="A sourced claim",
        source_ids=["src_2"],
        evidence_status=ResearchClaimStatus.SUPPORTED,
    )
    with pytest.raises(CitationValidationError, match="unknown source"):
        validate_claim_citations([claim], [make_source()])


def test_supported_claim_without_source_is_rejected() -> None:
    claim = ResearchClaim(
        text="A sourced claim",
        evidence_status=ResearchClaimStatus.SUPPORTED,
    )
    with pytest.raises(CitationValidationError, match="must cite"):
        validate_claim_citations([claim], [make_source()])


def test_insufficient_claim_may_have_no_sources() -> None:
    claim = ResearchClaim(
        text="Available evidence is insufficient.",
        evidence_status=ResearchClaimStatus.INSUFFICIENT,
    )
    validate_claim_citations([claim], [])


def test_duplicate_source_ids_in_response_are_rejected() -> None:
    claim = ResearchClaim(
        text="A sourced claim",
        source_ids=["src_1"],
        evidence_status=ResearchClaimStatus.SUPPORTED,
    )
    with pytest.raises(CitationValidationError, match="duplicate source IDs"):
        validate_claim_citations([claim], [make_source(), make_source()])


def test_duplicate_source_ids_within_claim_are_rejected() -> None:
    claim = ResearchClaim(
        text="A sourced claim",
        source_ids=["src_1", "src_1"],
        evidence_status=ResearchClaimStatus.SUPPORTED,
    )
    with pytest.raises(CitationValidationError, match="duplicate source IDs"):
        validate_claim_citations([claim], [make_source()])


def test_source_ids_are_bounded_to_five_application_slots() -> None:
    with pytest.raises(ValidationError):
        make_source("src_6")
