"""
Integration tests: PermissionEngine → AuditLogService → audit_events.
Fail-soft: audit I/O must not change the computed PermissionDecision.
"""
from unittest.mock import AsyncMock

import pytest

from app.repositories.audit_log_repository import AuditLogRepository
from app.tools.audit_models import AuditEventType
from app.tools.audit_service import AuditLogService
from app.tools.models import (
    AutonomyMode,
    GrantType,
    PermissionDecisionType,
    PermissionEvaluationRequest,
    RiskLevel,
)
from app.tools.permission_engine import PermissionEngine
from tests.test_permission_engine import _create_test_registry


@pytest.mark.asyncio
async def test_evaluate_allow_writes_audit_event(session):
    repo = AuditLogRepository(session)
    audit = AuditLogService(repo)
    reg = _create_test_registry()
    engine = PermissionEngine(registry=reg, audit_service=audit)

    req = PermissionEvaluationRequest(
        tool_id="system.get_time",
        risk_level=RiskLevel.SAFE,
        action="read",
        autonomy_mode=AutonomyMode.MANUAL,
        arguments={"unit": "utc"},
        context={"request_id": "req_allow_1"},
    )
    decision = await engine.evaluate(req)
    assert decision.decision == PermissionDecisionType.ALLOW

    events = await repo.list(request_id="req_allow_1")
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == AuditEventType.PERMISSION_GRANTED
    assert ev.tool_id == "system.get_time"
    assert ev.action == "read"
    assert ev.risk_level == RiskLevel.SAFE.value
    assert ev.autonomy_mode == AutonomyMode.MANUAL.value
    assert ev.decision == PermissionDecisionType.ALLOW.value
    assert ev.reason == decision.reason
    assert ev.rule_id == decision.rule_id
    assert ev.request_id == "req_allow_1"
    assert ev.grant_id is None


@pytest.mark.asyncio
async def test_evaluate_deny_writes_audit_event(session):
    repo = AuditLogRepository(session)
    engine = PermissionEngine(
        registry=_create_test_registry(),
        audit_service=AuditLogService(repo),
    )
    req = PermissionEvaluationRequest(
        tool_id="unknown.tool_id",
        risk_level=RiskLevel.SAFE,
        action="read",
        context={"request_id": "req_deny_1"},
    )
    decision = await engine.evaluate(req)
    assert decision.decision == PermissionDecisionType.DENY

    events = await repo.list(request_id="req_deny_1")
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == AuditEventType.PERMISSION_DENIED
    assert ev.tool_id == "unknown.tool_id"
    assert ev.action == "read"
    assert ev.decision == "DENY"
    assert ev.reason == decision.reason
    assert ev.rule_id == "RULE_01_TOOL_NOT_FOUND"
    assert ev.request_id == "req_deny_1"


@pytest.mark.asyncio
async def test_evaluate_ask_user_writes_audit_event(session):
    repo = AuditLogRepository(session)
    engine = PermissionEngine(
        registry=_create_test_registry(),
        audit_service=AuditLogService(repo),
    )
    req = PermissionEvaluationRequest(
        tool_id="files.read_file",
        risk_level=RiskLevel.LOW,
        action="read",
        autonomy_mode=AutonomyMode.MANUAL,
        context={"request_id": "req_ask_1"},
    )
    decision = await engine.evaluate(req)
    assert decision.decision == PermissionDecisionType.ASK_USER

    events = await repo.list(request_id="req_ask_1")
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == AuditEventType.PERMISSION_EVALUATED
    assert ev.tool_id == "files.read_file"
    assert ev.action == "read"
    assert ev.risk_level == RiskLevel.LOW.value
    assert ev.autonomy_mode == AutonomyMode.MANUAL.value
    assert ev.decision == "ASK_USER"
    assert ev.reason == decision.reason
    assert ev.rule_id == "RULE_07_MANUAL_ASK_USER"
    assert ev.request_id == "req_ask_1"


@pytest.mark.asyncio
async def test_evaluate_allow_by_grant_records_grant_id(session):
    from app.repositories.grant_repository import GrantRepository

    grant_repo = GrantRepository(session)
    audit_repo = AuditLogRepository(session)
    grant = await grant_repo.create_grant(
        tool_id="files.write_file",
        action_pattern="write",
        grant_type=GrantType.PERSISTENT,
    )
    engine = PermissionEngine(
        registry=_create_test_registry(),
        grant_repo=grant_repo,
        audit_service=AuditLogService(audit_repo),
    )
    req = PermissionEvaluationRequest(
        tool_id="files.write_file",
        risk_level=RiskLevel.MEDIUM,
        action="write",
        autonomy_mode=AutonomyMode.MANUAL,
        context={"request_id": "req_grant_1"},
    )
    decision = await engine.evaluate(req)
    assert decision.decision == PermissionDecisionType.ALLOW
    assert decision.grant_id == grant.id

    events = await audit_repo.list(request_id="req_grant_1")
    assert len(events) == 1
    assert events[0].grant_id == grant.id
    assert events[0].decision == "ALLOW"
    assert events[0].reason == decision.reason


@pytest.mark.asyncio
async def test_evaluate_fail_soft_when_audit_record_raises():
    """AuditLogService.record raising must not change or re-raise the decision."""
    boom = AsyncMock(side_effect=RuntimeError("audit sqlite failure"))
    audit = AuditLogService(repo=AsyncMock())
    audit.record = boom

    engine = PermissionEngine(registry=_create_test_registry(), audit_service=audit)
    req = PermissionEvaluationRequest(
        tool_id="system.get_time",
        risk_level=RiskLevel.SAFE,
        action="read",
        autonomy_mode=AutonomyMode.MANUAL,
    )
    decision = await engine.evaluate(req)
    assert decision.decision == PermissionDecisionType.ALLOW
    assert decision.rule_id == "RULE_07_MANUAL_SAFE_ALLOWED"
    boom.assert_awaited()


@pytest.mark.asyncio
async def test_audit_service_swallows_repository_errors():
    class BoomRepo:
        async def record(self, event):
            raise RuntimeError("disk full")

    svc = AuditLogService(BoomRepo())
    result = await svc.record(
        AuditEventType.PERMISSION_DENIED,
        reason="swallow test",
        tool_id="files.read_file",
        action="read",
    )
    assert result is None
