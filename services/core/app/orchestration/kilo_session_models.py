"""
Kilo Session & Recovery — Domain Models.

Application-owned session tracking for Kilo Code CLI executions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class KiloSessionState(str, Enum):
    """Application-owned Kilo session states."""
    NEW = "NEW"
    ACTIVE = "ACTIVE"
    INTERRUPTED = "INTERRUPTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INVALIDATED = "INVALIDATED"


class KiloSessionRecord(BaseModel):
    """
    Application-owned Kilo session record.

    Kilo session ID is untrusted external metadata.
    All identity fields are application-owned.
    """
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    app_session_id: str
    task_id: str
    project_id: str
    workspace_path: str
    execution_id: str
    kilo_session_id: str | None = None
    kilo_session_state: str | None = None
    state: KiloSessionState = KiloSessionState.NEW
    recovery_eligible: bool = True
    pid: int | None = None
    process_started_at: datetime | None = None
    process_terminated_at: datetime | None = None
    authorization_version: str | None = None
    approval_scope_hash: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    error_message: str | None = None
    raw_event_snapshot: str | None = None


class KiloJsonEvent(BaseModel):
    """
    Minimal structured representation of a Kilo JSON event.

    JSON remains untrusted. Parsing is for diagnostics/session metadata only.
    Never grants authorization, approval, permission, verification, Git authority,
    or task completion authority.
    """
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    event_type: str
    timestamp: int | None = None
    session_id: str | None = None
    error_name: str | None = None
    error_data: dict[str, Any] | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class KiloSessionRecoveryEligibility(BaseModel):
    """
    Structured result of a recovery eligibility check.
    """
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    eligible: bool
    reason: str = ""
    checks: dict[str, bool] = Field(default_factory=dict)
