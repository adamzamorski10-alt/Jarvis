"""
Operator Capability — Resolver.

Maps OperationType to application-owned CapabilityResolution.

The resolver is deterministic and application-owned.
The model may propose intent, but the application decides the capability.
"""
from __future__ import annotations

from typing import Any
import re

from app.operator_capability.models import (
    CapabilityCategory,
    CapabilityKind,
    CapabilityResolution,
    UnsupportedCapabilityError,
)


class CapabilityResolver:
    """
    Application-owned resolver for OperationType → Capability.

    This resolver is deterministic and does not accept arbitrary model-selected tools.
    """

    # ── Tool-backed operations ──────────────────────────────────────────────────
    _TOOL_OPERATIONS: dict[str, dict[str, Any]] = {
        "INFORMATION_REQUEST": {
            "capability_id": "system.get_time",
            "category": CapabilityCategory.SYSTEM,
            "description": "Get current UTC time",
            "parameters": {},
            "requires_approval": False,
            "requires_permission": True,
        },
        "FILE_READ": {
            "capability_id": "files.read_file",
            "category": CapabilityCategory.FILES,
            "description": "Read a file from authorized workspace",
            "parameters": {"path": "string (required)"},
            "requires_approval": False,
            "requires_permission": True,
        },
        "FILE_WRITE": {
            "capability_id": "files.write_file",
            "category": CapabilityCategory.FILES,
            "description": "Write a file to authorized workspace",
            "parameters": {"path": "string (required)", "content": "string (required)"},
            "requires_approval": True,
            "requires_permission": True,
        },
        "FILE_DELETE": {
            "capability_id": "files.delete_file",
            "category": CapabilityCategory.FILES,
            "description": "Delete a file from authorized workspace",
            "parameters": {"path": "string (required)"},
            "requires_approval": True,
            "requires_permission": True,
        },
    }

    # ── Service-backed operations ──────────────────────────────────────────────
    _SERVICE_OPERATIONS: dict[str, dict[str, Any]] = {
        "TASK_CREATE": {
            "capability_id": "task.create",
            "category": CapabilityCategory.TASK,
            "description": "Create a task via application service",
            "parameters": {},
            "requires_approval": False,
            "requires_permission": True,
        },
        "TASK_UPDATE": {
            "capability_id": "task.update",
            "category": CapabilityCategory.TASK,
            "description": "Update a task via application service",
            "parameters": {},
            "requires_approval": False,
            "requires_permission": True,
        },
        "PLAN_CREATE": {
            "capability_id": "plan.create",
            "category": CapabilityCategory.PLAN,
            "description": "Create a plan via application service",
            "parameters": {},
            "requires_approval": False,
            "requires_permission": True,
        },
        "PLAN_EXECUTE": {
            "capability_id": "plan.execute",
            "category": CapabilityCategory.PLAN,
            "description": "Execute a plan via ExecutionBridge",
            "parameters": {},
            "requires_approval": True,
            "requires_permission": True,
        },
    }

    # ── Answer-only operations ─────────────────────────────────────────────────
    _ANSWER_ONLY_OPERATIONS: set[str] = {
        "CHAT",
        "INFORMATION_REQUEST",  # may also be tool-backed if system.get_time is needed
        "DECISION",
        "CODING",
        "PLANNING",
        "OTHER",
        "UNKNOWN",
        "MEMORY_QUERY",
        "MEMORY_CREATE",
        "SCHEDULE_CREATE",
    }

    def resolve(self, request: OperatorRequest) -> CapabilityResolution:
        """
        Resolve an OperatorRequest to a CapabilityResolution.

        Args:
            request: The operator request to resolve.

        Returns:
            CapabilityResolution describing the capability to execute.

        Raises:
            UnsupportedCapabilityError: If the operation type has no supported capability.
        """
        operation_type = request.operation_type.upper()

        # 1. Check tool-backed operations
        if operation_type in self._TOOL_OPERATIONS:
            # Generic information requests are not automatically tool calls.
            # Only an explicit time/date question may resolve to system.get_time.
            if operation_type == "INFORMATION_REQUEST" and not self._is_time_request(request.user_request):
                return self._answer_only(request, operation_type)

            tool_def = self._TOOL_OPERATIONS[operation_type]
            parameters = self._extract_parameters(request, operation_type)
            return CapabilityResolution(
                request_id=request.request_id,
                operation_type=operation_type,
                capability_kind=CapabilityKind.TOOL,
                capability_id=tool_def["capability_id"],
                capability_category=tool_def["category"],
                description=tool_def["description"],
                parameters=parameters,
                requires_approval=tool_def.get("requires_approval", False),
                requires_permission=tool_def.get("requires_permission", True),
                confidence=1.0,
                reason="deterministic-tool-mapping",
            )

        # 2. Check service-backed operations
        if operation_type in self._SERVICE_OPERATIONS:
            svc_def = self._SERVICE_OPERATIONS[operation_type]
            return CapabilityResolution(
                request_id=request.request_id,
                operation_type=operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id=svc_def["capability_id"],
                capability_category=svc_def["category"],
                description=svc_def["description"],
                parameters=svc_def.get("parameters", {}),
                requires_approval=svc_def.get("requires_approval", False),
                requires_permission=svc_def.get("requires_permission", True),
                confidence=1.0,
                reason="deterministic-service-mapping",
            )

        # 3. Check answer-only operations
        if operation_type in self._ANSWER_ONLY_OPERATIONS:
            return CapabilityResolution(
                request_id=request.request_id,
                operation_type=operation_type,
                capability_kind=CapabilityKind.ANSWER_ONLY,
                capability_id=None,
                capability_category=CapabilityCategory.CHAT,
                description="No capability execution required",
                parameters={},
                requires_approval=False,
                requires_permission=False,
                confidence=1.0,
                reason="answer-only-operation",
            )

        # 4. Unsupported operation
        raise UnsupportedCapabilityError(
            operation_type=operation_type,
            reason="No capability mapping defined for this operation type",
        )

    @staticmethod
    def _is_time_request(user_request: str) -> bool:
        """Return True only for explicit requests for the current time/date."""
        return bool(
            re.search(
                r"\b(what\s+time|current\s+time|time\s+is\s+it|current\s+date|today'?s\s+date)\b",
                user_request,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _extract_parameters(request: OperatorRequest, operation_type: str) -> dict[str, Any]:
        """Extract only allowlisted execution parameters from application-owned constraints."""
        allowed = {
            "FILE_READ": {"path"},
            "FILE_WRITE": {"path", "content"},
            "FILE_DELETE": {"path"},
        }.get(operation_type, set())

        return {
            key: request.constraints[key]
            for key in allowed
            if key in request.constraints
            and isinstance(request.constraints[key], (str, int, float, bool))
        }

    @staticmethod
    def _answer_only(request: OperatorRequest, operation_type: str) -> CapabilityResolution:
        return CapabilityResolution(
            request_id=request.request_id,
            operation_type=operation_type,
            capability_kind=CapabilityKind.ANSWER_ONLY,
            capability_id=None,
            capability_category=CapabilityCategory.INFORMATION,
            description="No capability execution required",
            parameters={},
            requires_approval=False,
            requires_permission=False,
            confidence=1.0,
            reason="generic-information-request",
        )

    def is_supported(self, operation_type: str) -> bool:
        """Check if an operation type has a supported capability."""
        op = operation_type.upper()
        return op in self._TOOL_OPERATIONS or op in self._SERVICE_OPERATIONS or op in self._ANSWER_ONLY_OPERATIONS

    def is_tool_backed(self, operation_type: str) -> bool:
        """Check if an operation type is tool-backed."""
        return operation_type.upper() in self._TOOL_OPERATIONS

    def is_service_backed(self, operation_type: str) -> bool:
        """Check if an operation type is service-backed."""
        return operation_type.upper() in self._SERVICE_OPERATIONS

    def is_answer_only(self, operation_type: str) -> bool:
        """Check if an operation type is answer-only."""
        return operation_type.upper() in self._ANSWER_ONLY_OPERATIONS


__all__ = ["CapabilityResolver"]
