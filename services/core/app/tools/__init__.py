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
from app.tools.permission_engine import PermissionEngine
from app.tools.registry import ToolRegistry, tool_registry

__all__ = [
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
    "PermissionEngine",
    "AuditEvent",
    "AuditEventType",
    "sanitize_payload",
    "make_audit_event",
    "AuditLogService",
]
