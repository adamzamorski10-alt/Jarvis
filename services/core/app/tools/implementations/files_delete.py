"""
FilesDeleteFile Tool Implementation.

Deletes a file from the authorized workspace with strict security controls.
Only regular files may be deleted. Directories, symlinks, and the workspace
root are always rejected.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from app.tools.base import BaseTool
from app.tools.models import RiskLevel, ToolCategory, ToolDefinition, ToolExecutionContext


class FilesDeleteFile(BaseTool):
    @property
    def tool_id(self) -> str:
        return "files.delete_file"

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_id="files.delete_file",
            action="delete_file",
            name="Delete File",
            description="Deletes a regular file from the authorized workspace. Directories and symlinks are rejected.",
            version="1.0.0",
            category=ToolCategory.FILES,
            risk_level=RiskLevel.HIGH,
            requires_confirmation=True,
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to file inside workspace",
                        "maxLength": 4096,
                    },
                },
                "required": ["path"],
            },
            result_schema={
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "path": {"type": "string"},
                    "deleted": {"type": "boolean"},
                },
                "required": ["success", "path", "deleted"],
            },
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext | None = None) -> dict[str, Any]:
        rel_path = str(arguments.get("path", "")).strip()
        if not rel_path:
            raise ValueError("path is required")

        p = Path(rel_path)
        if p.is_absolute() or p.drive != "" or re.match(r"^[A-Za-z]:", rel_path):
            raise ValueError("Absolute paths or drive specifiers are not allowed")
        if any(part == ".." for part in p.parts):
            raise ValueError("Path traversal components ('..') are not allowed")

        ws_path_str = context.workspace.workspace_path if context and context.workspace else None
        if not ws_path_str:
            raise ValueError("workspace_path context is required for files.delete_file execution")

        workspace_root = Path(ws_path_str).resolve()
        raw_target = workspace_root / p
        current = workspace_root
        for part in p.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("Symlinks are not allowed")
        target_path = raw_target.resolve()

        try:
            target_path.relative_to(workspace_root)
        except ValueError:
            raise ValueError("Target file path escapes authorized workspace root")

        if target_path == workspace_root:
            raise ValueError("Cannot delete workspace root")

        current = workspace_root
        for part in target_path.relative_to(workspace_root).parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise ValueError("Symlinks are not allowed")
            if not current.exists():
                break

        if target_path.exists():
            if target_path.is_dir():
                raise ValueError("Directories cannot be deleted with files.delete_file")
            if target_path.is_symlink():
                raise ValueError("Symlinks are not allowed")
            try:
                resolved = target_path.resolve()
                if resolved != target_path:
                    raise ValueError("Symlinks are not allowed")
            except (OSError, RuntimeError):
                raise ValueError("Symlinks are not allowed")

            try:
                target_path.unlink()
            except OSError as exc:
                raise ValueError(f"Failed to delete file: {exc}")
        else:
            raise ValueError(f"File not found: {rel_path}")

        rel_result = target_path.relative_to(workspace_root).as_posix()
        return {
            "success": True,
            "path": rel_result,
            "deleted": True,
        }
