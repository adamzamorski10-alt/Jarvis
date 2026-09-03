"""Domain models for approval flow."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ApprovalVerificationToken:
    """
    Server-side-only authorization token.

    IMMUTABLE after creation.
    Cannot be created outside ApprovalService.
    Cannot be serialized to JSON.
    Cannot be modified after creation.
    Verification always goes through ApprovalRepository.
    """
    __slots__ = (
        "_approval_id",
        "_tool_id",
        "_action",
        "_tool_call_id",
        "_arguments_hash",
        "_request_id",
        "_tool_definition_version",
        "_tool_definition_fingerprint",
        "_expires_at",
    )

    def __init__(
        self,
        approval_id: str,
        tool_id: str,
        action: str,
        tool_call_id: str,
        arguments_hash: str,
        request_id: str | None,
        tool_definition_version: str | None,
        tool_definition_fingerprint: str | None,
        expires_at: datetime,
    ) -> None:
        object.__setattr__(self, "_approval_id", approval_id)
        object.__setattr__(self, "_tool_id", tool_id)
        object.__setattr__(self, "_action", action)
        object.__setattr__(self, "_tool_call_id", tool_call_id)
        object.__setattr__(self, "_arguments_hash", arguments_hash)
        object.__setattr__(self, "_request_id", request_id)
        object.__setattr__(self, "_tool_definition_version", tool_definition_version)
        object.__setattr__(self, "_tool_definition_fingerprint", tool_definition_fingerprint)
        object.__setattr__(self, "_expires_at", expires_at)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("ApprovalVerificationToken is immutable and cannot be modified.")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("ApprovalVerificationToken is immutable and cannot be modified.")

    @property
    def approval_id(self) -> str:
        return self._approval_id

    @property
    def tool_id(self) -> str:
        return self._tool_id

    @property
    def action(self) -> str:
        return self._action

    @property
    def tool_call_id(self) -> str:
        return self._tool_call_id

    @property
    def arguments_hash(self) -> str:
        return self._arguments_hash

    @property
    def request_id(self) -> str | None:
        return self._request_id

    @property
    def tool_definition_version(self) -> str | None:
        return self._tool_definition_version

    @property
    def tool_definition_fingerprint(self) -> str | None:
        return self._tool_definition_fingerprint

    @property
    def expires_at(self) -> datetime:
        return self._expires_at


def canonicalize_arguments(arguments: dict[str, Any]) -> str:
    """
    Canonical JSON representation for hashing.
    Sorted keys, no whitespace, ensure_ascii=False.
    """
    if not isinstance(arguments, dict):
        raise ValueError("Arguments must be a dict")
    return json.dumps(arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_arguments_hash(arguments: dict[str, Any]) -> str:
    """
    Compute SHA256 hash of canonical arguments JSON.
    """
    canonical = canonicalize_arguments(arguments)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_tool_definition_fingerprint(tool_definition: Any) -> str:
    """
    Compute stable fingerprint of tool definition fields that affect security/behavior.
    Covers all security-relevant properties: tool_id, action, version, risk_level,
    requires_confirmation, enabled, parameters_schema, result_schema.
    """
    payload = {
        "tool_id": tool_definition.tool_id,
        "action": tool_definition.action,
        "version": tool_definition.version,
        "risk_level": tool_definition.risk_level.value if hasattr(tool_definition.risk_level, 'value') else tool_definition.risk_level,
        "requires_confirmation": tool_definition.requires_confirmation,
        "enabled": tool_definition.enabled,
        "parameters_schema": tool_definition.parameters_schema,
        "result_schema": tool_definition.result_schema,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PendingApproval(BaseModel):
    model_config = {"strict": True}

    approval_id: str = Field(..., description="Unique approval request identifier")
    conversation_id: str = Field(..., description="Conversation correlation ID")
    request_id: str | None = Field(None, description="Original request correlation ID")
    tool_id: str = Field(..., description="Target tool identifier")
    action: str = Field(..., description="Specific action being requested")
    tool_call_id: str = Field(..., description="Original tool call identifier")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool arguments")
    arguments_hash: str = Field(..., description="SHA256 of canonical arguments JSON")
    tool_definition_version: str | None = Field(None, description="Tool version at creation time")
    tool_definition_fingerprint: str | None = Field(None, description="Hash of full tool definition at creation time")
    status: ApprovalStatus = Field(ApprovalStatus.PENDING, description="Current approval status")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(..., description="Expiration timestamp")
    executed_at: datetime | None = Field(None, description="Execution timestamp if approved/executed")
    decision_reason: str | None = Field(None, description="Reason for approval/rejection")

    @field_validator("approval_id", "conversation_id", "tool_id", "action", "tool_call_id")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        v_clean = v.strip()
        if not v_clean:
            raise ValueError("Field cannot be empty.")
        return v_clean

    @field_validator("expires_at")
    @classmethod
    def validate_expiration(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v

    def is_expired(self, at: datetime | None = None) -> bool:
        now = at or datetime.now(timezone.utc)
        if self.expires_at.tzinfo is None:
            return now >= self.expires_at.replace(tzinfo=timezone.utc)
        return now >= self.expires_at


class ApprovalApproveRequest(BaseModel):
    decision_reason: str | None = Field(None, description="Optional user-provided reason")


class ApprovalRejectRequest(BaseModel):
    decision_reason: str | None = Field(None, description="Optional user-provided reason")


class ApprovalResponse(BaseModel):
    approval_id: str
    status: ApprovalStatus
    tool_id: str
    action: str
    tool_call_id: str
    arguments: dict[str, Any]
    conversation_id: str
    request_id: str | None = None
    created_at: datetime
    expires_at: datetime
    executed_at: datetime | None = None
    decision_reason: str | None = None
    execution_result: dict[str, Any] | None = Field(None, description="Execution result if approved and executed")
