"""
Manager — Provider-Neutral Planning Contracts.

This module defines the strict input/output contracts for plan decomposition.
Model output is treated as untrusted input.  All application-controlled fields
are generated deterministically by normalization routines, never by the model.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.tasks.models import DevelopmentTask
from app.verification.models import CheckCategory


# ── Bounded constants ─────────────────────────────────────────────────────────

_MAX_USER_REQUEST_LENGTH = 65_536
_MAX_TITLE_LENGTH = 200
_MAX_DESCRIPTION_LENGTH = 10_240
_MAX_ACCEPTANCE_CRITERIA = 20
_MAX_ACCEPTANCE_CRITERIA_ITEM = 1_024
_MAX_DEPENDENCIES_PER_TASK = 20
_MAX_VERIFICATION_EXPECTATIONS = 10
_MAX_PLAN_TASKS = 100
_MAX_CONSTRAINTS_VALUE_LENGTH = 2_048
_PROPOSAL_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PLAN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DEPENDENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ── Exceptions ────────────────────────────────────────────────────────────────


class ManagerError(Exception):
    """Base exception for all Manager errors."""


class ManagerProviderError(ManagerError):
    """Base for provider communication failures."""
    def __init__(self, reason: str, retryable: bool = False) -> None:
        self.reason = reason
        self.retryable = retryable
        super().__init__(reason)


class ManagerTimeoutError(ManagerProviderError):
    def __init__(self, reason: str = "Provider request timed out") -> None:
        super().__init__(reason, retryable=True)


class ManagerRateLimitedError(ManagerProviderError):
    def __init__(self, reason: str = "Provider rate limit exceeded") -> None:
        super().__init__(reason, retryable=True)


class ManagerContextLimitError(ManagerProviderError):
    def __init__(self, reason: str = "Provider context limit exceeded") -> None:
        super().__init__(reason, retryable=False)


class ManagerEmptyResponseError(ManagerProviderError):
    def __init__(self, reason: str = "Provider returned empty response") -> None:
        super().__init__(reason, retryable=True)


class ManagerMalformedResponseError(ManagerError):
    """Model output failed schema validation."""
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Malformed plan response: {reason}")


class ManagerInvalidPlanError(ManagerError):
    """Plan structure is invalid (cycles, missing fields, contradictions)."""
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Invalid plan: {reason}")


class PlanConflictError(ManagerError):
    """A plan with the same plan_id already exists with different canonical content."""
    def __init__(self, plan_id: str) -> None:
        self.plan_id = plan_id
        super().__init__(f"Plan {plan_id!r} already exists with different content")


# ── Planning constraints ──────────────────────────────────────────────────────


class PlanningConstraints(BaseModel):
    """
    Ordinary planning limits supplied by the user/application.

    These are NOT security permissions.  They constrain task decomposition
    scope only.
    """
    model_config = ConfigDict(frozen=True)

    max_tasks: int = Field(default=20, ge=1, le=_MAX_PLAN_TASKS)
    max_dependencies_per_task: int = Field(default=20, ge=0, le=_MAX_DEPENDENCIES_PER_TASK)
    max_priority: int = Field(default=1000)
    min_priority: int = Field(default=-100)
    default_max_attempts: int = Field(default=3, ge=1, le=100)

    @model_validator(mode="after")
    def _check_priority_range(self) -> "PlanningConstraints":
        if self.min_priority > self.max_priority:
            raise ValueError("min_priority must be <= max_priority")
        return self


# ── Verification expectation ──────────────────────────────────────────────────


class VerificationExpectation(BaseModel):
    """
    Structured hint about what verification should cover.

    This is metadata only.  VerificationGate never reads these values
    to make execution decisions.
    """
    model_config = ConfigDict(frozen=True)

    category: CheckCategory
    required: bool = True
    custom_check_id: str | None = None

    @field_validator("custom_check_id")
    @classmethod
    def _validate_custom_check_id(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^[A-Za-z0-9_-]{1,128}$", v):
            raise ValueError("custom_check_id must match ^[A-Za-z0-9_-]{1,128}$")
        return v


# ── Untrusted model output ────────────────────────────────────────────────────


class ProviderTaskProposal(BaseModel):
    """
    Untrusted output from the planning model.

    ONLY these fields may be populated by the model.  Any additional fields
    are rejected by ConfigDict(extra="forbid").
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_key: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=_MAX_TITLE_LENGTH)
    description: str = Field(min_length=1, max_length=_MAX_DESCRIPTION_LENGTH)
    priority: int = Field(default=0)
    verification_expectations: list[VerificationExpectation] = Field(
        default_factory=list, max_length=_MAX_VERIFICATION_EXPECTATIONS
    )
    dependencies: list[str] = Field(default_factory=list, max_length=_MAX_DEPENDENCIES_PER_TASK)

    @field_validator("proposal_key")
    @classmethod
    def _validate_proposal_key(cls, v: str) -> str:
        if not _PROPOSAL_KEY_PATTERN.match(v):
            raise ValueError(
                "proposal_key must match ^[A-Za-z0-9_-]{1,64}$"
            )
        return v

    @field_validator("title")
    @classmethod
    def _validate_title(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("title must not be empty")
        return v.strip()

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("description must not be empty")
        return v.strip()

    @field_validator("priority")
    @classmethod
    def _validate_priority(cls, v: int, info: Any) -> int:
        if not isinstance(v, int):
            raise ValueError("priority must be an integer")
        return v

    @field_validator("dependencies")
    @classmethod
    def _validate_dependencies_format(cls, v: list[str]) -> list[str]:
        for dep in v:
            if not _DEPENDENCY_KEY_PATTERN.match(dep):
                raise ValueError(
                    f"dependency key {dep!r} must match ^[A-Za-z0-9_-]{1,64}$"
                )
        return v


GeminiTaskProposal = ProviderTaskProposal


# ── User-supplied input ───────────────────────────────────────────────────────


class UserPlan(BaseModel):
    """
    Input supplied by the user/application to the Manager.

    The application controls all fields here.  None are model-proposed.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str = Field(min_length=1, max_length=64)
    user_request: str = Field(min_length=1, max_length=_MAX_USER_REQUEST_LENGTH)
    project_id: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=_MAX_ACCEPTANCE_CRITERIA)
    planning_constraints: PlanningConstraints = Field(default_factory=PlanningConstraints)
    strict: bool = Field(default=True)

    @field_validator("plan_id")
    @classmethod
    def _validate_plan_id(cls, v: str) -> str:
        if not _PLAN_ID_PATTERN.match(v):
            raise ValueError("plan_id must match ^[A-Za-z0-9_-]{1,64}$")
        return v

    @field_validator("acceptance_criteria")
    @classmethod
    def _validate_acceptance_criteria(cls, v: list[str]) -> list[str]:
        for item in v:
            if not item or not item.strip():
                raise ValueError("each acceptance criterion must be a non-empty string")
        return [item.strip() for item in v]


# ── Manager output ────────────────────────────────────────────────────────────


class ManagerPlanResult(BaseModel):
    """
    Output of successful plan normalization.

    All tasks have application-controlled fields already populated.
    The task_graph adjacency list uses proposal_keys, not task_ids.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str
    tasks: list[DevelopmentTask]
    task_graph: dict[str, list[str]]  # proposal_key -> [proposal_keys]
    plan_fingerprint: str
    warnings: list[str] = Field(default_factory=list)
    rejected: bool = False
    rejection_reason: str | None = None


# ── Normalization helpers ─────────────────────────────────────────────────────


def compute_task_id(plan_id: str, task_key: str) -> str:
    """
    Deterministic task identity.  Stable across retries and re-submissions.

    task_key is the stable application-owned identifier (derived from proposal_key
    at normalization time).  task_id never depends on title, description,
    list position, or any mutable text.

    Identity formula: task_ + full SHA-256(plan_id:task_key)
    """
    raw = f"{plan_id}:{task_key}".encode()
    return f"task_{hashlib.sha256(raw).hexdigest()}"


def compute_plan_fingerprint(
    plan_id: str,
    project_id: str,
    acceptance_criteria: list[str],
    tasks: list[DevelopmentTask],
    dependencies: list[tuple[str, str]],
) -> str:
    """
    Deterministic fingerprint over canonical plan content.

    Canonicalization rules:
      - plan_id, project_id: included as-is (already validated)
      - acceptance_criteria: sorted alphabetically (order is not semantically meaningful)
      - tasks: sorted by task_key; each task includes task_key, title, description,
        priority, and verification_expectations (sorted by category then custom_check_id)
      - dependencies: sorted by (task_key, depends_on_task_key)

    Included (semantic plan content):
      - plan_id
      - project_id
      - acceptance_criteria (sorted)
      - for each task: task_key, title, description, priority,
        max_attempts, verification_expectations (sorted)
      - for each dependency edge: (task_key, depends_on_task_key)

    Excluded (ephemeral / runtime / derived):
      - task_id (derived from plan_id + task_key, redundant)
      - timestamps (created_at, started_at, completed_at, last_heartbeat, ...)
      - mutable runtime state (status, attempt_count, recovery_*, execution_token,
        workspace_path, execution_terminated_at, workspace_cleanup_pending)
      - parent_task_id (runtime hierarchy, not plan semantics)

    Invariant: changing max_attempts changes the fingerprint because retry
    semantics are part of canonical task behavior.
    """
    canonical_tasks = sorted(
        [
            {
                "task_key": t.task_key,
                "title": t.title,
                "description": t.description,
                "priority": t.priority,
                "max_attempts": t.max_attempts,
                "verification_expectations": sorted(
                    t.metadata.get("verification_expectations", []),
                    key=lambda ve: (ve.get("category", ""), ve.get("custom_check_id") or ""),
                ),
            }
            for t in tasks
        ],
        key=lambda t: t["task_key"],
    )

    canonical_deps = sorted(
        [(dep_task_key, on_task_key) for dep_task_key, on_task_key in dependencies],
        key=lambda e: (e[0], e[1]),
    )

    payload = {
        "plan_id": plan_id,
        "project_id": project_id,
        "acceptance_criteria": sorted(acceptance_criteria),
        "tasks": canonical_tasks,
        "dependencies": canonical_deps,
    }

    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def validate_dag(
    proposals: dict[str, ProviderTaskProposal],
) -> list[str]:
    """
    Validate the proposal dependency graph.

    Returns a list of deterministic validation errors.
    Empty list means the graph is valid.
    """
    errors: list[str] = []

    # Check all dependency references exist
    for key, proposal in proposals.items():
        for dep in proposal.dependencies:
            if dep not in proposals:
                errors.append(f"Task {key!r} references unknown dependency {dep!r}")

    # Check no self-dependencies
    for key, proposal in proposals.items():
        if key in proposal.dependencies:
            errors.append(f"Task {key!r} has self-dependency")

    # Check no duplicate dependencies per task
    for key, proposal in proposals.items():
        seen = set(proposal.dependencies)
        if len(seen) != len(proposal.dependencies):
            errors.append(f"Task {key!r} has duplicate dependencies")

    # Cycle detection via Kahn's algorithm
    if not errors:
        in_degree: dict[str, int] = {k: 0 for k in proposals}
        adjacency: dict[str, list[str]] = {k: [] for k in proposals}

        for key, proposal in proposals.items():
            for dep in proposal.dependencies:
                adjacency[dep].append(key)
                in_degree[key] += 1

        queue = [k for k, d in in_degree.items() if d == 0]
        visited = 0

        while queue:
            queue.sort()
            node = queue.pop(0)
            visited += 1
            for neighbor in sorted(adjacency[node]):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited != len(proposals):
            in_degree2 = {k: 0 for k in proposals}
            adj2: dict[str, list[str]] = {k: [] for k in proposals}
            for key, proposal in proposals.items():
                for dep in proposal.dependencies:
                    adj2[dep].append(key)
                    in_degree2[key] += 1
            queue2 = [k for k, d in in_degree2.items() if d == 0]
            reachable = set()
            while queue2:
                queue2.sort()
                node = queue2.pop(0)
                reachable.add(node)
                for neighbor in sorted(adj2[node]):
                    in_degree2[neighbor] -= 1
                    if in_degree2[neighbor] == 0:
                        queue2.append(neighbor)
            cycle_members = [k for k in proposals if k not in reachable]
            errors.append(f"Dependency cycle detected involving: {', '.join(sorted(cycle_members))}")

    return errors


def resolve_dag_edges(
    task_key_to_task_id: dict[str, str],
    task_graph: dict[str, list[str]],
) -> list[tuple[str, str]]:
    """
    Convert proposal_key-based task_graph into (task_id, depends_on_task_id) pairs.

    Raises ManagerInvalidPlanError if:
      - any proposal_key in the graph is unknown
      - any task depends on itself
      - any dependency edge is duplicated

    Returns a sorted list of edges for deterministic persistence.
    """
    errors: list[str] = []
    seen_edges: set[tuple[str, str]] = set()

    for task_key, deps in task_graph.items():
        if task_key not in task_key_to_task_id:
            errors.append(f"Unknown task_key {task_key!r} in dependency graph")
            continue
        task_id = task_key_to_task_id[task_key]
        for dep_key in deps:
            if dep_key not in task_key_to_task_id:
                errors.append(f"Task {task_key!r} references unknown dependency {dep_key!r}")
                continue
            if dep_key == task_key:
                errors.append(f"Task {task_key!r} has self-dependency")
                continue
            dep_task_id = task_key_to_task_id[dep_key]
            edge = (task_id, dep_task_id)
            if edge in seen_edges:
                errors.append(f"Task {task_key!r} has duplicate dependency on {dep_key!r}")
                continue
            seen_edges.add(edge)

    if errors:
        raise ManagerInvalidPlanError("; ".join(errors))

    edges = sorted(seen_edges)
    return edges


def normalize_plan(
    user_plan: UserPlan,
    proposals: list[ProviderTaskProposal],
) -> ManagerPlanResult:
    """
    Deterministic normalization of model proposals into application-controlled tasks.

    Raises ManagerInvalidPlanError if the plan is invalid.
    """
    if len(proposals) == 0:
        raise ManagerInvalidPlanError("Plan contains no tasks")
    if len(proposals) > user_plan.planning_constraints.max_tasks:
        raise ManagerInvalidPlanError(
            f"Plan has {len(proposals)} tasks, exceeding max_tasks={user_plan.planning_constraints.max_tasks}"
        )

    proposal_map: dict[str, ProviderTaskProposal] = {}
    for p in proposals:
        if p.proposal_key in proposal_map:
            raise ManagerInvalidPlanError(f"Duplicate proposal_key: {p.proposal_key!r}")
        proposal_map[p.proposal_key] = p

    constraints = user_plan.planning_constraints
    for key, p in proposal_map.items():
        if p.priority < constraints.min_priority or p.priority > constraints.max_priority:
            raise ManagerInvalidPlanError(
                f"Task {key!r} priority {p.priority} outside allowed range "
                f"[{constraints.min_priority}, {constraints.max_priority}]"
            )

    for key, p in proposal_map.items():
        if len(p.dependencies) > constraints.max_dependencies_per_task:
            raise ManagerInvalidPlanError(
                f"Task {key!r} has {len(p.dependencies)} dependencies, exceeding "
                f"max_dependencies_per_task={constraints.max_dependencies_per_task}"
            )

    dag_errors = validate_dag(proposal_map)
    if dag_errors:
        raise ManagerInvalidPlanError("; ".join(dag_errors))

    sorted_keys = sorted(proposal_map.keys())
    now = datetime.now(timezone.utc)
    tasks: list[DevelopmentTask] = []
    task_graph: dict[str, list[str]] = {}
    task_key_to_task_id: dict[str, str] = {}

    for key in sorted_keys:
        proposal = proposal_map[key]
        task_key = key
        task_id = compute_task_id(user_plan.plan_id, task_key)
        task_key_to_task_id[task_key] = task_id

        metadata: dict[str, Any] = {
            "source_plan_id": user_plan.plan_id,
            "proposal_key": key,
            "acceptance_criteria": user_plan.acceptance_criteria,
            "verification_expectations": [
                {
                    "category": ve.category.value,
                    "required": ve.required,
                    "custom_check_id": ve.custom_check_id,
                }
                for ve in proposal.verification_expectations
            ],
        }

        task = DevelopmentTask(
            task_id=task_id,
            task_key=task_key,
            project_id=user_plan.project_id,
            title=proposal.title,
            description=proposal.description,
            status="PENDING",
            priority=proposal.priority,
            attempt_count=0,
            max_attempts=constraints.default_max_attempts,
            created_at=now,
            metadata=metadata,
        )
        tasks.append(task)
        task_graph[key] = list(proposal.dependencies)

    dep_edges = resolve_dag_edges(task_key_to_task_id, task_graph)
    fingerprint = compute_plan_fingerprint(
        user_plan.plan_id,
        user_plan.project_id,
        user_plan.acceptance_criteria,
        tasks,
        dep_edges,
    )

    return ManagerPlanResult(
        plan_id=user_plan.plan_id,
        tasks=tasks,
        task_graph=task_graph,
        plan_fingerprint=fingerprint,
    )
