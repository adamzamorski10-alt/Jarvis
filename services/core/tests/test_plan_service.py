"""
Tests — PlanService persistence bridge.
"""
from __future__ import annotations

import asyncio
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.db.database import Base
from app.db.models.development_task import DevelopmentTaskModel
from app.db.models.plan_fingerprint import PlanFingerprintModel
from app.manager.models import (
    ManagerInvalidPlanError,
    ManagerPlanResult,
    PlanConflictError,
    PlanningConstraints,
    compute_plan_fingerprint,
    compute_task_id,
    GeminiTaskProposal,
    UserPlan,
    normalize_plan,
)
from app.repositories.task_repository import TaskRepository
from app.services.plan_service import PlanService
from app.tasks.models import DevelopmentTask, TaskStatus


# ===========================================================================
# DB fixture
# ===========================================================================

@pytest.fixture
async def db_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_fk(dbapi_conn, connection_record):
        dbapi_conn.execute("PRAGMA foreign_keys = ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


# ===========================================================================
# Helpers
# ===========================================================================

def _build_manager_result(
    plan_id: str,
    project_id: str,
    proposals: list[GeminiTaskProposal],
    acceptance_criteria=None,
    task_graph=None,
) -> ManagerPlanResult:
    user_plan = UserPlan(
        plan_id=plan_id,
        user_request="req",
        project_id=project_id,
        acceptance_criteria=acceptance_criteria or [],
        planning_constraints=PlanningConstraints(default_max_attempts=3),
    )
    return normalize_plan(user_plan, proposals)


# ===========================================================================
# 1. Successful persistence
# ===========================================================================

class TestPersistSuccess:
    async def test_persist_valid_plan_writes_tasks(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        service = PlanService(repo)

        proposals = [
            GeminiTaskProposal(proposal_key="a", title="A", description="d", priority=1),
            GeminiTaskProposal(proposal_key="b", title="B", description="d", priority=2),
        ]
        result = _build_manager_result("plan_ok", "proj", proposals)

        persisted = await service.persist_manager_plan_result(result)
        await db_session.commit()

        assert len(persisted) == 2
        assert {t.task_key for t in persisted} == {"a", "b"}
        assert all(t.status == "PENDING" for t in persisted)
        assert all(t.project_id == "proj" for t in persisted)

    async def test_persist_valid_plan_writes_fingerprint(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        service = PlanService(repo)

        proposals = [
            GeminiTaskProposal(proposal_key="a", title="A", description="d"),
        ]
        result = _build_manager_result("plan_fp", "proj", proposals)

        await service.persist_manager_plan_result(result)
        await db_session.commit()

        fp_row = await db_session.execute(
            select(PlanFingerprintModel).where(PlanFingerprintModel.plan_id == "plan_fp")
        )
        row = fp_row.scalar_one_or_none()
        assert row is not None
        assert row.fingerprint == result.plan_fingerprint

    async def test_persist_writes_dependencies(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        service = PlanService(repo)

        proposals = [
            GeminiTaskProposal(proposal_key="a", title="A", description="d"),
            GeminiTaskProposal(proposal_key="b", title="B", description="d", dependencies=["a"]),
        ]
        result = _build_manager_result("plan_deps", "proj", proposals)

        await service.persist_manager_plan_result(result)
        await db_session.commit()

        deps = await db_session.execute(text("SELECT * FROM task_dependencies"))
        rows = list(deps)
        assert len(rows) == 1

        task_a_id = compute_task_id("plan_deps", "a")
        task_b_id = compute_task_id("plan_deps", "b")
        assert rows[0].task_id == task_b_id
        assert rows[0].depends_on_task_id == task_a_id


# ===========================================================================
# 2. Rejected plan
# ===========================================================================

class TestRejectedPlan:
    async def test_rejected_plan_raises(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        service = PlanService(repo)

        proposals = [
            GeminiTaskProposal(proposal_key="a", title="A", description="d"),
        ]
        user_plan = UserPlan(
            plan_id="plan_rej",
            user_request="req",
            project_id="proj",
            acceptance_criteria=[],
            planning_constraints=PlanningConstraints(default_max_attempts=3),
        )
        result = ManagerPlanResult(
            plan_id="plan_rej",
            tasks=[],
            task_graph={},
            plan_fingerprint="fp",
            rejected=True,
            rejection_reason="unsafe",
        )

        with pytest.raises(ManagerInvalidPlanError, match="unsafe"):
            await service.persist_manager_plan_result(result)

    async def test_rejected_plan_performs_no_persistence(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        service = PlanService(repo)

        result = ManagerPlanResult(
            plan_id="plan_rej2",
            tasks=[],
            task_graph={},
            plan_fingerprint="fp",
            rejected=True,
            rejection_reason="unsafe",
        )

        with pytest.raises(ManagerInvalidPlanError):
            await service.persist_manager_plan_result(result)

        # Verify zero tasks and zero fingerprint rows
        tasks = await db_session.execute(
            select(DevelopmentTaskModel).where(DevelopmentTaskModel.plan_id == "plan_rej2")
        )
        assert tasks.scalars().first() is None

        fp = await db_session.execute(
            select(PlanFingerprintModel).where(PlanFingerprintModel.plan_id == "plan_rej2")
        )
        assert fp.scalar_one_or_none() is None


# ===========================================================================
# 3. Dependency conversion
# ===========================================================================

class TestDependencyConversion:
    async def test_task_graph_edges_persisted(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        service = PlanService(repo)

        proposals = [
            GeminiTaskProposal(proposal_key="a", title="A", description="d"),
            GeminiTaskProposal(proposal_key="b", title="B", description="d", dependencies=["a"]),
            GeminiTaskProposal(proposal_key="c", title="C", description="d", dependencies=["a", "b"]),
        ]
        result = _build_manager_result("plan_graph", "proj", proposals)

        await service.persist_manager_plan_result(result)
        await db_session.commit()

        deps = await db_session.execute(text("SELECT * FROM task_dependencies ORDER BY task_id"))
        rows = list(deps)
        assert len(rows) == 3

        task_a_id = compute_task_id("plan_graph", "a")
        task_b_id = compute_task_id("plan_graph", "b")
        task_c_id = compute_task_id("plan_graph", "c")

        expected = {
            (task_b_id, task_a_id),
            (task_c_id, task_a_id),
            (task_c_id, task_b_id),
        }
        actual = {(r.task_id, r.depends_on_task_id) for r in rows}
        assert actual == expected


# ===========================================================================
# 4. No execution side effects
# ===========================================================================

class TestNoExecutionSideEffects:
    def test_plan_service_has_no_execution_imports(self):
        import app.services.plan_service as ps
        source = open(ps.__file__).read()
        forbidden = [
            "AutonomousTaskOrchestrator",
            "RunController",
            "StateMachine",
            "VerificationGate",
            "WorkspaceManager",
            "ToolExecutionService",
            "PermissionEngine",
        ]
        for name in forbidden:
            assert name not in source, f"PlanService must not import {name}"


# ===========================================================================
# 5. Idempotent replay
# ===========================================================================

class TestIdempotentReplay:
    async def test_replay_returns_existing_tasks_without_duplicates(
        self, db_session: AsyncSession
    ):
        repo = TaskRepository(db_session)
        service = PlanService(repo)

        proposals = [
            GeminiTaskProposal(proposal_key="a", title="A", description="d"),
            GeminiTaskProposal(proposal_key="b", title="B", description="d"),
        ]
        result = _build_manager_result("plan_replay", "proj", proposals)

        first = await service.persist_manager_plan_result(result)
        await db_session.commit()

        second = await service.persist_manager_plan_result(result)
        await db_session.commit()

        assert len(first) == 2
        assert len(second) == 2
        assert {t.task_id for t in first} == {t.task_id for t in second}
        assert {t.task_key for t in second} == {"a", "b"}

        rows = await db_session.execute(
            select(DevelopmentTaskModel).where(
                DevelopmentTaskModel.plan_id == "plan_replay"
            )
        )
        all_rows = list(rows.scalars().all())
        assert len(all_rows) == 2


# ===========================================================================
# 6. Plan conflict
# ===========================================================================

class TestPlanConflict:
    async def test_conflicting_fingerprint_raises_and_preserves_original(
        self, db_session: AsyncSession
    ):
        repo = TaskRepository(db_session)
        service = PlanService(repo)

        proposals_v1 = [
            GeminiTaskProposal(proposal_key="a", title="A v1", description="d"),
        ]
        result_v1 = _build_manager_result("plan_conflict", "proj", proposals_v1)
        await service.persist_manager_plan_result(result_v1)
        await db_session.commit()

        proposals_v2 = [
            GeminiTaskProposal(proposal_key="a", title="A v2", description="different"),
        ]
        result_v2 = _build_manager_result("plan_conflict", "proj", proposals_v2)

        with pytest.raises(PlanConflictError):
            await service.persist_manager_plan_result(result_v2)

        tasks = await db_session.execute(
            select(DevelopmentTaskModel).where(
                DevelopmentTaskModel.plan_id == "plan_conflict"
            )
        )
        rows = list(tasks.scalars().all())
        assert len(rows) == 1
        assert rows[0].title == "A v1"


# ===========================================================================
# 7. Duplicate task_key rejection
# ===========================================================================

class TestDuplicateTaskKeyRejection:
    async def test_duplicate_task_key_rejected_before_persistence(
        self, db_session: AsyncSession
    ):
        repo = TaskRepository(db_session)
        service = PlanService(repo)

        task_a = DevelopmentTask(
            task_id=compute_task_id("plan_dup", "a"),
            task_key="a",
            project_id="proj",
            title="A",
            description="d",
        )
        task_a_dup = DevelopmentTask(
            task_id=compute_task_id("plan_dup", "a"),
            task_key="a",
            project_id="proj",
            title="A duplicate",
            description="d2",
        )

        fingerprint = compute_plan_fingerprint(
            plan_id="plan_dup",
            project_id="proj",
            acceptance_criteria=[],
            tasks=[task_a, task_a_dup],
            dependencies=[],
        )

        result = ManagerPlanResult(
            plan_id="plan_dup",
            tasks=[task_a, task_a_dup],
            task_graph={},
            plan_fingerprint=fingerprint,
        )

        with pytest.raises(ManagerInvalidPlanError, match="Duplicate task_key"):
            await service.persist_manager_plan_result(result)

        tasks = await db_session.execute(
            select(DevelopmentTaskModel).where(
                DevelopmentTaskModel.plan_id == "plan_dup"
            )
        )
        assert tasks.scalars().first() is None


# ===========================================================================
# 8. Invalid normalized task state
# ===========================================================================

class TestInvalidNormalizedTaskState:
    async def test_rejects_non_pending_status(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        service = PlanService(repo)

        task = DevelopmentTask(
            task_id=compute_task_id("plan_bad", "a"),
            task_key="a",
            project_id="proj",
            title="A",
            description="d",
            status=TaskStatus.RUNNING,
        )
        fingerprint = compute_plan_fingerprint(
            plan_id="plan_bad",
            project_id="proj",
            acceptance_criteria=[],
            tasks=[task],
            dependencies=[],
        )
        result = ManagerPlanResult(
            plan_id="plan_bad",
            tasks=[task],
            task_graph={},
            plan_fingerprint=fingerprint,
        )

        with pytest.raises(ManagerInvalidPlanError, match="has status"):
            await service.persist_manager_plan_result(result)

        tasks = await db_session.execute(
            select(DevelopmentTaskModel).where(
                DevelopmentTaskModel.plan_id == "plan_bad"
            )
        )
        assert tasks.scalars().first() is None

    async def test_rejects_non_zero_attempt_count(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        service = PlanService(repo)

        task = DevelopmentTask(
            task_id=compute_task_id("plan_bad2", "a"),
            task_key="a",
            project_id="proj",
            title="A",
            description="d",
            attempt_count=5,
        )
        fingerprint = compute_plan_fingerprint(
            plan_id="plan_bad2",
            project_id="proj",
            acceptance_criteria=[],
            tasks=[task],
            dependencies=[],
        )
        result = ManagerPlanResult(
            plan_id="plan_bad2",
            tasks=[task],
            task_graph={},
            plan_fingerprint=fingerprint,
        )

        with pytest.raises(ManagerInvalidPlanError, match="non-zero attempt_count"):
            await service.persist_manager_plan_result(result)

        tasks = await db_session.execute(
            select(DevelopmentTaskModel).where(
                DevelopmentTaskModel.plan_id == "plan_bad2"
            )
        )
        assert tasks.scalars().first() is None

    async def test_rejects_populated_workspace_path(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        service = PlanService(repo)

        task = DevelopmentTask(
            task_id=compute_task_id("plan_bad3", "a"),
            task_key="a",
            project_id="proj",
            title="A",
            description="d",
            workspace_path="/tmp/evil",
        )
        fingerprint = compute_plan_fingerprint(
            plan_id="plan_bad3",
            project_id="proj",
            acceptance_criteria=[],
            tasks=[task],
            dependencies=[],
        )
        result = ManagerPlanResult(
            plan_id="plan_bad3",
            tasks=[task],
            task_graph={},
            plan_fingerprint=fingerprint,
        )

        with pytest.raises(ManagerInvalidPlanError, match="workspace_path populated"):
            await service.persist_manager_plan_result(result)

        tasks = await db_session.execute(
            select(DevelopmentTaskModel).where(
                DevelopmentTaskModel.plan_id == "plan_bad3"
            )
        )
        assert tasks.scalars().first() is None

    async def test_rejects_populated_execution_token(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        service = PlanService(repo)

        task = DevelopmentTask(
            task_id=compute_task_id("plan_bad4", "a"),
            task_key="a",
            project_id="proj",
            title="A",
            description="d",
            execution_token="hijack",
        )
        fingerprint = compute_plan_fingerprint(
            plan_id="plan_bad4",
            project_id="proj",
            acceptance_criteria=[],
            tasks=[task],
            dependencies=[],
        )
        result = ManagerPlanResult(
            plan_id="plan_bad4",
            tasks=[task],
            task_graph={},
            plan_fingerprint=fingerprint,
        )

        with pytest.raises(ManagerInvalidPlanError, match="execution_token populated"):
            await service.persist_manager_plan_result(result)

        tasks = await db_session.execute(
            select(DevelopmentTaskModel).where(
                DevelopmentTaskModel.plan_id == "plan_bad4"
            )
        )
        assert tasks.scalars().first() is None


# ===========================================================================
# 9. Canonical task ID
# ===========================================================================

class TestCanonicalTaskId:
    async def test_non_canonical_task_id_rejected(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        service = PlanService(repo)

        task = DevelopmentTask(
            task_id="task_not_canonical",
            task_key="a",
            project_id="proj",
            title="A",
            description="d",
        )
        fingerprint = compute_plan_fingerprint(
            plan_id="plan_bad_id",
            project_id="proj",
            acceptance_criteria=[],
            tasks=[task],
            dependencies=[],
        )
        result = ManagerPlanResult(
            plan_id="plan_bad_id",
            tasks=[task],
            task_graph={},
            plan_fingerprint=fingerprint,
        )

        with pytest.raises(ManagerInvalidPlanError, match="non-canonical task_id"):
            await service.persist_manager_plan_result(result)

        tasks = await db_session.execute(
            select(DevelopmentTaskModel).where(
                DevelopmentTaskModel.plan_id == "plan_bad_id"
            )
        )
        assert tasks.scalars().first() is None
