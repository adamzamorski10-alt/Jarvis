"""
Files API routes.

Read-only safe bridge between the frontend and the existing filesystem tools.
All operations go through:
  Frontend -> Core API -> ToolExecutionService -> PermissionEngine -> filesystem tool -> authorized workspace

No direct filesystem access is provided to the frontend.
Write and delete operations are intentionally NOT exposed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.schemas.files import DirectoryListResponse, FileEntry, FileReadResponse, FileSearchResponse
from app.dependencies import get_tool_execution_service
from app.identity.auth import get_request_context, require_authenticated
from app.tools.tool_execution_service import ToolExecutionService

router = APIRouter(prefix="/files", tags=["Files"])

_WORKSPACE_ROOT = str(Path.cwd())


def _get_actor_id(request: Request) -> str | None:
    ctx = get_request_context(request)
    if ctx and ctx.is_authenticated:
        return ctx.actor_id
    return None


@router.get("/list", response_model=DirectoryListResponse)
async def list_directory(
    request: Request,
    path: str = Query(default=".", description="Relative directory path inside workspace"),
    recursive: bool = Query(default=False, description="Recursively list subdirectories"),
    tool_svc: ToolExecutionService = Depends(get_tool_execution_service),
) -> DirectoryListResponse:
    _ = require_authenticated(request)
    """List directory contents through the existing files.list_directory tool."""
    tool_call = type("ToolCall", (), {"tool_id": "files.list_directory", "arguments": {"path": path, "recursive": recursive}, "tool_call_id": None, "request_id": None})()
    result = await tool_svc.execute_tool_call(
        tool_call,
        action="list_directory",
        autonomy_mode="semi_autonomous",
        request_id=None,
        approval_context=None,
        actor_id=_get_actor_id(request),
        workspace_path=_WORKSPACE_ROOT,
    )
    if not result.success:
        detail = result.error_message or "Failed to list directory"
        if result.error_code == "permission_denied":
            raise HTTPException(status_code=403, detail=detail)
        if result.error_code == "ask_user":
            raise HTTPException(status_code=409, detail=detail)
        raise HTTPException(status_code=400, detail=detail)

    data = result.result or {}
    return DirectoryListResponse(
        path=data.get("path", path),
        entries=[FileEntry(name=e["name"], type=e["type"], size=e.get("size"), path=e.get("path", f"{path}/{e['name']}")) for e in data.get("entries", [])],
        truncated=data.get("truncated", False),
    )


@router.get("/read", response_model=FileReadResponse)
async def read_file(
    request: Request,
    path: str = Query(default="", description="Relative file path inside workspace"),
    max_bytes: int = Query(default=1024 * 1024, ge=1, le=1024 * 1024, description="Maximum bytes to read"),
    tool_svc: ToolExecutionService = Depends(get_tool_execution_service),
) -> FileReadResponse:
    _ = require_authenticated(request)
    """Read a file through the existing files.read_file tool."""
    if not path.strip():
        raise HTTPException(status_code=400, detail="path is required")

    tool_call = type("ToolCall", (), {"tool_id": "files.read_file", "arguments": {"path": path, "max_bytes": max_bytes}, "tool_call_id": None, "request_id": None})()
    result = await tool_svc.execute_tool_call(
        tool_call,
        action="read_file",
        autonomy_mode="semi_autonomous",
        request_id=None,
        approval_context=None,
        actor_id=_get_actor_id(request),
        workspace_path=_WORKSPACE_ROOT,
    )
    if not result.success:
        detail = result.error_message or "Failed to read file"
        if result.error_code == "permission_denied":
            raise HTTPException(status_code=403, detail=detail)
        if result.error_code == "ask_user":
            raise HTTPException(status_code=409, detail=detail)
        raise HTTPException(status_code=400, detail=detail)

    data = result.result or {}
    return FileReadResponse(
        content=data.get("content", ""),
        size=data.get("size", 0),
        encoding=data.get("encoding", "utf-8"),
        truncated=data.get("truncated", False),
    )


@router.get("/search", response_model=FileSearchResponse)
async def search_files(
    request: Request,
    query: str = Query(default="", description="Substring to match against file names and paths"),
    path: str = Query(default=".", description="Relative subdirectory to search within"),
    max_results: int = Query(default=50, ge=1, le=200, description="Maximum number of results"),
    tool_svc: ToolExecutionService = Depends(get_tool_execution_service),
) -> FileSearchResponse:
    _ = require_authenticated(request)
    """Search files through the existing files.search tool."""
    if not query.strip():
        raise HTTPException(status_code=400, detail="query is required")

    tool_call = type("ToolCall", (), {"tool_id": "files.search", "arguments": {"query": query, "path": path, "max_results": max_results}, "tool_call_id": None, "request_id": None})()
    result = await tool_svc.execute_tool_call(
        tool_call,
        action="search",
        autonomy_mode="semi_autonomous",
        request_id=None,
        approval_context=None,
        actor_id=_get_actor_id(request),
        workspace_path=_WORKSPACE_ROOT,
    )
    if not result.success:
        detail = result.error_message or "Failed to search files"
        if result.error_code == "permission_denied":
            raise HTTPException(status_code=403, detail=detail)
        if result.error_code == "ask_user":
            raise HTTPException(status_code=409, detail=detail)
        raise HTTPException(status_code=400, detail=detail)

    data = result.result or {}
    return FileSearchResponse(
        success=data.get("success", True),
        query=data.get("query", query),
        path=data.get("path", path),
        matches=[FileEntry(name=m.get("name", m.get("path", "").split("/")[-1]), type=m.get("type", "other"), size=m.get("size"), path=m.get("path", "")) for m in data.get("matches", [])],
        truncated=data.get("truncated", False),
    )
