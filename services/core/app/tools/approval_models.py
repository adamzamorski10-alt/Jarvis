"""Domain models for approval flow."""
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    EXECUTED = "EXECUTED"


class PendingApproval(BaseModel):
    model_config = {"strict": True}

    approval_id: str = Field(..., description="Unique approval request identifier")
    conversation_id: str = Field(..., description="Conversation correlation ID")
    request_id: str | None = Field(None, description="Original request correlation ID")
    tool_id: str = Field(..., description="Target tool identifier")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool arguments")
    tool_call_id: str = Field(..., description="Original tool call identifier")
    status: ApprovalStatus = Field(ApprovalStatus.PENDING, description="Current approval status")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(..., description="Expiration timestamp")
    executed_at: datetime | None = Field(None, description="Execution timestamp if approved/executed")
    decision_reason: str | None = Field(None, description="Reason for approval/rejection")

    @field_validator("approval_id", "conversation_id", "tool_id", "tool_call_id")
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
    approval_id: str = Field(..., min_length=1, description="Approval request ID to approve")
    decision_reason: str | None = Field(None, description="Optional user-provided reason")


class ApprovalRejectRequest(BaseModel):
    approval_id: str = Field(..., min_length=1, description="Approval request ID to reject")
    decision_reason: str | None = Field(None, description="Optional user-provided reason")


class ApprovalResponse(BaseModel):
    approval_id: str
    status: ApprovalStatus
    tool_id: str
    arguments: dict[str, Any]
    tool_call_id: str
    conversation_id: str
    created_at: datetime
    expires_at: datetime
    decision_reason: str | None = None
    execution_result: dict[str, Any] | None = Field(None, description="Execution result if approved and executed")
