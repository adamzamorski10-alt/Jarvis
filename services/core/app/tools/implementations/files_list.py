"""
FilesListDirectory Tool Implementation.

Lists directory contents within the authorized workspace with strict security controls.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.tools.base import BaseTool
from app.tools.models import RiskLevel, ToolCategory, ToolDefinition, ToolExecutionContext


MAX_LIST_DIRECTORY_RECURSION_DEPTH = 10
MAX_LIST_DIRECTORY_ENTRIES = 1000


class FilesListDirectory(BaseTool):
    @property
    def tool_id(self) -> str:
        return "files.list_directory"

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_id="files.list_directory",
            action="list_directory",
            name="List Directory",
            description="Lists contents of a directory within the authorized workspace.",
            version="1.0.0",
            category=ToolCategory.FILES,
            risk_level=RiskLevel.LOW,
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to directory inside workspace",
                        "maxLength": 4096,
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Recursively list subdirectories",
                        "default": False,
                    },
                },
                "required": ["path"],
            },
            result_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "entries": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string", "enum": ["file", "directory", "symlink", "other"]},
                                "size": {"type": ["integer", "null"]},
                            },
                            "required": ["name", "type", "size"],
                        },
                    },
                    "truncated": {"type": "boolean"},
                },
                "required": ["path", "entries", "truncated"],
            },
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext | None = None) -> dict[str, Any]:
        rel_path = str(arguments.get("path", "")).strip()
        if not rel_path:
            raise ValueError("path is required")

        recursive = arguments.get("recursive", False)
        if not isinstance(recursive, bool):
            recursive = False

        p = Path(rel_path)
        if p.is_absolute() or p.drive != "":
            raise ValueError("Absolute paths or drive specifiers not allowed")
        if any(part == ".." for part in p.parts):
            raise ValueError("Path traversal not allowed")

        ws_path_str = context.workspace.workspace_path if context and context.workspace else None
        if not ws_path_str:
            raise ValueError("workspace_path context is required for files.list_directory execution")

        workspace_root = Path(ws_path_str).resolve()
        target_path = (workspace_root / p).resolve()

        try:
            target_path.relative_to(workspace_root)
        except ValueError:
            raise ValueError("Target path escapes authorized workspace root")

        if target_path.is_symlink():
            raise ValueError("Symlinks are not allowed")
        try:
            resolved = target_path.resolve()
            if resolved != target_path:
                raise ValueError("Symlinks are not allowed")
        except (OSError, RuntimeError):
            raise ValueError("Symlinks are not allowed")

        if not target_path.exists():
            raise ValueError(f"Directory not found: {rel_path}")
        if not target_path.is_dir():
            raise ValueError(f"Target is not a directory: {rel_path}")

        entries: list[dict[str, Any]] = []
        depth_hit = False

        if recursive:
            depth_hit = _collect_recursive(target_path, workspace_root, entries, depth=0)
        else:
            _collect_immediate(target_path, entries)

        truncated = False
        if len(entries) > MAX_LIST_DIRECTORY_ENTRIES:
            entries = entries[:MAX_LIST_DIRECTORY_ENTRIES]
            truncated = True

        if depth_hit:
            truncated = True

        return {
            "path": rel_path,
            "entries": entries,
            "truncated": truncated,
        }


def _collect_immediate(directory: Path, entries: list[dict[str, Any]]) -> None:
    for child in directory.iterdir():
        if len(entries) >= MAX_LIST_DIRECTORY_ENTRIES:
            break
        entries.append(_describe_entry(child))


def _collect_recursive(directory: Path, workspace_root: Path, entries: list[dict[str, Any]], depth: int) -> bool:
    if depth >= MAX_LIST_DIRECTORY_RECURSION_DEPTH:
        return True

    depth_hit = False
    for child in directory.iterdir():
        if len(entries) >= MAX_LIST_DIRECTORY_ENTRIES:
            depth_hit = True
            break

        entries.append(_describe_entry(child))

        if child.is_dir() and not child.is_symlink():
            if _collect_recursive(child, workspace_root, entries, depth + 1):
                depth_hit = True

    return depth_hit


def _describe_entry(path: Path) -> dict[str, Any]:
    name = path.name
    size = None
    entry_type = "other"

    try:
        if path.is_symlink():
            entry_type = "symlink"
        elif path.is_dir():
            entry_type = "directory"
        elif path.is_file():
            entry_type = "file"
            try:
                size = path.stat().st_size
            except OSError:
                size = None
    except OSError:
        entry_type = "other"

    return {"name": name, "type": entry_type, "size": size}
