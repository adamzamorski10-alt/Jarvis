"""
FilesWriteFile Tool Implementation.

Writes a file to the authorized workspace with strict security controls.
Uses atomic temp-file + replace strategy to preserve existing target on failure.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from app.tools.base import BaseTool
from app.tools.models import RiskLevel, ToolCategory, ToolDefinition, ToolExecutionContext


MAX_WRITE_BYTES = 1024 * 1024
MAX_ARGUMENTS_SIZE_BYTES = MAX_WRITE_BYTES + 4096


class FilesWriteFile(BaseTool):
    @property
    def tool_id(self) -> str:
        return "files.write_file"

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_id="files.write_file",
            action="write_file",
            name="Write File",
            description="Writes a file to the authorized workspace using an atomic temp-file and replace strategy.",
            version="1.0.0",
            category=ToolCategory.FILES,
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=True,
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to file inside workspace",
                        "maxLength": 4096,
                    },
                    "content": {
                        "type": "string",
                        "description": "UTF-8 text content to write",
                        "maxLength": MAX_WRITE_BYTES,
                    },
                    "create_dirs": {
                        "type": "boolean",
                        "description": "Create missing parent directories when True",
                        "default": False,
                    },
                },
                "required": ["path", "content"],
            },
            result_schema={
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "path": {"type": "string"},
                    "size": {"type": "integer"},
                },
                "required": ["success", "path", "size"],
            },
            max_arguments_size_bytes=MAX_ARGUMENTS_SIZE_BYTES,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext | None = None) -> dict[str, Any]:
        rel_path = str(arguments.get("path", "")).strip()
        if not rel_path:
            raise ValueError("path is required")

        content = str(arguments.get("content", ""))

        encoded = content.encode("utf-8")
        if len(encoded) > MAX_WRITE_BYTES:
            raise ValueError(
                f"Content size {len(encoded)} bytes exceeds maximum allowed size of {MAX_WRITE_BYTES} bytes (1 MiB)."
            )

        create_dirs = arguments.get("create_dirs", False)
        if not isinstance(create_dirs, bool):
            create_dirs = False

        p = Path(rel_path)
        if p.is_absolute() or p.drive != "":
            raise ValueError("Absolute paths or drive specifiers are not allowed")
        if any(part == ".." for part in p.parts):
            raise ValueError("Path traversal components ('..') are not allowed")

        ws_path_str = context.workspace.workspace_path if context and context.workspace else None
        if not ws_path_str:
            raise ValueError("workspace_path context is required for files.write_file execution")

        workspace_root = Path(ws_path_str).resolve()
        target_path = (workspace_root / p).resolve()

        try:
            target_path.relative_to(workspace_root)
        except ValueError:
            raise ValueError("Target file path escapes authorized workspace root")

        current = workspace_root
        for part in target_path.relative_to(workspace_root).parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise ValueError("Symlinks are not allowed")
            if not current.exists():
                break

        if target_path.exists():
            if target_path.is_dir():
                raise ValueError("Target is a directory, not a file")
            if target_path.is_symlink():
                raise ValueError("Symlinks are not allowed")
            try:
                resolved = target_path.resolve()
                if resolved != target_path:
                    raise ValueError("Symlinks are not allowed")
            except (OSError, RuntimeError):
                raise ValueError("Symlinks are not allowed")

        parent_path = target_path.parent
        if not parent_path.exists():
            if not create_dirs:
                raise ValueError("Parent directory does not exist")
            try:
                parent_path.relative_to(workspace_root)
            except ValueError:
                raise ValueError("Parent directory escapes workspace root")
            parent_path.mkdir(parents=True, exist_ok=True)
            try:
                parent_path.resolve().relative_to(workspace_root)
            except ValueError:
                raise ValueError("Parent directory escapes workspace root after creation")

        dir_name = str(parent_path)
        fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix=".ai_op_tmp_")
        try:
            if os.name == "posix":
                os.chmod(temp_path, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(temp_path, target_path)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

        rel_result = target_path.relative_to(workspace_root).as_posix()
        return {
            "success": True,
            "path": rel_result,
            "size": len(encoded),
        }
