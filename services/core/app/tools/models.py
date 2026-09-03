"""
Domain Models and Schemas for Tool Registry, Permission Engine,
Consent System, Approval Requests, and Tool Execution.
"""
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.tools.approval_models import ApprovalStatus


# --- CONSTANTS & PATTERNS ---

TOOL_ID_REGEX = re.compile(r"^[a-z0-9_-]+(\.[a-z0-9_-]+)+$")
SEMVER_REGEX = re.compile(r"^\d+\.\d+\.\d+$")


# --- ENUMS ---

class ToolCategory(str, Enum):
    FILES = "files"
    SYSTEM = "system"
    BROWSER = "browser"
    NETWORK = "network"
    AUTOMATION = "automation"


class RiskLevel(str, Enum):
    SAFE = "SAFE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AutonomyMode(str, Enum):
    MANUAL = "manual"
    SEMI_AUTONOMOUS = "semi_autonomous"
    AUTONOMOUS = "autonomous"


class PermissionDecisionType(str, Enum):
    ALLOW = "ALLOW"
    ASK_USER = "ASK_USER"
    DENY = "DENY"


class GrantType(str, Enum):
    ONE_TIME = "one_time"
    TIME_BOUND = "time_bound"
    PERSISTENT = "persistent"


class ToolExecutionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"
    DENIED = "DENIED"
    CANCELLED = "CANCELLED"


# --- DOMAIN MODELS ---

class ToolDefinition(BaseModel):
    """
    Immutable tool definition specification.
    Once created, tool definitions cannot be mutated.
    """
    model_config = ConfigDict(frozen=True)

    tool_id: str = Field(..., description="Unique tool identifier, e.g. 'files.read_file'")
    action: str = Field(..., min_length=1, description="Canonical authorization action for this tool, e.g. 'read'")
    name: str = Field(..., min_length=1, description="Human-readable tool name")
    description: str = Field(..., min_length=1, description="Tool purpose and behavior description")
    version: str = Field("1.0.0", description="Semantic version string, e.g. '1.0.0'")
    category: ToolCategory = Field(..., description="Functional category")
    risk_level: RiskLevel = Field(..., description="Security risk level classification")
    requires_confirmation: bool = Field(False, description="Forces user confirmation regardless of autonomy mode")
    enabled: bool = Field(True, description="Master enable/disable switch")
    parameters_schema: dict[str, Any] = Field(default_factory=dict, description="JSON Schema for input arguments")
    result_schema: dict[str, Any] = Field(default_factory=dict, description="JSON Schema for output result")

    @field_validator("tool_id")
    @classmethod
    def validate_tool_id(cls, v: str) -> str:
        v_clean = v.strip()
        if not TOOL_ID_REGEX.match(v_clean):
            raise ValueError(
                f"Invalid tool_id format '{v}'. Must be lowercase alphanumeric with dots/underscores/hyphens, e.g. 'files.read_file'."
            )
        return v_clean

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if not isinstance(v, str) or not v or not v.strip():
            raise ValueError("Action cannot be empty or whitespace only.")
        return v.strip()

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: str) -> str:
        v_clean = v.strip()
        if not SEMVER_REGEX.match(v_clean):
            raise ValueError(f"Invalid semver version format '{v}'. Expected 'X.Y.Z', e.g. '1.0.0'.")
        return v_clean

    @field_validator("name", "description")
    @classmethod
    def validate_non_empty_string(cls, v: str) -> str:
        v_clean = v.strip()
        if not v_clean:
            raise ValueError("Field cannot be empty or whitespace only.")
        return v_clean


class ToolCall(BaseModel):
    """
    Request payload representing an AI model tool invocation.
    """
    tool_id: str = Field(..., description="Target tool_id to invoke")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Key-value arguments for tool execution")
    request_id: str | None = Field(None, description="Optional request correlation ID")
    tool_call_id: str = Field(default_factory=lambda: uuid4().hex, description="Unique tool call identifier")

    @field_validator("tool_id")
    @classmethod
    def validate_tool_id(cls, v: str) -> str:
        v_clean = v.strip()
        if not TOOL_ID_REGEX.match(v_clean):
            raise ValueError(f"Invalid tool_id format '{v}'. Expected e.g. 'files.read_file'.")
        return v_clean


class ToolExecutionResult(BaseModel):
    """
    Outcome of a tool execution attempt.
    """
    success: bool = Field(..., description="True if tool executed successfully")
    tool_id: str = Field(..., description="Target tool_id")
    request_id: str | None = Field(None, description="Request correlation ID")
    tool_call_id: str | None = Field(None, description="Unique tool call identifier")
    status: ToolExecutionStatus = Field(..., description="Detailed execution status")
    result: Any = Field(None, description="Result payload if successful")
    error_code: str | None = Field(None, description="Categorized error code if failed")
    error_message: str | None = Field(None, description="Detailed error description if failed")
    execution_time_ms: float | None = Field(None, ge=0.0, description="Execution duration in milliseconds")

    @field_validator("tool_id")
    @classmethod
    def validate_tool_id(cls, v: str) -> str:
        v_clean = v.strip()
        if not TOOL_ID_REGEX.match(v_clean):
            raise ValueError(f"Invalid tool_id format '{v}'. Expected e.g. 'files.read_file'.")
        return v_clean


class PermissionDecision(BaseModel):
    """
    Result of evaluating a permission request through the Permission Engine.
    """
    decision: PermissionDecisionType = Field(..., description="ALLOW, ASK_USER, or DENY")
    reason: str = Field(..., description="Explanation for the decision")
    rule_id: str = Field("default", description="Identifier of the rule or policy evaluated")
    grant_id: str | None = Field(None, description="ID of matching consent grant if applicable")

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        v_clean = v.strip()
        if not v_clean:
            raise ValueError("Reason cannot be empty.")
        return v_clean


class PermissionEvaluationRequest(BaseModel):
    """
    Input model sent to PermissionEngine.evaluate().
    """
    tool_id: str = Field(..., description="Target tool_id")
    risk_level: RiskLevel = Field(..., description="Risk level of the tool")
    action: str = Field(..., min_length=1, description="Specific action being requested, e.g. 'read'")
    autonomy_mode: AutonomyMode = Field(AutonomyMode.MANUAL, description="Current system autonomy mode")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Sanitized argument summary")
    context: dict[str, Any] = Field(default_factory=dict, description="Additional evaluation context")
    approval_context: Any = Field(None, description="Server-side approval verification token if executing approved request")

    @field_validator("tool_id")
    @classmethod
    def validate_tool_id(cls, v: str) -> str:
        v_clean = v.strip()
        if not TOOL_ID_REGEX.match(v_clean):
            raise ValueError(f"Invalid tool_id format '{v}'. Expected e.g. 'files.read_file'.")
        return v_clean

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        v_clean = v.strip()
        if not v_clean:
            raise ValueError("Action cannot be empty or whitespace only.")
        return v_clean


class PermissionGrant(BaseModel):
    """
    Represents a granted user consent (one-time, time-bound, or persistent).
    """
    id: str | None = Field(None, description="Unique grant ID")
    tool_id: str = Field(..., min_length=1, description="Target tool_id or pattern, e.g. 'files.*'")
    action_pattern: str = Field("*", description="Action scope pattern, default '*'")
    grant_type: GrantType = Field(..., description="Grant duration policy")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when grant was created",
    )
    expires_at: datetime | None = Field(None, description="Expiration timestamp for time-bound grants")

    @field_validator("tool_id", "action_pattern")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        v_clean = v.strip()
        if not v_clean:
            raise ValueError("Field cannot be empty or whitespace only.")
        return v_clean

    @model_validator(mode="after")
    def validate_grant_expiration(self) -> "PermissionGrant":
        if self.grant_type == GrantType.TIME_BOUND:
            if self.expires_at is None:
                raise ValueError("Time-bound grants must specify an 'expires_at' datetime.")
            if self.expires_at <= self.created_at:
                raise ValueError("'expires_at' must be after 'created_at'.")
        elif self.grant_type == GrantType.ONE_TIME:
            # One-time grants do not require an expiration date
            pass
        return self

    def is_expired(self, at: datetime | None = None) -> bool:
        """Returns True if the grant is time-bound and has expired."""
        if self.grant_type != GrantType.TIME_BOUND or self.expires_at is None:
            return False
        now = at or datetime.now(timezone.utc)
        return now >= self.expires_at


class ApprovalRequest(BaseModel):
    """
    Represents a pending or resolved user confirmation request in the UI.
    """
    approval_id: str = Field(..., min_length=1, description="Unique approval request ID")
    tool_id: str = Field(..., description="Target tool_id")
    action: str = Field(..., min_length=1, description="Action requested")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Arguments summary for user display")
    risk_level: RiskLevel = Field(..., description="Risk level requiring approval")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when approval request was issued",
    )
    expires_at: datetime = Field(..., description="Expiration timestamp for pending approval")
    status: ApprovalStatus = Field(ApprovalStatus.PENDING, description="Current approval status")

    @field_validator("approval_id", "action")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        v_clean = v.strip()
        if not v_clean:
            raise ValueError("Field cannot be empty or whitespace only.")
        return v_clean

    @field_validator("tool_id")
    @classmethod
    def validate_tool_id(cls, v: str) -> str:
        v_clean = v.strip()
        if not TOOL_ID_REGEX.match(v_clean):
            raise ValueError(f"Invalid tool_id format '{v}'. Expected e.g. 'files.read_file'.")
        return v_clean

    @model_validator(mode="after")
    def validate_expiration(self) -> "ApprovalRequest":
        if self.expires_at <= self.created_at:
            raise ValueError("'expires_at' must be strictly after 'created_at'.")
        return self

    def is_expired(self, at: datetime | None = None) -> bool:
        """Returns True if the approval request has passed its expiration time."""
        now = at or datetime.now(timezone.utc)
        return now >= self.expires_at
