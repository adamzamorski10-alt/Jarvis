"""
Pytest verification check.

Runs pytest in the workspace. Required-check missing tooling/path => FAIL.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.verification.checks.base_workspace_check import BaseWorkspaceCheck
from app.verification.exceptions import CheckExecutionError
from app.verification.models import CheckCategory, CheckStatus


class PytestCheck(BaseWorkspaceCheck):
    check_id = "pytest"
    check_name = "Pytest"
    required = True
    category = CheckCategory.UNIT_TESTS

    def _run_workspace_check(
        self, context: VerificationContext, ws_path: Path
    ) -> tuple[CheckStatus, dict[str, Any]]:
        tests_dir = ws_path / "tests"
        if not tests_dir.is_dir():
            return CheckStatus.FAIL, {
                "reason": "tests directory not found in workspace",
                "path": str(tests_dir),
            }

        executor = self._executor(ws_path)
        try:
            result = executor.run_command(["pytest", "-q", "tests"], timeout=120.0)
        except CheckExecutionError as exc:
            return CheckStatus.FAIL, {
                "reason": f"pytest tool unavailable: {type(exc).__name__}: {exc}",
            }
        except Exception as exc:
            return CheckStatus.ERROR, {
                "reason": f"pytest execution failed: {type(exc).__name__}: {exc}",
            }

        if result.timed_out:
            return CheckStatus.ERROR, {
                "reason": f"pytest timed out after 120s",
                "timed_out": True,
            }

        if result.exit_code != 0:
            return CheckStatus.FAIL, {
                "reason": "pytest reported failures",
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

        return CheckStatus.PASS, {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
        }
