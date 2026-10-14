"""
FilesReadFile Tool Implementation.

Reads a file from the authorized workspace with strict security controls.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from app.tools.base import BaseTool
from app.tools.models import RiskLevel, ToolCategory, ToolDefinition, ToolExecutionContext


MAX_READ_BYTES = 1024 * 1024


class FilesReadFile(BaseTool):
    @property
    def tool_id(self) -> str:
        return "files.read_file"

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_id="files.read_file",
            action="read_file",
            name="Read File",
            description="Reads a file from the authorized workspace.",
            version="1.0.0",
            category=ToolCategory.FILES,
            risk_level=RiskLevel.LOW,
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to file inside workspace",
                        "maxLength": 4096,
                    },
                    "max_bytes": {
                        "type": "integer",
                        "description": "Maximum bytes to read",
                        "maximum": MAX_READ_BYTES,
                        "default": MAX_READ_BYTES,
                    },
                },
                "required": ["path"],
            },
            result_schema={
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "size": {"type": "integer"},
                    "encoding": {"type": "string"},
                    "truncated": {"type": "boolean"},
                },
                "required": ["content", "size", "encoding", "truncated"],
            },
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext | None = None) -> dict[str, Any]:
        rel_path = str(arguments.get("path", "")).strip()
        if not rel_path:
            raise ValueError("path is required")

        max_bytes = arguments.get("max_bytes", MAX_READ_BYTES)
        if not isinstance(max_bytes, int) or max_bytes <= 0 or max_bytes > MAX_READ_BYTES:
            max_bytes = MAX_READ_BYTES

        p = Path(rel_path)
        if p.is_absolute() or p.drive != "" or re.match(r"^[A-Za-z]:", rel_path):
            raise ValueError("Absolute paths or drive specifiers not allowed")
        if any(part == ".." for part in p.parts):
            raise ValueError("Path traversal not allowed")

        ws_path_str = context.workspace.workspace_path if context and context.workspace else None
        if not ws_path_str:
            raise ValueError("workspace_path context is required for files.read_file execution")

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

        if not target_path.exists():
            raise ValueError(f"File not found: {rel_path}")
        if target_path.is_dir():
            raise ValueError(f"Target is a directory, not a file: {rel_path}")

        if target_path.is_symlink():
            raise ValueError("Symlinks are not allowed")

        try:
            resolved = target_path.resolve()
            if resolved != target_path:
                raise ValueError("Symlinks are not allowed")
        except (OSError, RuntimeError):
            raise ValueError("Symlinks are not allowed")

        file_size = target_path.stat().st_size
        read_limit = min(max_bytes, file_size)
        truncated = file_size > max_bytes

        try:
            content_parts = []
            remaining = read_limit
            with target_path.open("rb") as fh:
                while remaining > 0:
                    chunk = fh.read(min(65536, remaining))
                    if not chunk:
                        break
                    content_parts.append(chunk)
                    remaining -= len(chunk)
            content = b"".join(content_parts)
        except OSError as exc:
            raise ValueError(f"Failed to read file: {exc}")

        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("utf-8", errors="replace")

        return {
            "content": text,
            "size": len(content),
            "encoding": "utf-8",
            "truncated": truncated,
        }
