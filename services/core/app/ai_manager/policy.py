"""
AI Manager — Policy and Authorization Models.

Application-owned policy and authorization decision models.

The model may propose fields like risk or approval_required, but the
application policy and capability registry are the sole authorities.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.ai_manager.capabilities import CapabilityIdentifier
from app.ai_manager.models import RiskLevel


class PolicyDecision(str, Enum):
    """
    Application policy authorization outcomes.

    DENY  → never authorized.
    ALLOW → authorized only if all other policy conditions pass.
    NEEDS_APPROVAL → not authorized yet; approval is required.
    DISABLED → denied.
    UNKNOWN → denied.
    """
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"


class AuthorizationDecision(BaseModel):
    """
    Structured application-owned authorization result.

    Clearly distinguishes MODEL PROPOSED from APPLICATION AUTHORIZED.
    A model saying approval_required=False must never make authorized=True
    by itself.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    authorized: bool
    outcome: PolicyDecision
    evaluated_capabilities: list[CapabilityIdentifier] = Field(default_factory=list, max_length=20)
    effective_risk: RiskLevel = RiskLevel.LOW
    denied_capabilities: list[CapabilityIdentifier] = Field(default_factory=list, max_length=20)
    approval_required_capabilities: list[CapabilityIdentifier] = Field(default_factory=list, max_length=20)
    reason: str | None = Field(default=None, max_length=2048)
    policy_snapshot_id: str | None = Field(default=None)


class ApplicationPolicy(BaseModel):
    """
    Application-owned policy model.

    The application defines:
      - maximum allowed risk level
      - allowed capabilities (whitelist, None means all known capabilities)
      - denied capabilities (blacklist)
      - capabilities that require approval
      - global enabled/disabled state
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_risk_level: RiskLevel = RiskLevel.MEDIUM
    allowed_capabilities: list[CapabilityIdentifier] | None = None
    denied_capabilities: list[CapabilityIdentifier] | None = None
    require_approval_for_capabilities: list[CapabilityIdentifier] | None = None
    global_enabled: bool = True
