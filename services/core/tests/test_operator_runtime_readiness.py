"""Focused tests for the Stage 22 Operator Runtime readiness handoff."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.services.ai_service import AIService
from app.operator_request.models import OperatorRequestUpdate


def _make_ai(operator_request_service) -> AIService:
    return AIService(
        providers=MagicMock(),
        conversations=MagicMock(),
        activity=MagicMock(),
        settings=MagicMock(),
        tool_execution_service=MagicMock(),
        approval_service=MagicMock(),
        operator_request_service=operator_request_service,
    )


@pytest.mark.asyncio
async def test_readiness_handoff_promotes_request_after_sufficiency():
    service = MagicMock()
    normalized = object()
    ready = object()
    service.update_request = AsyncMock(return_value=normalized)
    service.mark_ready = AsyncMock(return_value=ready)
    ai = _make_ai(service)

    result = await ai._promote_operator_request_to_ready(
        operator_request=object(),
        assembled_context={"success": True},
    )

    assert result is ready
    service.update_request.assert_awaited_once()
    update = service.update_request.await_args.args[1]
    assert isinstance(update, OperatorRequestUpdate)
    assert update.assembled_context == {"success": True}
    service.mark_ready.assert_awaited_once_with(normalized)


@pytest.mark.asyncio
async def test_readiness_handoff_fails_closed_on_service_error():
    service = MagicMock()
    service.update_request = AsyncMock(side_effect=RuntimeError("persistence unavailable"))
    service.mark_ready = AsyncMock()
    ai = _make_ai(service)

    result = await ai._promote_operator_request_to_ready(
        operator_request=object(),
        assembled_context=None,
    )

    assert result is None
    service.mark_ready.assert_not_awaited()
