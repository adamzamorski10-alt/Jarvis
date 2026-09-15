"""
Repository — Kilo Sessions
Data access layer for Kilo Code CLI execution session records.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.kilo_session import KiloSessionModel
from app.orchestration.kilo_session_models import KiloSessionRecord, KiloSessionState


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class KiloSessionRepository:
    """Data access layer for KiloSessionModel records."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_session(self, record: KiloSessionRecord) -> KiloSessionRecord:
        model = KiloSessionModel(
            app_session_id=record.app_session_id,
            task_id=record.task_id,
            project_id=record.project_id,
            workspace_path=record.workspace_path,
            execution_id=record.execution_id,
            kilo_session_id=record.kilo_session_id,
            kilo_session_state=record.kilo_session_state,
            state=record.state.value,
            recovery_eligible=1 if record.recovery_eligible else 0,
            pid=record.pid,
            process_started_at=record.process_started_at,
            process_terminated_at=record.process_terminated_at,
            authorization_version=record.authorization_version,
            approval_scope_hash=record.approval_scope_hash,
            created_at=record.created_at,
            last_observed_at=record.last_observed_at,
            completed_at=record.completed_at,
            error_message=record.error_message,
            raw_event_snapshot=record.raw_event_snapshot,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.commit()
        await self._session.refresh(model)
        return self._to_domain(model)

    async def get_session(self, app_session_id: str) -> KiloSessionRecord | None:
        result = await self._session.execute(
            select(KiloSessionModel).where(KiloSessionModel.app_session_id == app_session_id)
        )
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def get_session_by_kilo_id(self, kilo_session_id: str) -> KiloSessionRecord | None:
        result = await self._session.execute(
            select(KiloSessionModel).where(KiloSessionModel.kilo_session_id == kilo_session_id)
        )
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def get_latest_session_for_execution(self, task_id: str, execution_id: str) -> KiloSessionRecord | None:
        result = await self._session.execute(
            select(KiloSessionModel)
            .where(
                KiloSessionModel.task_id == task_id,
                KiloSessionModel.execution_id == execution_id,
            )
            .order_by(KiloSessionModel.created_at.desc())
            .limit(1)
        )
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def list_sessions_for_task(self, task_id: str) -> list[KiloSessionRecord]:
        result = await self._session.execute(
            select(KiloSessionModel)
            .where(KiloSessionModel.task_id == task_id)
            .order_by(KiloSessionModel.created_at.desc())
        )
        models = list(result.scalars().all())
        return [self._to_domain(m) for m in models]

    async def list_active_sessions(self) -> list[KiloSessionRecord]:
        result = await self._session.execute(
            select(KiloSessionModel)
            .where(KiloSessionModel.state == KiloSessionState.ACTIVE.value)
            .order_by(KiloSessionModel.created_at.asc())
        )
        models = list(result.scalars().all())
        return [self._to_domain(m) for m in models]

    async def update_session_state(
        self,
        app_session_id: str,
        state: KiloSessionState,
        last_observed_at: datetime | None = None,
    ) -> KiloSessionRecord | None:
        now = last_observed_at or _utc_now()
        stmt = (
            update(KiloSessionModel)
            .where(KiloSessionModel.app_session_id == app_session_id)
            .values(state=state.value, last_observed_at=now)
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        await self._session.commit()
        if res.rowcount == 0:
            return None
        return await self.get_session(app_session_id)

    async def update_session_pid(
        self,
        app_session_id: str,
        pid: int | None,
        process_started_at: datetime | None = None,
    ) -> KiloSessionRecord | None:
        now = _utc_now()
        values: dict[str, Any] = {"last_observed_at": now}
        if pid is not None:
            values["pid"] = pid
        if process_started_at is not None:
            values["process_started_at"] = process_started_at
        stmt = (
            update(KiloSessionModel)
            .where(KiloSessionModel.app_session_id == app_session_id)
            .values(**values)
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        await self._session.commit()
        if res.rowcount == 0:
            return None
        return await self.get_session(app_session_id)

    async def update_session_kilo_id(
        self,
        app_session_id: str,
        kilo_session_id: str | None,
        kilo_session_state: str | None = None,
    ) -> KiloSessionRecord | None:
        """Update Kilo session ID and optional state for an existing session."""
        now = _utc_now()
        values: dict[str, Any] = {"kilo_session_id": kilo_session_id, "last_observed_at": now}
        if kilo_session_state is not None:
            values["kilo_session_state"] = kilo_session_state
        stmt = (
            update(KiloSessionModel)
            .where(KiloSessionModel.app_session_id == app_session_id)
            .values(**values)
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        await self._session.commit()
        if res.rowcount == 0:
            return None
        return await self.get_session(app_session_id)

    async def mark_session_completed(
        self,
        app_session_id: str,
        state: KiloSessionState,
        completed_at: datetime | None = None,
        error_message: str | None = None,
    ) -> KiloSessionRecord | None:
        now = completed_at or _utc_now()
        stmt = (
            update(KiloSessionModel)
            .where(KiloSessionModel.app_session_id == app_session_id)
            .values(
                state=state.value,
                completed_at=now,
                last_observed_at=now,
                recovery_eligible=0 if state in (KiloSessionState.COMPLETED, KiloSessionState.INVALIDATED) else 1,
                error_message=error_message,
            )
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        await self._session.commit()
        if res.rowcount == 0:
            return None
        return await self.get_session(app_session_id)

    async def invalidate_sessions_for_task(self, task_id: str) -> int:
        """Invalidate all non-completed sessions for a task. Returns count of invalidated rows."""
        now = _utc_now()
        stmt = (
            update(KiloSessionModel)
            .where(
                KiloSessionModel.task_id == task_id,
                KiloSessionModel.state.notin_([
                    KiloSessionState.COMPLETED.value,
                    KiloSessionState.INVALIDATED.value,
                ]),
            )
            .values(
                state=KiloSessionState.INVALIDATED.value,
                completed_at=now,
                last_observed_at=now,
                recovery_eligible=0,
                error_message="Invalidated by new execution",
            )
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        await self._session.commit()
        return res.rowcount

    async def count_active_sessions_for_task(self, task_id: str) -> int:
        result = await self._session.execute(
            select(KiloSessionModel)
            .where(
                KiloSessionModel.task_id == task_id,
                KiloSessionModel.state == KiloSessionState.ACTIVE.value,
            )
        )
        return len(list(result.scalars().all()))

    @staticmethod
    def _to_domain(model: KiloSessionModel | None) -> KiloSessionRecord | None:
        if model is None:
            return None
        return KiloSessionRecord(
            app_session_id=model.app_session_id,
            task_id=model.task_id,
            project_id=model.project_id,
            workspace_path=model.workspace_path,
            execution_id=model.execution_id,
            kilo_session_id=model.kilo_session_id,
            kilo_session_state=model.kilo_session_state,
            state=KiloSessionState(model.state),
            recovery_eligible=bool(model.recovery_eligible),
            pid=model.pid,
            process_started_at=model.process_started_at,
            process_terminated_at=model.process_terminated_at,
            authorization_version=model.authorization_version,
            approval_scope_hash=model.approval_scope_hash,
            created_at=model.created_at,
            last_observed_at=model.last_observed_at,
            completed_at=model.completed_at,
            error_message=model.error_message,
            raw_event_snapshot=model.raw_event_snapshot,
        )
