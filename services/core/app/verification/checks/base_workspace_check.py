"""
Base class for workspace-bound verification checks.

Provides shared helpers for resolving workspace-relative paths and
detecting missing tooling/paths with consistent FAIL semantics.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.verification.check_runner import BaseVerificationCheck, CheckExecutor
from app.verification.models import CheckCategory, CheckResult, CheckStatus, VerificationContext


class BaseWorkspaceCheck(BaseVerificationCheck):
    """
    Convenience base for checks that operate inside an autonomous worktree.

    Subclasses set ``check_id``, ``check_name``, ``required``, and
    ``category`` and implement ``_run_workspace_check``.
    """

    category: CheckCategory = CheckCategory.UNIT_TESTS

    def _run(
        self, context: VerificationContext
    ) -> tuple[CheckStatus, dict[str, Any]]:
        ws_path = Path(context.workspace_path)
        if not ws_path.is_dir():
            return CheckStatus.FAIL, {
                "reason": f"workspace_path is not a directory: {ws_path}",
            }
        return self._run_workspace_check(context, ws_path)

    def _run_workspace_check(
        self, context: VerificationContext, ws_path: Path
    ) -> tuple[CheckStatus, dict[str, Any]]:
        raise NotImplementedError

    def _executor(self, ws_path: Path, timeout: float = 60.0) -> CheckExecutor:
        return CheckExecutor(cwd=ws_path, default_timeout=timeout)
