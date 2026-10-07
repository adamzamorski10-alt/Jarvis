"""
Operator Request — Operation / Intent Classifier.

Determines WHAT the user is asking for, not whether the action is authorized.

Classification may be:
1. deterministic first
2. AI-assisted when needed
3. bounded
4. fail-closed when classification is required for safe execution
"""
from __future__ import annotations

import re
from typing import Any

from app.context_assembly.models import OperationType
from app.operator_request.errors import ClassificationFailedError
from app.operator_request.models import OperatorRequest, OperatorRequestState


_DETERMINISTIC_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "FILE_READ": [
        re.compile(r"(read|show|display|cat|view|open|get).*(file|content|text)", re.IGNORECASE),
        re.compile(r"what('?s| is).*(in|inside).*(file|document)", re.IGNORECASE),
    ],
    "FILE_WRITE": [
        re.compile(r"(write|create|save|generate|edit|modify|append).*(file|document)", re.IGNORECASE),
        re.compile(r"put.*(this|the).*(in|into).*(file|document)", re.IGNORECASE),
    ],
    "FILE_DELETE": [
        re.compile(r"(delete|remove|trash|erase).*(file|document)", re.IGNORECASE),
    ],
    "MEMORY_QUERY": [
        re.compile(r"(whats+dos+yous+remember|recall|looks+ups+memory|searchs+memory)", re.IGNORECASE),
    ],
    "MEMORY_CREATE": [
        re.compile(r"(remember|saves+this|stores+this|memorize)", re.IGNORECASE),
    ],
    "WEB_SEARCH": [
        re.compile(r"(search|looks+up|research|google|searchs+thes+web)", re.IGNORECASE),
        re.compile(r"find(?:s+me)?.*(information|sources?|articles?|news|results?|details?)", re.IGNORECASE),
        re.compile(r"find(?:s+me)?.*(online|ons+thes+web|ons+thes+internet|internet|web)", re.IGNORECASE),
        re.compile(r"(check|look).*(online|ons+thes+web|ons+thes+internet|internet|web)", re.IGNORECASE),
        re.compile(r"(?:latest|current|today|recent).*(?:news|information|price|score|results|status).*(?:online|web|internet)", re.IGNORECASE),
        re.compile(r"(znajd[źz]|wyszukaj|poszukaj|researchuj|sprawd[źz]|sprawdz|szukaj).*(informacj[ei]|źródeł|zródeł|artykuł[uy]|wiadomości|wynik[io]|szczegół[óo]w|online|ws+internecie|ws+sieci|internet|sieć)", re.IGNORECASE),
        re.compile(r"(sprawd[źz]|sprawdz|zobacz).*(ws+internecie|ws+sieci|online|internet)", re.IGNORECASE),
    ],
    "INFORMATION_REQUEST": [
        re.compile(r"^s*(what|who|where|when|why|how|explain|describe|tells+me|cans+yous+explain)", re.IGNORECASE),
        re.compile(r"?$"),
    ],
    "TASK_CREATE": [
        re.compile(r"(create|make|sets+up|start).*(task|ticket|issue)", re.IGNORECASE),
        re.compile(r"is+needs+to.*(implement|build|develop|fix|add)", re.IGNORECASE),
    ],
    "PLAN_CREATE": [
        re.compile(r"(create|make|design|draft).*(plan|roadmap|strategy)", re.IGNORECASE),
    ],
    "PLAN_EXECUTE": [
        re.compile(r"(execute|run|start|implement).*(plan|roadmap)", re.IGNORECASE),
    ],
    "SCHEDULE_CREATE": [
        re.compile(r"(schedule|sets+ups+as+reminder|reminds+me|calendar)", re.IGNORECASE),
    ],
}


_WEB_SEARCH_INTENT = re.compile(
    r"(?:(search|looks+up|research|google)|"
    r"(check|look).*(online|ons+thes+web|ons+thes+internet|internet|web)|"
    r"(?:search|check|looks+up|find|research).*(?:web|internet|online)|"
    r"find(?:s+me)?.*(?:information|sources?|articles?|news|results?|details?)|"
    r"(?:znajd[źz]|wyszukaj|poszukaj|researchuj|sprawd[źz]|sprawdz|szukaj).*(?:informacj[ei]|źródeł|zródeł|artykuł[uy]|wiadomości|wynik[io]|szczegół[óo]w|online|ws+internecie|ws+sieci|internet|sieć)|"
    r"(?:sprawd[źz]|sprawdz|zobacz).*(?:ws+internecie|ws+sieci|online|internet))",
    re.IGNORECASE,
)

_DEV_PATTERN = re.compile(
    r"(implement|develop|build|code|refactor|fixs+(thes+)?bug|debug|adds+(as+)?feature|writes+(somes+)?code)",
    re.IGNORECASE,
)


class OperationClassifier:
    """Deterministic + bounded AI-assisted operation classifier."""

    def __init__(self, ai_classifier: Any = None) -> None:
        self._ai_classifier = ai_classifier

    def classify(self, request: OperatorRequest) -> OperatorRequest:
        try:
            normalized = request.normalized_request or request.user_request
            det_type, det_confidence = self._classify_deterministic(normalized)

            if det_type is not None and det_confidence >= 0.8:
                return self._apply_classification(request, det_type, det_confidence, "deterministic")

            if self._ai_classifier is not None:
                try:
                    ai_type, ai_confidence, ai_reason = self._classify_ai(request)
                    # Network access must never result from an AI-only guess.
                    if ai_type == "WEB_SEARCH" and not self._has_explicit_web_search_intent(normalized):
                        ai_type, ai_confidence, ai_reason = None, 0.0, None
                    if ai_type is not None:
                        return self._apply_classification(
                            request, ai_type, ai_confidence, ai_reason or "ai-assisted"
                        )
                except Exception:
                    pass

            if _DEV_PATTERN.search(normalized):
                return self._apply_classification(request, "DEVELOPMENT", 0.6, "development-pattern")

            return self._apply_classification(request, "UNKNOWN", 0.0, "no-match")
        except ClassificationFailedError:
            raise
        except Exception as exc:
            raise ClassificationFailedError(str(exc)) from exc

    @staticmethod
    def _has_explicit_web_search_intent(text: str) -> bool:
        return bool(_WEB_SEARCH_INTENT.search(text))

    def _classify_deterministic(self, text: str) -> tuple[str | None, float]:
        for op_type, patterns in _DETERMINISTIC_PATTERNS.items():
            for pattern in patterns:
                if pattern.search(text):
                    confidence = 0.9 if pattern.groups else 0.85
                    return op_type, confidence
        return None, 0.0

    def _classify_ai(self, request: OperatorRequest) -> tuple[str | None, float, str | None]:
        if self._ai_classifier is None:
            return None, 0.0, None
        result = self._ai_classifier(
            user_request=request.user_request,
            normalized_request=request.normalized_request,
            constraints=request.constraints,
        )
        if not result:
            return None, 0.0, None
        op_type = result.get("operation_type")
        confidence = result.get("confidence", 0.0)
        reason = result.get("reason")
        if op_type is None:
            return None, 0.0, None

        allowed = {
            "DECISION", "PLANNING", "CODING", "CHAT",
            "INFORMATION_REQUEST", "WEB_SEARCH", "FILE_READ", "FILE_WRITE", "FILE_DELETE",
            "DEVELOPMENT", "TASK_CREATE", "TASK_UPDATE",
            "PLAN_CREATE", "PLAN_EXECUTE", "SCHEDULE_CREATE",
            "MEMORY_QUERY", "MEMORY_CREATE", "OTHER", "UNKNOWN",
        }
        op_upper = str(op_type).upper()
        if op_upper not in allowed:
            return None, 0.0, None
        return op_upper, float(confidence), reason

    def _apply_classification(self, request, operation_type: str, confidence: float, reason: str):
        return request.model_copy(
            update={
                "operation_type": operation_type,
                "interaction_mode": self._map_to_interaction_mode(operation_type),
                "classification_confidence": confidence,
                "classification_reason": reason,
                "state": OperatorRequestState.CLASSIFIED,
                "updated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            }
        )

    def _map_to_interaction_mode(self, operation_type: str) -> str:
        execution_types = {
            "FILE_WRITE", "FILE_DELETE", "DEVELOPMENT",
            "PLAN_EXECUTE", "SCHEDULE_CREATE", "MEMORY_CREATE",
        }
        planning_types = {"PLANNING", "CODING", "DECISION", "PLAN_CREATE"}
        task_types = {"TASK_CREATE", "TASK_UPDATE"}
        if operation_type in execution_types:
            return "execution"
        if operation_type in planning_types:
            return "planning"
        if operation_type in task_types:
            return "task"
        return "conversational"


__all__ = ["OperationClassifier"]
