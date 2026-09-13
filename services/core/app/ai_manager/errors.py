"""
AI Manager — Error Hierarchy.

All AI Manager errors are application-defined.  Model output is untrusted;
only these exceptions communicate application-level failures.
"""
from __future__ import annotations


class AIManagerError(Exception):
    """Base exception for all AI Manager errors."""


class AIManagerProviderError(AIManagerError):
    """Base for provider communication failures."""
    def __init__(self, reason: str, retryable: bool = False) -> None:
        self.reason = reason
        self.retryable = retryable
        super().__init__(reason)


class AIManagerTimeoutError(AIManagerProviderError):
    def __init__(self, reason: str = "Manager provider request timed out") -> None:
        super().__init__(reason, retryable=True)


class AIManagerRateLimitedError(AIManagerProviderError):
    def __init__(self, reason: str = "Manager provider rate limit exceeded") -> None:
        super().__init__(reason, retryable=True)


class AIManagerContextLimitError(AIManagerProviderError):
    def __init__(self, reason: str = "Manager provider context limit exceeded") -> None:
        super().__init__(reason, retryable=False)


class AIManagerEmptyResponseError(AIManagerProviderError):
    def __init__(self, reason: str = "Manager provider returned empty response") -> None:
        super().__init__(reason, retryable=True)


class AIManagerUnavailableError(AIManagerProviderError):
    def __init__(self, reason: str = "Manager provider is unavailable") -> None:
        super().__init__(reason, retryable=True)


class AIManagerMalformedDecisionError(AIManagerError):
    """Model output failed schema or structural validation."""
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Malformed manager decision: {reason}")


class AIManagerInvalidDecisionError(AIManagerError):
    """Decision structure is invalid (semantic validation failure)."""
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Invalid manager decision: {reason}")


class AIManagerPolicyRejectionError(AIManagerError):
    """Application policy rejected the proposed action."""
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Policy rejection: {reason}")


class AIManagerClarificationRequiredError(AIManagerError):
    """Decision requires additional user input before proceeding."""
    def __init__(self, reason: str, questions: list[str] | None = None) -> None:
        self.reason = reason
        self.questions = questions or []
        super().__init__(f"Clarification required: {reason}")
