"""
FilesSearch Tool Implementation.

Searches for files by filename/path substring within the authorized workspace.
No content is read. No regex/glob interpretation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.tools.base import BaseTool
from app.tools.models import RiskLevel, ToolCategory, ToolDefinition, ToolExecutionContext


MAX_SEARCH_RESULTS = 200
MAX_SEARCH_DEPTH = 10
MAX_FILES_INSPECTED = 5000
MAX_QUERY_LENGTH = 256


class FilesSearch(BaseTool):
    @property
    def tool_id(self) -> str:
        return "files.search"

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_id="files.search",
            action="search",
            name="Search Files",
            description="Searches for files by filename/path substring within the workspace.",
            version="1.0.0",
            category=ToolCategory.FILES,
            risk_level=RiskLevel.LOW,
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Substring to match against file names and relative paths",
                        "maxLength": MAX_QUERY_LENGTH,
                    },
                    "path": {
                        "type": "string",
                        "description": "Relative subdirectory to search within",
                        "maxLength": 4096,
                        "default": ".",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "maximum": MAX_SEARCH_RESULTS,
                        "default": 50,
                    },
                },
                "required": ["query"],
            },
            result_schema={
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                    "matches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "name": {"type": "string"},
                                "type": {"type": "string", "enum": ["file", "directory", "symlink", "other"]},
                                "size": {"type": ["integer", "null"]},
                            },
                            "required": ["path", "name", "type", "size"],
                        },
                    },
                    "truncated": {"type": "boolean"},
                },
                "required": ["success", "query", "path", "matches", "truncated"],
            },
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext | None = None) -> dict[str, Any]:
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ValueError("query is required")

        rel_root = str(arguments.get("path", ".")).strip()
        if not rel_root:
            rel_root = "."

        max_results = arguments.get("max_results", 50)
        if not isinstance(max_results, int) or max_results <= 0:
            max_results = 50
        max_results = min(max_results, MAX_SEARCH_RESULTS)

        p = Path(rel_root)
        if p.is_absolute() or p.drive != "":
            raise ValueError("Absolute paths or drive specifiers are not allowed")
        if any(part == ".." for part in p.parts):
            raise ValueError("Path traversal components ('..') are not allowed")

        ws_path_str = context.workspace.workspace_path if context and context.workspace else None
        if not ws_path_str:
            raise ValueError("workspace_path context is required for files.search execution")

        workspace_root = Path(ws_path_str).resolve()
        raw_root_path = workspace_root / p

        # Validate the user-selected root before resolving it. Resolving first
        # would follow a symlink and could turn an explicitly forbidden root
        # into an apparently safe directory.
        if raw_root_path.is_symlink():
            raise ValueError("Symlinks are not allowed")

        root_path = raw_root_path.resolve()

        try:
            root_path.relative_to(workspace_root)
        except ValueError:
            raise ValueError("Search path escapes authorized workspace root")

        # A non-root search path may not traverse through a symlinked component.
        # Symlink entries themselves remain visible as results, but are never
        # followed during recursive descent.
        if root_path != workspace_root:
            current = workspace_root
            for part in p.parts:
                if part in ("", "."):
                    continue
                current = current / part
                if current.is_symlink():
                    raise ValueError("Symlinks are not allowed")

        if not root_path.exists():
            raise ValueError(f"Directory not found: {rel_root}")
        if not root_path.is_dir():
            raise ValueError(f"Target is not a directory: {rel_root}")

        query_lower = query.lower()
        matches: list[dict[str, Any]] = []
        files_inspected = 0
        truncated = False

        def _collect(directory: Path, depth: int) -> bool:
            nonlocal truncated, files_inspected

            if depth >= MAX_SEARCH_DEPTH:
                truncated = True
                return True

            depth_hit = False
            try:
                children = sorted(directory.iterdir(), key=lambda child: child.name.lower())
            except OSError:
                return False

            for child in children:
                if len(matches) >= max_results:
                    truncated = True
                    return True

                if files_inspected >= MAX_FILES_INSPECTED:
                    truncated = True
                    depth_hit = True
                    break

                files_inspected += 1

                try:
                    rel_child = child.relative_to(workspace_root).as_posix()
                except ValueError:
                    continue

                # Search only the safe, non-resolved entry name and relative
                # workspace path. Symlink targets are intentionally excluded so
                # matching never requires inspecting or trusting the target.
                search_text = child.name.lower() + " " + rel_child.lower()
                if query_lower in search_text:
                    entry = _describe_entry(child, rel_child)
                    matches.append(entry)

                if child.is_dir() and not child.is_symlink():
                    if _collect(child, depth + 1):
                        depth_hit = True

            return depth_hit

        _collect(root_path, 0)

        matches.sort(key=lambda item: item["path"].lower())

        return {
            "success": True,
            "query": query,
            "path": rel_root,
            "matches": matches,
            "truncated": truncated,
        }


def _describe_entry(path: Path, rel_path: str) -> dict[str, Any]:
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

    return {"path": rel_path, "name": name, "type": entry_type, "size": size}
