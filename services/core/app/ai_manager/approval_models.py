"""
AI Manager — Approval Domain Models.

Application-owned approval request/state models for AI Manager
policy/capability decisions.

The model cannot control approval state or grant approval.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ai_manager.models import RiskLevel


class ApprovalStatus(str, Enum):
    """
    Explicit approval states.

    State transitions:
      PENDING  -> APPROVED
      PENDING  -> DENIED
      PENDING  -> CANCELLED
      PENDING  -> EXPIRED
      APPROVED -> CONSUMED

    All other transitions are invalid.
    """
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    CONSUMED = "CONSUMED"


class CapabilityApprovalRequest(BaseModel):
    """
    Application-controlled parameters for creating an approval request.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    intent_id: str = Field(min_length=1, max_length=64)
    capabilities: list[str] = Field(min_length=1, max_length=20)
    effective_risk: RiskLevel = RiskLevel.LOW
    operation_type: str = Field(min_length=1, max_length=50)
    project_id: str | None = Field(default=None, max_length=64)
    request_reason: str | None = Field(default=None, max_length=1024)
    requested_by: str | None = Field(default=None, max_length=64)
    ttl_seconds: float | None = Field(default=None, ge=0, le=86400)


class CapabilityApproval(BaseModel):
    """
    Application-owned approval record.

    Status is application-controlled. The model cannot influence it.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: str = Field(min_length=1, max_length=64)
    intent_id: str = Field(min_length=1, max_length=64)
    operation_type: str = Field(min_length=1, max_length=50)
    scope_fingerprint: str = Field(min_length=1, max_length=64)
    effective_risk: RiskLevel = RiskLevel.LOW
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_by: str | None = Field(default=None, max_length=64)
    approved_by: str | None = Field(default=None, max_length=64)
    denied_by: str | None = Field(default=None, max_length=64)
    request_reason: str | None = Field(default=None, max_length=1024)
    decision_reason: str | None = Field(default=None, max_length=1024)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(...)
    consumed_at: datetime | None = Field(default=None)

    @field_validator("created_at", "expires_at", "consumed_at")
    @classmethod
    def _validate_utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @field_validator("expires_at")
    @classmethod
    def _validate_expiry(cls, v: datetime, info: Any) -> datetime:
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        created = info.data.get("created_at")
        if created is not None:
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if v <= created:
                raise ValueError("expires_at must be after created_at")
        return v

    def is_expired(self, at: datetime | None = None) -> bool:
        now = at or datetime.now(timezone.utc)
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return now >= expires

    def can_transition_to(self, new_status: ApprovalStatus) -> bool:
        valid = {
            ApprovalStatus.PENDING: [ApprovalStatus.APPROVED, ApprovalStatus.DENIED, ApprovalStatus.CANCELLED, ApprovalStatus.EXPIRED],
            ApprovalStatus.APPROVED: [ApprovalStatus.CONSUMED],
        }
        return new_status in valid.get(self.status, [])


def compute_scope_fingerprint(
    intent_id: str,
    capabilities: list[str],
    effective_risk: RiskLevel,
    operation_type: str,
    project_id: str | None = None,
) -> str:
    """
    Compute a deterministic fingerprint for the approval scope.
    Trusted application code only. Model cannot provide the final fingerprint.
    """
    payload = {
        "intent_id": intent_id,
        "capabilities": sorted(capabilities),
        "effective_risk": effective_risk.value,
        "operation_type": operation_type,
        "project_id": project_id,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
