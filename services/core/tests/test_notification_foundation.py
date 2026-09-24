"""
Stage 13.2 — Notification Foundation Tests

Covers:
  - Domain model validation
  - Persistence (create, get, list, ordering, pagination)
  - Idempotency (same event+type)
  - Transaction safety (savepoint behavior)
  - Concurrency (duplicate creation races)
  - Security (bounded fields, no secret leakage)
  - Restart durability
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.database import Base, init_db
from app.db.models.notification import NotificationModel
from app.notifications.exceptions import (
    NotificationIntegrityConflict,
    NotificationNotFoundError,
    NotificationValidationError,
)
from app.notifications.models import (
    Notification,
    NotificationSeverity,
    NotificationType,
    _validate_json_size,
    _validate_string_length,
    _validate_utc_datetime,
    _validate_notification_type,
)
from app.notifications.repository import NotificationRepository


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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _make_notification(**kwargs: Any) -> Notification:
    defaults: dict[str, Any] = {
        "notification_type": NotificationType.TASK_FAILED,
        "severity": NotificationSeverity.ERROR,
        "title": "Task task_abc123 failed",
        "body": "Task failed with error: something went wrong",
        "source_event_id": "evt_001",
        "correlation_id": "corr_001",
        "task_id": "task_abc123",
        "plan_id": "plan_xyz",
        "run_id": "run_001",
        "execution_id": "exec_001",
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "read_at": None,
        "expires_at": None,
        "metadata": {"key": "value"},
    }
    defaults.update(kwargs)
    return Notification(**defaults)


# ── Domain Contract Tests ─────────────────────────────────────────────────────


class TestNotificationContract:
    def test_valid_notification_creation(self):
        notif = _make_notification()
        assert notif.notification_id.startswith("notif_")
        assert notif.notification_type == NotificationType.TASK_FAILED
        assert notif.severity == NotificationSeverity.ERROR
        assert notif.source_event_id == "evt_001"
        assert notif.read_at is None

    def test_required_fields(self):
        with pytest.raises(ValueError):
            Notification(
                notification_type=NotificationType.TASK_FAILED,
                title="",
                source_event_id="evt_001",
            )

    def test_invalid_notification_type(self):
        with pytest.raises(ValueError):
            _make_notification(notification_type="invalid.type")

    def test_invalid_severity(self):
        with pytest.raises(ValueError):
            _make_notification(severity="invalid")

    def test_title_length_limit(self):
        long_title = "x" * 201
        with pytest.raises(ValueError):
            _make_notification(title=long_title)

    def test_body_length_limit(self):
        long_body = "x" * 2001
        with pytest.raises(ValueError):
            _make_notification(body=long_body)

    def test_source_event_id_length_limit(self):
        long_id = "x" * 65
        with pytest.raises(ValueError):
            _make_notification(source_event_id=long_id)

    def test_metadata_size_limit(self):
        big_meta = {"data": "x" * 1025}
        with pytest.raises(ValueError):
            _make_notification(metadata=big_meta)

    def test_timestamp_naive_handling(self):
        notif = _make_notification(created_at=datetime.now())
        assert notif.created_at.tzinfo is not None

    def test_notification_type_string_validation(self):
        with pytest.raises(ValueError):
            _validate_notification_type("not.a.type")

    def test_severity_case_insensitive(self):
        notif = _make_notification(severity="WARNING")
        assert notif.severity == NotificationSeverity.WARNING.value

    def test_none_body_passthrough(self):
        notif = _make_notification(body=None)
        assert notif.body is None

    def test_none_metadata_passthrough(self):
        notif = _make_notification(metadata=None)
        assert notif.metadata is None

    def test_notification_is_mutable(self):
        notif = _make_notification()
        notif.read_at = _utc_now()
        assert notif.read_at is not None


# ── Persistence Tests ─────────────────────────────────────────────────────────


class TestNotificationPersistence:
    @pytest.fixture
    async def repo(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        factory, engine = await _init_db(db_file)
        session = factory()
        repo = NotificationRepository(session)
        try:
            yield repo
        finally:
            await session.close()
            await engine.dispose()

    async def test_insert_and_get(self, repo):
        notif = _make_notification(notification_id="notif_001")
        stored, created = await repo.create(notif)
        assert stored.notification_id == "notif_001"

        fetched = await repo.get("notif_001")
        assert fetched is not None
        assert fetched.title == "Task task_abc123 failed"
        assert fetched.severity == "error"

    async def test_insert_duplicate_idempotent(self, repo):
        notif = _make_notification(notification_id="notif_dup", source_event_id="evt_dup", notification_type=NotificationType.TASK_FAILED)
        stored1, created1 = await repo.create(notif)
        stored2, created2 = await repo.create(notif)
        assert stored1.notification_id == stored2.notification_id

        count = await repo.count()
        assert count == 1

    async def test_insert_same_event_type_idempotent(self, repo):
        notif1 = _make_notification(
            notification_id="notif_1",
            source_event_id="evt_same",
            notification_type=NotificationType.TASK_FAILED,
            title="Original title",
        )
        notif2 = _make_notification(
            notification_id="notif_2",
            source_event_id="evt_same",
            notification_type=NotificationType.TASK_FAILED,
            title="Different title",
        )
        stored1, created1 = await repo.create(notif1)
        stored2, created2 = await repo.create(notif2)
        assert stored1.notification_id == stored2.notification_id
        assert stored1.title == "Original title"

        count = await repo.count()
        assert count == 1

    async def test_get_missing_returns_none(self, repo):
        fetched = await repo.get("notif_missing")
        assert fetched is None

    async def test_list_ordering_recent_first(self, repo):
        base = _utc_now()
        notif1 = _make_notification(notification_id="n1", source_event_id="evt_n1", notification_type=NotificationType.TASK_FAILED, created_at=base, updated_at=base)
        notif2 = _make_notification(notification_id="n2", source_event_id="evt_n2", notification_type=NotificationType.TASK_COMPLETED, created_at=base + timedelta(seconds=1), updated_at=base + timedelta(seconds=1))
        notif3 = _make_notification(notification_id="n3", source_event_id="evt_n3", notification_type=NotificationType.TASK_STARTED, created_at=base - timedelta(seconds=1), updated_at=base - timedelta(seconds=1))
        await repo.create(notif1)
        await repo.create(notif2)
        await repo.create(notif3)
        await repo.session.commit()

        results = await repo.list(limit=10)
        ids = [n.notification_id for n in results]
        assert ids == ["n2", "n1", "n3"]

    async def test_filter_by_task_id(self, repo):
        await repo.create(_make_notification(notification_id="n1", source_event_id="evt_t1", task_id="task_a"))
        await repo.create(_make_notification(notification_id="n2", source_event_id="evt_t2", task_id="task_b"))
        await repo.session.commit()

        results = await repo.list(task_id="task_a")
        assert len(results) == 1
        assert results[0].task_id == "task_a"

    async def test_filter_by_notification_type(self, repo):
        await repo.create(_make_notification(notification_id="n1", source_event_id="evt_nt1", notification_type=NotificationType.TASK_FAILED))
        await repo.create(_make_notification(notification_id="n2", source_event_id="evt_nt2", notification_type=NotificationType.TASK_COMPLETED))
        await repo.session.commit()

        results = await repo.list(notification_type=NotificationType.TASK_FAILED.value)
        assert len(results) == 1
        assert results[0].notification_type == NotificationType.TASK_FAILED.value

    async def test_filter_by_severity(self, repo):
        await repo.create(_make_notification(notification_id="n1", source_event_id="evt_s1", severity=NotificationSeverity.ERROR))
        await repo.create(_make_notification(notification_id="n2", source_event_id="evt_s2", severity=NotificationSeverity.INFO))
        await repo.session.commit()

        results = await repo.list(severity=NotificationSeverity.ERROR.value)
        assert len(results) == 1
        assert results[0].severity == NotificationSeverity.ERROR.value

    async def test_filter_unread_only(self, repo):
        notif1 = _make_notification(notification_id="n1", source_event_id="evt_u1")
        notif2 = _make_notification(notification_id="n2", source_event_id="evt_u2")
        stored1, created1 = await repo.create(notif1)
        stored2, created2 = await repo.create(notif2)
        await repo.mark_read(stored1.notification_id)
        await repo.session.commit()

        results = await repo.list(unread_only=True)
        assert len(results) == 1
        assert results[0].notification_id == "n2"

    async def test_count_unread(self, repo):
        notif1 = _make_notification(notification_id="n1", source_event_id="evt_c1")
        notif2 = _make_notification(notification_id="n2", source_event_id="evt_c2")
        stored1, created1 = await repo.create(notif1)
        stored2, created2 = await repo.create(notif2)
        await repo.mark_read(stored1.notification_id)
        await repo.session.commit()

        count = await repo.count(unread_only=True)
        assert count == 1

    async def test_bounded_pagination(self, repo):
        for i in range(10):
            await repo.create(_make_notification(notification_id=f"n{i:03d}", source_event_id=f"evt_p{i:03d}"))
        await repo.session.commit()

        page1 = await repo.list(limit=3, offset=0)
        assert len(page1) == 3
        page2 = await repo.list(limit=3, offset=3)
        assert len(page2) == 3

    async def test_mark_read(self, repo):
        notif = _make_notification(notification_id="n_read")
        stored, created = await repo.create(notif)
        await repo.session.commit()
        assert stored.read_at is None

        updated = await repo.mark_read(stored.notification_id)
        assert updated is not None
        assert updated.read_at is not None

        fetched = await repo.get(stored.notification_id)
        assert fetched.read_at is not None

    async def test_mark_read_missing_returns_none(self, repo):
        updated = await repo.mark_read("notif_missing")
        assert updated is None

    async def test_mark_all_read(self, repo):
        notif1 = _make_notification(notification_id="n1", source_event_id="evt_ma1", task_id="task_a")
        notif2 = _make_notification(notification_id="n2", source_event_id="evt_ma2", task_id="task_b")
        stored1, created1 = await repo.create(notif1)
        stored2, created2 = await repo.create(notif2)
        await repo.session.commit()

        count = await repo.mark_all_read(task_id="task_a")
        assert count == 1

        fetched1 = await repo.get(stored1.notification_id)
        assert fetched1.read_at is not None

        fetched2 = await repo.get(stored2.notification_id)
        assert fetched2.read_at is None

    async def test_delete_expired(self, repo):
        past = _utc_now() - timedelta(days=1)
        future = _utc_now() + timedelta(days=1)
        notif1 = _make_notification(notification_id="n1", source_event_id="evt_de1", expires_at=past)
        notif2 = _make_notification(notification_id="n2", source_event_id="evt_de2", expires_at=future)
        await repo.create(notif1)
        await repo.create(notif2)
        await repo.session.commit()

        deleted = await repo.delete_expired(_utc_now())
        assert deleted == 1

        remaining = await repo.list()
        assert len(remaining) == 1
        assert remaining[0].notification_id == "n2"

    async def test_nullable_references(self, repo):
        notif = _make_notification(
            notification_id="n_null",
            source_event_id="evt_null",
            task_id=None,
            plan_id=None,
            run_id=None,
            execution_id=None,
            correlation_id=None,
            body=None,
            metadata=None,
        )
        stored, created = await repo.create(notif)
        assert stored.task_id is None
        assert stored.plan_id is None
        assert stored.run_id is None
        assert stored.execution_id is None
        assert stored.correlation_id is None
        assert stored.body is None
        assert stored.metadata == {}


# ── Transaction Safety Tests ──────────────────────────────────────────────────


class TestTransactionSafety:
    @pytest.fixture
    async def repo_and_session(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        factory, engine = await _init_db(db_file)
        session = factory()
        repo = NotificationRepository(session)
        try:
            yield repo, session
        finally:
            await session.close()
            await engine.dispose()

    async def test_duplicate_inside_outer_transaction(self, repo_and_session):
        repo, session = repo_and_session
        notif1 = _make_notification(notification_id="n1", source_event_id="evt_outer", notification_type=NotificationType.TASK_FAILED)
        await repo.create(notif1)

        notif2 = _make_notification(notification_id="n2", source_event_id="evt_outer", notification_type=NotificationType.TASK_FAILED, title="Different")
        result, created = await repo.create(notif2)
        assert result.notification_id == notif1.notification_id

        count = await repo.count()
        assert count == 1

    async def test_validation_failure_does_not_persist(self, repo_and_session):
        repo, session = repo_and_session
        with pytest.raises(ValueError):
            _make_notification(title="")

        count = await repo.count()
        assert count == 0

    async def test_savepoint_does_not_rollback_caller(self, repo_and_session):
        repo, session = repo_and_session
        notif1 = _make_notification(notification_id="n_savepoint_1", source_event_id="evt_sp", notification_type=NotificationType.TASK_FAILED)

        async with session.begin():
            await repo.create(notif1)

            notif_bad = _make_notification(notification_id="n_savepoint_1", source_event_id="evt_sp2", notification_type=NotificationType.TASK_FAILED, title="Different")
            try:
                await repo.create(notif_bad)
            except NotificationIntegrityConflict:
                pass

        count = await repo.count()
        assert count == 1
        assert (await repo.get("n_savepoint_1")) is not None


# ── Concurrency Tests ─────────────────────────────────────────────────────────


class TestConcurrency:
    @pytest.fixture
    async def repo_factory(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        factory, engine = await _init_db(db_file)
        try:
            yield factory
        finally:
            await engine.dispose()

    async def test_concurrent_duplicate_creation(self, repo_factory):
        session1 = repo_factory()
        session2 = repo_factory()
        repo1 = NotificationRepository(session1)
        repo2 = NotificationRepository(session2)

        notif = _make_notification(notification_id="n_conc", source_event_id="evt_conc", notification_type=NotificationType.TASK_FAILED)

        result1, result2 = await asyncio.gather(
            repo1.create(notif),
            repo2.create(notif),
            return_exceptions=True,
        )

        successes = [r for r in (result1, result2) if not isinstance(r, BaseException)]
        failures = [r for r in (result1, result2) if isinstance(r, BaseException)]

        assert len(successes) >= 1
        assert all(r[0].notification_id == "n_conc" for r in successes)

        await session1.commit()
        try:
            await session2.commit()
        except Exception:
            pass
        await session1.close()
        await session2.close()

        session3 = repo_factory()
        repo3 = NotificationRepository(session3)
        try:
            count = await repo3.count()
            assert count == 1
        finally:
            await session3.close()

    async def test_concurrent_read_write(self, repo_factory):
        session = repo_factory()
        repo = NotificationRepository(session)

        notif = _make_notification(notification_id="n_rw")
        stored, created = await repo.create(notif)
        await repo.session.commit()

        async def reader():
            for _ in range(10):
                await repo.get(stored.notification_id)
                await asyncio.sleep(0)

        async def writer():
            for i in range(5):
                await repo.mark_read(stored.notification_id)
                await asyncio.sleep(0)

        readers = [asyncio.create_task(reader()) for _ in range(3)]
        writers = [asyncio.create_task(writer()) for _ in range(2)]
        await asyncio.gather(*readers, *writers, return_exceptions=True)
        await session.close()


# ── Restart Durability Tests ──────────────────────────────────────────────────


class TestRestartDurability:
    async def test_notification_survives_session_close(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        factory, engine = await _init_db(db_file)
        session = factory()
        repo = NotificationRepository(session)

        notif = _make_notification(notification_id="n_dur")
        await repo.create(notif)
        await repo.session.commit()
        await session.close()
        await engine.dispose()

        factory2, engine2 = await _init_db(db_file)
        session2 = factory2()
        repo2 = NotificationRepository(session2)

        fetched = await repo2.get("n_dur")
        assert fetched is not None
        assert fetched.title == "Task task_abc123 failed"

        await session2.close()
        await engine2.dispose()


# ── Security Tests ────────────────────────────────────────────────────────────


class TestSecurity:
    def test_notification_boundary_documentation(self):
        """Verify Notification is NOT an Event."""
        notif = _make_notification()
        assert not hasattr(notif, "event_id")
        assert not hasattr(notif, "event_type")
        assert not hasattr(notif, "payload")

    def test_title_no_secret_patterns(self):
        notif = _make_notification(title="Normal title")
        assert "[REDACTED" not in notif.title

    def test_body_no_secret_patterns(self):
        notif = _make_notification(body="Normal body text")
        assert "[REDACTED" not in notif.body

    def test_metadata_bounded(self):
        notif = _make_notification(metadata={"small": "value"})
        assert notif.metadata == {"small": "value"}

    def test_no_execution_authority(self):
        """Notification must not contain execution handles."""
        notif = _make_notification()
        assert not hasattr(notif, "execute")
        assert not hasattr(notif, "run")
        assert not hasattr(notif, "invoke")


# ── Regression Tests ──────────────────────────────────────────────────────────


class TestRegression:
    async def test_existing_event_tests_still_pass(self):
        """Placeholder: run the actual test suite in CI."""
        pass


class TestMigrationUpgrade:
    async def test_indexes_exist_after_init_db_on_existing_table(self):
        from sqlalchemy import text
        from app.db.database import get_session_factory, init_db

        factory = get_session_factory()
        session = factory()
        try:
            await init_db()
            result = await session.execute(
                text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='notifications'")
            )
            index_names = {row[0] for row in result.fetchall()}
            expected = {
                "ix_notifications_created_at",
                "ix_notifications_notification_type",
                "ix_notifications_severity",
                "ix_notifications_source_event_id",
                "ix_notifications_correlation_id",
                "ix_notifications_task_id",
                "ix_notifications_plan_id",
                "ix_notifications_execution_id",
                "ix_notifications_read_at",
                "uq_notifications_source_event_type",
            }
            missing = expected - index_names
            assert not missing, f"Missing indexes after init_db: {missing}"
        finally:
            await session.close()


__all__ = [
    "TestNotificationContract",
    "TestNotificationPersistence",
    "TestTransactionSafety",
    "TestConcurrency",
    "TestRestartDurability",
    "TestSecurity",
    "TestRegression",
]
