"""
Audit Log Service — production write path for structured security audit events.

Fail-soft: persistence errors are logged and swallowed so callers such as
PermissionEngine never change a permission decision because audit I/O failed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.tools.audit_models import (
    AuditEvent,
    AuditEventType,
    make_audit_event,
)

if TYPE_CHECKING:
    from app.repositories.audit_log_repository import AuditLogRepository

logger = get_logger(__name__)


class AuditLogService:
    """
    Production facade for recording audit events.
    Always builds events via make_audit_event() (UTC now + sanitize_payload).
    """

    def __init__(self, repo: "AuditLogRepository") -> None:
        self._repo = repo

    async def record(
        self,
        event_type: AuditEventType | str,
        *,
        reason: str,
        request_id: str | None = None,
        tool_id: str | None = None,
        action: str | None = None,
        risk_level: str | None = None,
        autonomy_mode: str | None = None,
        decision: str | None = None,
        rule_id: str | None = None,
        grant_id: str | None = None,
        execution_status: str | None = None,
        success: bool | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        execution_time_ms: float | None = None,
        arguments: Any = None,
        result: Any = None,
        event_id: str | None = None,
    ) -> AuditEvent | None:
        """
        Persist a sanitized audit event.
        Returns the stored AuditEvent, or None if persistence failed.
        Never raises to the caller.
        """
        try:
            event = make_audit_event(
                event_type,
                reason=reason,
                request_id=request_id,
                tool_id=tool_id,
                action=action,
                risk_level=risk_level,
                autonomy_mode=autonomy_mode,
                decision=decision,
                rule_id=rule_id,
                grant_id=grant_id,
                execution_status=execution_status,
                success=success,
                error_code=error_code,
                error_message=error_message,
                execution_time_ms=execution_time_ms,
                arguments=arguments,
                result=result,
                event_id=event_id,
            )
            return await self._repo.record(event)
        except Exception as exc:
            logger.error(
                "Audit log write failed",
                event_type=str(event_type),
                tool_id=tool_id,
                request_id=request_id,
                error=str(exc),
                exc_info=True,
            )
            return None
