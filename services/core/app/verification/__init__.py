"""Verification Gate package for AI Operator.

Provides deterministic verification primitives, result models, check execution framework,
and gate orchestration to decide PASS/FAIL before code is allowed to proceed to commit.
"""

from app.verification.check_registry import CheckRegistry
from app.verification.check_runner import (
    BaseVerificationCheck,
    CheckCommandResult,
    CheckExecutor,
    VerificationCheck,
)
from app.verification.exceptions import (
    CheckExecutionError,
    CheckNotFoundError,
    DuplicateCheckError,
    InvalidContextError,
    PrimaryWorktreeVerificationError,
    RegistryLockedError,
    VerificationError,
    WorkspaceIdentityError,
)
from app.verification.gate import VerificationGate
from app.verification.models import (
    CheckCategory,
    CheckResult,
    CheckStatus,
    GateDecision,
    VerificationContext,
    VerificationReport,
)

from app.verification.reporting import (
    build_verification_report,
    render_json,
    render_markdown,
)

__all__ = [
    # Enums & Decision models
    "CheckCategory",
    "CheckStatus",
    "GateDecision",
    "VerificationContext",
    "CheckResult",
    "VerificationReport",
    # Exceptions
    "VerificationError",
    "DuplicateCheckError",
    "CheckNotFoundError",
    "RegistryLockedError",
    "InvalidContextError",
    "PrimaryWorktreeVerificationError",
    "WorkspaceIdentityError",
    "CheckExecutionError",
    # Core Components
    "VerificationCheck",
    "CheckCommandResult",
    "CheckExecutor",
    "BaseVerificationCheck",
    "CheckRegistry",
    "VerificationGate",
    # Checks
    "BaseWorkspaceCheck",
    "CheckSuite",
    "PytestCheck",
    "TypeScriptCheck",
    "FrontendBuildCheck",
    "LintCheck",
    # Reporting
    "build_verification_report",
    "render_json",
    "render_markdown",
]
