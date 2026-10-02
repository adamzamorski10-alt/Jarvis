"""
Operator Request — Service.

Application-owned service that manages the full Operator Request lifecycle.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.context_assembly.models import ContextAssemblyRequest, OperationType
from app.identity.models import ActorType, RequestContext
from app.information_sufficiency.models import InformationSufficiencyRequest
from app.operator_request.classifier import OperationClassifier
from app.operator_request.errors import (
    ClassificationFailedError,
    InvalidOperatorRequestError,
    NormalizationFailedError,
    OperatorRequestError,
    OperatorServiceUnavailableError,
    ReadinessCheckFailedError,
)
from app.operator_request.models import (
    OperatorRequest,
    OperatorRequestCreate,
    OperatorRequestState,
    OperatorRequestUpdate,
    ReadinessState,
)


class OperatorRequestService:
    """
    Application-owned service for managing Operator Requests.

    Responsibilities:
    1. Create normalized OperatorRequest from raw input
    2. Classify operation type
    3. Coordinate with existing subsystems (Context Assembly, Information Sufficiency, etc.)
    4. Manage lifecycle state transitions
    5. Emit events via EventEmitter
    """

    def __init__(
        self,
        normalizer: Any = None,
        classifier: Any = None,
        event_emitter: Any = None,
    ) -> None:
        self._normalizer = normalizer or _DefaultNormalizer()
        self._classifier = classifier or OperationClassifier()
        self._event_emitter = event_emitter

    async def create_request(
        self,
        user_request: str,
        request_context: RequestContext,
        conversation_id: str | None = None,
        message_id: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        constraints: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OperatorRequest:
        """
        Create a new normalized Operator Request.

        Args:
            user_request: Raw user request text.
            request_context: Authenticated request context (application-controlled).
            conversation_id: Optional conversation ID.
            message_id: Optional message ID.
            project_id: Optional project ID.
            task_id: Optional task ID.
            correlation_id: Optional correlation ID.
            causation_id: Optional causation ID.
            constraints: Optional constraints.
            metadata: Optional metadata.

        Returns:
            Normalized OperatorRequest.

        Raises:
            InvalidOperatorRequestError: If input validation fails.
            NormalizationFailedError: If normalization fails.
        """
        if request_context is None:
            raise InvalidOperatorRequestError("request_context is required")

        create = OperatorRequestCreate(
            conversation_id=conversation_id,
            message_id=message_id,
            user_request=user_request,
            project_id=project_id,
            task_id=task_id,
            correlation_id=correlation_id or self._generate_correlation_id(),
            causation_id=causation_id,
            constraints=constraints or {},
            metadata=metadata or {},
            request_context=request_context,
        )

        request = self._normalizer.normalize(create)
        self._emit_event("operator.request.received", request)
        return request

    async def classify_request(self, request: OperatorRequest) -> OperatorRequest:
        """
        Classify the operation type for the request.

        Args:
            request: The operator request to classify.

        Returns:
            Updated OperatorRequest with classification.

        Raises:
            ClassificationFailedError: If classification fails.
        """
        classified = self._classifier.classify(request)
        self._emit_event("operator.request.classified", classified)
        return classified

    async def update_request(
        self,
        request: OperatorRequest,
        update: OperatorRequestUpdate,
    ) -> OperatorRequest:
        """
        Update an Operator Request with new state.

        Args:
            request: The current operator request.
            update: The update to apply.

        Returns:
            Updated OperatorRequest.
        """
        update_data = update.model_dump(exclude_none=True)
        if not update_data:
            return request

        updated = request.model_copy(
            update={
                **update_data,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        self._emit_event("operator.request.updated", updated)
        return updated

    async def set_readiness(
        self,
        request: OperatorRequest,
        readiness_state: ReadinessState,
        reason: str | None = None,
    ) -> OperatorRequest:
        """
        Set the readiness state for the request.

        Args:
            request: The operator request.
            readiness_state: The new readiness state.
            reason: Optional reason for the state.

        Returns:
            Updated OperatorRequest.
        """
        update = OperatorRequestUpdate(
            readiness_state=readiness_state,
            readiness_reason=reason,
        )
        return await self.update_request(request, update)

    async def mark_clarification_required(
        self,
        request: OperatorRequest,
        clarification_session_id: str,
        reason: str | None = None,
    ) -> OperatorRequest:
        """
        Mark the request as requiring clarification.

        Args:
            request: The operator request.
            clarification_session_id: The clarification session ID.
            reason: Optional reason.

        Returns:
            Updated OperatorRequest.
        """
        update = OperatorRequestUpdate(
            state=OperatorRequestState.CLARIFICATION_REQUIRED,
            readiness_state=ReadinessState.INSUFFICIENT,
            clarification_session_id=clarification_session_id,
            readiness_reason=reason or "Clarification required",
        )
        updated = await self.update_request(request, update)
        self._emit_event("operator.request.clarification_required", updated)
        return updated

    async def mark_ready(self, request: OperatorRequest) -> OperatorRequest:
        """
        Mark the request as ready for processing.

        Args:
            request: The operator request.

        Returns:
            Updated OperatorRequest.
        """
        update = OperatorRequestUpdate(
            state=OperatorRequestState.READY,
            readiness_state=ReadinessState.SUFFICIENT,
            readiness_reason=None,
        )
        updated = await self.update_request(request, update)
        self._emit_event("operator.request.ready", updated)
        return updated

    async def mark_blocked(self, request: OperatorRequest, reason: str) -> OperatorRequest:
        """
        Mark the request as blocked.

        Args:
            request: The operator request.
            reason: The blocking reason.

        Returns:
            Updated OperatorRequest.
        """
        update = OperatorRequestUpdate(
            state=OperatorRequestState.BLOCKED,
            error=reason,
        )
        updated = await self.update_request(request, update)
        self._emit_event("operator.request.blocked", updated)
        return updated

    async def mark_rejected(self, request: OperatorRequest, reason: str) -> OperatorRequest:
        """
        Mark the request as rejected.

        Args:
            request: The operator request.
            reason: The rejection reason.

        Returns:
            Updated OperatorRequest.
        """
        update = OperatorRequestUpdate(
            state=OperatorRequestState.REJECTED,
            error=reason,
        )
        updated = await self.update_request(request, update)
        self._emit_event("operator.request.rejected", updated)
        return updated

    def _generate_correlation_id(self) -> str:
        """Generate a new correlation ID."""
        raw = uuid.uuid4().hex
        return f"corr_{raw[:16]}"

    def _emit_event(self, event_type: str, request: OperatorRequest) -> None:
        """Emit an event for the request lifecycle transition."""
        if self._event_emitter is None:
            return
        try:
            # Schedule async emission without awaiting (fire-and-forget)
            # Event emission must never block the request flow.
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._event_emitter.emit(
                    event_type=event_type,
                    source="operator_request",
                    correlation_id=request.correlation_id,
                    causation_id=request.causation_id or request.request_id,
                    request_id=request.request_id,
                    task_id=request.task_id,
                    plan_id=None,
                    run_id=None,
                    execution_id=None,
                    actor=_build_actor(request.request_context),
                    payload=_build_event_payload(request),
                    metadata={"operation_type": request.operation_type},
                    sensitivity="internal",
                ))
            else:
                loop.run_until_complete(self._event_emitter.emit(
                    event_type=event_type,
                    source="operator_request",
                    correlation_id=request.correlation_id,
                    causation_id=request.causation_id or request.request_id,
                    request_id=request.request_id,
                    task_id=request.task_id,
                    plan_id=None,
                    run_id=None,
                    execution_id=None,
                    actor=_build_actor(request.request_context),
                    payload=_build_event_payload(request),
                    metadata={"operation_type": request.operation_type},
                    sensitivity="internal",
                ))
        except Exception:
            pass  # Event emission must never grant authority or break the flow


def _build_actor(request_context: RequestContext | None) -> dict[str, Any]:
    """Build actor dict from request context."""
    if request_context is None:
        return {"actor_type": "service", "actor_id": "system"}
    return {
        "actor_type": request_context.actor_type.value,
        "actor_id": request_context.actor_id,
        "user_id": request_context.user_id,
        "session_id": request_context.session_id,
    }


def _build_event_payload(request: OperatorRequest) -> dict[str, Any]:
    """Build bounded event payload."""
    return {
        "request_id": request.request_id,
        "conversation_id": request.conversation_id,
        "state": request.state.value,
        "operation_type": request.operation_type,
        "readiness_state": request.readiness_state.value,
        "project_id": request.project_id,
    }


class _DefaultNormalizer:
    """Default normalizer that delegates to RequestNormalizer."""

    def normalize(self, create: Any) -> Any:
        from app.operator_request.normalizer import RequestNormalizer
        normalizer = RequestNormalizer()
        return normalizer.normalize(create)


__all__ = ["OperatorRequestService"]
