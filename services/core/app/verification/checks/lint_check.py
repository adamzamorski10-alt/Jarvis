"""
Lint verification check.

Runs ruff check . in the workspace.
ruff missing or non-zero exit => FAIL.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.verification.checks.base_workspace_check import BaseWorkspaceCheck
from app.verification.exceptions import CheckExecutionError
from app.verification.models import CheckCategory, CheckStatus


class LintCheck(BaseWorkspaceCheck):
    check_id = "lint"
    check_name = "Lint"
    required = True
    category = CheckCategory.LINT

    def _run_workspace_check(
        self, context: VerificationContext, ws_path: Path
    ) -> tuple[CheckStatus, dict[str, Any]]:
        executor = self._executor(ws_path)
        try:
            result = executor.run_command(["ruff", "check", "."], timeout=120.0)
        except CheckExecutionError as exc:
            return CheckStatus.FAIL, {
                "reason": f"Lint tool unavailable: {type(exc).__name__}: {exc}",
            }
        except Exception as exc:
            return CheckStatus.ERROR, {
                "reason": f"Lint execution failed: {type(exc).__name__}: {exc}",
            }

        if result.timed_out:
            return CheckStatus.ERROR, {
                "reason": "Lint timed out after 120s",
                "timed_out": True,
            }

        if result.exit_code != 0:
            return CheckStatus.FAIL, {
                "reason": "Lint reported errors",
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

        return CheckStatus.PASS, {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
        }
