"""
Operator Request — Error Hierarchy.

All Operator Request errors are application-defined.
Model output is untrusted; only these exceptions communicate application-level failures.
"""
from __future__ import annotations


class OperatorRequestError(Exception):
    """Base exception for all Operator Request errors."""
    pass


class InvalidOperatorRequestError(OperatorRequestError):
    """Input validation failed."""
    pass


class OversizedRequestError(InvalidOperatorRequestError):
    """Request exceeds size limits."""
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Oversized request: {reason}")


class MalformedRequestError(InvalidOperatorRequestError):
    """Request structure is malformed."""
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Malformed request: {reason}")


class ClassificationFailedError(OperatorRequestError):
    """Operation classification failed."""
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Classification failed: {reason}")


class ReadinessCheckFailedError(OperatorRequestError):
    """Readiness check failed due to infrastructure error."""
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Readiness check failed: {reason}")


class NormalizationFailedError(OperatorRequestError):
    """Request normalization failed."""
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Normalization failed: {reason}")


class OperatorServiceUnavailableError(OperatorRequestError):
    """A required subsystem is unavailable."""
    def __init__(self, reason: str, retryable: bool = False) -> None:
        self.reason = reason
        self.retryable = retryable
        super().__init__(f"Service unavailable: {reason}")


__all__ = [
    "OperatorRequestError",
    "InvalidOperatorRequestError",
    "OversizedRequestError",
    "MalformedRequestError",
    "ClassificationFailedError",
    "ReadinessCheckFailedError",
    "NormalizationFailedError",
    "OperatorServiceUnavailableError",
]
