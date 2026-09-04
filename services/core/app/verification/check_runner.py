"""
Verification Gate Foundation — Check Contract & Restricted Command Executor.

This module defines:

1. VerificationCheck (Protocol)
   The interface every verification check must implement.
   Checks receive a VerificationContext and return a CheckResult.
   Checks must NOT receive ToolExecutor, PermissionEngine, or approval data.

2. CheckCommandResult
   Structured output from a controlled subprocess call.

3. CheckExecutor
   Restricted subprocess runner for verification commands.

   DESIGN INVARIANTS (mirroring GitRunner):
     - Never uses shell=True.
     - Commands are argv lists only (no shell strings).
     - Explicit timeout on every invocation.
     - Working directory validated as a real, absolute, non-primary path.
     - stdout/stderr captured; structured exit code preserved.
     - Timeout → timed_out=True (caller maps to CheckStatus.ERROR).
     - No generic "run arbitrary command" API.
     - No command injection via task_id, workspace_path, or context values.

   SCOPE: CheckExecutor is a building block for concrete checks.
   The VerificationGate itself does NOT use CheckExecutor directly.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.verification.exceptions import CheckExecutionError
from app.verification.models import (
    CheckResult,
    CheckStatus,
    VerificationContext,
)


# ── VerificationCheck Protocol ───────────────────────────────────────────────

@runtime_checkable
class VerificationCheck(Protocol):
    """
    Interface for a single verification check.

    Every check must:
      - Have a unique, immutable check_id.
      - Have a human-readable check_name.
      - Declare whether it is required.
      - Implement execute() which accepts a VerificationContext and returns
        a CheckResult with a definite status (PASS, FAIL, or ERROR).
      - Never receive or interact with ToolExecutor, PermissionEngine,
        ApprovalService, or any grant/approval data.
      - NOT return SKIPPED from execute() — SKIPPED is set by the gate
        infrastructure when a check is not reached.

    Exception handling:
      - If execute() raises an exception, the gate catches it and creates
        a CheckResult with status=ERROR.  The check's exception becomes the
        error_message.
      - Checks should NOT swallow exceptions internally to manufacture PASS
        results.
    """

    check_id: str
    check_name: str
    required: bool

    def execute(self, context: VerificationContext) -> CheckResult:
        """
        Execute the check against the given verification context.

        Args:
            context: The immutable verification context for this run.
                     Contains workspace_path, repository_root, task_id, etc.
                     Does NOT contain execution authority or credentials.

        Returns:
            CheckResult with status PASS, FAIL, or ERROR.
            Must never return status=SKIPPED (that is gate infrastructure).

        Raises:
            Any exception — the gate will catch this and create ERROR result.
        """
        ...


# ── CheckCommandResult ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class CheckCommandResult:
    """
    Structured result from a CheckExecutor.run_command() call.

    Attributes:
        exit_code: Process exit code (0 = success). -1 if timed out.
        stdout: Captured stdout text.
        stderr: Captured stderr text.
        timed_out: True if the command exceeded its timeout.
        argv: The exact argv list that was executed (for audit).
    """

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    argv: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        """True if exit_code is 0 and the command did not time out."""
        return self.exit_code == 0 and not self.timed_out


# ── CheckExecutor ────────────────────────────────────────────────────────────

# Explicit default timeout (seconds) for all verification subprocess calls.
_DEFAULT_TIMEOUT: float = 60.0

# Hard upper-bound on timeout to prevent unbounded blocking.
_MAX_TIMEOUT: float = 300.0

# Explicitly permitted executable names for verification commands.
# This is NOT a shell — only recognised executables are allowed.
# Future checks that need new executables must extend this set explicitly.
_ALLOWED_EXECUTABLES: frozenset[str] = frozenset({
    "python",
    "python3",
    "pytest",
    "mypy",
    "ruff",
    "black",
    "isort",
    "npm",
    "node",
    "npx",
    "tsc",
    "git",
    "cargo",
    "go",
    "make",
})


class CheckExecutor:
    """
    Restricted subprocess runner for verification commands.

    Usage:
        executor = CheckExecutor(cwd=Path("/path/to/autonomous/worktree"))
        result = executor.run_command(["pytest", "tests/"], timeout=60.0)

    Only executables listed in _ALLOWED_EXECUTABLES are permitted.
    This prevents accidental exposure of an arbitrary shell executor.

    The cwd must be:
      - An absolute path
      - Not the primary worktree (caller must enforce)
      - A real, existing directory

    This executor does NOT enforce worktree registration — callers (i.e. the
    VerificationGate) must verify workspace identity before invoking checks.
    """

    def __init__(
        self,
        cwd: Path,
        *,
        default_timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        """
        Args:
            cwd: Absolute path to the working directory for commands.
                 Must be absolute and must exist.
            default_timeout: Default timeout in seconds. Capped at _MAX_TIMEOUT.

        Raises:
            CheckExecutionError: if cwd is not absolute or does not exist.
        """
        if not cwd.is_absolute():
            raise CheckExecutionError(
                f"CheckExecutor cwd must be absolute, got: {cwd}"
            )
        if not cwd.exists():
            raise CheckExecutionError(
                f"CheckExecutor cwd does not exist: {cwd}"
            )
        self._cwd = cwd
        self._default_timeout = min(max(default_timeout, 1.0), _MAX_TIMEOUT)

    @property
    def cwd(self) -> Path:
        return self._cwd

    def run_command(
        self,
        argv: list[str],
        *,
        timeout: float | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> CheckCommandResult:
        """
        Execute a verification command as a subprocess.

        SAFETY CONTRACT:
          - shell=False is enforced (explicit, never overridable).
          - argv must be a list of strings; no shell metacharacter expansion.
          - The first element of argv must be in _ALLOWED_EXECUTABLES.
          - Working directory is self._cwd (validated at construction time).
          - Timeout is enforced; expiry returns timed_out=True, not PASS.
          - stdout and stderr are fully captured.

        Args:
            argv: Command argument vector.  Must not be empty.
                  argv[0] must be in _ALLOWED_EXECUTABLES.
            timeout: Per-call timeout override in seconds. Capped at _MAX_TIMEOUT.
            extra_env: Additional environment variables merged into os.environ.
                       Existing variables are not removed.

        Returns:
            CheckCommandResult with exit_code, stdout, stderr, timed_out, argv.

        Raises:
            CheckExecutionError: if argv is invalid or executable is not allowed.
        """
        if not argv:
            raise CheckExecutionError("argv must be non-empty")

        if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
            raise CheckExecutionError(
                "argv must be a list of strings — no shell injection allowed"
            )

        executable = argv[0]
        if executable not in _ALLOWED_EXECUTABLES:
            raise CheckExecutionError(
                f"Executable {executable!r} is not in the allowed list. "
                f"Allowed: {sorted(_ALLOWED_EXECUTABLES)}"
            )

        effective_timeout = min(
            timeout if timeout is not None else self._default_timeout,
            _MAX_TIMEOUT,
        )
        if effective_timeout <= 0:
            raise CheckExecutionError(
                f"timeout must be positive, got: {effective_timeout}"
            )

        import os
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)

        try:
            proc = subprocess.run(
                argv,
                cwd=str(self._cwd),
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                shell=False,  # EXPLICIT: never shell=True
                env=env,
            )
            return CheckCommandResult(
                exit_code=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                timed_out=False,
                argv=list(argv),
            )

        except subprocess.TimeoutExpired:
            return CheckCommandResult(
                exit_code=-1,
                stdout="",
                stderr=f"Command timed out after {effective_timeout}s",
                timed_out=True,
                argv=list(argv),
            )

        except FileNotFoundError as exc:
            raise CheckExecutionError(
                f"Executable not found: {argv[0]!r} — {exc}"
            ) from exc


# ── BaseVerificationCheck ────────────────────────────────────────────────────

class BaseVerificationCheck:
    """
    Convenience base class for concrete verification checks.

    Subclass this and implement _run() to create a new check.
    The execute() method handles timing, exception wrapping, and result
    construction automatically.

    Example::

        class MyCheck(BaseVerificationCheck):
            check_id = "my_check"
            check_name = "My Custom Check"
            required = True

            def _run(self, context: VerificationContext) -> tuple[CheckStatus, dict]:
                # Return (status, details_dict)
                return CheckStatus.PASS, {}
    """

    check_id: str
    check_name: str
    required: bool

    def _run(
        self, context: VerificationContext
    ) -> tuple[CheckStatus, dict[str, Any]]:
        """
        Implement the check logic here.

        Returns:
            (status, details) — status must be PASS, FAIL, or ERROR.
            SKIPPED must never be returned from _run().

        Raises:
            Any exception — execute() will catch and produce ERROR result.
        """
        raise NotImplementedError(
            f"{type(self).__name__}._run() must be implemented"
        )

    def execute(self, context: VerificationContext) -> CheckResult:
        """
        Execute the check, wrapping _run() with timing and error handling.

        Exceptions from _run() are caught and become CheckResult(status=ERROR).
        This ensures the gate always receives a structured result, never a raw
        exception propagating through gate orchestration.
        """
        started_at = datetime.now(timezone.utc)
        error_message: str | None = None
        status = CheckStatus.ERROR
        details: dict[str, Any] = {}

        try:
            status, details = self._run(context)
        except Exception as exc:
            status = CheckStatus.ERROR
            error_message = f"{type(exc).__name__}: {exc}"

        finished_at = datetime.now(timezone.utc)
        duration = (finished_at - started_at).total_seconds()

        return CheckResult(
            check_id=self.check_id,
            check_name=self.check_name,
            required=self.required,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration,
            exit_code=None,
            stdout="",
            stderr="",
            error_message=error_message,
            details=details,
        )
