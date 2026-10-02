"""
Verification Gate Foundation — VerificationGate.

The VerificationGate is the central orchestrator of Stage 6.5.

It:
  1. Validates the VerificationContext (workspace identity, no primary worktree).
  2. Locks the CheckRegistry.
  3. Executes all registered checks in deterministic insertion order.
  4. Catches exceptions from individual checks → ERROR result (never PASS).
  5. Marks unexecuted checks as SKIPPED.
  6. Produces a VerificationReport with a fail-closed GateDecision.

CRITICAL INVARIANTS:
  - PASS is fail-closed: every required check must return PASS.
  - The gate does NOT create Git commits.
  - The gate does NOT invoke ToolExecutor or PermissionEngine.
  - The gate does NOT grant permissions or approvals.
  - The gate CANNOT be used to authorize execution of tools.
  - Exceptions inside checks become ERROR results, never PASS.
  - Timeouts inside checks become ERROR results, never PASS.
  - SKIPPED required checks → gate FAIL.
  - The primary worktree is absolutely forbidden as verification target.

GATE DECISION LOGIC:
  gate_decision = PASS
    if and only if:
      ∀ result ∈ results where result.required:
        result.status == PASS

  Otherwise gate_decision = FAIL.

OPTIONAL CHECKS:
  Optional FAIL/ERROR/SKIPPED is always visible in the report.
  It does NOT affect gate_decision.
  It is never silently ignored.
"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.verification.check_registry import CheckRegistry
from app.verification.check_runner import VerificationCheck
from app.verification.exceptions import (
    InvalidContextError,
    PrimaryWorktreeVerificationError,
    WorkspaceIdentityError,
)
from app.verification.models import (
    CheckResult,
    CheckStatus,
    GateDecision,
    VerificationContext,
    VerificationReport,
    _utc_now,
)

# ── Constants ────────────────────────────────────────────────────────────────

# Full 40-char hex SHA pattern for commit validation.
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")

# Maximum stdout/stderr captured per git identity check (safety truncation).
_GIT_OUTPUT_MAX = 4096

# Git identity check timeout (seconds) — short since these are local ops.
_GIT_IDENTITY_TIMEOUT = 15.0


# ── VerificationGate ─────────────────────────────────────────────────────────

class VerificationGate:
    """
    Fail-closed verification gate orchestrator.

    Each instance is bound to a CheckRegistry and a WorkspaceManager reference
    (by repository_root path).  Run with a VerificationContext.

    Usage::

        registry = CheckRegistry()
        registry.register(MyRequiredCheck())
        registry.register(MyOptionalCheck())

        gate = VerificationGate(registry=registry, primary_worktree_path=Path("/my/repo"))
        report = gate.run(context)

        if report.passed:
            # downstream stage 6.6/6.8 may authorize a commit
            ...

    Thread Safety:
        VerificationGate.run() is NOT safe to call concurrently on the same
        instance because it mutates the registry (lock).  Create a new
        VerificationGate instance per run.

    SCOPE LIMITATIONS (Stage 6.5):
        - Checks run sequentially.
        - No concurrency introduced here (deferred to later stages if needed).
        - No crash recovery / retry logic.
        - No network-dependent checks.
    """

    def __init__(
        self,
        registry: CheckRegistry,
        primary_worktree_path: Path,
        *,
        git_timeout: float = _GIT_IDENTITY_TIMEOUT,
    ) -> None:
        """
        Args:
            registry: The CheckRegistry containing all checks to run.
            primary_worktree_path: Absolute path of the user's primary worktree.
                                   Provided so the gate can enforce that
                                   context.workspace_path != primary worktree.
            git_timeout: Timeout for internal git identity-check subprocess calls.

        Raises:
            ValueError: if primary_worktree_path is not absolute.
        """
        if not primary_worktree_path.is_absolute():
            raise ValueError(
                f"primary_worktree_path must be absolute, got: {primary_worktree_path!r}"
            )
        self._registry = registry
        self._primary_worktree = primary_worktree_path.resolve()
        self._git_timeout = git_timeout

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self, context: VerificationContext) -> VerificationReport:
        """
        Execute all registered checks against the supplied context.

        Workflow:
          1. Validate context fields (fast, no I/O).
          2. Validate workspace identity (git I/O, fail-closed).
          3. Lock the registry (no new checks after this point).
          4. Execute each check in insertion order.
          5. Catch exceptions → ERROR result.
          6. Mark any unreached checks as SKIPPED.
          7. Compute GateDecision.
          8. Return VerificationReport.

        Args:
            context: Immutable verification context for this run.

        Returns:
            VerificationReport with gate_decision and all check results.
            NEVER raises an unhandled exception — gate failures are always
            expressed as a VerificationReport with gate_decision=FAIL.
        """
        gate_started = _utc_now()
        results: list[CheckResult] = []

        # Phase 1: Context validation (no Git I/O)
        context_error = self._validate_context(context)
        if context_error is not None:
            return self._fail_report(gate_started, context, context_error, results)

        # Phase 2: Workspace identity validation (Git I/O)
        identity_error = self._validate_workspace_identity(context)
        if identity_error is not None:
            return self._fail_report(gate_started, context, identity_error, results)

        # Canonicalize the changed-file set from Git. Agent/caller supplied paths
        # are metadata only and must never define what was actually modified.
        try:
            actual_changed_files = self._get_actual_changed_files(context)
        except Exception as exc:
            return self._fail_report(
                gate_started,
                context,
                f"Failed to determine authoritative changed files: {type(exc).__name__}: {exc}",
                results,
            )
        context = context.model_copy(update={"changed_files": actual_changed_files})

        # Phase 3: Lock registry and retrieve ordered checks
        self._registry.lock()
        checks = self._registry.get_ordered()

        if not checks:
            return self._fail_report(
                gate_started,
                context,
                "CheckRegistry is empty — at least one verification check is required",
                results,
            )

        # Phase 4: Execute checks sequentially
        executed_ids: set[str] = set()
        for check in checks:
            result = self._execute_one(check, context)
            results.append(result)
            executed_ids.add(check.check_id)

        # Phase 5: Any checks not executed → SKIPPED
        # (In sequential execution this should never happen, but the contract
        # requires we account for it defensively — e.g. if a future refactor
        # adds early termination of the check loop.)
        all_checks = self._registry.get_ordered()
        for check in all_checks:
            if check.check_id not in executed_ids:
                skipped = self._skipped_result(check)
                results.append(skipped)

        # Phase 6: Compute gate decision
        gate_decision = self._compute_gate_decision(results)

        gate_finished = _utc_now()
        duration = (gate_finished - gate_started).total_seconds()

        return VerificationReport(
            task_id=context.task_id,
            gate_decision=gate_decision,
            started_at=gate_started,
            finished_at=gate_finished,
            duration_seconds=duration,
            results=tuple(results),
        )

    # ── Context validation ────────────────────────────────────────────────────

    def _validate_context(self, context: VerificationContext) -> str | None:
        """
        Validate VerificationContext fields without performing I/O.

        Returns None if valid, or an error description string if invalid.
        The gate turns this into a FAIL report.
        """
        if not context.task_id or not context.task_id.strip():
            return "task_id must not be empty"

        ws_path_str = context.workspace_path
        if not ws_path_str:
            return "workspace_path must not be empty"

        ws_path = Path(ws_path_str)
        if not ws_path.is_absolute():
            return f"workspace_path must be absolute, got: {ws_path_str!r}"

        repo_root_str = context.repository_root
        if not repo_root_str:
            return "repository_root must not be empty"

        repo_root = Path(repo_root_str)
        if not repo_root.is_absolute():
            return f"repository_root must be absolute, got: {repo_root_str!r}"

        # Validate expected_base_commit format
        if not _SHA40_RE.match(context.expected_base_commit):
            return (
                f"expected_base_commit must be a 40-char hex SHA, "
                f"got: {context.expected_base_commit!r}"
            )

        # Validate current_commit format
        if not _SHA40_RE.match(context.current_commit):
            return (
                f"current_commit must be a 40-char hex SHA, "
                f"got: {context.current_commit!r}"
            )

        # Primary worktree guard (path-level, before I/O)
        try:
            resolved_ws = ws_path.resolve()
        except (OSError, ValueError):
            return f"workspace_path could not be resolved: {ws_path_str!r}"

        if resolved_ws == self._primary_worktree:
            return (
                f"workspace_path {ws_path_str!r} is the primary worktree — "
                "verification must target the autonomous worktree only"
            )

        try:
            resolved_ws.relative_to(self._primary_worktree)
            # If the above succeeds, ws is INSIDE the primary worktree
            return (
                f"workspace_path {ws_path_str!r} is inside the primary worktree "
                f"{str(self._primary_worktree)!r} — not allowed"
            )
        except ValueError:
            pass  # ws is not inside primary ✓

        return None  # valid

    # ── Workspace identity validation ─────────────────────────────────────────

    def _validate_workspace_identity(self, context: VerificationContext) -> str | None:
        """
        Validate workspace identity using real Git subprocess calls.

        Returns None if identity is valid, or an error description string.

        Checks:
          1. workspace_path exists and is a directory.
          2. It is a real Git worktree (git rev-parse --git-common-dir).
          3. Its git common dir matches the declared repository_root/.git.
          4. Its HEAD matches context.current_commit.
          5. expected_base_commit is an ancestor-or-equal of HEAD.
        """
        ws_path = Path(context.workspace_path)
        repo_root = Path(context.repository_root)

        # 1. Existence check
        if not ws_path.exists():
            return f"workspace_path does not exist: {str(ws_path)!r}"
        if not ws_path.is_dir():
            return f"workspace_path is not a directory: {str(ws_path)!r}"

        # 2. Verify it is a registered Git worktree by checking common-dir
        common_dir_result = self._git(ws_path, ["rev-parse", "--git-common-dir"])
        if common_dir_result is None:
            return (
                f"workspace_path {str(ws_path)!r} is not a Git worktree "
                "(git rev-parse --git-common-dir failed)"
            )

        # 3. Common dir must match repo_root/.git
        try:
            common_dir = Path(common_dir_result.strip()).resolve()
            expected_git_dir = (repo_root / ".git").resolve()
            if common_dir != expected_git_dir:
                return (
                    f"Workspace git common dir {str(common_dir)!r} does not match "
                    f"declared repository root git dir {str(expected_git_dir)!r}"
                )
        except (OSError, ValueError) as exc:
            return f"Failed to resolve git common dir: {exc}"

        # 4. HEAD in the worktree must match context.current_commit
        head_result = self._git(ws_path, ["rev-parse", "HEAD"])
        if head_result is None:
            return f"Failed to read HEAD in workspace {str(ws_path)!r}"

        actual_head = head_result.strip()
        if actual_head != context.current_commit:
            return (
                f"Workspace HEAD {actual_head!r} does not match "
                f"context.current_commit {context.current_commit!r}"
            )

        # 5. expected_base_commit must be an ancestor-or-equal of HEAD
        ancestry_result = self._git_returncode(
            ws_path,
            ["merge-base", "--is-ancestor", context.expected_base_commit, "HEAD"],
        )
        if ancestry_result != 0:
            return (
                f"expected_base_commit {context.expected_base_commit!r} is not "
                f"an ancestor of HEAD {actual_head!r} in the autonomous worktree"
            )

        return None  # identity valid

    def _git(self, cwd: Path, args: list[str]) -> str | None:
        """
        Run a git command in cwd, return stdout string or None on failure.
        Never raises; errors return None.
        This is ONLY used for workspace identity checking — not for check execution.
        """
        import os
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_PAGER"] = ""
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=self._git_timeout,
                shell=False,  # EXPLICIT: never shell=True
                env=env,
            )
            if proc.returncode != 0:
                return None
            return (proc.stdout or "")[:_GIT_OUTPUT_MAX]
        except Exception:
            return None

    def _git_returncode(self, cwd: Path, args: list[str]) -> int:
        """
        Run a git command, return returncode. Returns -1 on exception.
        Never raises.
        """
        import os
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_PAGER"] = ""
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=self._git_timeout,
                shell=False,  # EXPLICIT: never shell=True
                env=env,
            )
            return proc.returncode
        except Exception:
            return -1

    def _get_actual_changed_files(self, context: VerificationContext) -> list[str]:
        """Return tracked + untracked changes from Git, excluding the internal ownership marker."""
        ws_path = Path(context.workspace_path)

        tracked = self._git(ws_path, [
            "diff", "--name-only", f"{context.expected_base_commit}..HEAD"
        ])
        if tracked is None:
            raise RuntimeError("git diff could not determine tracked changes")

        untracked = self._git(ws_path, [
            "ls-files", "--others", "--exclude-standard"
        ])
        if untracked is None:
            raise RuntimeError("git ls-files could not determine untracked changes")

        files = {
            line.strip().replace("\\", "/")
            for line in (tracked.splitlines() + untracked.splitlines())
            if line.strip()
        }
        files.discard(".ai_operator_ownership")
        return sorted(files)

    # ── Check execution ───────────────────────────────────────────────────────

    def _execute_one(
        self, check: VerificationCheck, context: VerificationContext
    ) -> CheckResult:
        """
        Execute a single check, catching all exceptions → ERROR result.

        INVARIANT: This method NEVER raises an exception.
        All errors from check.execute() are converted to ERROR results.
        """
        started_at = _utc_now()
        try:
            result = check.execute(context)

            # Defensive: ensure the returned result is a CheckResult
            if not isinstance(result, CheckResult):
                finished_at = _utc_now()
                return CheckResult(
                    check_id=check.check_id,
                    check_name=check.check_name,
                    required=check.required,
                    status=CheckStatus.ERROR,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_seconds=(finished_at - started_at).total_seconds(),
                    error_message=(
                        f"Check {check.check_id!r} returned {type(result).__name__!r} "
                        "instead of CheckResult — treated as ERROR."
                    ),
                    category=getattr(check, 'category', None),
                )

            # Defensive: ensure check cannot manufacture a PASS with wrong check_id
            if result.check_id != check.check_id:
                finished_at = _utc_now()
                return CheckResult(
                    check_id=check.check_id,
                    check_name=check.check_name,
                    required=check.required,
                    status=CheckStatus.ERROR,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_seconds=(finished_at - started_at).total_seconds(),
                    error_message=(
                        f"Check {check.check_id!r} returned result with "
                        f"mismatched check_id {result.check_id!r} — treated as ERROR."
                    ),
                    category=getattr(check, 'category', None),
                )

            # Defensive: SKIPPED must not come from execute() — it is gate infra
            return result

        except Exception as exc:
            finished_at = _utc_now()
            return CheckResult(
                check_id=check.check_id,
                check_name=check.check_name,
                required=check.required,
                status=CheckStatus.ERROR,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=(finished_at - started_at).total_seconds(),
                error_message=f"{type(exc).__name__}: {exc}",
                category=getattr(check, 'category', None),
            )

    @staticmethod
    def _skipped_result(check: VerificationCheck) -> CheckResult:
        """Create a SKIPPED CheckResult for a check that was never executed."""
        now = _utc_now()
        return CheckResult(
            check_id=check.check_id,
            check_name=check.check_name,
            required=check.required,
            status=CheckStatus.SKIPPED,
            started_at=now,
            finished_at=now,
            duration_seconds=0.0,
            error_message="Check was not executed (SKIPPED by gate infrastructure).",
            category=getattr(check, 'category', None),
        )

    # ── Gate decision ─────────────────────────────────────────────────────────

    @staticmethod
    def _compute_gate_decision(results: list[CheckResult]) -> GateDecision:
        """
        Compute the overall GateDecision from all check results.

        PASS only if every required check returned PASS.
        Any required FAIL, ERROR, or SKIPPED → FAIL.
        Optional FAIL/ERROR/SKIPPED is visible but does not affect the decision.
        An empty results list produces FAIL (fail-closed).
        """
        if not results:
            return GateDecision.FAIL

        for result in results:
            if result.required and result.status != CheckStatus.PASS:
                return GateDecision.FAIL
        return GateDecision.PASS

    # ── Fail-report helper ────────────────────────────────────────────────────

    @staticmethod
    def _fail_report(
        gate_started: datetime,
        context: VerificationContext,
        error_message: str,
        partial_results: list[CheckResult],
    ) -> VerificationReport:
        """
        Produce a FAIL report when the gate aborts before executing checks.

        Used for context-validation failures and workspace-identity failures.
        The error is recorded as a synthetic ERROR result named 'gate.preflight'.
        """
        now = _utc_now()
        preflight_result = CheckResult(
            check_id="gate.preflight",
            check_name="Gate Preflight Validation",
            required=True,
            status=CheckStatus.ERROR,
            started_at=gate_started,
            finished_at=now,
            duration_seconds=(now - gate_started).total_seconds(),
            error_message=error_message,
            category=None,
        )
        all_results = [preflight_result, *partial_results]
        duration = (now - gate_started).total_seconds()
        return VerificationReport(
            task_id=context.task_id,
            gate_decision=GateDecision.FAIL,
            started_at=gate_started,
            finished_at=now,
            duration_seconds=duration,
            results=tuple(all_results),
        )
