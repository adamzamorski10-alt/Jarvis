"""
Repository — Development Tasks
Data access layer for autonomous development tasks in SQLite.
"""
from datetime import datetime, timezone
import json
from typing import Any
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.development_task import DevelopmentTaskModel
from app.tasks.models import DevelopmentTask, TaskStatus


def _serialize_metadata(metadata: dict[str, Any] | None) -> str | None:
    if not metadata:
        return None
    return json.dumps(metadata, sort_keys=True, ensure_ascii=False)


def _deserialize_metadata(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class TaskRepository:
    """
    Data access layer for DevelopmentTask records.
    """
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_task(self, task: DevelopmentTask) -> DevelopmentTask:
        model = DevelopmentTaskModel(
            task_id=task.task_id,
            project_id=task.project_id.strip(),
            parent_task_id=task.parent_task_id.strip() if task.parent_task_id else None,
            title=task.title.strip(),
            description=task.description.strip(),
            status=task.status.value if isinstance(task.status, TaskStatus) else str(task.status),
            priority=task.priority,
            attempt_count=task.attempt_count,
            max_attempts=task.max_attempts,
            created_at=task.created_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
            failure_reason=task.failure_reason,
            task_metadata=_serialize_metadata(task.metadata),
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_domain(model)

    async def get_task(self, task_id: str) -> DevelopmentTask | None:
        result = await self._session.execute(
            select(DevelopmentTaskModel).where(DevelopmentTaskModel.task_id == task_id)
        )
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def list_tasks(
        self,
        project_id: str | None = None,
        status: TaskStatus | str | None = None,
        parent_task_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DevelopmentTask]:
        query = select(DevelopmentTaskModel)
        if project_id:
            query = query.where(DevelopmentTaskModel.project_id == project_id.strip())
        if status:
            s_str = status.value if isinstance(status, TaskStatus) else str(status)
            query = query.where(DevelopmentTaskModel.status == s_str)
        if parent_task_id is not None:
            query = query.where(DevelopmentTaskModel.parent_task_id == parent_task_id.strip())

        query = (
            query.order_by(DevelopmentTaskModel.priority.desc(), DevelopmentTaskModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(query)
        models = list(result.scalars().all())
        return [self._to_domain(m) for m in models]

    async def update_task_status(
        self,
        task_id: str,
        status: TaskStatus | str,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        failure_reason: str | None = None,
    ) -> DevelopmentTask | None:
        s_str = status.value if isinstance(status, TaskStatus) else str(status)
        values: dict[str, Any] = {"status": s_str}
        if started_at is not None:
            values["started_at"] = started_at
        if completed_at is not None:
            values["completed_at"] = completed_at
        if failure_reason is not None:
            values["failure_reason"] = failure_reason

        stmt = (
            update(DevelopmentTaskModel)
            .where(DevelopmentTaskModel.task_id == task_id)
            .values(**values)
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None
        return await self.get_task(task_id)

    async def transition_status_atomically(
        self,
        task_id: str,
        expected_status: TaskStatus | str,
        new_status: TaskStatus | str,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        failure_reason: str | None = None,
    ) -> tuple[DevelopmentTask | None, bool]:
        """
        Atomically updates task status using Compare-And-Set (CAS) query:
        UPDATE development_tasks SET status = :new_status WHERE task_id = :task_id AND status = :expected_status
        Returns (updated_task, True) on success.
        Returns (None, False) if expected_status does not match or task does not exist.
        """
        exp_str = expected_status.value if isinstance(expected_status, TaskStatus) else str(expected_status)
        new_str = new_status.value if isinstance(new_status, TaskStatus) else str(new_status)

        values: dict[str, Any] = {"status": new_str}
        if started_at is not None:
            values["started_at"] = started_at
        if completed_at is not None:
            values["completed_at"] = completed_at
        if failure_reason is not None:
            values["failure_reason"] = failure_reason.strip()

        stmt = (
            update(DevelopmentTaskModel)
            .where(
                DevelopmentTaskModel.task_id == task_id,
                DevelopmentTaskModel.status == exp_str,
            )
            .values(**values)
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None, False

        task = await self.get_task(task_id)
        return task, True


    async def claim_task_attempt(
        self,
        task_id: str,
        expected_status: TaskStatus | str,
        max_attempts: int,
        started_at: datetime | None = None,
    ) -> tuple[DevelopmentTask | None, bool]:
        """
        Atomically claims a task execution attempt using a single SQL UPDATE statement.

        The UPDATE succeeds only when ALL three conditions hold simultaneously:
          • task_id matches
          • current status == expected_status
          • current attempt_count < max_attempts

        On success it atomically writes:
          • status = RUNNING
          • attempt_count = attempt_count + 1
          • started_at = now

        Because status and attempt_count are written in one SQL statement there is no
        crash window between them — a process crash before or after the statement
        leaves the database in a consistent state.  A losing concurrent worker whose
        UPDATE matches zero rows never touches either column.

        Returns:
            (updated_task, True)  — claim won; status is now RUNNING.
            (None, False)         — claim lost (concurrent winner, attempt limit
                                   already reached, or task not found).
        """
        exp_str = expected_status.value if isinstance(expected_status, TaskStatus) else str(expected_status)
        now = started_at or datetime.now(timezone.utc)

        stmt = (
            update(DevelopmentTaskModel)
            .where(
                DevelopmentTaskModel.task_id == task_id,
                DevelopmentTaskModel.status == exp_str,
                DevelopmentTaskModel.attempt_count < max_attempts,
            )
            .values(
                status=TaskStatus.RUNNING.value,
                attempt_count=DevelopmentTaskModel.attempt_count + 1,
                started_at=now,
            )
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None, False

        task = await self.get_task(task_id)
        return task, True

    async def update_task_metadata(
        self,
        task_id: str,
        metadata: dict[str, Any],
    ) -> DevelopmentTask | None:
        stmt = (
            update(DevelopmentTaskModel)
            .where(DevelopmentTaskModel.task_id == task_id)
            .values(task_metadata=_serialize_metadata(metadata))
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None
        return await self.get_task(task_id)

    async def record_attempt_atomically(
        self,
        task_id: str,
        failure_reason: str | None = None,
    ) -> tuple[DevelopmentTask | None, int]:
        """
        Atomically increments attempt_count in a single SQL UPDATE statement
        to eliminate race conditions during concurrent retry attempts.
        Returns (updated_task, new_attempt_count). If task does not exist, returns (None, 0).
        """
        values: dict[str, Any] = {
            "attempt_count": DevelopmentTaskModel.attempt_count + 1
        }
        if failure_reason is not None:
            values["failure_reason"] = failure_reason

        stmt = (
            update(DevelopmentTaskModel)
            .where(DevelopmentTaskModel.task_id == task_id)
            .values(**values)
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None, 0

        task = await self.get_task(task_id)
        count = task.attempt_count if task else 0
        return task, count

    async def record_failure(
        self,
        task_id: str,
        failure_reason: str,
    ) -> DevelopmentTask | None:
        now = datetime.now(timezone.utc)
        stmt = (
            update(DevelopmentTaskModel)
            .where(DevelopmentTaskModel.task_id == task_id)
            .values(
                status=TaskStatus.FAILED.value,
                completed_at=now,
                failure_reason=failure_reason.strip(),
            )
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None
        return await self.get_task(task_id)

    @staticmethod
    def _to_domain(model: DevelopmentTaskModel | None) -> DevelopmentTask | None:
        if model is None:
            return None
        return DevelopmentTask.model_construct(
            task_id=model.task_id,
            project_id=model.project_id,
            parent_task_id=model.parent_task_id,
            title=model.title,
            description=model.description,
            status=TaskStatus(model.status),
            priority=model.priority,
            attempt_count=model.attempt_count,
            max_attempts=model.max_attempts,
            created_at=model.created_at,
            started_at=model.started_at,
            completed_at=model.completed_at,
            failure_reason=model.failure_reason,
            metadata=_deserialize_metadata(model.task_metadata),
        )
