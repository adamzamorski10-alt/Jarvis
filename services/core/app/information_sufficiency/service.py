"""
Information Sufficiency — Service.

Application-owned service that evaluates whether available information
is sufficient to proceed with a task.

SECURITY:
  - Does NOT execute tools, shell commands, subprocesses, or model calls directly.
  - Does NOT mutate Memory, Task, Plan, or Execution state.
  - Does NOT grant permissions, approvals, or execution authority.
  - Does NOT import execution, permission, or approval components.
  - Fails closed: invalid input or evaluation failure never produces SUFFICIENT.
  - Assumptions are documented; they do NOT grant permissions.
"""
from __future__ import annotations

import logging
from typing import Any

from app.context_assembly.models import ContextMode
from app.information_sufficiency.llm_enhancer import (
    LLMEnhancer,
    LLMProvider,
    SufficiencyOutcome,
)
from app.information_sufficiency.models import (
    Assumption,
    ClarifyingQuestion,
    InformationSufficiencyError,
    InformationSufficiencyRequest,
    InsufficiencyEvaluationError,
    InvalidInputError,
    MissingInformation,
    MissingInformationCategory,
    MissingRequiredFieldError,
    SufficiencyResult,
    _MAX_ASSUMPTION_DESC_LENGTH,
    _MAX_CAPABILITIES,
    _MAX_CONSTRAINTS_ENTRIES,
    _MAX_ID_LENGTH,
    _MAX_IMPACT_LENGTH,
    _MAX_QUESTION_LENGTH,
    _MAX_QUESTIONS,
    _MAX_REASON_LENGTH,
    _MAX_SOURCE_LENGTH,
    _MAX_USER_REQUEST_LENGTH,
)

logger = logging.getLogger(__name__)


class InformationSufficiencyService:
    """
    Application-owned Information Sufficiency service.

    Answers only: "Do we have enough reliable information to understand
    the task sufficiently?"

    Does NOT decide permissions, authorization, approvals, tool execution,
    task execution, model selection, or business decisions.
    """

    def __init__(self, llm_provider: LLMProvider | None = None) -> None:
        self._llm_enhancer = LLMEnhancer(llm_provider=llm_provider)

    async def assess(self, request: InformationSufficiencyRequest) -> SufficiencyResult:
        """
        Evaluate information sufficiency for the given request.

        Returns SufficiencyResult with success=True on normal path,
        or success=False with error description on failure.
        """
        try:
            self._validate_request(request)
        except InvalidInputError as exc:
            logger.warning("Invalid input to Information Sufficiency: %s", exc)
            return SufficiencyResult(
                success=False,
                error=f"InvalidInputError: {exc}",
            )

        try:
            deterministic_result = self._evaluate_deterministic(request)
            if deterministic_result.success and deterministic_result.outcome == SufficiencyOutcome.INSUFFICIENT:
                final_result = await self._llm_enhancer.enhance(
                    deterministic_result,
                    request.user_request,
                    self._build_context_summary(request),
                )
            else:
                final_result = deterministic_result

            return final_result
        except InformationSufficiencyError as exc:
            logger.error("Information Sufficiency evaluation error: %s", exc)
            return SufficiencyResult(
                success=False,
                error=f"InsufficiencyEvaluationError: {exc}",
            )
        except Exception as exc:
            logger.error("Unexpected Information Sufficiency error: %s", exc)
            return SufficiencyResult(
                success=False,
                error=f"InsufficiencyEvaluationError: unexpected failure: {type(exc).__name__}",
            )

    def _validate_request(self, request: InformationSufficiencyRequest) -> None:
        """Validate request inputs. Raises on invalid input."""
        _MAX_CONSTRAINTS_VALUE_LENGTH = 2_048
        if request.request_context is None:
            raise MissingRequiredFieldError("request_context is required")

        if not request.user_request or not request.user_request.strip():
            raise MissingRequiredFieldError("user_request must not be empty")

        if len(request.user_request) > _MAX_USER_REQUEST_LENGTH:
            raise InvalidInputError(f"user_request exceeds {_MAX_USER_REQUEST_LENGTH} characters")

        if len(request.constraints) > _MAX_CONSTRAINTS_ENTRIES:
            raise InvalidInputError(f"constraints must have at most {_MAX_CONSTRAINTS_ENTRIES} entries")

        for key, value in request.constraints.items():
            if not isinstance(key, str):
                raise InvalidInputError(f"constraint key must be a string, got {type(key).__name__}")
            if len(key) > 128:
                raise InvalidInputError(f"constraint key exceeds 128 chars")
            if isinstance(value, str) and len(value) > _MAX_CONSTRAINTS_VALUE_LENGTH:
                raise InvalidInputError(f"constraint value for '{key}' exceeds {_MAX_CONSTRAINTS_VALUE_LENGTH} chars")

        if len(request.available_capabilities) > _MAX_CAPABILITIES:
            raise InvalidInputError(f"available_capabilities must have at most {_MAX_CAPABILITIES} entries")

    def _evaluate_deterministic(self, request: InformationSufficiencyRequest) -> SufficiencyResult:
        """Run deterministic evaluation rules."""
        missing: list[MissingInformation] = []
        ambiguities_list: list[MissingInformation] = []
        conflicts_list: list[MissingInformation] = []
        questions: list[ClarifyingQuestion] = []
        assumptions: list[Assumption] = []

        if request.mode == ContextMode.QUICK:
            missing.extend(self._check_required_fields_quick(request))
        else:
            missing.extend(self._check_required_fields(request))
            conflicts_list.extend(self._detect_conflicts(request))
            if request.mode == ContextMode.DEEP:
                ambiguities_list.extend(self._detect_ambiguities_deep(request))
            else:
                ambiguities_list.extend(self._detect_ambiguities_standard(request))

        if missing:
            return self._insufficient_result(missing, ambiguities_list, conflicts_list, questions, assumptions, request)

        if ambiguities_list or conflicts_list:
            return self._ambiguous_result(missing, ambiguities_list, conflicts_list, questions, assumptions, request)

        return SufficiencyResult(
            success=True,
            outcome=SufficiencyOutcome.SUFFICIENT,
            missing_information=missing,
            ambiguities=ambiguities_list,
            conflicts=conflicts_list,
            questions=questions,
            assumptions=assumptions,
            metadata=self._build_metadata(request),
        )

    def _check_required_fields_quick(self, request: InformationSufficiencyRequest) -> list[MissingInformation]:
        """QUICK mode: only the most critical required fields."""
        missing: list[MissingInformation] = []

        if request.operation_type.value in ("coding", "planning"):
            if not request.project_id:
                missing.append(MissingInformation(
                    information_id="project_id",
                    description="project_id is required for project-scoped operations",
                    category=MissingInformationCategory.REQUIRED,
                ))

        if not request.user_request or not request.user_request.strip():
            missing.append(MissingInformation(
                information_id="user_request",
                description="user_request must not be empty",
                category=MissingInformationCategory.REQUIRED,
            ))

        return missing

    def _check_required_fields(self, request: InformationSufficiencyRequest) -> list[MissingInformation]:
        """STANDARD/DEEP mode: comprehensive required field checks."""
        missing: list[MissingInformation] = []

        if request.operation_type.value in ("coding", "planning"):
            if not request.project_id:
                missing.append(MissingInformation(
                    information_id="project_id",
                    description="project_id is required for project-scoped operations",
                    category=MissingInformationCategory.REQUIRED,
                ))

        if request.operation_type == "coding":
            if not request.workspace_path:
                missing.append(MissingInformation(
                    information_id="workspace_path",
                    description="workspace_path is required for coding operations",
                    category=MissingInformationCategory.REQUIRED,
                ))

        if not request.user_request or not request.user_request.strip():
            missing.append(MissingInformation(
                information_id="user_request",
                description="user_request must not be empty",
                category=MissingInformationCategory.REQUIRED,
            ))

        return missing

    def _detect_conflicts(self, request: InformationSufficiencyRequest) -> list[MissingInformation]:
        """Detect conflicts between authoritative fields and user request."""
        conflicts: list[MissingInformation] = []

        if request.project_id:
            project_id_in_request = self._extract_project_id_from_user_request(request.user_request)
            if project_id_in_request and project_id_in_request != request.project_id:
                conflicts.append(MissingInformation(
                    information_id="project_id_conflict",
                    description=f"User request mentions project '{project_id_in_request}' but authoritative project_id is '{request.project_id}'",
                    category=MissingInformationCategory.CONFLICTING,
                ))

        return conflicts

    def _detect_ambiguities_standard(self, request: InformationSufficiencyRequest) -> list[MissingInformation]:
        """STANDARD mode: basic ambiguity detection."""
        ambiguities: list[MissingInformation] = []
        user_request = request.user_request.strip()

        generic_phrases = [
            "fix the bug",
            "fix it",
            "update the config",
            "update it",
            "make it work",
            "do the thing",
            "improve it",
            "clean it up",
        ]

        lower_request = user_request.lower()
        for phrase in generic_phrases:
            if phrase in lower_request:
                ambiguities.append(MissingInformation(
                    information_id=f"ambiguity_{phrase.replace(' ', '_')}",
                    description=f"User request '{phrase}' is too generic and could refer to multiple items",
                    category=MissingInformationCategory.AMBIGUOUS,
                ))
                break

        return ambiguities

    def _detect_ambiguities_deep(self, request: InformationSufficiencyRequest) -> list[MissingInformation]:
        """DEEP mode: more granular ambiguity detection."""
        ambiguities = self._detect_ambiguities_standard(request)
        user_request = request.user_request.strip()

        if len(user_request.split()) < 3:
            ambiguities.append(MissingInformation(
                information_id="ambiguity_too_short",
                description="User request is very short and lacks sufficient detail for unambiguous interpretation",
                category=MissingInformationCategory.AMBIGUOUS,
            ))

        return ambiguities

    def _extract_project_id_from_user_request(self, user_request: str) -> str | None:
        """Extract potential project_id references from user request text."""
        import re
        patterns = [
            r'project[_\s-]+id[_\s:=]+([a-zA-Z0-9_-]{1,64})',
            r'\buse[_\s]+project[_\s]+([a-zA-Z0-9_-]{1,64})\b',
            r'\bset[_\s]+project[_\s]+to[_\s]+([a-zA-Z0-9_-]{1,64})\b',
            r'\bupdate[_\s]+project[_\s]+to[_\s]+([a-zA-Z0-9_-]{1,64})\b',
            r'\bswitch[_\s]+to[_\s]+project[_\s]+([a-zA-Z0-9_-]{1,64})\b',
            r'\bupdate[_\s]+the[_\s]+project[_\s]+([a-zA-Z0-9_-]{1,64})\b',
        ]
        for pattern in patterns:
            match = re.search(pattern, user_request, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _build_context_summary(self, request: InformationSufficiencyRequest) -> str:
        """Build a brief context summary for LLM enhancement."""
        parts: list[str] = []
        parts.append(f"Operation: {request.operation_type.value}")
        parts.append(f"Mode: {request.mode.value}")
        parts.append(f"Actor: {request.request_context.actor_type.value}")
        if request.project_id:
            parts.append(f"Project: {request.project_id}")
        if request.workspace_path:
            parts.append(f"Workspace: {request.workspace_path}")
        if request.assembled_context is not None:
            parts.append(f"Assembled context memories: {request.assembled_context.total_memories}")
        return "\n".join(parts)

    def _build_metadata(self, request: InformationSufficiencyRequest) -> dict[str, Any]:
        """Build bounded metadata for the result."""
        return {
            "operation_type": request.operation_type.value,
            "mode": request.mode.value,
            "actor_type": request.request_context.actor_type.value,
            "available_capabilities_count": len(request.available_capabilities),
            "assembled_context_available": request.assembled_context is not None,
            "project_id": request.project_id,
        }

    def _insufficient_result(
        self,
        missing: list[MissingInformation],
        ambiguities: list[MissingInformation],
        conflicts: list[MissingInformation],
        questions: list[ClarifyingQuestion],
        assumptions: list[Assumption],
        request: InformationSufficiencyRequest,
    ) -> SufficiencyResult:
        """Build an INSUFFICIENT result."""
        if not questions and missing:
            for item in missing[: _MAX_QUESTIONS]:
                questions.append(ClarifyingQuestion(
                    question_id=f"q_{item.information_id}",
                    question=f"What is the {item.description.lower().rstrip('.')}?",
                    reason=item.description,
                    category=item.category,
                    blocking=item.category == MissingInformationCategory.REQUIRED,
                    priority=100 if item.category == MissingInformationCategory.REQUIRED else 50,
                    expected_answer_type="text",
                    related_missing_information=[],
                ))

        return SufficiencyResult(
            success=True,
            outcome=SufficiencyOutcome.INSUFFICIENT,
            missing_information=missing[:_MAX_QUESTIONS],
            ambiguities=ambiguities,
            conflicts=conflicts,
            questions=questions[:_MAX_QUESTIONS],
            assumptions=assumptions,
            metadata=self._build_metadata(request),
        )

    def _ambiguous_result(
        self,
        missing: list[MissingInformation],
        ambiguities: list[MissingInformation],
        conflicts: list[MissingInformation],
        questions: list[ClarifyingQuestion],
        assumptions: list[Assumption],
        request: InformationSufficiencyRequest,
    ) -> SufficiencyResult:
        """Build an AMBIGUOUS result."""
        for item in ambiguities[: _MAX_QUESTIONS]:
            questions.append(ClarifyingQuestion(
                question_id=f"q_{item.information_id}",
                question=f"Please clarify: {item.description.lower().rstrip('.')}",
                reason=item.description,
                category=item.category,
                blocking=True,
                priority=80,
                expected_answer_type="text",
                related_missing_information=[],
            ))

        for item in conflicts[:_MAX_QUESTIONS]:
            questions.append(ClarifyingQuestion(
                question_id=f"q_{item.information_id}",
                question=f"Conflict detected: {item.description.lower().rstrip('.')}. Please resolve.",
                reason=item.description,
                category=item.category,
                blocking=True,
                priority=90,
                expected_answer_type="text",
                related_missing_information=[],
            ))

        return SufficiencyResult(
            success=True,
            outcome=SufficiencyOutcome.AMBIGUOUS,
            missing_information=missing[:_MAX_QUESTIONS],
            ambiguities=ambiguities,
            conflicts=conflicts,
            questions=questions[:_MAX_QUESTIONS],
            assumptions=assumptions,
            metadata=self._build_metadata(request),
        )


__all__ = ["InformationSufficiencyService"]
