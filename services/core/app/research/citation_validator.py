"""Deterministic validation for source references in research claims."""
from __future__ import annotations

from collections.abc import Sequence

from app.research.models import ResearchClaim, ResearchClaimStatus, ResearchSource


class CitationValidationError(ValueError):
    """Raised when a claim references evidence not supplied by the application."""


def validate_claim_citations(
    claims: Sequence[ResearchClaim],
    sources: Sequence[ResearchSource],
) -> None:
    """Reject duplicate source IDs, unknown citations, and unsupported empty links."""
    source_ids = [source.source_id for source in sources]
    if len(source_ids) != len(set(source_ids)):
        raise CitationValidationError("Research response contains duplicate source IDs.")

    known_ids = set(source_ids)
    for claim in claims:
        if len(claim.source_ids) != len(set(claim.source_ids)):
            raise CitationValidationError("A research claim contains duplicate source IDs.")
        unknown_ids = set(claim.source_ids) - known_ids
        if unknown_ids:
            raise CitationValidationError("A research claim references an unknown source ID.")
        if claim.evidence_status in {
            ResearchClaimStatus.SUPPORTED,
            ResearchClaimStatus.CONFLICTING,
        } and not claim.source_ids:
            raise CitationValidationError("Supported or conflicting claims must cite at least one source.")


__all__ = ["CitationValidationError", "validate_claim_citations"]
