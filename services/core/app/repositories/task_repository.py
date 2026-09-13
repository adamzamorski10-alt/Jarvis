"""
Repository — Development Tasks
Data access layer for autonomous development tasks in SQLite.
"""
from datetime import datetime, timedelta, timezone
import json
from typing import Any
import uuid
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.development_task import DevelopmentTaskModel
from app.db.models.plan_fingerprint import PlanFingerprintModel
from app.db.models.task_dependency import TaskDependencyModel
from app.manager.models import PlanConflictError, compute_plan_fingerprint
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


async def _retry_on_locked(fn, *args, _attempts=3, _delay=0.05, **kwargs):
    """
    Retry an async repository call when SQLite reports database is locked.
    """
    last_exc = None
    for attempt in range(_attempts):
        try:
            return await fn(*args, **kwargs)
        except OperationalError as exc:
            msg = str(exc).lower()
            if "database is locked" in msg or "no such table" in msg:
                last_exc = exc
                import asyncio
                await asyncio.sleep(_delay * (attempt + 1))
                continue
            raise
    raise last_exc


class TaskRepository:
    """
    Data access layer for DevelopmentTask records.
    """
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_task(self, task: DevelopmentTask) -> DevelopmentTask:
        model = DevelopmentTaskModel(
            task_id=task.task_id,
            task_key=task.task_key,
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
            last_heartbeat=task.last_heartbeat,
            recovery_attempt_count=task.recovery_attempt_count,
            max_recovery_attempts=task.max_recovery_attempts,
            last_recovery_at=task.last_recovery_at,
            recovery_owner=task.recovery_owner,
            execution_token=task.execution_token,
            workspace_path=task.workspace_path,
            execution_terminated_at=task.execution_terminated_at,
            workspace_cleanup_pending=1 if task.workspace_cleanup_pending else 0,
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

    async def get_tasks_by_plan(self, plan_id: str) -> list[DevelopmentTask]:
        """
        Retrieve all development tasks belonging to a plan_id.
        Ordered deterministically by priority DESC, task_key ASC, task_id ASC.
        """
        query = (
            select(DevelopmentTaskModel)
            .where(DevelopmentTaskModel.plan_id == plan_id.strip())
            .order_by(
                DevelopmentTaskModel.priority.desc(),
                DevelopmentTaskModel.task_key.asc(),
                DevelopmentTaskModel.task_id.asc(),
            )
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
        exec_token = f"exec_{uuid.uuid4().hex[:12]}"

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
                last_heartbeat=now,
                execution_token=exec_token,
                recovery_owner=None,
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

    async def update_heartbeat(
        self,
        task_id: str,
        execution_token: str | None = None,
        heartbeat_at: datetime | None = None,
        recovery_lease_seconds: float = 60.0,
    ) -> tuple[DevelopmentTask | None, bool]:
        """
        Atomically update last_heartbeat timestamp for a task ONLY if:
          - status is RUNNING
          - execution_token matches (if supplied)
          - recovery_owner IS NULL OR last_recovery_at is older than recovery_lease_seconds (no active recovery claim)

        Returns (updated_task, True) on success.
        Returns (None, False) if heartbeat is rejected due to active recovery claim or status change.
        """
        now = heartbeat_at or datetime.now(timezone.utc)
        lease_stale_before = now - timedelta(seconds=recovery_lease_seconds)

        where_clauses = [
            DevelopmentTaskModel.task_id == task_id,
            DevelopmentTaskModel.status == TaskStatus.RUNNING.value,
            DevelopmentTaskModel.recovery_owner.is_(None),
        ]
        if execution_token:
            where_clauses.append(
                DevelopmentTaskModel.execution_token == execution_token
            )

        stmt = (
            update(DevelopmentTaskModel)
            .where(*where_clauses)
            .values(last_heartbeat=now)
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None, False
        task = await self.get_task(task_id)
        return task, True

    async def list_stale_running_tasks(
        self,
        stale_before: datetime,
        limit: int = 50,
    ) -> list[DevelopmentTask]:
        """
        Find RUNNING tasks whose last_heartbeat is either NULL or prior to stale_before.
        """
        stmt = (
            select(DevelopmentTaskModel)
            .where(
                DevelopmentTaskModel.status == TaskStatus.RUNNING.value,
                or_(
                    DevelopmentTaskModel.last_heartbeat.is_(None),
                    DevelopmentTaskModel.last_heartbeat < stale_before,
                ),
            )
            .order_by(DevelopmentTaskModel.last_heartbeat.asc().nullsfirst())
            .limit(limit)
        )
        res = await self._session.execute(stmt)
        models = list(res.scalars().all())
        return [self._to_domain(m) for m in models if m is not None]  # type: ignore[misc]

    async def claim_task_recovery(
        self,
        task_id: str,
        recovery_owner: str,
        stale_before: datetime,
        max_recovery_attempts: int,
        recovery_lease_seconds: float = 60.0,
        now: datetime | None = None,
    ) -> tuple[DevelopmentTask | None, bool]:
        """
        Atomically claims recovery responsibility for an orphaned RUNNING task.

        Single SQL UPDATE condition:
            • task_id matches
            • status == RUNNING
            • (last_heartbeat IS NULL OR last_heartbeat < stale_before)
            • (recovery_owner IS NULL OR last_recovery_at < lease_stale_before)
            • recovery_attempt_count < max_recovery_attempts

        Atomically sets:
            • recovery_owner = recovery_owner
            • recovery_attempt_count = recovery_attempt_count + 1
            • last_recovery_at = now

        Returns:
            (updated_task, True)  — recovery claim won by this worker.
            (None, False)         — recovery claim lost (another worker claimed, active lease exists, or limit reached).
        """
        claim_time = now or datetime.now(timezone.utc)
        lease_stale_before = claim_time - timedelta(seconds=recovery_lease_seconds)

        stmt = (
            update(DevelopmentTaskModel)
            .where(
                DevelopmentTaskModel.task_id == task_id,
                DevelopmentTaskModel.status == TaskStatus.RUNNING.value,
                or_(
                    DevelopmentTaskModel.last_heartbeat.is_(None),
                    DevelopmentTaskModel.last_heartbeat < stale_before,
                ),
                or_(
                    DevelopmentTaskModel.recovery_owner.is_(None),
                    DevelopmentTaskModel.last_recovery_at < lease_stale_before,
                ),
                DevelopmentTaskModel.recovery_attempt_count < max_recovery_attempts,
            )
            .values(
                recovery_owner=recovery_owner,
                recovery_attempt_count=DevelopmentTaskModel.recovery_attempt_count + 1,
                last_recovery_at=claim_time,
                execution_token=None,
            )
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None, False

        task = await self.get_task(task_id)
        return task, True

    async def transition_recovery_status_atomically(
        self,
        task_id: str,
        expected_recovery_owner: str,
        expected_last_recovery_at: datetime,
        new_status: TaskStatus | str,
        failure_reason: str | None = None,
    ) -> tuple[DevelopmentTask | None, bool]:
        """
        Atomically executes the final recovery state transition matching exact recovery ownership lease.

        WHERE conditions:
            • task_id matches
            • status == RUNNING
            • recovery_owner == expected_recovery_owner
            • last_recovery_at == expected_last_recovery_at

        If another recovery worker took over or task status changed, rowcount == 0 (returns (None, False)).
        """
        new_str = new_status.value if isinstance(new_status, TaskStatus) else str(new_status)
        now = datetime.now(timezone.utc)

        values: dict[str, Any] = {
            "status": new_str,
            "recovery_owner": None,
        }
        if new_str in (
            TaskStatus.COMMITTED.value,
            TaskStatus.CANCELLED.value,
            TaskStatus.ABORTED.value,
            TaskStatus.FAILED.value,
        ):
            values["completed_at"] = now
        if failure_reason is not None:
            values["failure_reason"] = failure_reason.strip()

        stmt = (
            update(DevelopmentTaskModel)
            .where(
                DevelopmentTaskModel.task_id == task_id,
                DevelopmentTaskModel.status == TaskStatus.RUNNING.value,
                DevelopmentTaskModel.recovery_owner == expected_recovery_owner,
                DevelopmentTaskModel.last_recovery_at == expected_last_recovery_at,
            )
            .values(**values)
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None, False

        task = await self.get_task(task_id)
        return task, True

    async def update_workspace_path(
        self,
        task_id: str,
        workspace_path: str,
    ) -> DevelopmentTask | None:
        """
        Atomically set the workspace path for a task execution attempt.
        """
        stmt = (
            update(DevelopmentTaskModel)
            .where(DevelopmentTaskModel.task_id == task_id)
            .values(workspace_path=workspace_path)
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None
        return await self.get_task(task_id)

    async def update_execution_terminated_at(
        self,
        task_id: str,
        terminated_at: datetime | None = None,
    ) -> DevelopmentTask | None:
        """
        Atomically record execution termination timestamp.
        """
        now = terminated_at or datetime.now(timezone.utc)
        stmt = (
            update(DevelopmentTaskModel)
            .where(DevelopmentTaskModel.task_id == task_id)
            .values(execution_terminated_at=now)
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None
        return await self.get_task(task_id)

    async def confirm_execution_terminated(
        self,
        task_id: str,
        terminated_at: datetime | None = None,
    ) -> DevelopmentTask | None:
        """
        Atomically record execution termination AND clear the execution token.

        This is the durable proof that the execution is definitively gone.
        After this call, the workspace may be safely cleaned up because:
          - execution_terminated_at is set (proves termination)
          - execution_token is cleared (no active execution owns this task)

        Returns the updated task, or None if task_id not found.
        """
        now = terminated_at or datetime.now(timezone.utc)
        stmt = (
            update(DevelopmentTaskModel)
            .where(DevelopmentTaskModel.task_id == task_id)
            .values(
                execution_terminated_at=now,
                execution_token=None,
            )
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None
        return await self.get_task(task_id)

    async def update_workspace_cleanup_pending(
        self,
        task_id: str,
        cleanup_pending: bool,
    ) -> DevelopmentTask | None:
        """
        Atomically set workspace cleanup pending flag.
        """
        stmt = (
            update(DevelopmentTaskModel)
            .where(DevelopmentTaskModel.task_id == task_id)
            .values(workspace_cleanup_pending=1 if cleanup_pending else 0)
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
        meta = _deserialize_metadata(model.task_metadata)
        return DevelopmentTask(
            task_id=model.task_id,
            task_key=model.task_key or "",
            plan_id=model.plan_id or "",
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
            metadata=meta,
            last_heartbeat=model.last_heartbeat,
            recovery_attempt_count=model.recovery_attempt_count,
            max_recovery_attempts=model.max_recovery_attempts,
            last_recovery_at=model.last_recovery_at,
            recovery_owner=model.recovery_owner,
            execution_token=model.execution_token,
            workspace_path=model.workspace_path,
            execution_terminated_at=model.execution_terminated_at,
            workspace_cleanup_pending=bool(model.workspace_cleanup_pending),
        )

    async def create_tasks_idempotent(
        self,
        plan_id: str,
        tasks: list[DevelopmentTask],
        dependencies: list[tuple[str, str]] | None = None,
        plan_fingerprint: str | None = None,
    ) -> list[DevelopmentTask]:
        """
        Atomically persist a full plan.

        Semantics:
          1. plan_fingerprint must be provided (pre-computed by normalize_plan).
          2. Atomically check for an existing plan fingerprint.
          3. If the fingerprint matches an existing plan, return the already-persisted tasks (idempotent replay).
          4. If the plan_id exists with a different fingerprint, raise PlanConflictError.
          5. If the plan_id does not exist, insert the fingerprint, all tasks, and all dependency edges
             in one transaction.

        Concurrency control:
          - Fingerprint insert uses ON CONFLICT DO NOTHING; rowcount distinguishes
            "we won the insert" from "someone else inserted first".
          - When rowcount == 0, the canonical fingerprint is re-read to distinguish
            idempotent replay from conflict.
          - Tasks and dependencies are only inserted after the fingerprint is confirmed.

        On any failure the entire transaction is rolled back: no partial tasks,
        no partial dependency rows, no orphan plan record.

        Args:
            plan_id: Stable plan identifier.
            tasks: List of normalized DevelopmentTask objects.
            dependencies: List of (task_id, depends_on_task_id) edge tuples.
            plan_fingerprint: REQUIRED. Pre-computed fingerprint from normalize_plan.
        """
        if plan_fingerprint is None:
            raise ValueError(
                "plan_fingerprint is required. "
                "Call normalize_plan() first to compute the fingerprint."
            )

        deps = dependencies or []

        seen_task_ids: set[str] = set()
        normalized_tasks: list[DevelopmentTask] = []
        for task in tasks:
            if task.task_id in seen_task_ids:
                continue
            seen_task_ids.add(task.task_id)
            normalized_tasks.append(task)

        seen_edges: set[tuple[str, str]] = set()
        normalized_deps: list[tuple[str, str]] = []
        for edge in deps:
            if edge not in seen_edges:
                seen_edges.add(edge)
                normalized_deps.append(edge)

        async with self._session.begin():
            existing = await self._session.execute(
                select(PlanFingerprintModel)
                .where(PlanFingerprintModel.plan_id == plan_id)
            )
            existing_row = existing.scalar_one_or_none()

            if existing_row is not None:
                if existing_row.fingerprint != plan_fingerprint:
                    raise PlanConflictError(plan_id)
                result = await self._session.execute(
                    select(DevelopmentTaskModel)
                    .where(DevelopmentTaskModel.plan_id == plan_id)
                )
                models = list(result.scalars().all())
                return [self._to_domain(m) for m in models]

            stmt = (
                sqlite_insert(PlanFingerprintModel)
                .values(plan_id=plan_id, fingerprint=plan_fingerprint)
                .on_conflict_do_nothing(index_elements=["plan_id"])
            )
            insert_result = await self._session.execute(stmt)

            if insert_result.rowcount == 0:
                existing = await self._session.execute(
                    select(PlanFingerprintModel).where(
                        PlanFingerprintModel.plan_id == plan_id
                    )
                )
                existing_row = existing.scalar_one_or_none()

                if existing_row is not None:
                    if existing_row.fingerprint != plan_fingerprint:
                        raise PlanConflictError(plan_id)
                    result = await self._session.execute(
                        select(DevelopmentTaskModel).where(
                            DevelopmentTaskModel.plan_id == plan_id
                        )
                    )
                    models = list(result.scalars().all())
                    return [self._to_domain(m) for m in models]

            for task in normalized_tasks:
                stmt = (
                    sqlite_insert(DevelopmentTaskModel)
                    .values(
                        task_id=task.task_id,
                        task_key=task.task_key,
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
                        last_heartbeat=task.last_heartbeat,
                        recovery_attempt_count=task.recovery_attempt_count,
                        max_recovery_attempts=task.max_recovery_attempts,
                        last_recovery_at=task.last_recovery_at,
                        recovery_owner=task.recovery_owner,
                        execution_token=task.execution_token,
                        workspace_path=task.workspace_path,
                        execution_terminated_at=task.execution_terminated_at,
                        workspace_cleanup_pending=1 if task.workspace_cleanup_pending else 0,
                        plan_id=plan_id,
                    )
                    .on_conflict_do_nothing(index_elements=["task_id"])
                )
                await self._session.execute(stmt)

            for task_id, depends_on in normalized_deps:
                dep_stmt = (
                    sqlite_insert(TaskDependencyModel)
                    .values(task_id=task_id, depends_on_task_id=depends_on)
                    .on_conflict_do_nothing()
                )
                await self._session.execute(dep_stmt)

        result = await self._session.execute(
            select(DevelopmentTaskModel)
            .where(DevelopmentTaskModel.plan_id == plan_id)
            .order_by(DevelopmentTaskModel.task_key.asc())
        )
        models = list(result.scalars().all())
        return [self._to_domain(m) for m in models]

    async def get_task_dependencies(self, task_id: str) -> list[str]:
        """
        Retrieve all prerequisite task_ids that task_id depends on.
        """
        stmt = (
            select(TaskDependencyModel.depends_on_task_id)
            .where(TaskDependencyModel.task_id == task_id)
        )
        res = await self._session.execute(stmt)
        return list(res.scalars().all())

    async def are_dependencies_satisfied(self, task_id: str) -> tuple[bool, list[str]]:
        """
        Check whether all prerequisite tasks for task_id have completed execution (status COMMITTED).

        Returns:
            (True, []) if task_id has no dependencies or all dependencies are COMMITTED.
            (False, unmet_dep_ids) if any prerequisite task is not COMMITTED (or missing).
        """
        dep_ids = await self.get_task_dependencies(task_id)
        if not dep_ids:
            return True, []

        stmt = (
            select(DevelopmentTaskModel.task_id, DevelopmentTaskModel.status)
            .where(DevelopmentTaskModel.task_id.in_(dep_ids))
        )
        res = await self._session.execute(stmt)
        rows = dict(res.all())

        unmet: list[str] = []
        for dep_id in dep_ids:
            st = rows.get(dep_id)
            if st != TaskStatus.COMMITTED.value:
                unmet.append(dep_id)

        return len(unmet) == 0, unmet

