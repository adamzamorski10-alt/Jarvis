"""
Stage 11.3 — History Integration Tests

Covers full end-to-end paths:
  EventEmitter → database → EventRepository → HistoryService → HistoryPage
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.database import Base, init_db
from app.events.emitter import EventEmitter
from app.events.history import HistoryEntry, HistoryPage, HistoryService
from app.events.repository import EventRepository
from app.events.schemas import EventType, Sensitivity
from app.repositories.task_repository import TaskRepository
from app.tasks.models import DevelopmentTask


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _init_in_memory_db() -> tuple[AsyncSession, async_sessionmaker, Any]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    return session, factory, engine


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ── Integration Tests ──────────────────────────────────────────────────────────


class TestFullTaskLifecycleHistory:
    @pytest.fixture
    async def db(self):
        session, factory, engine = await _init_in_memory_db()
        try:
            yield session
        finally:
            await session.close()
            await engine.dispose()

    async def test_full_task_lifecycle(self, db: AsyncSession):
        task_repo = TaskRepository(db)
        event_repo = EventRepository(db)
        emitter = EventEmitter(event_repo)

        task = DevelopmentTask(
            project_id="proj_1",
            title="Task 1",
            description="Test task",
        )
        created = await task_repo.create_task(task)
        await db.commit()

        evt_started = await emitter.emit(
            EventType.TASK_STARTED,
            source="orchestrator",
            task_id=created.task_id,
            plan_id="plan_1",
            actor="system",
        )
        await emitter.emit(
            EventType.VERIFICATION_STARTED,
            source="orchestrator",
            task_id=created.task_id,
            plan_id="plan_1",
            correlation_id="corr_1",
            causation_id=evt_started.event_id,
        )
        await emitter.emit(
            EventType.VERIFICATION_PASSED,
            source="orchestrator",
            task_id=created.task_id,
            plan_id="plan_1",
            correlation_id="corr_1",
        )
        await emitter.emit(
            EventType.CHECKPOINT_CREATED,
            source="workspace_manager",
            task_id=created.task_id,
            plan_id="plan_1",
            commit_sha="abc123",
            checkpoint_id="cp_001",
        )
        await emitter.emit(
            EventType.TASK_COMPLETED,
            source="orchestrator",
            task_id=created.task_id,
            plan_id="plan_1",
            commit_sha="abc123",
            actor="system",
        )
        await db.commit()

        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_task_history(created.task_id)
        assert len(page.entries) == 5
        types = [e.event_type for e in page.entries]
        assert EventType.TASK_STARTED in types
        assert EventType.VERIFICATION_STARTED in types
        assert EventType.VERIFICATION_PASSED in types
        assert EventType.CHECKPOINT_CREATED in types
        assert EventType.TASK_COMPLETED in types
        assert page.total == 5


class TestFullPlanLifecycleHistory:
    @pytest.fixture
    async def db(self):
        session, factory, engine = await _init_in_memory_db()
        try:
            yield session
        finally:
            await session.close()
            await engine.dispose()

    async def test_full_plan_lifecycle(self, db: AsyncSession):
        task_repo = TaskRepository(db)
        event_repo = EventRepository(db)
        emitter = EventEmitter(event_repo)

        task1 = DevelopmentTask(
            project_id="proj_1",
            title="Task 1",
            description="Test task",
            plan_id="plan_life",
            task_key="task_1",
        )
        task2 = DevelopmentTask(
            project_id="proj_1",
            title="Task 2",
            description="Test task 2",
            plan_id="plan_life",
            task_key="task_2",
        )
        created1 = await task_repo.create_task(task1)
        created2 = await task_repo.create_task(task2)
        await db.commit()

        await emitter.emit(EventType.PLAN_STARTED, source="plan_executor", plan_id="plan_life", actor="system")
        await emitter.emit(EventType.TASK_STARTED, source="orchestrator", task_id=created1.task_id, plan_id="plan_life")
        await emitter.emit(EventType.TASK_COMPLETED, source="orchestrator", task_id=created1.task_id, plan_id="plan_life")
        await emitter.emit(EventType.TASK_STARTED, source="orchestrator", task_id=created2.task_id, plan_id="plan_life")
        await emitter.emit(EventType.TASK_FAILED, source="orchestrator", task_id=created2.task_id, plan_id="plan_life", reason="error")
        await emitter.emit(EventType.PLAN_COMPLETED, source="plan_executor", plan_id="plan_life", actor="system")
        await db.commit()

        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_plan_history("plan_life")
        assert len(page.entries) == 6
        assert page.total == 6


class TestCorrelationChainHistory:
    @pytest.fixture
    async def db(self):
        session, factory, engine = await _init_in_memory_db()
        try:
            yield session
        finally:
            await session.close()
            await engine.dispose()

    async def test_correlation_chain_across_boundaries(self, db: AsyncSession):
        task_repo = TaskRepository(db)
        event_repo = EventRepository(db)
        emitter = EventEmitter(event_repo)

        task = DevelopmentTask(
            project_id="proj_1",
            title="Task 1",
            description="Test task",
        )
        created = await task_repo.create_task(task)
        await db.commit()

        evt1 = await emitter.emit(
            EventType.TASK_STARTED,
            source="orchestrator",
            task_id=created.task_id,
            correlation_id="corr_chain",
        )
        await emitter.emit(
            EventType.VERIFICATION_STARTED,
            source="orchestrator",
            task_id=created.task_id,
            correlation_id="corr_chain",
            causation_id=evt1.event_id,
        )
        await emitter.emit(
            EventType.TASK_COMPLETED,
            source="orchestrator",
            task_id=created.task_id,
            correlation_id="corr_chain",
        )
        await db.commit()

        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_correlation_history("corr_chain")
        assert len(page.entries) == 3
        assert all(e.correlation_id == "corr_chain" for e in page.entries)


class TestNoCrossContamination:
    @pytest.fixture
    async def db(self):
        session, factory, engine = await _init_in_memory_db()
        try:
            yield session
        finally:
            await session.close()
            await engine.dispose()

    async def test_events_for_task_a_do_not_appear_in_task_b(self, db: AsyncSession):
        task_repo = TaskRepository(db)
        event_repo = EventRepository(db)
        emitter = EventEmitter(event_repo)

        task_a = DevelopmentTask(
            project_id="proj_1",
            title="Task A",
            description="Task A",
        )
        task_b = DevelopmentTask(
            project_id="proj_1",
            title="Task B",
            description="Task B",
        )
        created_a = await task_repo.create_task(task_a)
        created_b = await task_repo.create_task(task_b)
        await db.commit()

        await emitter.emit(EventType.TASK_STARTED, source="orchestrator", task_id=created_a.task_id)
        await emitter.emit(EventType.TASK_COMPLETED, source="orchestrator", task_id=created_a.task_id)
        await emitter.emit(EventType.TASK_STARTED, source="orchestrator", task_id=created_b.task_id)
        await db.commit()

        svc = HistoryService(event_repo, task_repo)
        page_a = await svc.get_task_history(created_a.task_id)
        page_b = await svc.get_task_history(created_b.task_id)
        assert len(page_a.entries) == 2
        assert len(page_b.entries) == 1
        assert all(e.task_id == created_a.task_id for e in page_a.entries)
        assert all(e.task_id == created_b.task_id for e in page_b.entries)


class TestIntegrationPath:
    @pytest.fixture
    async def db(self):
        session, factory, engine = await _init_in_memory_db()
        try:
            yield session
        finally:
            await session.close()
            await engine.dispose()

    async def test_full_path_emitter_to_historypage(self, db: AsyncSession):
        task_repo = TaskRepository(db)
        event_repo = EventRepository(db)
        emitter = EventEmitter(event_repo)

        task = DevelopmentTask(
            project_id="proj_1",
            title="Integration Task",
            description="Integration test task",
        )
        created = await task_repo.create_task(task)
        await db.commit()

        await emitter.emit(EventType.TASK_STARTED, source="orchestrator", task_id=created.task_id)
        await db.commit()

        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_task_history(created.task_id)
        assert isinstance(page, HistoryPage)
        assert len(page.entries) == 1
        assert isinstance(page.entries[0], HistoryEntry)
        assert page.entries[0].task_id == created.task_id
        assert page.entries[0].event_type == EventType.TASK_STARTED


__all__ = [
    "TestFullTaskLifecycleHistory",
    "TestFullPlanLifecycleHistory",
    "TestCorrelationChainHistory",
    "TestNoCrossContamination",
    "TestIntegrationPath",
]
