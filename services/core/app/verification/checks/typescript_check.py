"""
TypeScript type-check verification check.

Runs npx tsc --noEmit in the workspace when tsconfig.json is present.
Missing tsconfig.json => FAIL.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.verification.checks.base_workspace_check import BaseWorkspaceCheck
from app.verification.exceptions import CheckExecutionError
from app.verification.models import CheckCategory, CheckStatus


class TypeScriptCheck(BaseWorkspaceCheck):
    check_id = "typescript"
    check_name = "TypeScript"
    required = True
    category = CheckCategory.TYPE_CHECK

    def _run_workspace_check(
        self, context: VerificationContext, ws_path: Path
    ) -> tuple[CheckStatus, dict[str, Any]]:
        tsconfig = ws_path / "tsconfig.json"
        if not tsconfig.is_file():
            return CheckStatus.FAIL, {
                "reason": "tsconfig.json not found in workspace",
                "path": str(tsconfig),
            }

        executor = self._executor(ws_path)
        try:
            result = executor.run_command(["npx", "tsc", "--noEmit"], timeout=120.0)
        except CheckExecutionError as exc:
            return CheckStatus.FAIL, {
                "reason": f"TypeScript tool unavailable: {type(exc).__name__}: {exc}",
            }
        except Exception as exc:
            return CheckStatus.ERROR, {
                "reason": f"TypeScript execution failed: {type(exc).__name__}: {exc}",
            }

        if result.timed_out:
            return CheckStatus.ERROR, {
                "reason": "TypeScript type-check timed out after 120s",
                "timed_out": True,
            }

        if result.exit_code != 0:
            return CheckStatus.FAIL, {
                "reason": "TypeScript type-check reported errors",
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

        return CheckStatus.PASS, {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
        }
