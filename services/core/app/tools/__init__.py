"""
Tools & Security Domain Package
Provides domain models, schemas, and enums for Tool Registry,
Permission Engine, Consent Management, and Tool Execution.
"""
from app.tools.exceptions import (
    InvalidToolDefinitionError,
    ToolAlreadyRegisteredError,
    ToolDisabledError,
    ToolDisabledInDefinitionError,
    ToolNotFoundError,
    ToolRegistryError,
)
from app.tools.models import (
    ApprovalRequest,
    ApprovalStatus,
    AutonomyMode,
    GrantType,
    PermissionDecision,
    PermissionDecisionType,
    PermissionEvaluationRequest,
    PermissionGrant,
    RiskLevel,
    ToolCall,
    ToolCategory,
    ToolDefinition,
    ToolExecutionResult,
    ToolExecutionStatus,
)
from app.tools.audit_models import (
    AuditEvent,
    AuditEventType,
    sanitize_payload,
    make_audit_event,
)
from app.tools.audit_service import AuditLogService
from app.tools.base import BaseTool
from app.tools.executor import ToolExecutor
from app.tools.implementation_registry import ToolImplementationRegistry, tool_implementation_registry
from app.tools.models import (
    ApprovalRequest,
    ApprovalStatus,
    AutonomyMode,
    GrantType,
    PermissionDecision,
    PermissionDecisionType,
    PermissionEvaluationRequest,
    PermissionGrant,
    RiskLevel,
    ToolCall,
    ToolCategory,
    ToolDefinition,
    ToolExecutionResult,
    ToolExecutionStatus,
)
from app.tools.permission_engine import PermissionEngine
from app.tools.registry import ToolRegistry, tool_registry
from app.tools.tool_execution_service import ToolExecutionService
from app.tools.validation import validate_arguments, validate_result

__all__ = [
    "BaseTool",
    "ToolExecutor",
    "ToolImplementationRegistry",
    "tool_implementation_registry",
    "ToolExecutionService",
    "validate_arguments",
    "validate_result",
    "ToolCategory",
    "RiskLevel",
    "AutonomyMode",
    "PermissionDecisionType",
    "GrantType",
    "ApprovalStatus",
    "ToolExecutionStatus",
    "ToolDefinition",
    "ToolCall",
    "ToolExecutionResult",
    "PermissionDecision",
    "PermissionEvaluationRequest",
    "PermissionGrant",
    "ApprovalRequest",
    "ToolRegistryError",
    "ToolAlreadyRegisteredError",
    "ToolNotFoundError",
    "ToolDisabledError",
    "ToolDisabledInDefinitionError",
    "InvalidToolDefinitionError",
    "ToolRegistry",
    "tool_registry",
    "ToolImplementationRegistry",
    "tool_implementation_registry",
    "PermissionEngine",
    "AuditEvent",
    "AuditEventType",
    "sanitize_payload",
    "make_audit_event",
    "AuditLogService",
]
