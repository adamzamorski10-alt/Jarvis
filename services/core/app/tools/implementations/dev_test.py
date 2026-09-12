"""
DevRunTest Tool Implementation.

Narrowly-scoped, controlled tool for executing project test suites inside an authorized workspace.
Does NOT provide arbitrary shell execution.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Any

from app.tools.base import BaseTool
from app.tools.models import RiskLevel, ToolCategory, ToolDefinition


class DevRunTest(BaseTool):
    """
    Controlled development test execution tool.

    Executes pytest on a specified target_file relative to the workspace path.
    """

    @property
    def tool_id(self) -> str:
        return "dev.run_test"

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_id="dev.run_test",
            action="run_test",
            name="Run Development Test",
            description="Executes a test file inside the authorized workspace using pytest.",
            version="1.0.0",
            category=ToolCategory.AUTOMATION,
            risk_level=RiskLevel.LOW,
            parameters_schema={
                "type": "object",
                "properties": {
                    "target_file": {
                        "type": "string",
                        "description": "Relative path to test file inside workspace",
                        "maxLength": 4096,
                    },
                },
                "required": ["target_file"],
            },
            result_schema={
                "type": "object",
                "properties": {
                    "passed": {"type": "boolean"},
                    "stdout": {"type": "string"},
                    "stderr": {"type": "string"},
                    "exit_code": {"type": "integer"},
                },
                "required": ["passed", "stdout", "stderr", "exit_code"],
            },
        )

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
        target_file = str(arguments.get("target_file", "")).strip()
        if not target_file:
            raise ValueError("target_file is required")

        p = Path(target_file)
        if p.is_absolute() or p.drive != "":
            raise ValueError("Absolute paths or drive specifiers not allowed")
        if any(part == ".." for part in p.parts):
            raise ValueError("Path traversal not allowed")

        ws_path_str = (context or {}).get("workspace_path")
        if not ws_path_str:
            raise ValueError("workspace_path context is required for dev.run_test execution")

        workspace_root = Path(ws_path_str).resolve()
        target_path = (workspace_root / p).resolve()

        # Symlink containment check
        try:
            target_path.relative_to(workspace_root)
        except ValueError:
            raise ValueError("Target file path escapes authorized workspace root")

        # Execute pytest with shell=False
        cmd = [sys.executable, "-m", "pytest", str(target_path)]
        res = subprocess.run(
            cmd,
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            shell=False,
        )

        stdout = res.stdout[:65536] if res.stdout else ""
        stderr = res.stderr[:65536] if res.stderr else ""

        return {
            "passed": res.returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": res.returncode,
        }
