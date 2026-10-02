"""
Operator Capability — Models.

Defines the contracts for capability resolution and execution.

SECURITY:
  - Capability resolution is application-owned.
  - The model may propose intent, but the application decides the capability.
  - No model output may directly execute tools or grant permissions.
"""
from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.operator_request.models import OperatorRequest


# ── Enumerations ────────────────────────────────────────────────────────────────


class CapabilityKind(str, enum.Enum):
    """Kinds of capabilities."""
    TOOL = "tool"
    SERVICE = "service"
    ANSWER_ONLY = "answer_only"
    UNSUPPORTED = "unsupported"


class CapabilityCategory(str, enum.Enum):
    """Categories of capabilities."""
    FILES = "files"
    SYSTEM = "system"
    MEMORY = "memory"
    TASK = "task"
    PLAN = "plan"
    SCHEDULE = "schedule"
    CHAT = "chat"
    INFORMATION = "information"


# ── Resolution Contracts ────────────────────────────────────────────────────────


class CapabilityResolution(BaseModel):
    """
    Result of resolving an OperatorRequest to a capability.

    This is application-owned. The model does not decide the capability.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(min_length=1, max_length=64)
    operation_type: str = Field(min_length=1, max_length=64)
    capability_kind: CapabilityKind
    capability_id: str | None = Field(default=None, max_length=128)
    capability_category: CapabilityCategory | None = None
    description: str | None = Field(default=None, max_length=512)
    parameters: dict[str, Any] = Field(default_factory=dict, max_length=50)
    constraints: dict[str, Any] = Field(default_factory=dict, max_length=50)
    requires_approval: bool = False
    requires_permission: bool = True
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reason: str | None = Field(default=None, max_length=1024)


class CapabilityExecutionResult(BaseModel):
    """
    Result of executing a capability.

    This is the structured result returned to the user and stored in history.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(min_length=1, max_length=64)
    operation_type: str = Field(min_length=1, max_length=64)
    capability_kind: CapabilityKind
    capability_id: str | None = Field(default=None, max_length=128)
    success: bool
    state: str = Field(min_length=1, max_length=64)
    user_message: str = Field(min_length=1, max_length=4096)
    result_metadata: dict[str, Any] = Field(default_factory=dict, max_length=50)
    error_category: str | None = Field(default=None, max_length=64)
    error_detail: str | None = Field(default=None, max_length=1024)
    # Tool-specific fields
    tool_output: Any = None
    tool_output_truncated: bool = False
    # Service-specific fields
    service_result: Any = None


class UnsupportedCapabilityError(Exception):
    """Raised when an operation type has no supported capability."""
    def __init__(self, operation_type: str, reason: str) -> None:
        self.operation_type = operation_type
        self.reason = reason
        super().__init__(f"Unsupported capability for {operation_type}: {reason}")


class CapabilityExecutionError(Exception):
    """Raised when capability execution fails."""
    def __init__(self, operation_type: str, reason: str) -> None:
        self.operation_type = operation_type
        self.reason = reason
        super().__init__(f"Capability execution failed for {operation_type}: {reason}")


__all__ = [
    "CapabilityKind",
    "CapabilityCategory",
    "CapabilityResolution",
    "CapabilityExecutionResult",
    "UnsupportedCapabilityError",
    "CapabilityExecutionError",
]
