"""
Stage 11.3 — History Repository & Contract Tests

Covers:
  - HistoryEntry mapping from Event
  - HistoryPage validation
  - EventRepository.list_recent() ordering
  - Deterministic ordering
  - Bounded pagination
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.database import Base, init_db
from app.db.models.event import EventModel
from app.events.emitter import EventEmitter
from app.events.history import HistoryEntry, HistoryPage
from app.events.repository import EventRepository
from app.events.schemas import Event, EventType, Sensitivity
from app.repositories.task_repository import TaskRepository
from app.tasks.models import DevelopmentTask


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_session_factory(db_path: str):
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, connect_args={"check_same_thread": False})
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


async def _init_db(db_path: str) -> tuple[async_sessionmaker, Any]:
    factory, engine = _make_session_factory(db_path)
    import app.db.database as db_mod
    old_engine = db_mod._engine
    db_mod._engine = engine
    try:
        await init_db()
    finally:
        db_mod._engine = old_engine
    return factory, engine


async def _make_repo(db_path: str) -> tuple[EventRepository, async_sessionmaker, Any]:
    factory, engine = await _init_db(db_path)
    session = factory()
    repo = EventRepository(session)
    return repo, factory, engine


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _make_event(**kwargs: Any) -> Event:
    defaults: dict[str, Any] = {
        "event_type": EventType.TASK_STARTED,
        "source": "test",
        "task_id": "task_abc123",
        "plan_id": "plan_xyz",
        "run_id": "run_001",
        "execution_id": "exec_001",
        "correlation_id": "corr_001",
        "causation_id": "evt_prev",
        "actor": "system",
        "payload": {"key": "value"},
        "metadata": {"count": 1},
        "sensitivity": Sensitivity.INTERNAL,
        "occurred_at": _utc_now(),
        "recorded_at": _utc_now(),
        "event_version": "1.0",
    }
    defaults.update(kwargs)
    return Event(**defaults)


# ── HistoryEntry Tests ─────────────────────────────────────────────────────────


class TestHistoryEntry:
    def test_from_event_preserves_all_fields(self):
        evt = _make_event()
        entry = HistoryEntry.from_event(evt)
        assert entry.event_id == evt.event_id
        assert entry.event_type == evt.event_type
        assert entry.occurred_at == evt.occurred_at
        assert entry.recorded_at == evt.recorded_at
        assert entry.source == evt.source
        assert entry.event_version == evt.event_version
        assert entry.correlation_id == evt.correlation_id
        assert entry.causation_id == evt.causation_id
        assert entry.task_id == evt.task_id
        assert entry.plan_id == evt.plan_id
        assert entry.run_id == evt.run_id
        assert entry.execution_id == evt.execution_id
        assert entry.actor == evt.actor
        assert entry.payload == evt.payload
        assert entry.metadata == evt.metadata
        assert entry.sensitivity == evt.sensitivity

    def test_history_entry_is_frozen(self):
        evt = _make_event()
        entry = HistoryEntry.from_event(evt)
        with pytest.raises(Exception):
            entry.event_id = "new_id"

    def test_none_fields_preserved(self):
        evt = _make_event(correlation_id=None, causation_id=None, task_id=None)
        entry = HistoryEntry.from_event(evt)
        assert entry.correlation_id is None
        assert entry.causation_id is None
        assert entry.task_id is None


# ── HistoryPage Tests ──────────────────────────────────────────────────────────


class TestHistoryPage:
    def test_valid_page(self):
        entries = (HistoryEntry.from_event(_make_event(event_id="evt_1")),)
        page = HistoryPage(entries=entries, total=1, limit=50, offset=0, has_more=False)
        assert page.limit == 50
        assert page.offset == 0
        assert page.has_more is False

    def test_limit_validation_max(self):
        with pytest.raises(ValueError, match="limit must be between 1 and 500"):
            HistoryPage(entries=(), total=0, limit=501, offset=0, has_more=False)

    def test_limit_validation_min(self):
        with pytest.raises(ValueError, match="limit must be between 1 and 500"):
            HistoryPage(entries=(), total=0, limit=0, offset=0, has_more=False)

    def test_offset_validation_max(self):
        with pytest.raises(ValueError, match="offset must be between 0 and 100000"):
            HistoryPage(entries=(), total=0, limit=50, offset=100001, has_more=False)

    def test_offset_validation_negative(self):
        with pytest.raises(ValueError, match="offset must be between 0 and 100000"):
            HistoryPage(entries=(), total=0, limit=50, offset=-1, has_more=False)

    def test_boundary_limit_500(self):
        entries = (HistoryEntry.from_event(_make_event(event_id="evt_1")),)
        page = HistoryPage(entries=entries, total=1, limit=500, offset=0, has_more=False)
        assert page.limit == 500

    def test_boundary_offset_100000(self):
        entries = (HistoryEntry.from_event(_make_event(event_id="evt_1")),)
        page = HistoryPage(entries=entries, total=1, limit=50, offset=100000, has_more=False)
        assert page.offset == 100000


# ── EventRepository.list_recent Tests ─────────────────────────────────────────


class TestListRecent:
    @pytest.fixture
    async def repo(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        repo, factory, engine = await _make_repo(db_file)
        try:
            yield repo
        finally:
            await engine.dispose()

    async def test_recent_ordering_recorded_at_desc(self, repo):
        base = _utc_now()
        evt1 = _make_event(event_id="evt_old", recorded_at=base - timedelta(minutes=5), occurred_at=base)
        evt2 = _make_event(event_id="evt_mid", recorded_at=base - timedelta(minutes=2), occurred_at=base)
        evt3 = _make_event(event_id="evt_new", recorded_at=base, occurred_at=base)
        await repo.record(evt1)
        await repo.record(evt2)
        await repo.record(evt3)
        await repo.session.commit()

        results = await repo.list_recent(limit=10)
        ids = [e.event_id for e in results]
        assert ids == ["evt_new", "evt_mid", "evt_old"]

    async def test_recent_ordering_event_id_desc_for_ties(self, repo):
        base = _utc_now()
        evt1 = _make_event(event_id="evt_b", recorded_at=base, occurred_at=base)
        evt2 = _make_event(event_id="evt_a", recorded_at=base, occurred_at=base)
        await repo.record(evt1)
        await repo.record(evt2)
        await repo.session.commit()

        results = await repo.list_recent(limit=10)
        ids = [e.event_id for e in results]
        assert ids == ["evt_b", "evt_a"]

    async def test_recent_bounded_by_limit(self, repo):
        for i in range(10):
            await repo.record(_make_event(event_id=f"evt_{i:03d}", recorded_at=_utc_now() + timedelta(seconds=i)))
        await repo.session.commit()

        results = await repo.list_recent(limit=3)
        assert len(results) == 3

    async def test_recent_empty_when_no_events(self, repo):
        results = await repo.list_recent(limit=10)
        assert len(results) == 0


# ── Deterministic Ordering Tests ──────────────────────────────────────────────


class TestDeterministicOrdering:
    @pytest.fixture
    async def repo(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        repo, factory, engine = await _make_repo(db_file)
        try:
            yield repo
        finally:
            await engine.dispose()

    async def test_occurred_at_asc_event_id_asc(self, repo):
        base = _utc_now()
        evt1 = _make_event(event_id="evt_b", occurred_at=base, recorded_at=base)
        evt2 = _make_event(event_id="evt_a", occurred_at=base, recorded_at=base + timedelta(seconds=1))
        evt3 = _make_event(event_id="evt_c", occurred_at=base - timedelta(seconds=1), recorded_at=base)
        await repo.record(evt1)
        await repo.record(evt2)
        await repo.record(evt3)
        await repo.session.commit()

        results = await repo.list(limit=10)
        ids = [e.event_id for e in results]
        assert ids == ["evt_c", "evt_a", "evt_b"]


# ── Security: Persisted Redaction Intact ──────────────────────────────────────


class TestSecurityRedaction:
    @pytest.fixture
    async def repo(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        repo, factory, engine = await _make_repo(db_file)
        try:
            yield repo
        finally:
            await engine.dispose()

    async def test_redacted_payload_in_history_entry(self, repo):
        from app.events.sanitizer import redact_payload
        payload = {"Authorization": "Bearer sk-1234567890abcdef"}
        sanitized = redact_payload(payload)
        evt = _make_event(event_id="evt_sec_1", payload=sanitized)
        await repo.record(evt)
        await repo.session.commit()

        results = await repo.list()
        assert len(results) == 1
        entry = HistoryEntry.from_event(results[0])
        assert "sk-1234567890abcdef" not in str(entry.payload)
        assert "[REDACTED_BEARER]" in str(entry.payload)

    async def test_no_secret_reconstruction(self, repo):
        from app.events.sanitizer import redact_payload
        payload = {"password": "secret123", "api_key": "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
        sanitized = redact_payload(payload)
        evt = _make_event(event_id="evt_sec_2", payload=sanitized)
        await repo.record(evt)
        await repo.session.commit()

        results = await repo.list()
        entry = HistoryEntry.from_event(results[0])
        assert entry.payload.get("password") == "[REDACTED_PASSWORD]"
        assert "[REDACTED_API_KEY]" in entry.payload.get("api_key", "")


__all__ = [
    "TestHistoryEntry",
    "TestHistoryPage",
    "TestListRecent",
    "TestDeterministicOrdering",
    "TestSecurityRedaction",
]
