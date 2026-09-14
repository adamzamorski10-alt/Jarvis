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
from app.ai_manager.providers.ollama import OllamaAIManagerAdapter
from app.ai_manager.providers.openrouter import OpenRouterAIManagerAdapter
from app.ai_manager.bridge import ManagerPlanningError, ManagerPlanningNotEligibleError, ManagerPlanningPolicyError, ManagerPlanningResult, ManagerPlanningService
from app.ai_manager.gate import PolicyCapabilityGate
from app.ai_manager.policy import ApplicationPolicy, AuthorizationDecision, PolicyDecision
from app.ai_manager.capabilities import CapabilityDefinition, CapabilityRegistry, create_default_registry
from app.ai_manager.prompt_builder import ManagerPromptBuilder
from app.ai_manager.output_parser import ManagerOutputParser

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
    "ManagerPlanningError",
    "ManagerPlanningNotEligibleError",
    "ManagerPlanningPolicyError",
    "ManagerPlanningResult",
    "ManagerPlanningService",
    "ManagerPromptBuilder",
    "ManagerResult",
    "ManagerOutputParser",
    "OpenRouterAIManagerAdapter",
    "OllamaAIManagerAdapter",
    "PolicyCapabilityGate",
    "ApplicationPolicy",
    "AuthorizationDecision",
    "PolicyDecision",
    "CapabilityDefinition",
    "CapabilityRegistry",
    "create_default_registry",
    "RawActionProposal",
    "RawManagerDecision",
    "RiskLevel",
    "UserIntent",
    "normalize_raw_decision",
    "AIManagerProvider",
]
