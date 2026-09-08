"""
Verification Gate Foundation — Domain Models.

All models are immutable Pydantic dataclasses / frozen BaseModels.

IMPORTANT:
  - VerificationContext does NOT contain ToolExecutor, BaseTool,
    PermissionEngine internals, or approval mechanisms.
  - Verification is completely separate from execution authorization.
  - CheckResult is the per-check record of what happened (including timing).
  - VerificationReport is the final gate output — it does NOT authorize commits;
    downstream stages (6.6/6.8) consume it to decide on commits.

RESULT SEMANTICS:
  PASS     — check executed and explicitly passed
  FAIL     — check executed and explicitly failed
  ERROR    — check raised an exception or timed out
  SKIPPED  — check was registered but not executed (counts as gate FAIL)

GATE DECISION:
  PASS     — every *required* check returned PASS, none were SKIPPED/ERROR/FAIL
  FAIL     — any required check was not PASS (fail-closed)
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ── Status enumerations ─────────────────────────────────────────────────────

class CheckStatus(str, Enum):
    """Outcome of a single verification check execution."""

    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


class CheckCategory(str, Enum):
    """Category of a verification check for grouping and reporting."""

    UNIT_TESTS = "UNIT_TESTS"
    TYPE_CHECK = "TYPE_CHECK"
    BUILD = "BUILD"
    LINT = "LINT"
    SECURITY = "SECURITY"


class GateDecision(str, Enum):
    """
    Overall gate verdict for a complete verification run.

    PASS is fail-closed: it requires every required check to have returned PASS.
    FAIL is the default; the gate can only produce PASS through explicit success.
    """

    PASS = "PASS"
    FAIL = "FAIL"


# ── UTC helper ───────────────────────────────────────────────────────────────

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ── VerificationContext ──────────────────────────────────────────────────────

class VerificationContext(BaseModel):
    """
    Input context provided to every verification check.

    SECURITY: This model must NOT contain any of the following:
      - ToolExecutor or BaseTool instances
      - PermissionEngine internals
      - ApprovalService, grants, or approval context
      - Secrets or credentials

    The workspace_path MUST be the autonomous worktree path (never primary).
    The gate enforces this before executing any check.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=False)

    task_id: str
    """The DevelopmentTask identifier for which verification is being run."""

    workspace_path: str
    """
    Absolute path to the autonomous worktree being verified.
    MUST be the isolated worktree, never the primary worktree.
    """

    repository_root: str
    """Absolute path to the Git repository root (.git parent)."""

    expected_base_commit: str
    """
    The 40-char SHA this worktree was originally branched from.
    Used to verify workspace identity before running checks.
    """

    current_commit: str
    """
    The 40-char SHA of the HEAD in the autonomous worktree at verification time.
    Set by the gate after it validates the workspace; checks may read this.
    """

    changed_files: list[str] = Field(default_factory=list)
    """
    Optional list of file paths changed relative to expected_base_commit.
    Populated by the gate from git diff output; checks may use this for
    targeted analysis.
    """

    metadata: dict[str, Any] = Field(default_factory=dict)
    """
    Optional key-value metadata for checks that need task-level information.
    Must NOT contain secrets, credentials, or raw tool arguments.
    """


# ── CheckResult ──────────────────────────────────────────────────────────────

class CheckResult(BaseModel):
    """
    Structured result from a single verification check execution.

    This is an immutable audit record.  It contains enough information to
    reconstruct what happened, debug failures, and produce audit logs.

    SECURITY: stdout/stderr are captured from controlled subprocesses.
    The framework does not intentionally emit secrets here.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=False)

    check_id: str
    """Unique identifier for the check that produced this result."""

    check_name: str
    """Human-readable name of the check."""

    required: bool
    """Whether this check was required (FAIL/ERROR/SKIPPED → gate FAIL)."""

    status: CheckStatus
    """The outcome of this check execution."""

    started_at: datetime
    """UTC timestamp when the check began executing."""

    finished_at: datetime
    """UTC timestamp when the check finished (including errors/timeouts)."""

    duration_seconds: float
    """Wall-clock seconds between started_at and finished_at."""

    exit_code: int | None = None
    """Process exit code, if the check involved a subprocess; None otherwise."""

    stdout: str = ""
    """Captured stdout from subprocess execution, or empty string."""

    stderr: str = ""
    """Captured stderr from subprocess execution, or empty string."""

    error_message: str | None = None
    """
    If status is ERROR, the exception message or timeout description.
    None for PASS, FAIL, and SKIPPED.
    """

    details: dict[str, Any] = Field(default_factory=dict)
    """
    Optional structured metadata from the check for debugging/audit.
    Must NOT contain secrets.
    """

    category: "CheckCategory | None" = None
    """Category of the check, if applicable."""

    @property
    def passed(self) -> bool:
        """True only if status is PASS."""
        return self.status == CheckStatus.PASS

    @property
    def is_gate_failure(self) -> bool:
        """True if this result causes a required gate to fail."""
        return self.required and self.status != CheckStatus.PASS


# ── VerificationReport ───────────────────────────────────────────────────────

class VerificationReport(BaseModel):
    """
    Complete output of a VerificationGate execution run.

    This is the authoritative verdict that downstream stages (6.6/6.8)
    consume to decide whether a commit is authorized.

    INVARIANT:
      gate_decision == PASS  ⟺
        ∀ r ∈ results where r.required: r.status == PASS

    The gate itself does NOT create commits.  It only produces this report.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=False)

    task_id: str
    """The DevelopmentTask identifier this report covers."""

    gate_decision: GateDecision
    """
    Overall verdict. PASS only if every required check passed.
    Fail-closed: defaults to FAIL; only becomes PASS by explicit success.
    """

    started_at: datetime
    """UTC timestamp when the gate began execution."""

    finished_at: datetime
    """UTC timestamp when the gate completed (all checks run)."""

    duration_seconds: float
    """Total wall-clock seconds for the entire gate run."""

    results: tuple[CheckResult, ...]
    """
    Ordered tuple of individual check results.
    Order is deterministic: matches the registration order in CheckRegistry.
    All registered checks appear here — SKIPPED if not executed.
    """

    @property
    def passed(self) -> bool:
        """True only if gate_decision is PASS."""
        return self.gate_decision == GateDecision.PASS

    @property
    def required_results(self) -> tuple[CheckResult, ...]:
        """Subset of results for required checks."""
        return tuple(r for r in self.results if r.required)

    @property
    def optional_results(self) -> tuple[CheckResult, ...]:
        """Subset of results for optional checks."""
        return tuple(r for r in self.results if not r.required)

    @property
    def failed_required(self) -> tuple[CheckResult, ...]:
        """Required check results that are not PASS."""
        return tuple(r for r in self.required_results if not r.passed)

    @property
    def failed_optional(self) -> tuple[CheckResult, ...]:
        """Optional check results that are not PASS (visible but not fatal)."""
        return tuple(r for r in self.optional_results if not r.passed)
