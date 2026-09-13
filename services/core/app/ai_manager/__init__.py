"""
AI Manager — Provider-Neutral Decision Contracts.

MODEL PROPOSES.
APPLICATION DECIDES.
AUTHORIZED APPLICATION SERVICES EXECUTE.
"""
from app.ai_manager.ai_manager import AIManager
from app.ai_manager.errors import (
    AIManagerClarificationRequiredError,
    AIManagerContextLimitError,
    AIManagerEmptyResponseError,
    AIManagerError,
    AIManagerInvalidDecisionError,
    AIManagerMalformedDecisionError,
    AIManagerPolicyRejectionError,
    AIManagerProviderError,
    AIManagerRateLimitedError,
    AIManagerTimeoutError,
    AIManagerUnavailableError,
)
from app.ai_manager.models import (
    ActionProposal,
    ApprovalRequirement,
    CapabilityIdentifier,
    DecisionOutcome,
    ManagerDecision,
    ManagerResult,
    RawActionProposal,
    RawManagerDecision,
    RiskLevel,
    UserIntent,
)
from app.ai_manager.normalizer import normalize_raw_decision
from app.ai_manager.providers.base import AIManagerProvider

__all__ = [
    "AIManager",
    "AIManagerClarificationRequiredError",
    "AIManagerContextLimitError",
    "AIManagerEmptyResponseError",
    "AIManagerError",
    "AIManagerInvalidDecisionError",
    "AIManagerMalformedDecisionError",
    "AIManagerPolicyRejectionError",
    "AIManagerProviderError",
    "AIManagerRateLimitedError",
    "AIManagerTimeoutError",
    "AIManagerUnavailableError",
    "ActionProposal",
    "ApprovalRequirement",
    "CapabilityIdentifier",
    "DecisionOutcome",
    "ManagerDecision",
    "ManagerResult",
    "RawActionProposal",
    "RawManagerDecision",
    "RiskLevel",
    "UserIntent",
    "normalize_raw_decision",
    "AIManagerProvider",
]
