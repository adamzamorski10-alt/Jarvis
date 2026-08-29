"""
Unit tests for Stage 5.4 — Audit Log.
Tests AuditEvent domain model, sanitize_payload(), AuditLogRepository CRUD,
filtering, pagination, UTC handling, payload security, and immutability.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect

from app.db.models.audit_event import AuditEventModel
from app.repositories.audit_log_repository import AuditLogRepository, make_audit_event
from app.tools.audit_models import (
    AUDIT_PAYLOAD_MAX_BYTES,
    AuditEvent,
    AuditEventType,
    sanitize_payload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_event(**kwargs) -> AuditEvent:
    defaults = dict(
        id="audit_test_001",
        event_type=AuditEventType.PERMISSION_EVALUATED,
        tool_id="files.read_file",
        action="read",
        decision="ALLOW",
        rule_id="RULE_04_GRANT_ALLOWED",
    )
    defaults.update(kwargs)
    return AuditEvent(**defaults)


# ===========================================================================
# 1. AuditEvent Domain Model
# ===========================================================================

def test_audit_event_valid_creation():
    """Test creating a valid AuditEvent with all required fields."""
    ev = _make_event()
    assert ev.id == "audit_test_001"
    assert ev.event_type == AuditEventType.PERMISSION_EVALUATED
    assert ev.tool_id == "files.read_file"
    assert ev.decision == "ALLOW"
    assert ev.timestamp.tzinfo is not None


def test_audit_event_immutability():
    """Test that AuditEvent is frozen — cannot be mutated after creation."""
    ev = _make_event()
    with pytest.raises((ValidationError, AttributeError, TypeError)):
        ev.decision = "DENY"


def test_audit_event_id_cannot_be_empty():
    """Test that empty id raises ValidationError."""
    with pytest.raises(ValidationError):
        AuditEvent(id="   ", event_type=AuditEventType.TOOL_CALL_REQUESTED)


def test_audit_event_all_optional_fields_none():
    """Test that all optional fields can be None without error."""
    ev = AuditEvent(id="audit_min", event_type=AuditEventType.TOOL_CALL_REQUESTED)
    assert ev.tool_id is None
    assert ev.request_id is None
    assert ev.decision is None
    assert ev.arguments is None
    assert ev.result is None
    assert ev.success is None


def test_audit_event_utc_timezone_enforcement():
    """Test that naive datetime timestamps are coerced to UTC-aware."""
    naive_dt = datetime(2024, 1, 15, 10, 30, 0)  # no tzinfo
    ev = AuditEvent(
        id="audit_tz",
        event_type=AuditEventType.PERMISSION_EVALUATED,
        timestamp=naive_dt,
    )
    assert ev.timestamp.tzinfo is not None
    assert ev.timestamp.tzinfo == timezone.utc


def test_audit_event_utc_aware_timestamp_preserved():
    """Test that a UTC-aware timestamp is preserved unchanged."""
    utc_dt = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    ev = AuditEvent(
        id="audit_utc",
        event_type=AuditEventType.TOOL_EXECUTION_STARTED,
        timestamp=utc_dt,
    )
    assert ev.timestamp == utc_dt


def test_audit_event_non_utc_timestamp_converted_to_utc():
    """Test that aware non-UTC timestamps are converted to UTC."""
    plus_two = timezone(timedelta(hours=2))
    ev = AuditEvent(
        id="audit_non_utc",
        event_type=AuditEventType.TOOL_EXECUTION_STARTED,
        timestamp=datetime(2024, 6, 1, 12, 0, 0, tzinfo=plus_two),
    )
    assert ev.timestamp == datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc)


# ===========================================================================
# 2. All Event Types
# ===========================================================================

def test_all_event_types_valid():
    """Test that all AuditEventType variants can be used to create AuditEvents."""
    for et in AuditEventType:
        ev = AuditEvent(id=f"audit_{et.value}", event_type=et)
        assert ev.event_type == et


def test_audit_event_type_enum_values():
    """Test that all required event type enum values are present."""
    required = {
        "TOOL_CALL_REQUESTED",
        "PERMISSION_EVALUATED",
        "PERMISSION_GRANTED",
        "PERMISSION_DENIED",
        "APPROVAL_REQUESTED",
        "APPROVAL_GRANTED",
        "APPROVAL_DENIED",
        "TOOL_EXECUTION_STARTED",
        "TOOL_EXECUTION_SUCCEEDED",
        "TOOL_EXECUTION_FAILED",
        "TOOL_EXECUTION_TIMEOUT",
        "TOOL_EXECUTION_CANCELLED",
    }
    actual = {et.value for et in AuditEventType}
    assert required.issubset(actual)


@pytest.mark.asyncio
async def test_audit_events_table_schema_and_indexes(engine):
    """Test that SQLite table has requested columns and search indexes."""
    async with engine.begin() as conn:
        def inspect_schema(connection):
            inspector = inspect(connection)
            columns = {col["name"] for col in inspector.get_columns("audit_events")}
            indexes = {idx["name"] for idx in inspector.get_indexes("audit_events")}
            return columns, indexes

        columns, indexes = await conn.run_sync(inspect_schema)

    assert set(AuditEventModel.__table__.columns.keys()).issubset(columns)
    assert {
        "id",
        "timestamp",
        "event_type",
        "request_id",
        "tool_id",
        "action",
        "risk_level",
        "autonomy_mode",
        "decision",
        "rule_id",
        "grant_id",
        "execution_status",
        "success",
        "error_code",
        "error_message",
        "execution_time_ms",
        "arguments",
        "result",
    }.issubset(columns)
    assert {
        "ix_audit_events_timestamp",
        "ix_audit_events_event_type",
        "ix_audit_events_request_id",
        "ix_audit_events_tool_id",
        "ix_audit_events_decision",
        "ix_audit_events_grant_id",
    }.issubset(indexes)


# ===========================================================================
# 3. sanitize_payload() Security
# ===========================================================================

def test_sanitize_payload_none_returns_none():
    """Test that sanitize_payload(None) returns None."""
    assert sanitize_payload(None) is None


def test_sanitize_payload_simple_dict():
    """Test that a safe dict is serialized as JSON."""
    result = sanitize_payload({"tool_id": "files.read_file", "action": "read"})
    assert result is not None
    parsed = json.loads(result)
    assert parsed["tool_id"] == "files.read_file"


def test_sanitize_payload_redacts_password():
    """Test that 'password' key is redacted from payload."""
    result = sanitize_payload({"user": "admin", "password": "supersecret123"})
    parsed = json.loads(result)
    assert parsed["password"] == "[REDACTED]"
    assert parsed["user"] == "admin"


def test_sanitize_payload_redacts_token():
    """Test that 'token' and 'api_key' keys are redacted."""
    result = sanitize_payload({"api_key": "sk-abc123", "bearer_token": "eyJhbGc..."})
    parsed = json.loads(result)
    assert parsed["api_key"] == "[REDACTED]"
    assert parsed["bearer_token"] == "[REDACTED]"


def test_sanitize_payload_redacts_nested_secrets():
    """Test that nested dicts also have secrets redacted."""
    result = sanitize_payload({
        "config": {"auth_token": "secret", "host": "localhost"}
    })
    parsed = json.loads(result)
    assert parsed["config"]["auth_token"] == "[REDACTED]"
    assert parsed["config"]["host"] == "localhost"


def test_sanitize_payload_truncates_large_data():
    """Test that payloads exceeding max bytes are truncated and marked."""
    large_value = "x" * (AUDIT_PAYLOAD_MAX_BYTES * 2)
    result = sanitize_payload({"data": large_value})
    assert result is not None
    # Must fit within reasonable overhead
    assert len(result.encode("utf-8")) <= AUDIT_PAYLOAD_MAX_BYTES + 256
    parsed = json.loads(result)
    assert parsed.get("__truncated__") is True


def test_sanitize_payload_non_serializable_safe_fallback():
    """Test that non-JSON-serializable objects don't raise exceptions."""
    class Unserializable:
        pass

    # Should not raise, should return a safe fallback or use default=str
    result = sanitize_payload({"obj": Unserializable()})
    assert result is not None
    parsed = json.loads(result)
    assert isinstance(parsed, dict)


def test_sanitize_payload_non_dict_input():
    """Test that non-dict inputs (list, string) are handled safely."""
    result = sanitize_payload(["item1", "item2"])
    assert result is not None
    parsed = json.loads(result)
    assert isinstance(parsed, list)


# ===========================================================================
# 4. make_audit_event() Factory
# ===========================================================================

def test_make_audit_event_factory():
    """Test the make_audit_event factory applies sanitize_payload to arguments/result."""
    ev = make_audit_event(
        AuditEventType.PERMISSION_EVALUATED,
        tool_id="files.write_file",
        action="write",
        decision="ALLOW",
        arguments={"path": "/tmp/test.txt", "password": "secret"},
        result={"bytes_written": 128},
    )
    assert ev.event_type == AuditEventType.PERMISSION_EVALUATED
    assert ev.tool_id == "files.write_file"

    # arguments must be serialized with redaction applied
    args = json.loads(ev.arguments)
    assert args["password"] == "[REDACTED]"
    assert args["path"] == "/tmp/test.txt"

    # result should be properly serialized
    res = json.loads(ev.result)
    assert res["bytes_written"] == 128


# ===========================================================================
# 5. Repository — record() / get()
# ===========================================================================

@pytest.mark.asyncio
async def test_repository_record_and_get(session):
    """Test recording and retrieving an AuditEvent."""
    ev = make_audit_event(
        AuditEventType.PERMISSION_EVALUATED,
        tool_id="files.read_file",
        action="read",
        decision="ALLOW",
        rule_id="RULE_04_GRANT_ALLOWED",
    )
    repo = AuditLogRepository(session)
    recorded = await repo.record(ev)
    assert recorded.id == ev.id

    fetched = await repo.get(ev.id)
    assert fetched is not None
    assert fetched.id == ev.id
    assert fetched.event_type == AuditEventType.PERMISSION_EVALUATED
    assert fetched.tool_id == "files.read_file"
    assert fetched.decision == "ALLOW"


@pytest.mark.asyncio
async def test_repository_get_nonexistent_returns_none(session):
    """Test that getting a non-existent event returns None."""
    repo = AuditLogRepository(session)
    result = await repo.get("nonexistent_audit_id_xyz")
    assert result is None


@pytest.mark.asyncio
async def test_repository_record_rejects_non_audit_event(session):
    """Test that record() rejects non-AuditEvent objects."""
    repo = AuditLogRepository(session)
    with pytest.raises(TypeError):
        await repo.record({"id": "fake", "event_type": "PERMISSION_EVALUATED"})


# ===========================================================================
# 6. Repository — list() + Filtering
# ===========================================================================

@pytest.mark.asyncio
async def test_repository_list_filter_by_tool_id(session):
    """Test list() filters correctly by tool_id."""
    repo = AuditLogRepository(session)
    ev1 = make_audit_event(AuditEventType.PERMISSION_EVALUATED, tool_id="files.read_file", action="read")
    ev2 = make_audit_event(AuditEventType.PERMISSION_EVALUATED, tool_id="system.restart", action="restart")
    await repo.record(ev1)
    await repo.record(ev2)

    results = await repo.list(tool_id="files.read_file")
    tool_ids = {e.tool_id for e in results}
    assert "files.read_file" in tool_ids
    assert "system.restart" not in tool_ids


@pytest.mark.asyncio
async def test_repository_list_filter_by_request_id(session):
    """Test list() filters correctly by request_id."""
    rid = f"req_{id(object())}"
    repo = AuditLogRepository(session)
    ev = make_audit_event(AuditEventType.TOOL_CALL_REQUESTED, request_id=rid, tool_id="files.test")
    await repo.record(ev)

    results = await repo.list(request_id=rid)
    assert len(results) >= 1
    assert all(e.request_id == rid for e in results)


@pytest.mark.asyncio
async def test_repository_list_filter_by_event_type(session):
    """Test list() filters correctly by event_type."""
    repo = AuditLogRepository(session)
    ev1 = make_audit_event(AuditEventType.PERMISSION_GRANTED, tool_id="filter.test.1", action="read")
    ev2 = make_audit_event(AuditEventType.TOOL_EXECUTION_STARTED, tool_id="filter.test.2", action="exec")
    await repo.record(ev1)
    await repo.record(ev2)

    results = await repo.list(event_type=AuditEventType.PERMISSION_GRANTED, tool_id="filter.test.1")
    assert len(results) >= 1
    assert all(e.event_type == AuditEventType.PERMISSION_GRANTED for e in results)


@pytest.mark.asyncio
async def test_repository_list_filter_by_decision(session):
    """Test list() filters correctly by decision."""
    repo = AuditLogRepository(session)
    ev_allow = make_audit_event(AuditEventType.PERMISSION_GRANTED, tool_id="decision.test.1", decision="ALLOW")
    ev_deny = make_audit_event(AuditEventType.PERMISSION_DENIED, tool_id="decision.test.2", decision="DENY")
    await repo.record(ev_allow)
    await repo.record(ev_deny)

    results = await repo.list(tool_id="decision.test.1", decision="ALLOW")
    assert len(results) >= 1
    assert all(e.decision == "ALLOW" for e in results)

    results_deny = await repo.list(tool_id="decision.test.2", decision="DENY")
    assert len(results_deny) >= 1
    assert all(e.decision == "DENY" for e in results_deny)


@pytest.mark.asyncio
async def test_repository_list_filter_by_time_range(session):
    """Test list() filters correctly by from_timestamp/to_timestamp."""
    now = datetime.now(timezone.utc)
    past = now - timedelta(hours=2)
    future = now + timedelta(hours=2)

    repo = AuditLogRepository(session)
    ev = make_audit_event(AuditEventType.PERMISSION_EVALUATED, tool_id="time.range.test")
    await repo.record(ev)

    # Within range
    results_in = await repo.list(
        tool_id="time.range.test",
        from_timestamp=past,
        to_timestamp=future,
    )
    assert len(results_in) >= 1

    # Completely before event timestamp
    results_before = await repo.list(
        tool_id="time.range.test",
        from_timestamp=future,
        to_timestamp=future + timedelta(hours=1),
    )
    assert len(results_before) == 0


# ===========================================================================
# 7. Deterministic Ordering
# ===========================================================================

@pytest.mark.asyncio
async def test_repository_list_deterministic_order(session):
    """Test list() returns events ordered by timestamp ascending."""
    rid = f"req_order_{id(object())}"
    repo = AuditLogRepository(session)
    for i in range(5):
        ev = make_audit_event(
            AuditEventType.TOOL_CALL_REQUESTED,
            request_id=rid,
            tool_id=f"order.test.{i}",
        )
        await repo.record(ev)

    results = await repo.list(request_id=rid)
    timestamps = [e.timestamp for e in results]
    assert timestamps == sorted(timestamps)


# ===========================================================================
# 8. Pagination
# ===========================================================================

@pytest.mark.asyncio
async def test_repository_list_pagination(session):
    """Test list() pagination with limit and offset."""
    rid = f"req_page_{id(object())}"
    repo = AuditLogRepository(session)
    for i in range(10):
        ev = make_audit_event(AuditEventType.PERMISSION_EVALUATED, request_id=rid, tool_id=f"page.tool.{i}")
        await repo.record(ev)

    page1 = await repo.list(request_id=rid, limit=3, offset=0)
    page2 = await repo.list(request_id=rid, limit=3, offset=3)

    assert len(page1) == 3
    assert len(page2) == 3
    ids1 = {e.id for e in page1}
    ids2 = {e.id for e in page2}
    assert ids1.isdisjoint(ids2)


@pytest.mark.asyncio
async def test_repository_list_limit_capped_at_max(session):
    """Test list() clamps limit to MAX_LIMIT (500)."""
    repo = AuditLogRepository(session)
    # Should not raise, limit is clamped
    results = await repo.list(limit=999999)
    assert isinstance(results, tuple)


# ===========================================================================
# 9. count()
# ===========================================================================

@pytest.mark.asyncio
async def test_repository_count(session):
    """Test count() returns correct total matching filter."""
    rid = f"req_count_{id(object())}"
    repo = AuditLogRepository(session)
    for i in range(4):
        await repo.record(make_audit_event(AuditEventType.PERMISSION_EVALUATED, request_id=rid, tool_id=f"count.tool.{i}"))

    total = await repo.count(request_id=rid)
    assert total == 4

    count_filtered = await repo.count(request_id=rid, event_type=AuditEventType.PERMISSION_EVALUATED)
    assert count_filtered == 4

    count_zero = await repo.count(request_id=rid, event_type=AuditEventType.TOOL_EXECUTION_FAILED)
    assert count_zero == 0


# ===========================================================================
# 10. delete_before()
# ===========================================================================

@pytest.mark.asyncio
async def test_repository_delete_before(session):
    """Test delete_before() removes events older than cutoff timestamp."""
    rid = f"req_delete_{id(object())}"
    repo = AuditLogRepository(session)
    ev = make_audit_event(AuditEventType.PERMISSION_EVALUATED, request_id=rid, tool_id="delete.before.tool")
    await repo.record(ev)

    # Delete events created strictly before now+1h (should delete ev)
    cutoff = datetime.now(timezone.utc) + timedelta(hours=1)
    deleted = await repo.delete_before(cutoff)
    assert deleted >= 1

    # Event should no longer be findable
    fetched = await repo.get(ev.id)
    assert fetched is None


# ===========================================================================
# 11. UTC Storage
# ===========================================================================

@pytest.mark.asyncio
async def test_repository_utc_preserved_round_trip(session):
    """Test that UTC-aware timestamps survive a round-trip through SQLite."""
    utc_dt = datetime(2024, 6, 15, 8, 0, 0, tzinfo=timezone.utc)
    ev = AuditEvent(
        id=f"audit_utc_rt_{id(object())}",
        event_type=AuditEventType.PERMISSION_EVALUATED,
        timestamp=utc_dt,
        tool_id="files.read_file",
    )
    repo = AuditLogRepository(session)
    await repo.record(ev)
    fetched = await repo.get(ev.id)

    assert fetched is not None
    assert fetched.timestamp.tzinfo is not None
    # Timestamps must be equivalent (both UTC-aware, same moment)
    assert fetched.timestamp.replace(tzinfo=timezone.utc) == utc_dt


# ===========================================================================
# 12. Immutability / Isolation
# ===========================================================================

@pytest.mark.asyncio
async def test_events_are_isolated(session):
    """Test that two different events in the same session are completely independent."""
    repo = AuditLogRepository(session)
    ev1 = make_audit_event(AuditEventType.PERMISSION_GRANTED, tool_id="iso.tool.1", decision="ALLOW")
    ev2 = make_audit_event(AuditEventType.PERMISSION_DENIED, tool_id="iso.tool.2", decision="DENY")
    await repo.record(ev1)
    await repo.record(ev2)

    fetched1 = await repo.get(ev1.id)
    fetched2 = await repo.get(ev2.id)

    assert fetched1.tool_id == "iso.tool.1"
    assert fetched2.tool_id == "iso.tool.2"
    assert fetched1.id != fetched2.id
    assert fetched1.decision != fetched2.decision


def test_fetched_event_immutability():
    """Test that AuditEvent objects built via model_construct are still frozen."""
    ev = AuditEvent.model_construct(
        id="audit_immutable",
        event_type=AuditEventType.PERMISSION_EVALUATED,
        decision="ALLOW",
        timestamp=datetime.now(timezone.utc),
    )
    with pytest.raises((ValidationError, AttributeError, TypeError)):
        ev.decision = "DENY"


# ===========================================================================
# 13. Payload Security — explicit tests
# ===========================================================================

def test_payload_redaction_case_insensitive():
    """Test that sensitive key detection is case-insensitive."""
    result = sanitize_payload({"PASSWORD": "hidden", "Token": "abc", "normal_key": "value"})
    parsed = json.loads(result)
    assert parsed["PASSWORD"] == "[REDACTED]"
    assert parsed["Token"] == "[REDACTED]"
    assert parsed["normal_key"] == "value"


def test_payload_does_not_accidentally_redact_normal_keys():
    """Test that normal tool-related keys are NOT redacted."""
    result = sanitize_payload({
        "tool_id": "files.read",
        "action": "read",
        "path": "/home/user/file.txt",
        "bytes": 512,
    })
    parsed = json.loads(result)
    assert parsed["tool_id"] == "files.read"
    assert parsed["action"] == "read"
    assert parsed["path"] == "/home/user/file.txt"
    assert parsed["bytes"] == 512


def test_payload_large_value_truncation_marks_clearly():
    """Test that truncated payloads contain the __truncated__ marker for consumers."""
    oversized = {"key": "x" * (AUDIT_PAYLOAD_MAX_BYTES * 3)}
    result = sanitize_payload(oversized)
    parsed = json.loads(result)
    assert "__truncated__" in parsed
    assert parsed["__truncated__"] is True
    assert "__original_bytes__" in parsed
