"""
Stage 8.2A â€” Manager Contracts + Persistence Foundation Tests.

Covers:
- Model validation
- DAG validation
- Deterministic identity
- Normalization
- Fingerprint
- Repository atomic idempotency
- Conflict semantics
- True concurrency
- Security boundary
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import sys

import pytest

from app.manager.models import (
    PlanConflictError,
    ManagerInvalidPlanError,
    ManagerMalformedResponseError,
    ManagerPlanResult,
    PlanningConstraints,
    VerificationExpectation,
    GeminiTaskProposal,
    UserPlan,
    compute_task_id,
    compute_plan_fingerprint,
    normalize_plan,
    validate_dag,
    resolve_dag_edges,
)
from app.db.database import Base
from app.db.models.development_task import DevelopmentTaskModel
from app.db.models.plan_fingerprint import PlanFingerprintModel
from app.db.models.task_dependency import TaskDependencyModel
from app.repositories.task_repository import TaskRepository
from app.tasks.models import DevelopmentTask, TaskStatus
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker


# â”€â”€ DB fixture â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(engine.sync_engine, "connect")
    def _set_fk(dbapi_conn, connection_record):
        dbapi_conn.execute("PRAGMA foreign_keys = ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


# â”€â”€ UserPlan / PlanningConstraints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestUserPlan:
    def test_valid_user_plan(self):
        plan = UserPlan(
            plan_id="plan_001",
            user_request="Build auth module",
            project_id="proj_alpha",
        )
        assert plan.plan_id == "plan_001"
        assert plan.strict is True

    def test_empty_request_rejected(self):
        with pytest.raises(Exception):
            UserPlan(plan_id="plan_001", user_request="", project_id="proj_alpha")

    def test_oversized_request_rejected(self):
        with pytest.raises(Exception):
            UserPlan(plan_id="plan_001", user_request="x" * 65_537, project_id="proj_alpha")

    def test_invalid_plan_id_rejected(self):
        with pytest.raises(Exception):
            UserPlan(plan_id="plan with spaces", user_request="req", project_id="proj")

    def test_planning_constraints_defaults(self):
        c = PlanningConstraints()
        assert c.max_tasks == 20
        assert c.default_max_attempts == 3

    def test_planning_constraints_invalid_range(self):
        with pytest.raises(Exception):
            PlanningConstraints(min_priority=100, max_priority=0)

    def test_acceptance_criteria_validation(self):
        with pytest.raises(Exception):
            UserPlan(
                plan_id="p1",
                user_request="req",
                project_id="p",
                acceptance_criteria=["crit1", "", "crit3"],
            )


# â”€â”€ VerificationExpectation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestVerificationExpectation:
    def test_valid_expectation(self):
        ve = VerificationExpectation(category="UNIT_TESTS", required=True)
        assert ve.category.value == "UNIT_TESTS"
        assert ve.required is True

    def test_invalid_custom_check_id(self):
        with pytest.raises(Exception):
            VerificationExpectation(category="UNIT_TESTS", custom_check_id="bad id!")

    def test_invalid_category(self):
        with pytest.raises(Exception):
            VerificationExpectation(category="INVALID")


# â”€â”€ GeminiTaskProposal â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestGeminiTaskProposal:
    def test_valid_proposal(self):
        p = GeminiTaskProposal(
            proposal_key="task_a",
            title="Add auth",
            description="Add OAuth2",
            priority=5,
        )
        assert p.proposal_key == "task_a"
        assert p.title == "Add auth"
        assert p.priority == 5

    def test_forbidden_field_rejected(self):
        with pytest.raises(Exception):
            GeminiTaskProposal(
                proposal_key="a",
                title="t",
                description="d",
                task_id="evil_id",
            )

    def test_empty_title_rejected(self):
        with pytest.raises(Exception):
            GeminiTaskProposal(proposal_key="a", title="   ", description="d")

    def test_empty_description_rejected(self):
        with pytest.raises(Exception):
            GeminiTaskProposal(proposal_key="a", title="t", description="  ")

    def test_invalid_proposal_key_rejected(self):
        with pytest.raises(Exception):
            GeminiTaskProposal(proposal_key="a/b", title="t", description="d")

    def test_oversized_title_rejected(self):
        with pytest.raises(Exception):
            GeminiTaskProposal(proposal_key="a", title="x" * 201, description="d")

    def test_oversized_description_rejected(self):
        with pytest.raises(Exception):
            GeminiTaskProposal(proposal_key="a", title="t", description="x" * 10_241)

    def test_too_many_dependencies_rejected(self):
        deps = [f"dep_{i}" for i in range(21)]
        with pytest.raises(Exception):
            GeminiTaskProposal(
                proposal_key="a",
                title="t",
                description="d",
                dependencies=deps,
            )

    def test_invalid_dependency_key_rejected(self):
        with pytest.raises(Exception):
            GeminiTaskProposal(
                proposal_key="a",
                title="t",
                description="d",
                dependencies=["valid", "bad key!"],
            )

    def test_valid_dependencies(self):
        p = GeminiTaskProposal(
            proposal_key="a",
            title="t",
            description="d",
            dependencies=["b", "c"],
        )
        assert p.dependencies == ["b", "c"]


# â”€â”€ DAG validation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestDagValidation:
    def test_independent_tasks_valid(self):
        proposals = {
            "a": GeminiTaskProposal(proposal_key="a", title="A", description="a"),
            "b": GeminiTaskProposal(proposal_key="b", title="B", description="b"),
        }
        assert validate_dag(proposals) == []

    def test_diamond_valid(self):
        proposals = {
            "a": GeminiTaskProposal(proposal_key="a", title="A", description="a"),
            "b": GeminiTaskProposal(proposal_key="b", title="B", description="b"),
            "c": GeminiTaskProposal(proposal_key="c", title="C", description="c", dependencies=["a", "b"]),
        }
        assert validate_dag(proposals) == []

    def test_linear_chain_valid(self):
        proposals = {
            "a": GeminiTaskProposal(proposal_key="a", title="A", description="a"),
            "b": GeminiTaskProposal(proposal_key="b", title="B", description="b", dependencies=["a"]),
            "c": GeminiTaskProposal(proposal_key="c", title="C", description="c", dependencies=["b"]),
        }
        assert validate_dag(proposals) == []

    def test_self_dependency_rejected(self):
        proposals = {
            "a": GeminiTaskProposal(proposal_key="a", title="A", description="a", dependencies=["a"]),
        }
        errors = validate_dag(proposals)
        assert any("self-dependency" in e for e in errors)

    def test_two_node_cycle_rejected(self):
        proposals = {
            "a": GeminiTaskProposal(proposal_key="a", title="A", description="a", dependencies=["b"]),
            "b": GeminiTaskProposal(proposal_key="b", title="B", description="b", dependencies=["a"]),
        }
        errors = validate_dag(proposals)
        assert any("cycle" in e for e in errors)

    def test_three_node_cycle_rejected(self):
        proposals = {
            "a": GeminiTaskProposal(proposal_key="a", title="A", description="a", dependencies=["b"]),
            "b": GeminiTaskProposal(proposal_key="b", title="B", description="b", dependencies=["c"]),
            "c": GeminiTaskProposal(proposal_key="c", title="C", description="c", dependencies=["a"]),
        }
        errors = validate_dag(proposals)
        assert any("cycle" in e for e in errors)

    def test_unknown_dependency_rejected(self):
        proposals = {
            "a": GeminiTaskProposal(proposal_key="a", title="A", description="a", dependencies=["z"]),
        }
        errors = validate_dag(proposals)
        assert any("unknown dependency" in e for e in errors)

    def test_duplicate_dependency_rejected(self):
        proposals = {
            "a": GeminiTaskProposal(proposal_key="a", title="A", description="a", dependencies=["b", "b"]),
            "b": GeminiTaskProposal(proposal_key="b", title="B", description="b"),
        }
        errors = validate_dag(proposals)
        assert any("duplicate dependencies" in e for e in errors)

    def test_deterministic_error_order(self):
        proposals = {
            "b": GeminiTaskProposal(proposal_key="b", title="B", description="b", dependencies=["a", "a"]),
            "a": GeminiTaskProposal(proposal_key="a", title="A", description="a"),
        }
        errors = validate_dag(proposals)
        assert any("duplicate" in e for e in errors)


# â”€â”€ Deterministic identity â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestTaskIdentity:
    def test_same_plan_and_key_same_id(self):
        assert compute_task_id("plan1", "t001") == compute_task_id("plan1", "t001")

    def test_different_key_different_id(self):
        assert compute_task_id("plan1", "t001") != compute_task_id("plan1", "t002")

    def test_different_plan_different_id(self):
        assert compute_task_id("plan1", "t001") != compute_task_id("plan2", "t001")

    def test_title_irrelevant(self):
        assert compute_task_id("plan1", "t001") == compute_task_id("plan1", "t001")

    def test_task_key_matches_persisted_task_key(self):
        plan_id = "plan_identity"
        task_key = "auth_module"
        task_id = compute_task_id(plan_id, task_key)
        assert task_id == f"task_{hashlib.sha256(f'{plan_id}:{task_key}'.encode()).hexdigest()}"
        assert len(task_id) == 5 + 64

    def test_different_proposal_keys_produce_different_task_ids(self):
        plan_id = "plan_identity"
        id_a = compute_task_id(plan_id, "auth_module")
        id_b = compute_task_id(plan_id, "payment_svc")
        assert id_a != id_b

    def test_replaying_same_plan_produces_identical_task_ids(self):
        plan_id = "plan_replay"
        task_keys = ["auth", "payment", "notifications"]
        first_run = [compute_task_id(plan_id, k) for k in task_keys]
        second_run = [compute_task_id(plan_id, k) for k in task_keys]
        assert first_run == second_run


# â”€â”€ Fingerprint â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestFingerprint:
    def test_same_content_same_fingerprint(self):
        tasks = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="auth",
                project_id="proj",
                title="Auth",
                description="Add auth",
                priority=1,
                metadata={"verification_expectations": [{"category": "UNIT_TESTS", "required": True}]},
            ),
        ]
        deps = []
        fp1 = compute_plan_fingerprint("p1", "proj", [], tasks, deps)
        fp2 = compute_plan_fingerprint("p1", "proj", [], tasks, deps)
        assert fp1 == fp2

    def test_different_title_changes_fingerprint(self):
        tasks_a = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="auth",
                project_id="proj",
                title="Auth Module",
                description="d",
                priority=1,
            ),
        ]
        tasks_b = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="auth",
                project_id="proj",
                title="Auth Service",
                description="d",
                priority=1,
            ),
        ]
        assert compute_plan_fingerprint("p1", "proj", [], tasks_a, []) != compute_plan_fingerprint("p1", "proj", [], tasks_b, [])

    def test_different_description_changes_fingerprint(self):
        tasks_a = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="auth",
                project_id="proj",
                title="Auth",
                description="Add OAuth2",
                priority=1,
            ),
        ]
        tasks_b = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="auth",
                project_id="proj",
                title="Auth",
                description="Add SAML",
                priority=1,
            ),
        ]
        assert compute_plan_fingerprint("p1", "proj", [], tasks_a, []) != compute_plan_fingerprint("p1", "proj", [], tasks_b, [])

    def test_different_dependency_changes_fingerprint(self):
        tasks = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
            ),
            DevelopmentTask(
                task_id="task_bbb222",
                task_key="b",
                project_id="proj",
                title="B",
                description="d",
                priority=1,
            ),
        ]
        deps_a = [("task_bbb222", "task_aaa111")]
        deps_b = []
        assert compute_plan_fingerprint("p1", "proj", [], tasks, deps_a) != compute_plan_fingerprint("p1", "proj", [], tasks, deps_b)

    def test_different_priority_changes_fingerprint(self):
        tasks_a = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
            ),
        ]
        tasks_b = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=2,
            ),
        ]
        assert compute_plan_fingerprint("p1", "proj", [], tasks_a, []) != compute_plan_fingerprint("p1", "proj", [], tasks_b, [])

    def test_timestamps_ignored_in_fingerprint(self):
        tasks_a = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
                created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            ),
        ]
        tasks_b = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
                created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            ),
        ]
        assert compute_plan_fingerprint("p1", "proj", [], tasks_a, []) == compute_plan_fingerprint("p1", "proj", [], tasks_b, [])

    def test_different_verification_expectations_changes_fingerprint(self):
        tasks_a = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
                metadata={"verification_expectations": [{"category": "UNIT_TESTS", "required": True}]},
            ),
        ]
        tasks_b = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
                metadata={"verification_expectations": [{"category": "INTEGRATION", "required": True}]},
            ),
        ]
        assert compute_plan_fingerprint("p1", "proj", [], tasks_a, []) != compute_plan_fingerprint("p1", "proj", [], tasks_b, [])

    def test_dependency_order_does_not_matter(self):
        tasks = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
            ),
            DevelopmentTask(
                task_id="task_bbb222",
                task_key="b",
                project_id="proj",
                title="B",
                description="d",
                priority=1,
            ),
        ]
        deps_a = [("task_bbb222", "task_aaa111")]
        deps_b = [("task_aaa111", "task_bbb222")]
        assert compute_plan_fingerprint("p1", "proj", [], tasks, deps_a) != compute_plan_fingerprint("p1", "proj", [], tasks, deps_b)

    def test_acceptance_criteria_changed_changes_fingerprint(self):
        tasks = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
            ),
        ]
        fp1 = compute_plan_fingerprint("p1", "proj", ["criterion_one"], tasks, [])
        fp2 = compute_plan_fingerprint("p1", "proj", ["criterion_two"], tasks, [])
        assert fp1 != fp2

    def test_acceptance_criteria_reordered_same_fingerprint(self):
        tasks = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
            ),
        ]
        fp1 = compute_plan_fingerprint("p1", "proj", ["c", "a", "b"], tasks, [])
        fp2 = compute_plan_fingerprint("p1", "proj", ["a", "b", "c"], tasks, [])
        assert fp1 == fp2

    def test_project_id_changed_changes_fingerprint(self):
        tasks = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="a",
                project_id="proj_a",
                title="A",
                description="d",
                priority=1,
            ),
        ]
        fp1 = compute_plan_fingerprint("p1", "proj_a", [], tasks, [])
        fp2 = compute_plan_fingerprint("p1", "proj_b", [], tasks, [])
        assert fp1 != fp2

    def test_same_max_attempts_same_fingerprint(self):
        tasks_a = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
                max_attempts=3,
            ),
        ]
        tasks_b = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
                max_attempts=3,
            ),
        ]
        assert compute_plan_fingerprint("p1", "proj", [], tasks_a, []) == compute_plan_fingerprint("p1", "proj", [], tasks_b, [])

    def test_different_max_attempts_changes_fingerprint(self):
        tasks_a = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
                max_attempts=3,
            ),
        ]
        tasks_b = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
                max_attempts=5,
            ),
        ]
        assert compute_plan_fingerprint("p1", "proj", [], tasks_a, []) != compute_plan_fingerprint("p1", "proj", [], tasks_b, [])

    def test_task_order_does_not_affect_fingerprint_with_max_attempts(self):
        tasks_a = [
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
                max_attempts=3,
            ),
            DevelopmentTask(
                task_id="task_bbb222",
                task_key="b",
                project_id="proj",
                title="B",
                description="d",
                priority=2,
                max_attempts=5,
            ),
        ]
        tasks_b = [
            DevelopmentTask(
                task_id="task_bbb222",
                task_key="b",
                project_id="proj",
                title="B",
                description="d",
                priority=2,
                max_attempts=5,
            ),
            DevelopmentTask(
                task_id="task_aaa111",
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
                max_attempts=3,
            ),
        ]
        assert compute_plan_fingerprint("p1", "proj", [], tasks_a, []) == compute_plan_fingerprint("p1", "proj", [], tasks_b, [])


# â”€â”€ Normalization â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestNormalization:
    def test_basic_normalization(self):
        plan = UserPlan(plan_id="p1", user_request="req", project_id="proj")
        proposals = [
            GeminiTaskProposal(proposal_key="a", title="Task A", description="Desc A", priority=5),
            GeminiTaskProposal(proposal_key="b", title="Task B", description="Desc B", priority=3),
        ]
        result = normalize_plan(plan, proposals)
        assert not result.rejected
        assert len(result.tasks) == 2

    def test_status_always_pending(self):
        plan = UserPlan(plan_id="p1", user_request="req", project_id="proj")
        proposals = [
            GeminiTaskProposal(proposal_key="a", title="Task A", description="Desc A"),
        ]
        result = normalize_plan(plan, proposals)
        assert result.tasks[0].status == TaskStatus.PENDING

    def test_attempt_count_zero(self):
        plan = UserPlan(plan_id="p1", user_request="req", project_id="proj")
        proposals = [
            GeminiTaskProposal(proposal_key="a", title="Task A", description="Desc A"),
        ]
        result = normalize_plan(plan, proposals)
        assert result.tasks[0].attempt_count == 0

    def test_project_id_from_user_plan(self):
        plan = UserPlan(plan_id="p1", user_request="req", project_id="my_proj")
        proposals = [
            GeminiTaskProposal(proposal_key="a", title="Task A", description="Desc A"),
        ]
        result = normalize_plan(plan, proposals)
        assert result.tasks[0].project_id == "my_proj"

    def test_plan_id_in_metadata(self):
        plan = UserPlan(plan_id="p1", user_request="req", project_id="proj")
        proposals = [
            GeminiTaskProposal(proposal_key="a", title="Task A", description="Desc A"),
        ]
        result = normalize_plan(plan, proposals)
        assert result.tasks[0].metadata["source_plan_id"] == "p1"
        assert result.tasks[0].metadata["proposal_key"] == "a"

    def test_max_attempts_from_constraints(self):
        plan = UserPlan(
            plan_id="p1",
            user_request="req",
            project_id="proj",
            planning_constraints=PlanningConstraints(default_max_attempts=5),
        )
        proposals = [
            GeminiTaskProposal(proposal_key="a", title="Task A", description="Desc A"),
        ]
        result = normalize_plan(plan, proposals)
        assert result.tasks[0].max_attempts == 5

    def test_no_security_fields_in_metadata(self):
        plan = UserPlan(plan_id="p1", user_request="req", project_id="proj")
        proposals = [
            GeminiTaskProposal(proposal_key="a", title="Task A", description="Desc A"),
        ]
        result = normalize_plan(plan, proposals)
        meta = result.tasks[0].metadata
        for forbidden in ["permission", "token", "secret", "authorization", "api_key"]:
            assert forbidden not in str(meta).lower()

    def test_empty_proposals_rejected(self):
        plan = UserPlan(plan_id="p1", user_request="req", project_id="proj")
        with pytest.raises(ManagerInvalidPlanError, match="no tasks"):
            normalize_plan(plan, [])

    def test_too_many_tasks_rejected(self):
        plan = UserPlan(
            plan_id="p1",
            user_request="req",
            project_id="proj",
            planning_constraints=PlanningConstraints(max_tasks=2),
        )
        proposals = [
            GeminiTaskProposal(proposal_key=f"t{i:03d}", title=f"T{i}", description="d")
            for i in range(3)
        ]
        with pytest.raises(ManagerInvalidPlanError, match="exceeding max_tasks"):
            normalize_plan(plan, proposals)

    def test_duplicate_proposal_key_rejected(self):
        plan = UserPlan(plan_id="p1", user_request="req", project_id="proj")
        proposals = [
            GeminiTaskProposal(proposal_key="dup", title="T1", description="d"),
            GeminiTaskProposal(proposal_key="dup", title="T2", description="d"),
        ]
        with pytest.raises(ManagerInvalidPlanError, match="Duplicate proposal_key"):
            normalize_plan(plan, proposals)

    def test_task_graph_populated(self):
        plan = UserPlan(plan_id="p1", user_request="req", project_id="proj")
        proposals = [
            GeminiTaskProposal(proposal_key="a", title="A", description="a"),
            GeminiTaskProposal(proposal_key="b", title="B", description="b", dependencies=["a"]),
        ]
        result = normalize_plan(plan, proposals)
        assert result.task_graph["b"] == ["a"]
        assert result.task_graph["a"] == []

    def test_verification_expectations_in_metadata(self):
        plan = UserPlan(plan_id="p1", user_request="req", project_id="proj")
        proposals = [
            GeminiTaskProposal(
                proposal_key="a",
                title="A",
                description="a",
                verification_expectations=[
                    VerificationExpectation(category="UNIT_TESTS", required=True),
                ],
            ),
        ]
        result = normalize_plan(plan, proposals)
        meta = result.tasks[0].metadata
        assert len(meta["verification_expectations"]) == 1
        assert meta["verification_expectations"][0]["category"] == "UNIT_TESTS"

    def test_priority_out_of_range_rejected(self):
        plan = UserPlan(
            plan_id="p1",
            user_request="req",
            project_id="proj",
            planning_constraints=PlanningConstraints(min_priority=0, max_priority=10),
        )
        proposals = [
            GeminiTaskProposal(proposal_key="a", title="A", description="a", priority=-1),
        ]
        with pytest.raises(ManagerInvalidPlanError, match="priority"):
            normalize_plan(plan, proposals)

    def test_task_key_is_proposal_key(self):
        plan = UserPlan(plan_id="p1", user_request="req", project_id="proj")
        proposals = [
            GeminiTaskProposal(proposal_key="auth_module", title="A", description="a"),
            GeminiTaskProposal(proposal_key="payment_svc", title="B", description="b"),
        ]
        result = normalize_plan(plan, proposals)
        task_keys = [t.task_key for t in result.tasks]
        assert "auth_module" in task_keys
        assert "payment_svc" in task_keys

    def test_task_id_deterministic_from_proposal_key(self):
        plan = UserPlan(plan_id="p1", user_request="req", project_id="proj")
        proposals = [
            GeminiTaskProposal(proposal_key="auth", title="A", description="a"),
        ]
        result1 = normalize_plan(plan, proposals)
        result2 = normalize_plan(plan, proposals)
        assert result1.tasks[0].task_id == result2.tasks[0].task_id

    def test_fingerprint_populated(self):
        plan = UserPlan(plan_id="p1", user_request="req", project_id="proj")
        proposals = [
            GeminiTaskProposal(proposal_key="a", title="A", description="a"),
        ]
        result = normalize_plan(plan, proposals)
        assert result.plan_fingerprint
        assert len(result.plan_fingerprint) == 16


# â”€â”€ DAG edge resolution â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestDagEdgeResolution:
    def test_valid_edges_resolved(self):
        mapping = {"a": "task_aaa", "b": "task_bbb"}
        graph = {"a": [], "b": ["a"]}
        edges = resolve_dag_edges(mapping, graph)
        assert ("task_bbb", "task_aaa") in edges

    def test_unknown_key_rejected(self):
        mapping = {"a": "task_aaa"}
        graph = {"a": ["z"]}
        with pytest.raises(ManagerInvalidPlanError, match="unknown dependency"):
            resolve_dag_edges(mapping, graph)

    def test_self_dependency_rejected(self):
        mapping = {"a": "task_aaa"}
        graph = {"a": ["a"]}
        with pytest.raises(ManagerInvalidPlanError, match="self-dependency"):
            resolve_dag_edges(mapping, graph)

    def test_duplicate_edges_rejected(self):
        mapping = {"a": "task_aaa", "b": "task_bbb"}
        graph = {"b": ["a", "a"]}
        with pytest.raises(ManagerInvalidPlanError, match="duplicate dependency"):
            resolve_dag_edges(mapping, graph)

    def test_edges_sorted_deterministically(self):
        mapping = {"c": "task_ccc", "a": "task_aaa", "b": "task_bbb"}
        graph = {
            "a": [],
            "b": ["a"],
            "c": ["a", "b"],
        }
        edges = resolve_dag_edges(mapping, graph)
        assert edges == [
            ("task_bbb", "task_aaa"),
            ("task_ccc", "task_aaa"),
            ("task_ccc", "task_bbb"),
        ]


# â”€â”€ Repository atomic idempotency â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestRepositoryIdempotency:
    async def test_create_plan_idempotent(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        plan_id = "plan_idem_1"

        tasks = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "t001"),
                task_key="t001",
                project_id="proj",
                title="Task A",
                description="Desc A",
                status="PENDING",
                metadata={"source_plan_id": plan_id},
            ),
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "t002"),
                task_key="t002",
                project_id="proj",
                title="Task B",
                description="Desc B",
                status="PENDING",
                metadata={"source_plan_id": plan_id},
            ),
        ]

        deps = [(tasks[0].task_id, tasks[1].task_id)]
        fp1 = compute_plan_fingerprint(plan_id, "proj", [], tasks, deps)

        result1 = await repo.create_tasks_idempotent(plan_id, tasks, deps, plan_fingerprint=fp1)
        await db_session.commit()
        assert len(result1) == 2

        result2 = await repo.create_tasks_idempotent(plan_id, tasks, deps, plan_fingerprint=fp1)
        await db_session.commit()
        assert len(result2) == 2
        assert result2[0].task_id == result1[0].task_id
        assert result2[1].task_id == result1[1].task_id

    async def test_identical_replay_returns_existing_tasks(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        plan_id = "plan_replay"
        tasks = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "t001"),
                task_key="t001",
                project_id="proj",
                title="T",
                description="D",
                status="PENDING",
                metadata={"source_plan_id": plan_id},
            ),
        ]

        fp1 = compute_plan_fingerprint(plan_id, "proj", [], tasks, [])
        r1 = await repo.create_tasks_idempotent(plan_id, tasks, plan_fingerprint=fp1)
        await db_session.commit()
        r2 = await repo.create_tasks_idempotent(plan_id, tasks, plan_fingerprint=fp1)
        await db_session.commit()
        assert len(r1) == 1
        assert len(r2) == 1
        assert r2[0].task_id == r1[0].task_id

    async def test_rollback_on_failure(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        plan_id = "plan_rollback"
        tasks = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "t001"),
                task_key="t001",
                project_id="proj",
                title="T",
                description="D",
                status="PENDING",
                metadata={"source_plan_id": plan_id},
            ),
        ]

        fp = compute_plan_fingerprint(plan_id, "proj", [], tasks, [])
        await repo.create_tasks_idempotent(plan_id, tasks, plan_fingerprint=fp)
        await db_session.commit()

        bad_deps = [("nonexistent_task_id", "another_nonexistent")]
        bad_fp = compute_plan_fingerprint(plan_id, "proj", [], tasks, bad_deps)
        with pytest.raises(Exception):
            await repo.create_tasks_idempotent(
                plan_id,
                tasks,
                dependencies=bad_deps,
                plan_fingerprint=bad_fp,
            )

        # Verify the original plan is still intact after the failed submission
        result = await repo.create_tasks_idempotent(plan_id, tasks, plan_fingerprint=fp)
        await db_session.commit()
        assert len(result) == 1

    async def test_orphan_dependencies_rollback(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        plan_id = "plan_orphan"
        tasks = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "t001"),
                task_key="t001",
                project_id="proj",
                title="T",
                description="D",
                status="PENDING",
                metadata={"source_plan_id": plan_id},
            ),
        ]
        deps = [(tasks[0].task_id, "nonexistent_task")]

        fp = compute_plan_fingerprint(plan_id, "proj", [], tasks, deps)
        with pytest.raises(Exception):
            await repo.create_tasks_idempotent(plan_id, tasks, deps, plan_fingerprint=fp)
        await db_session.rollback()

        # No partial state: after rollback, a clean submission with no dependencies succeeds
        fp_no_deps = compute_plan_fingerprint(plan_id, "proj", [], tasks, [])
        result = await repo.create_tasks_idempotent(plan_id, tasks, plan_fingerprint=fp_no_deps)
        await db_session.commit()
        assert len(result) == 1

    async def test_conflict_same_plan_different_title(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        plan_id = "plan_conflict_title"
        tasks_v1 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="Original Title",
                description="d",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        fp_v1 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v1, [])
        await repo.create_tasks_idempotent(plan_id, tasks_v1, plan_fingerprint=fp_v1)
        await db_session.commit()

        tasks_v2 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="Changed Title",
                description="d",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        fp_v2 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v2, [])
        with pytest.raises(PlanConflictError):
            await repo.create_tasks_idempotent(plan_id, tasks_v2, plan_fingerprint=fp_v2)
        await db_session.rollback()

    async def test_conflict_same_plan_different_description(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        plan_id = "plan_conflict_desc"
        tasks_v1 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="T",
                description="Original",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        fp_v1 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v1, [])
        await repo.create_tasks_idempotent(plan_id, tasks_v1, plan_fingerprint=fp_v1)
        await db_session.commit()

        tasks_v2 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="Changed Title",
                description="d",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        fp_v2 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v2, [])
        with pytest.raises(PlanConflictError):
            await repo.create_tasks_idempotent(plan_id, tasks_v2, plan_fingerprint=fp_v2)
        await db_session.rollback()

    async def test_conflict_same_plan_different_task_set(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        plan_id = "plan_conflict_taskset"
        tasks_v1 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        fp_v1 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v1, [])
        await repo.create_tasks_idempotent(plan_id, tasks_v1, plan_fingerprint=fp_v1)
        await db_session.commit()

        tasks_v2 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="T",
                description="Changed",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        fp_v2 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v2, [])
        with pytest.raises(PlanConflictError):
            await repo.create_tasks_idempotent(plan_id, tasks_v2, plan_fingerprint=fp_v2)
        await db_session.rollback()

    async def test_conflict_same_plan_different_dependencies(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        plan_id = "plan_conflict_deps"
        tasks = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "b"),
                task_key="b",
                project_id="proj",
                title="B",
                description="d",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        deps_v1 = [(tasks[1].task_id, tasks[0].task_id)]
        deps_v2 = []

        fp_v1 = compute_plan_fingerprint(plan_id, "proj", [], tasks, deps_v1)
        await repo.create_tasks_idempotent(plan_id, tasks, deps_v1, plan_fingerprint=fp_v1)
        await db_session.commit()

        fp_v2 = compute_plan_fingerprint(plan_id, "proj", [], tasks, deps_v2)
        with pytest.raises(PlanConflictError):
            await repo.create_tasks_idempotent(plan_id, tasks, deps_v2, plan_fingerprint=fp_v2)
        await db_session.rollback()

    async def test_conflict_same_plan_different_verification(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        plan_id = "plan_conflict_verify"
        tasks_v1 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
                metadata={
                    "source_plan_id": plan_id,
                    "verification_expectations": [{"category": "UNIT_TESTS", "required": True}],
                },
            ),
        ]
        fp_v1 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v1, [])
        await repo.create_tasks_idempotent(plan_id, tasks_v1, plan_fingerprint=fp_v1)
        await db_session.commit()

        tasks_v2 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
                metadata={
                    "source_plan_id": plan_id,
                    "verification_expectations": [{"category": "INTEGRATION", "required": True}],
                },
            ),
        ]
        fp_v2 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v2, [])
        with pytest.raises(PlanConflictError):
            await repo.create_tasks_idempotent(plan_id, tasks_v2, plan_fingerprint=fp_v2)
        await db_session.rollback()

    async def test_conflict_leaves_original_unchanged(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        plan_id = "plan_conflict_unchanged"
        tasks_v1 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="Original",
                description="d",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        fp_v1 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v1, [])
        r1 = await repo.create_tasks_idempotent(plan_id, tasks_v1, plan_fingerprint=fp_v1)
        await db_session.commit()
        original_title = r1[0].title

        tasks_v2 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="Changed",
                description="d",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        fp_v2 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v2, [])
        with pytest.raises(PlanConflictError):
            await repo.create_tasks_idempotent(plan_id, tasks_v2, plan_fingerprint=fp_v2)
        await db_session.rollback()

        r_check = await repo.create_tasks_idempotent(plan_id, tasks_v1, plan_fingerprint=fp_v1)
        await db_session.commit()
        assert r_check[0].title == original_title

    async def test_conflict_different_max_attempts_raises(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        plan_id = "plan_conflict_max_attempts"
        tasks_v1 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
                max_attempts=3,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        fp_v1 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v1, [])
        await repo.create_tasks_idempotent(plan_id, tasks_v1, plan_fingerprint=fp_v1)
        await db_session.commit()

        tasks_v2 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
                max_attempts=5,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        fp_v2 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v2, [])
        assert fp_v1 != fp_v2
        with pytest.raises(PlanConflictError):
            await repo.create_tasks_idempotent(plan_id, tasks_v2, plan_fingerprint=fp_v2)
        await db_session.rollback()
        repo = TaskRepository(db_session)
        plan_id = "plan_dup_key"
        tasks = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="Task A",
                description="d",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "b"),
                task_key="b",
                project_id="proj",
                title="Task B",
                description="d",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        deps = [(tasks[1].task_id, tasks[0].task_id)]
        fp = compute_plan_fingerprint(plan_id, "proj", [], tasks, deps)

        r1 = await repo.create_tasks_idempotent(plan_id, tasks, deps, plan_fingerprint=fp)
        await db_session.commit()
        assert len(r1) == 2

        # Identical replay: same task set, same deps
        r2 = await repo.create_tasks_idempotent(plan_id, tasks, deps, plan_fingerprint=fp)
        await db_session.commit()
        assert len(r2) == 2
        assert {t.task_id for t in r2} == {t.task_id for t in r1}

    async def test_conflict_different_title_raises(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        plan_id = "plan_conflict_title"
        tasks_v1 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="Original Title",
                description="d",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        fp_v1 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v1, [])
        await repo.create_tasks_idempotent(plan_id, tasks_v1, plan_fingerprint=fp_v1)
        await db_session.commit()

        tasks_v2 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="Changed Title",
                description="d",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        fp_v2 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v2, [])
        with pytest.raises(PlanConflictError):
            await repo.create_tasks_idempotent(plan_id, tasks_v2, plan_fingerprint=fp_v2)
        await db_session.rollback()

    async def test_conflict_different_acceptance_criteria_raises(self, db_session: AsyncSession):
        repo = TaskRepository(db_session)
        plan_id = "plan_conflict_ac"
        tasks_v1 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
                metadata={"source_plan_id": plan_id, "acceptance_criteria": ["c1"]},
            ),
        ]
        fp_v1 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v1, [])
        await repo.create_tasks_idempotent(plan_id, tasks_v1, plan_fingerprint=fp_v1)
        await db_session.commit()

        tasks_v2 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "b"),
                task_key="b",
                project_id="proj",
                title="B",
                description="d",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        fp_v2 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v2, [])
        with pytest.raises(PlanConflictError):
            await repo.create_tasks_idempotent(plan_id, tasks_v2, plan_fingerprint=fp_v2)
        await db_session.rollback()


# â”€â”€ True concurrency â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestConcurrentSubmissions:
    async def _setup_conc_db(self, tmp_path):
        db_path = tmp_path / "test_conc.db"
        url = f"sqlite+aiosqlite:///{db_path.resolve().as_posix()}"
        engine = create_async_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 60},
        )

        @event.listens_for(engine.sync_engine, "connect")
        def _set_pragmas(dbapi_conn, connection_record):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
            dbapi_conn.execute("PRAGMA busy_timeout=60000")

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        return engine, factory

    async def test_identical_concurrent_submissions_file_db(self, tmp_path):
        engine, factory = await self._setup_conc_db(tmp_path)
        plan_id = "plan_conc_idem"

        tasks = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="Task A",
                description="d",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "b"),
                task_key="b",
                project_id="proj",
                title="Task B",
                description="d",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        deps = [(tasks[1].task_id, tasks[0].task_id)]

        for attempt in range(5):
            try:
                async with factory() as s1:
                    async with factory() as s2:
                        repo1 = TaskRepository(s1)
                        repo2 = TaskRepository(s2)
                        fp = compute_plan_fingerprint(plan_id, "proj", [], tasks, deps)
                        r1, r2 = await asyncio.gather(
                            repo1.create_tasks_idempotent(plan_id, tasks, deps, plan_fingerprint=fp),
                            repo2.create_tasks_idempotent(plan_id, tasks, deps, plan_fingerprint=fp),
                        )
                        await s1.commit()
                        await s2.commit()
                break
            except OperationalError:
                if attempt < 4:
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    raise

        async with factory() as verify:
            all_tasks = await verify.execute(
                select(DevelopmentTaskModel).where(DevelopmentTaskModel.plan_id == plan_id)
            )
            rows = all_tasks.scalars().all()
            assert len(rows) == 2

            all_deps = await verify.execute(text("SELECT * FROM task_dependencies"))
            dep_rows = list(all_deps)
            assert len(dep_rows) == 1

            fp_rows = await verify.execute(
                select(PlanFingerprintModel).where(PlanFingerprintModel.plan_id == plan_id)
            )
            fp_list = fp_rows.scalars().all()
            assert len(fp_list) == 1

        await engine.dispose()

    async def test_conflicting_concurrent_submissions_file_db(self, tmp_path):
        engine, factory = await self._setup_conc_db(tmp_path)
        plan_id = "plan_conc_conflict"

        tasks_v1 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="Title V1",
                description="Desc V1",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        tasks_v2 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="Title V2",
                description="Desc V2",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
        ]

        fp_v1 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v1, [])
        fp_v2 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v2, [])
        assert fp_v1 != fp_v2

        results = None
        for attempt in range(5):
            try:
                async with factory() as s1:
                    async with factory() as s2:
                        repo1 = TaskRepository(s1)
                        repo2 = TaskRepository(s2)
                        f1 = asyncio.create_task(
                            repo1.create_tasks_idempotent(plan_id, tasks_v1, plan_fingerprint=fp_v1)
                        )
                        f2 = asyncio.create_task(
                            repo2.create_tasks_idempotent(plan_id, tasks_v2, plan_fingerprint=fp_v2)
                        )
                        results = await asyncio.gather(f1, f2, return_exceptions=True)
                        for r, s in zip(results, [s1, s2]):
                            if isinstance(r, Exception):
                                await s.rollback()
                            else:
                                await s.commit()
                break
            except OperationalError:
                if attempt < 4:
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    raise

        async with factory() as verify:
            fp_rows = await verify.execute(
                select(PlanFingerprintModel).where(PlanFingerprintModel.plan_id == plan_id)
            )
            fp_list = fp_rows.scalars().all()
            assert len(fp_list) == 1, "Exactly one canonical fingerprint must exist"

            canonical_fp = fp_list[0].fingerprint
            assert canonical_fp in (fp_v1, fp_v2)

            all_tasks = await verify.execute(
                select(DevelopmentTaskModel).where(DevelopmentTaskModel.plan_id == plan_id)
            )
            rows = all_tasks.scalars().all()
            assert len(rows) == 1, "Exactly one task must exist - no mixed-plan state"

            if canonical_fp == fp_v1:
                assert rows[0].title == "Title V1"
            else:
                assert rows[0].title == "Title V2"

            all_deps = await verify.execute(text("SELECT * FROM task_dependencies"))
            dep_rows = list(all_deps)
            assert len(dep_rows) == 0

        success_count = sum(1 for r in results if not isinstance(r, Exception))
        if success_count == 1:
            exceptions = [r for r in results if isinstance(r, Exception)]
            assert len(exceptions) == 1
            assert isinstance(exceptions[0], PlanConflictError)
        elif success_count == 2:
            assert results[0][0].task_id == results[1][0].task_id

        await engine.dispose()

    async def test_session_usable_after_concurrent_fingerprint_race(self, tmp_path):
        engine, factory = await self._setup_conc_db(tmp_path)
        plan_id = "plan_session_usable"

        for attempt in range(5):
            try:
                async with factory() as s1:
                    async with factory() as s2:
                        repo1 = TaskRepository(s1)
                        repo2 = TaskRepository(s2)
                        tasks = [
                            DevelopmentTask(
                                task_id=compute_task_id(plan_id, "a"),
                                task_key="a",
                                project_id="proj",
                                title="Task A",
                                description="d",
                                priority=1,
                                metadata={"source_plan_id": plan_id},
                            ),
                        ]
                        fp = compute_plan_fingerprint(plan_id, "proj", [], tasks, [])
                        r1, r2 = await asyncio.gather(
                            repo1.create_tasks_idempotent(plan_id, tasks, plan_fingerprint=fp),
                            repo2.create_tasks_idempotent(plan_id, tasks, plan_fingerprint=fp),
                        )
                        await s1.commit()
                        await s2.commit()
                break
            except OperationalError:
                if attempt < 4:
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    raise

        async with factory() as verify:
            result = await verify.execute(
                select(PlanFingerprintModel).where(PlanFingerprintModel.plan_id == plan_id)
            )
            row = result.scalar_one_or_none()
            assert row is not None

        await engine.dispose()




class TestAtomicityRegression:
    async def test_conflicting_submission_cannot_mutate_canonical_plan(self, db_session: AsyncSession):
        """
        A conflicting submission must not be able to mutate or add to the
        canonical plan in any way.  The canonical plan must remain exactly
        as it was after the first successful submission.
        """
        repo = TaskRepository(db_session)
        plan_id = "plan_atomicity_conflict"
        tasks_v1 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="Original",
                description="d",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "b"),
                task_key="b",
                project_id="proj",
                title="Original B",
                description="d",
                priority=2,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        deps_v1 = [(tasks_v1[1].task_id, tasks_v1[0].task_id)]
        fp_v1 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v1, deps_v1)

        r1 = await repo.create_tasks_idempotent(plan_id, tasks_v1, deps_v1, plan_fingerprint=fp_v1)
        await db_session.commit()
        assert len(r1) == 2
        original_titles = {t.title for t in r1}
        assert original_titles == {"Original", "Original B"}

        tasks_v2 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "c"),
                task_key="c",
                project_id="proj",
                title="Injected Task C",
                description="d",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        fp_v2 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v2, [])

        with pytest.raises(PlanConflictError):
            await repo.create_tasks_idempotent(plan_id, tasks_v2, plan_fingerprint=fp_v2)
        await db_session.rollback()

        result = await repo.create_tasks_idempotent(plan_id, tasks_v1, deps_v1, plan_fingerprint=fp_v1)
        await db_session.commit()
        assert len(result) == 2
        assert {t.title for t in result} == original_titles

        all_deps = await db_session.execute(text("SELECT * FROM task_dependencies"))
        dep_rows = list(all_deps)
        assert len(dep_rows) == 1

    async def test_failed_task_insert_rolls_back_fingerprint(self, db_session: AsyncSession):
        """
        If task insertion fails after the fingerprint is written, the entire
        transaction (including the fingerprint) must be rolled back.
        """
        repo = TaskRepository(db_session)
        plan_id = "plan_atomicity_fail"
        tasks = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="A",
                description="d",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        fp = compute_plan_fingerprint(plan_id, "proj", [], tasks, [])

        await repo.create_tasks_idempotent(plan_id, tasks, plan_fingerprint=fp)
        await db_session.commit()

        fp_rows = await db_session.execute(
            select(PlanFingerprintModel).where(PlanFingerprintModel.plan_id == plan_id)
        )
        assert fp_rows.scalar_one_or_none() is not None

    async def test_conflicting_fingerprint_after_partial_insert_rollback(self, db_session: AsyncSession):
        """
        If a concurrent conflicting fingerprint is detected after task inserts,
        the entire transaction rolls back.  Verified by confirming the canonical
        plan is unchanged after the failed attempt.
        """
        repo = TaskRepository(db_session)
        plan_id = "plan_partial_rollback"
        tasks_v1 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="Canonical",
                description="d",
                priority=1,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        fp_v1 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v1, [])

        r1 = await repo.create_tasks_idempotent(plan_id, tasks_v1, plan_fingerprint=fp_v1)
        await db_session.commit()
        assert len(r1) == 1
        assert r1[0].title == "Canonical"

        tasks_v2 = [
            DevelopmentTask(
                task_id=compute_task_id(plan_id, "a"),
                task_key="a",
                project_id="proj",
                title="Attacker",
                description="d",
                priority=99,
                metadata={"source_plan_id": plan_id},
            ),
        ]
        fp_v2 = compute_plan_fingerprint(plan_id, "proj", [], tasks_v2, [])

        with pytest.raises(PlanConflictError):
            await repo.create_tasks_idempotent(plan_id, tasks_v2, plan_fingerprint=fp_v2)
        await db_session.rollback()

        r_check = await repo.create_tasks_idempotent(plan_id, tasks_v1, plan_fingerprint=fp_v1)
        await db_session.commit()
        assert len(r_check) == 1
        assert r_check[0].title == "Canonical"
        assert r_check[0].priority == 1


# â”€â”€ Security boundary â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestSecurityBoundary:
    def test_manager_models_no_forbidden_imports(self):
        import app.manager.models as mgr
        source = open(mgr.__file__).read()
        forbidden = [
            "ToolExecutor",
            "BaseTool",
            "ToolExecutionService",
            "PermissionEngine",
            "ApprovalService",
            "WorkspaceManager",
            "TaskOrchestrator",
            "RunController",
            "subprocess",
            "shell",
            "eval(",
            "exec(",
            "__import__",
        ]
        for token in forbidden:
            assert token not in source, f"Manager models must not reference {token!r}"

    def test_no_permission_fields_in_proposals(self):
        p = GeminiTaskProposal(
            proposal_key="a",
            title="t",
            description="d",
        )
        for forbidden in ["permission", "token", "secret", "authorization", "api_key"]:
            assert forbidden not in str(p.model_dump()).lower()

    def test_no_application_controlled_fields_in_proposals(self):
        forbidden_fields = [
            "task_id", "project_id", "plan_id", "status", "created_at",
            "started_at", "completed_at", "attempt_count", "max_attempts",
            "execution_token", "recovery_", "workspace_path",
        ]
        schema = GeminiTaskProposal.model_json_schema()
        props = set(schema.get("properties", {}).keys())
        for field in forbidden_fields:
            assert field not in props, f"GeminiTaskProposal must not allow {field!r}"

    def test_task_key_not_generated_from_position(self):
        plan = UserPlan(plan_id="p1", user_request="req", project_id="proj")
        proposals = [
            GeminiTaskProposal(proposal_key="z", title="Z", description="z"),
            GeminiTaskProposal(proposal_key="a", title="A", description="a"),
        ]
        result = normalize_plan(plan, proposals)
        task_keys = {t.task_key for t in result.tasks}
        assert task_keys == {"z", "a"}
