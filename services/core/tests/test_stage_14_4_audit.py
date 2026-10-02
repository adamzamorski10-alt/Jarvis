"""
Stage 14.4 — Focused Audit Tests for Idempotency and Correlation

These tests verify the exact behavior questions raised in the audit:
1. Can duplicate historical events be created through retries?
2. Is correlation/causation handled correctly?
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.database import Base, init_db
from app.events.emitter import EventEmitter
from app.events.repository import EventRepository
from app.events.schemas import EventType
from app.memory.models import MemoryCategory, MemoryEntry, MemoryScope, TrustLevel, ProvenanceSource
from app.memory.repository import MemoryRepository
from app.memory.service import CallerType, MemoryCaller, MemoryService


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


_USER = MemoryCaller(CallerType.USER, "user_1")


class TestIdempotencyDuplicateEvents:
    """Verify that the current API cannot produce duplicate events for the same memory."""

    @pytest.mark.asyncio
    async def test_two_creates_with_same_content_produce_different_memories_and_events(self):
        """Two create() calls with same content produce two distinct memories and two events."""
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
                        content=_make_content("same"),
                    )
                    mem2 = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.PROJECT_KNOWLEDGE,
                        scope=MemoryScope.GLOBAL,
                        content=_make_content("same"),
                    )
                    await session.commit()

                    assert mem1.memory_id != mem2.memory_id

                    events_mem1 = await event_repo.list(correlation_id=mem1.memory_id)
                    events_mem2 = await event_repo.list(correlation_id=mem2.memory_id)

                    assert len(events_mem1) == 1
                    assert len(events_mem2) == 1
                    assert events_mem1[0].event_id != events_mem2[0].event_id
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_two_updates_on_same_memory_produce_two_distinct_events(self):
        """Two update() calls on the same memory produce two distinct events."""
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

                    await svc.update(_USER, mem.memory_id, {"confidence": 0.5})
                    await svc.update(_USER, mem.memory_id, {"confidence": 0.3})
                    await session.commit()

                    events = await event_repo.list(correlation_id=mem.memory_id)
                    assert len(events) == 3  # created + 2 updates
                    update_events = [e for e in events if e.event_type == EventType.MEMORY_UPDATED]
                    assert len(update_events) == 2
                    assert update_events[0].event_id != update_events[1].event_id
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_retried_create_after_rollback_creates_new_memory_and_event(self):
        """After rollback, a retried create() creates a new memory and event."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                # First attempt - rollback
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
                    await session.rollback()

                # Second attempt - commit
                async with factory() as session:
                    event_repo = EventRepository(session)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    mem2 = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.PROJECT_KNOWLEDGE,
                        scope=MemoryScope.GLOBAL,
                        content=_make_content(),
                    )
                    await session.commit()

                    repo = MemoryRepository(session)
                    entries, total = await repo.list()
                    assert total == 1

                    events = await event_repo.list()
                    assert len(events) == 1
                    assert events[0].correlation_id == mem2.memory_id
            finally:
                await _dispose_engine(engine)


class TestCorrelationCausation:
    """Verify correlation_id and causation_id handling for Memory events."""

    @pytest.mark.asyncio
    async def test_memory_create_uses_memory_id_as_correlation(self):
        """create() event uses the new memory_id as correlation_id."""
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
                    assert events[0].correlation_id == mem.memory_id
                    assert events[0].causation_id is None
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_memory_update_uses_memory_id_as_correlation(self):
        """update() event uses the memory_id as correlation_id."""
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

                    await svc.update(_USER, mem.memory_id, {"confidence": 0.5})
                    await session.commit()

                    events = await event_repo.list(correlation_id=mem.memory_id)
                    assert len(events) == 2
                    for evt in events:
                        assert evt.correlation_id == mem.memory_id
                        assert evt.causation_id is None
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_memory_delete_uses_memory_id_as_correlation(self):
        """delete() event uses the memory_id as correlation_id."""
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

                    await svc.delete(_USER, mem.memory_id)
                    await session.commit()

                    events = await event_repo.list(correlation_id=mem.memory_id)
                    assert len(events) == 2  # created + deleted
                    for evt in events:
                        assert evt.correlation_id == mem.memory_id
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_supersede_correlation_uses_old_memory_id(self):
        """supersede() event uses the old memory_id as correlation_id."""
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

                    new = await svc.supersede(_USER, old.memory_id, _make_content("new"))
                    await session.commit()

                    # The supersede event should be correlated with the OLD memory
                    old_events = await event_repo.list(correlation_id=old.memory_id)
                    assert len(old_events) == 2  # created + superseded
                    supersede_event = [e for e in old_events if e.event_type == EventType.MEMORY_SUPERSEDED][0]
                    assert supersede_event.correlation_id == old.memory_id
                    assert supersede_event.payload["superseded_by"] == new.memory_id

                    # The new memory does not get a separate MEMORY_CREATED event during
                    # supersede (the new memory is created via MemoryRepository.create(),
                    # not MemoryService.create()). This is a V1 limitation.
                    new_events = await event_repo.list(correlation_id=new.memory_id)
                    assert len(new_events) == 0  # No events for the new memory itself
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_memory_events_have_no_causation_id(self):
        """Memory events do not set causation_id (V1 limitation)."""
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
                    for evt in events:
                        assert evt.causation_id is None
            finally:
                await _dispose_engine(engine)


class TestHistoryVerification:
    """Verify history retrieval for memory events."""

    @pytest.mark.asyncio
    async def test_get_correlation_history_returns_memory_events(self):
        """HistoryService.get_correlation_history() returns all memory events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    task_repo = None  # HistoryService works without task_repo
                    from app.events.history import HistoryService
                    history_svc = HistoryService(event_repository=event_repo, task_repository=task_repo)
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

                    page = await history_svc.get_correlation_history(mem.memory_id)
                    assert page.total == 3
                    assert page.entries[0].event_type == EventType.MEMORY_CREATED
                    assert page.entries[1].event_type == EventType.MEMORY_UPDATED
                    assert page.entries[2].event_type == EventType.MEMORY_DELETED
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_get_memory_history_not_needed_correlation_works(self):
        """Verify that get_correlation_history(memory_id) already provides memory history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            factory, engine = await _init_db(db_path)
            try:
                async with factory() as session:
                    event_repo = EventRepository(session)
                    from app.events.history import HistoryService
                    history_svc = HistoryService(event_repo)
                    emitter = EventEmitter(event_repo)
                    svc = MemoryService(session, event_emitter=emitter)

                    mem = await svc.create(
                        caller=_USER,
                        category=MemoryCategory.PROJECT_KNOWLEDGE,
                        scope=MemoryScope.GLOBAL,
                        content=_make_content(),
                    )
                    await session.commit()

                    # get_correlation_history already returns memory events
                    page = await history_svc.get_correlation_history(mem.memory_id)
                    assert page.total == 1
                    assert page.entries[0].event_type == EventType.MEMORY_CREATED
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_rolled_back_operations_absent_from_history(self):
        """Rolled-back memory operations do not appear in history."""
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


class TestEventIdempotencyMechanism:
    """Verify the EventRepository idempotency mechanism."""

    @pytest.mark.asyncio
    async def test_same_event_id_same_content_is_idempotent(self):
        """Re-emitting the same event_id with same content returns existing record."""
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

                    original_events = await event_repo.list(correlation_id=mem.memory_id)
                    original_event_id = original_events[0].event_id
                    original_payload = original_events[0].payload

                    # Re-emit with same event_id and same content
                    result = await emitter.emit(
                        event_type=EventType.MEMORY_CREATED,
                        source="memory",
                        correlation_id=mem.memory_id,
                        actor=_USER.caller_id,
                        payload=original_payload,
                        event_id=original_event_id,
                    )
                    await session.commit()

                    assert result.event_id == original_event_id
                    events = await event_repo.list(correlation_id=mem.memory_id)
                    assert len(events) == 1  # No duplicate
            finally:
                await _dispose_engine(engine)

    @pytest.mark.asyncio
    async def test_same_event_id_different_content_raises_conflict(self):
        """Re-emitting the same event_id with different content raises EventIntegrityConflict."""
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

                    original_events = await event_repo.list(correlation_id=mem.memory_id)
                    original_event_id = original_events[0].event_id

                    from app.events.repository import EventIntegrityConflict
                    with pytest.raises(EventIntegrityConflict):
                        await emitter.emit(
                            event_type=EventType.MEMORY_CREATED,
                            source="memory",
                            correlation_id=mem.memory_id,
                            actor=_USER.caller_id,
                            payload={"memory_id": mem.memory_id, "category": "different"},
                            event_id=original_event_id,
                        )
            finally:
                await _dispose_engine(engine)


__all__ = [
    "TestIdempotencyDuplicateEvents",
    "TestCorrelationCausation",
    "TestHistoryVerification",
    "TestEventIdempotencyMechanism",
]
