"""
Notification Policy — Deterministic Event → NotificationDraft mapping.

This module is a pure, side-effect-free policy layer. It:

- inspects a durable Event
- decides whether a Notification should be created
- produces a deterministic NotificationDraft or None

The policy MUST NOT:
- write to the database
- emit Events
- execute tools
- change task/plan/execution state
- bypass PermissionEngine, ApprovalService, or VerificationGate
- call external providers or models
"""

from __future__ import annotations

from typing import Any

from app.events.schemas import Event, EventType
from app.notifications.models import NotificationSeverity, NotificationType


class NotificationDraft:
    """
    Deterministic notification data produced by the policy.

    This is NOT a persisted Notification. It is the minimal contract
    between the policy layer and the persistence layer.
    """

    __slots__ = (
        "notification_type",
        "severity",
        "title",
        "body",
        "source_event_id",
        "correlation_id",
        "task_id",
        "plan_id",
        "run_id",
        "execution_id",
        "metadata",
        "expires_at",
    )

    def __init__(
        self,
        *,
        notification_type: str,
        severity: str,
        title: str,
        body: str | None = None,
        source_event_id: str,
        correlation_id: str | None = None,
        task_id: str | None = None,
        plan_id: str | None = None,
        run_id: str | None = None,
        execution_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: str | None = None,
    ) -> None:
        self.notification_type = notification_type
        self.severity = severity
        self.title = title
        self.body = body
        self.source_event_id = source_event_id
        self.correlation_id = correlation_id
        self.task_id = task_id
        self.plan_id = plan_id
        self.run_id = run_id
        self.execution_id = execution_id
        self.metadata = metadata
        self.expires_at = expires_at

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, NotificationDraft):
            return NotImplemented
        return (
            self.notification_type == other.notification_type
            and self.severity == other.severity
            and self.title == other.title
            and self.body == other.body
            and self.source_event_id == other.source_event_id
            and self.correlation_id == other.correlation_id
            and self.task_id == other.task_id
            and self.plan_id == other.plan_id
            and self.run_id == other.run_id
            and self.execution_id == other.execution_id
            and self.metadata == other.metadata
            and self.expires_at == other.expires_at
        )

    def __repr__(self) -> str:
        return (
            f"NotificationDraft("
            f"notification_type={self.notification_type!r}, "
            f"severity={self.severity!r}, "
            f"title={self.title!r}, "
            f"source_event_id={self.source_event_id!r})"
        )


class NotificationPolicy:
    """
    Deterministic policy that maps Events to NotificationDrafts.

    V1 scope: only NOTIFICATION_REQUIRED events produce drafts.
    All other events return None.
    """

    def process(self, event: Event) -> NotificationDraft | None:
        """
        Return a NotificationDraft for the given Event, or None if no
        notification is required.

        This method is pure: it has no side effects and does not depend on
        any external system.
        """
        if not isinstance(event, Event):
            raise TypeError(f"Expected Event, got {type(event).__name__}")

        handler = self._mapping.get(event.event_type)
        if handler is None:
            return None
        return handler(self, event)

    def _handle_task_started(self, event: Event) -> NotificationDraft | None:
        return None

    def _handle_task_completed(self, event: Event) -> NotificationDraft | None:
        task_id = event.task_id or "unknown"
        plan_id = event.plan_id or "unknown"
        title = f"Task {task_id} completed"
        body = f"Task {task_id} completed successfully in plan {plan_id}."
        metadata = self._safe_metadata(event.payload)
        return NotificationDraft(
            notification_type=NotificationType.TASK_COMPLETED.value,
            severity=NotificationSeverity.INFO.value,
            title=title,
            body=body,
            source_event_id=event.event_id,
            correlation_id=event.correlation_id,
            task_id=event.task_id,
            plan_id=event.plan_id,
            run_id=event.run_id,
            execution_id=event.execution_id,
            metadata=metadata,
        )

    def _handle_task_failed(self, event: Event) -> NotificationDraft | None:
        task_id = event.task_id or "unknown"
        plan_id = event.plan_id or "unknown"
        reason = self._safe_reason(event.payload)
        title = f"Task {task_id} failed"
        body = f"Task {task_id} failed in plan {plan_id}: {reason}"
        metadata = self._safe_metadata(event.payload)
        return NotificationDraft(
            notification_type=NotificationType.TASK_FAILED.value,
            severity=NotificationSeverity.ERROR.value,
            title=title,
            body=body,
            source_event_id=event.event_id,
            correlation_id=event.correlation_id,
            task_id=event.task_id,
            plan_id=event.plan_id,
            run_id=event.run_id,
            execution_id=event.execution_id,
            metadata=metadata,
        )

    def _handle_plan_started(self, event: Event) -> NotificationDraft | None:
        return None

    def _handle_plan_completed(self, event: Event) -> NotificationDraft | None:
        plan_id = event.plan_id or "unknown"
        completed_count = self._safe_int(event.payload, "completed_tasks_count")
        total_count = self._safe_int(event.payload, "total_tasks_count")
        if completed_count is not None and total_count is not None:
            body = f"Plan {plan_id} completed successfully with {completed_count}/{total_count} tasks."
        else:
            body = f"Plan {plan_id} completed successfully."
        metadata = self._safe_metadata(event.payload)
        return NotificationDraft(
            notification_type=NotificationType.PLAN_COMPLETED.value,
            severity=NotificationSeverity.INFO.value,
            title=f"Plan {plan_id} completed",
            body=body,
            source_event_id=event.event_id,
            correlation_id=event.correlation_id,
            task_id=event.task_id,
            plan_id=event.plan_id,
            run_id=event.run_id,
            execution_id=event.execution_id,
            metadata=metadata,
        )

    def _handle_plan_failed(self, event: Event) -> NotificationDraft | None:
        plan_id = event.plan_id or "unknown"
        reason = self._safe_reason(event.payload)
        title = f"Plan {plan_id} failed"
        body = f"Plan {plan_id} failed: {reason}"
        metadata = self._safe_metadata(event.payload)
        return NotificationDraft(
            notification_type=NotificationType.PLAN_FAILED.value,
            severity=NotificationSeverity.ERROR.value,
            title=title,
            body=body,
            source_event_id=event.event_id,
            correlation_id=event.correlation_id,
            task_id=event.task_id,
            plan_id=event.plan_id,
            run_id=event.run_id,
            execution_id=event.execution_id,
            metadata=metadata,
        )

    def _handle_verification_started(self, event: Event) -> NotificationDraft | None:
        return None

    def _handle_verification_passed(self, event: Event) -> NotificationDraft | None:
        return None

    def _handle_verification_failed(self, event: Event) -> NotificationDraft | None:
        return None

    def _handle_checkpoint_created(self, event: Event) -> NotificationDraft | None:
        return None

    def _handle_scheduler_occurrence_claimed(self, event: Event) -> NotificationDraft | None:
        return None

    def _handle_scheduler_occurrence_triggered(self, event: Event) -> NotificationDraft | None:
        return None

    def _handle_scheduler_occurrence_failed(self, event: Event) -> NotificationDraft | None:
        job_id = self._safe_str(event.payload, "job_id") or "unknown"
        occurrence_id = self._safe_str(event.payload, "occurrence_id") or "unknown"
        reason_code = self._safe_str(event.payload, "reason_code") or "unknown"
        reason_detail = self._safe_str(event.payload, "reason_detail") or ""
        if reason_detail:
            reason_detail = reason_detail[:200]
            body = f"Scheduled job {job_id} (occurrence {occurrence_id}) failed: {reason_code} — {reason_detail}"
        else:
            body = f"Scheduled job {job_id} (occurrence {occurrence_id}) failed: {reason_code}"
        metadata = {
            "job_id": job_id,
            "occurrence_id": occurrence_id,
            "reason_code": reason_code,
        }
        if reason_detail:
            metadata["reason_detail"] = reason_detail[:200]
        return NotificationDraft(
            notification_type=NotificationType.SCHEDULER_OCCURRENCE_FAILED.value,
            severity=NotificationSeverity.ERROR.value,
            title="Scheduler occurrence failed",
            body=body,
            source_event_id=event.event_id,
            correlation_id=event.correlation_id,
            execution_id=event.execution_id,
            metadata=metadata,
        )

    def _handle_scheduler_occurrence_recovered(self, event: Event) -> NotificationDraft | None:
        job_id = self._safe_str(event.payload, "job_id") or "unknown"
        occurrence_id = self._safe_str(event.payload, "occurrence_id") or "unknown"
        reason = self._safe_str(event.payload, "reason") or "lease_expired"
        body = f"Scheduled job {job_id} (occurrence {occurrence_id}) recovered after {reason}."
        metadata = {
            "job_id": job_id,
            "occurrence_id": occurrence_id,
            "reason": reason[:200],
        }
        return NotificationDraft(
            notification_type=NotificationType.SCHEDULER_OCCURRENCE_RECOVERED.value,
            severity=NotificationSeverity.WARNING.value,
            title="Scheduler occurrence recovered",
            body=body,
            source_event_id=event.event_id,
            correlation_id=event.correlation_id,
            execution_id=event.execution_id,
            metadata=metadata,
        )

    def _handle_scheduler_occurrence_skipped(self, event: Event) -> NotificationDraft | None:
        return None

    def _safe_str(self, payload: dict[str, Any] | None, key: str) -> str | None:
        if not isinstance(payload, dict):
            return None
        value = payload.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            return str(value)
        return value[:200]

    def _safe_int(self, payload: dict[str, Any] | None, key: str) -> int | None:
        if not isinstance(payload, dict):
            return None
        value = payload.get(key)
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _safe_reason(self, payload: dict[str, Any] | None) -> str:
        if not isinstance(payload, dict):
            return "unknown cause"
        for key in ("reason", "error_message", "error", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:200]
        return "unknown cause"

    def _safe_metadata(self, payload: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        metadata: dict[str, Any] = {}
        for key in ("attempt_count", "commit_sha", "reason_code", "reason_detail", "error_message"):
            value = payload.get(key)
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                metadata[key] = value
        return metadata or None

    _mapping: dict[EventType, Any] = {
        EventType.TASK_STARTED: _handle_task_started,
        EventType.TASK_COMPLETED: _handle_task_completed,
        EventType.TASK_FAILED: _handle_task_failed,
        EventType.PLAN_STARTED: _handle_plan_started,
        EventType.PLAN_COMPLETED: _handle_plan_completed,
        EventType.PLAN_FAILED: _handle_plan_failed,
        EventType.VERIFICATION_STARTED: _handle_verification_started,
        EventType.VERIFICATION_PASSED: _handle_verification_passed,
        EventType.VERIFICATION_FAILED: _handle_verification_failed,
        EventType.CHECKPOINT_CREATED: _handle_checkpoint_created,
        EventType.SCHEDULER_OCCURRENCE_CLAIMED: _handle_scheduler_occurrence_claimed,
        EventType.SCHEDULER_OCCURRENCE_TRIGGERED: _handle_scheduler_occurrence_triggered,
        EventType.SCHEDULER_OCCURRENCE_FAILED: _handle_scheduler_occurrence_failed,
        EventType.SCHEDULER_OCCURRENCE_RECOVERED: _handle_scheduler_occurrence_recovered,
        EventType.SCHEDULER_OCCURRENCE_SKIPPED: _handle_scheduler_occurrence_skipped,
    }


def process_event(event: Event) -> NotificationDraft | None:
    """
    Convenience function for processing a single Event.

    Returns a NotificationDraft if the Event requires a notification,
    or None if no notification should be created.
    """
    return NotificationPolicy().process(event)


__all__ = [
    "NotificationDraft",
    "NotificationPolicy",
    "process_event",
]
