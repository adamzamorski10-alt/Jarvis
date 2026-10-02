"""
Stage 14.4 — Memory ↔ Event/History Integration Tests

Covers:
  - Memory event types
  - Event payload contract
  - Event emission after successful mutations
  - Transaction semantics (failure injection)
  - Idempotency
  - History retrieval
  - Security: no secret leakage in events
  - Authority separation
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.database import Base, init_db
from app.events.emitter import EventEmitter, EventEmissionError
from app.events.repository import EventRepository
from app.events.schemas import EventType, Sensitivity
from app.memory.models import MemoryCategory, MemoryEntry, MemoryScope, TrustLevel, ProvenanceSource
from app.memory.repository import MemoryRepository
from app.memory.service import CallerType, MemoryCaller, MemoryService


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_session_factory(db_path: str):
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, connect_args={"check_same_thread": False})
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


async def _init_db(db_path: str):
    factory, engine = _make_session_factory(db_path)
    import app.db.database as db_mod
    old_engine = db_mod._engine
    db_mod._engine = engine
    try:
        await init_db()
    finally:
        db_mod._engine = old_engine
    return factory, engine


async def _dispose_engine(engine):
    if engine is not None:
        try:
            await engine.dispose()
        except Exception:
            pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _make_content(text: str = "value") -> dict[str, Any]:
    return {"key": text}


def _make_memory(**kwargs: Any) -> MemoryEntry:
    defaults: dict[str, Any] = {
        "category": MemoryCategory.PROJECT_KNOWLEDGE,
        "scope": MemoryScope.GLOBAL,
        "content": _make_content(),
        "trust_level": TrustLevel.DERIVED,
        "source": ProvenanceSource.AGENT,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "archived_at": None,
        "expires_at": None,
        "superseded_by": None,
        "deleted_at": None,
        "metadata": {"source_task": "task_123"},
    }
    defaults.update(kwargs)
    return MemoryEntry(**defaults)


_USER = MemoryCaller(CallerType.USER, "user_1")


# ── Event Type Tests ──────────────────────────────────────────────────────────


class TestMemoryEventTypes:
    def test_memory_event_types_exist(self):
        assert EventType.MEMORY_CREATED.value == "memory.created"
        assert EventType.MEMORY_UPDATED.value == "memory.updated"
        assert EventType.MEMORY_SUPERSEDED.value == "memory.superseded"
        assert EventType.MEMORY_DELETED.value == "memory.deleted"


# ── Event Payload Contract Tests ──────────────────────────────────────────────


class TestMemoryEventPayload:
    @pytest.mark.asyncio
    async def test_create_event_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    mem = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.PROJECT_KNOWLEDGE,
                        scope=MemoryScope.GLOBAL,
                        content=_make_content(),
                    )
                    await session.commit()

                    events = await event_repo.list(correlation_id=mem.memory_id)
                    assert len(events) == 1
                    event = events[0]
                    assert event.event_type == EventType.MEMORY_CREATED
                    assert event.source == "memory"
                    assert event.correlation_id == mem.memory_id
                    payload = event.payload or {}
                    assert payload["memory_id"] == mem.memory_id
                    assert payload["category"] == "project_knowledge"
                    assert payload["scope"] == "global"
                    assert payload["trust_level"] == "DERIVED"
                    assert payload["source"] == "event"
                    assert "content" not in payload
                    assert "metadata" not in payload
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_update_event_payload_contains_changed_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    mem = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.PROJECT_KNOWLEDGE,
                        scope=MemoryScope.GLOBAL,
                        content=_make_content(),
                    )
                    await session.commit()

                    await svc.update(
                        _USER,
                        mem.memory_id,
                        {"confidence": 0.7, "metadata": {"note": "x"}},
                    )
                    await session.commit()

                    events = await event_repo.list(correlation_id=mem.memory_id)
                    assert len(events) == 2
                    update_event = events[-1]
                    assert update_event.event_type == EventType.MEMORY_UPDATED
                    payload = update_event.payload or {}
                    assert payload["memory_id"] == mem.memory_id
                    assert set(payload["changed_fields"]) == {"confidence", "metadata"}
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_delete_event_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    mem = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.PROJECT_KNOWLEDGE,
                        scope=MemoryScope.GLOBAL,
                        content=_make_content(),
                    )
                    await svc.delete(_USER, mem.memory_id)
                    await session.commit()

                    events = await event_repo.list(correlation_id=mem.memory_id)
                    assert len(events) == 2  # created + deleted
                    delete_event = events[-1]
                    assert delete_event.event_type == EventType.MEMORY_DELETED
                    payload = delete_event.payload or {}
                    assert payload["memory_id"] == mem.memory_id
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_supersede_event_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    old = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.PROJECT_KNOWLEDGE,
                        scope=MemoryScope.GLOBAL,
                        content=_make_content("old"),
                    )
                    new = await svc.supersede(_USER, old.memory_id, _make_content("new"))
                    await session.commit()

                    events = await event_repo.list(correlation_id=old.memory_id)
                    assert len(events) == 2  # created + superseded
                    supersede_event = events[-1]
                    assert supersede_event.event_type == EventType.MEMORY_SUPERSEDED
                    payload = supersede_event.payload or {}
                    assert payload["memory_id"] == old.memory_id
                    assert payload["superseded_by"] == new.memory_id
            finally:
                await _dispose_engine(engine)


# ── Transaction Semantics Tests ───────────────────────────────────────────────


class TestMemoryEventTransactionSemantics:
    @pytest.mark.asyncio
    async def test_successful_create_emits_event_and_commits(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    mem = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.PROJECT_KNOWLEDGE,
                        scope=MemoryScope.GLOBAL,
                        content=_make_content(),
                    )
                    await session.commit()

                async with factory() as session:
                    event_repo2 = EventRepository(session)
                    events = await event_repo2.list(correlation_id=mem.memory_id)
                    assert len(events) == 1
                    assert events[0].event_type == EventType.MEMORY_CREATED
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_rollback_after_create_removes_both_memory_and_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    mem = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.PROJECT_KNOWLEDGE,
                        scope=MemoryScope.GLOBAL,
                        content=_make_content(),
                    )
                    await session.rollback()

                async with factory() as session:
                    repo = MemoryRepository(session)
                    entries, total = await repo.list()
                    assert total == 0
                    event_repo2 = EventRepository(session)
                    events = await event_repo2.list()
                    assert len(events) == 0
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_event_emitter_failure_rolls_back_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    original_emit = emitter.emit

                    async def failing_emit(*args, **kwargs):
                        if kwargs.get("event_type") == EventType.MEMORY_CREATED:
                            raise EventEmissionError("forced event failure")
                        return await original_emit(*args, **kwargs)

                    with patch.object(emitter, "emit", failing_emit):
                        with pytest.raises(EventEmissionError, match="forced event failure"):
                            await svc.create(
                                caller=_USER,
                                category=MemoryCategory.PROJECT_KNOWLEDGE,
                                scope=MemoryScope.GLOBAL,
                                content=_make_content(),
                            )

                    await session.rollback()

                    repo = MemoryRepository(session)
                    entries, total = await repo.list()
                    assert total == 0
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_supersede_event_failure_rolls_back_both_memories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    old = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.PROJECT_KNOWLEDGE,
                        scope=MemoryScope.GLOBAL,
                        content=_make_content("old"),
                    )
                    await session.commit()

                    original_emit = emitter.emit

                    async def failing_emit(*args, **kwargs):
                        if kwargs.get("event_type") == EventType.MEMORY_SUPERSEDED:
                            raise EventEmissionError("forced supersede event failure")
                        return await original_emit(*args, **kwargs)

                    async with factory() as session:
                        event_repo2 = EventRepository(session)
                        emitter2 = EventEmitter(event_repo2)
                        svc2 = MemoryService(session, event_emitter=emitter2)
                        with patch.object(emitter2, "emit", failing_emit):
                            with pytest.raises(EventEmissionError, match="forced supersede event failure"):
                                await svc2.supersede(_USER, old.memory_id, _make_content("new"))
                        await session.rollback()

                        repo = MemoryRepository(session)
                        entries, total = await repo.list()
                        assert total == 1  # only the original old memory
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_update_failure_does_not_emit_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    mem = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.PROJECT_KNOWLEDGE,
                        scope=MemoryScope.GLOBAL,
                        content=_make_content(),
                    )
                    await session.commit()

                    try:
                        await svc.update(_USER, mem.memory_id, {"trust_level": TrustLevel.EXPLICIT_USER})
                    except ValueError:
                        pass
                    else:
                        pytest.fail("Expected MemoryError was not raised")

                    events = await event_repo.list(correlation_id=mem.memory_id)
                    assert len(events) == 1  # only created, no update event
                    assert events[0].event_type == EventType.MEMORY_CREATED
            finally:
                await _dispose_engine(engine)


# ── Idempotency Tests ─────────────────────────────────────────────────────────


class TestMemoryEventIdempotency:
    @pytest.mark.asyncio
    async def test_duplicate_create_event_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    mem1 = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.PROJECT_KNOWLEDGE,
                        scope=MemoryScope.GLOBAL,
                        content=_make_content(),
                    )
                    await session.commit()

                    # Capture the original event_id
                    original_events = await event_repo.list(correlation_id=mem1.memory_id)
                    original_event_id = original_events[0].event_id
                    original_payload = original_events[0].payload

                    # Retry with the same event_id and exact same content
                    await emitter.emit(
                        event_type=EventType.MEMORY_CREATED,
                        source="memory",
                        correlation_id=mem1.memory_id,
                        actor=_USER.caller_id,
                        payload=original_payload,
                        event_id=original_event_id,
                    )
                    await session.commit()

                    events = await event_repo.list(correlation_id=mem1.memory_id)
                    # Should still be 1 event (idempotent)
                    assert len(events) == 1
            finally:
                await _dispose_engine(engine)


# ── History Retrieval Tests ───────────────────────────────────────────────────


class TestMemoryHistoryRetrieval:
    @pytest.mark.asyncio
    async def test_memory_history_ordered_by_occurred_at(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    mem = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.PROJECT_KNOWLEDGE,
                        scope=MemoryScope.GLOBAL,
                        content=_make_content(),
                    )
                    await svc.update(_USER, mem.memory_id, {"confidence": 0.5})
                    await svc.delete(_USER, mem.memory_id)
                    await session.commit()

                    events = await event_repo.list(correlation_id=mem.memory_id)
                    assert len(events) == 3
                    assert events[0].event_type == EventType.MEMORY_CREATED
                    assert events[1].event_type == EventType.MEMORY_UPDATED
                    assert events[2].event_type == EventType.MEMORY_DELETED
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_rolled_back_operations_do_not_appear_in_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    mem = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.PROJECT_KNOWLEDGE,
                        scope=MemoryScope.GLOBAL,
                        content=_make_content(),
                    )
                    await session.rollback()

                async with factory() as session:
                    event_repo2 = EventRepository(session)
                    events = await event_repo2.list()
                    assert len(events) == 0
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_history_between_sees_memory_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    mem = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.PROJECT_KNOWLEDGE,
                        scope=MemoryScope.GLOBAL,
                        content=_make_content(),
                    )
                    await session.commit()

                    start = mem.created_at - __import__('datetime').timedelta(seconds=5)
                    end = mem.created_at + __import__('datetime').timedelta(seconds=5)
                    events = await event_repo.list(
                        from_occurred_at=start,
                        to_occurred_at=end,
                        event_type=EventType.MEMORY_CREATED,
                    )
                    assert len(events) == 1
                    assert events[0].correlation_id == mem.memory_id
            finally:
                await _dispose_engine(engine)


# ── Security Tests ────────────────────────────────────────────────────────────


class TestMemoryEventSecurity:
    @pytest.mark.asyncio
    async def test_event_payload_does_not_contain_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    mem = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.PROJECT_KNOWLEDGE,
                        scope=MemoryScope.GLOBAL,
                        content={"secret": "AKIAIOSFODNN7EXAMPLE", "cmd": "rm -rf /"},
                    )
                    await session.commit()

                    events = await event_repo.list(correlation_id=mem.memory_id)
                    assert len(events) == 1
                    payload = events[0].payload or {}
                    assert "content" not in payload
                    assert "metadata" not in payload
                    assert "secret" not in str(payload)
                    assert "rm -rf" not in str(payload)
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_event_payload_cannot_grant_authority(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    mem = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.USER_INSTRUCTION,
                        scope=MemoryScope.GLOBAL,
                        content={"authorization": "all_tools_allowed", "approve": "true"},
                    )
                    await session.commit()

                    events = await event_repo.list(correlation_id=mem.memory_id)
                    assert len(events) == 1
                    payload = events[0].payload or {}
                    assert "authorization" not in payload
                    assert "approve" not in payload
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_memory_event_cannot_trigger_execution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    mem = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.PROJECT_KNOWLEDGE,
                        scope=MemoryScope.GLOBAL,
                        content={"action": "execute_tool", "tool_id": "files.read_file"},
                    )
                    await session.commit()

                    events = await event_repo.list(correlation_id=mem.memory_id)
                    assert len(events) == 1
                    assert events[0].event_type == EventType.MEMORY_CREATED
                    assert not hasattr(svc, "execute_tool")
                    assert not hasattr(svc, "run_tool")
            finally:
                await _dispose_engine(engine)


# ── Failure Injection Tests ───────────────────────────────────────────────────


class TestMemoryEventFailureInjection:
    @pytest.mark.asyncio
    async def test_create_then_rollback_removes_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    mem = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.PROJECT_KNOWLEDGE,
                        scope=MemoryScope.GLOBAL,
                        content=_make_content(),
                    )
                    await session.rollback()

                async with factory() as session:
                    repo = MemoryRepository(session)
                    entries, total = await repo.list()
                    assert total == 0
                    event_repo2 = EventRepository(session)
                    events = await event_repo2.list()
                    assert len(events) == 0
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_update_event_failure_does_not_leave_inconsistent_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    mem = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.PROJECT_KNOWLEDGE,
                        scope=MemoryScope.GLOBAL,
                        content=_make_content(),
                    )
                    await session.commit()

                    original_emit = emitter.emit

                    async def failing_emit(*args, **kwargs):
                        if kwargs.get("event_type") == EventType.MEMORY_UPDATED:
                            raise EventEmissionError("forced update event failure")
                        return await original_emit(*args, **kwargs)

                    async with factory() as session:
                        event_repo2 = EventRepository(session)
                        emitter2 = EventEmitter(event_repo2)
                        svc2 = MemoryService(session, event_emitter=emitter2)
                        with patch.object(emitter2, "emit", failing_emit):
                            with pytest.raises(EventEmissionError):
                                await svc2.update(_USER, mem.memory_id, {"confidence": 0.5})
                        await session.rollback()

                        repo = MemoryRepository(session)
                        updated = await repo.get(mem.memory_id)
                        assert updated is not None
                        assert updated.confidence == 1.0  # unchanged
            finally:
                await _dispose_engine(engine)


# ── Correlation/Causation Tests ───────────────────────────────────────────────


class TestMemoryEventCorrelation:
    @pytest.mark.asyncio
    async def test_memory_events_share_correlation_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    mem = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.PROJECT_KNOWLEDGE,
                        scope=MemoryScope.GLOBAL,
                        content=_make_content(),
                    )
                    await svc.update(_USER, mem.memory_id, {"confidence": 0.5})
                    await svc.delete(_USER, mem.memory_id)
                    await session.commit()

                    events = await event_repo.list(correlation_id=mem.memory_id)
                    assert len(events) == 3
                    for event in events:
                        assert event.correlation_id == mem.memory_id
            finally:
                await _dispose_engine(engine)


__all__ = [
    "TestMemoryEventTypes",
    "TestMemoryEventPayload",
    "TestMemoryEventTransactionSemantics",
    "TestMemoryEventIdempotency",
    "TestMemoryHistoryRetrieval",
    "TestMemoryEventSecurity",
    "TestMemoryEventFailureInjection",
    "TestMemoryEventCorrelation",
]
