"""
Permission Engine for AI Operator.
Evaluates PermissionEvaluationRequests against registered tools, safety policies,
autonomy modes, and persistent PermissionGrants.

Enforces Fail-Closed security guarantee for permission decisions.
Audit I/O is fail-soft and never changes the computed decision.
"""
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.repositories.grant_repository import GrantRepository
    from app.tools.audit_service import AuditLogService

from app.core.logging import get_logger
from app.tools.audit_models import AuditEventType
from app.tools.models import (
    AutonomyMode,
    GrantType,
    PermissionDecision,
    PermissionDecisionType,
    PermissionEvaluationRequest,
    RiskLevel,
)
from app.tools.registry import ToolRegistry, tool_registry as default_tool_registry

logger = get_logger(__name__)


def _decision_event_type(decision: PermissionDecisionType) -> AuditEventType:
    if decision == PermissionDecisionType.ALLOW:
        return AuditEventType.PERMISSION_GRANTED
    if decision == PermissionDecisionType.DENY:
        return AuditEventType.PERMISSION_DENIED
    return AuditEventType.PERMISSION_EVALUATED


class PermissionEngine:
    """
    Central, secure Permission Engine.
    Evaluates tool requests against safety rules and grants.
    """
    def __init__(
        self,
        registry: ToolRegistry | None = None,
        grant_repo: "GrantRepository | None" = None,
        audit_service: "AuditLogService | None" = None,
    ) -> None:
        self._registry = registry or default_tool_registry
        self._grant_repo = grant_repo
        self._audit_service = audit_service

    async def evaluate(
        self,
        request: PermissionEvaluationRequest,
        grant_repo_override: "GrantRepository | None" = None,
    ) -> PermissionDecision:
        """
        Evaluates a tool execution request and returns a PermissionDecision.
        Enforces Fail-Closed principles for authorization.
        Audit persistence runs after the decision and cannot alter it.
        """
        repo = grant_repo_override or self._grant_repo
        decision = await self._compute_decision(request, repo)
        await self._record_audit(request, decision)
        return decision

    async def _record_audit(
        self,
        request: PermissionEvaluationRequest,
        decision: PermissionDecision,
    ) -> None:
        if self._audit_service is None:
            return

        tool_def = self._registry.get(request.tool_id)
        risk = (
            tool_def.risk_level.value
            if tool_def is not None
            else request.risk_level.value
        )
        request_id: str | None = None
        ctx: dict[str, Any] = request.context or {}
        raw_rid = ctx.get("request_id")
        if isinstance(raw_rid, str) and raw_rid.strip():
            request_id = raw_rid.strip()

        try:
            await self._audit_service.record(
                _decision_event_type(decision.decision),
                request_id=request_id,
                tool_id=request.tool_id,
                action=request.action,
                risk_level=risk,
                autonomy_mode=request.autonomy_mode.value,
                decision=decision.decision.value,
                reason=decision.reason,
                rule_id=decision.rule_id,
                grant_id=decision.grant_id,
                arguments=request.arguments,
            )
        except Exception as exc:
            logger.error(
                "Audit log write failed during permission evaluation",
                tool_id=request.tool_id,
                error=str(exc),
                exc_info=True,
            )

    async def _compute_decision(
        self,
        request: PermissionEvaluationRequest,
        repo: "GrantRepository | None",
    ) -> PermissionDecision:
        """
        Evaluates a tool execution request and returns a PermissionDecision.
        Enforces Fail-Closed principles.
        """
        # --- Rule 1: Tool Existence ---
        tool_def = self._registry.get(request.tool_id)
        if tool_def is None:
            return PermissionDecision(
                decision=PermissionDecisionType.DENY,
                reason=f"Tool '{request.tool_id}' is not registered in ToolRegistry.",
                rule_id="RULE_01_TOOL_NOT_FOUND",
            )

        # --- Rule 2: Tool Enabled State ---
        if not self._registry.is_enabled(request.tool_id):
            return PermissionDecision(
                decision=PermissionDecisionType.DENY,
                reason=f"Tool '{request.tool_id}' is currently disabled.",
                rule_id="RULE_02_TOOL_DISABLED",
            )

        # --- Rule 3: Input / Action Validation ---
        if not request.action or not request.action.strip():
            return PermissionDecision(
                decision=PermissionDecisionType.DENY,
                reason="Invalid or missing action in permission request.",
                rule_id="RULE_03_INVALID_ACTION",
            )

        # --- Rule 4: Permission Grants Evaluation ---
        if repo is not None:
            matching_grant = await repo.find_matching_grant(
                tool_id=request.tool_id,
                action=request.action,
            )

            if matching_grant is not None:
                # Check CRITICAL risk restriction: CRITICAL risk level requires persistent grant
                if tool_def.risk_level == RiskLevel.CRITICAL and matching_grant.grant_type != GrantType.PERSISTENT:
                    return PermissionDecision(
                        decision=PermissionDecisionType.ASK_USER,
                        reason="CRITICAL risk operations require explicit persistent user grant or user confirmation.",
                        rule_id="RULE_05_CRITICAL_RISK_RESTRICTION",
                        grant_id=matching_grant.id,
                    )

                # Attempt one-time grant atomic consumption if one_time
                if matching_grant.grant_type == GrantType.ONE_TIME:
                    consumed = await repo.consume_one_time_grant_atomically(matching_grant.id)
                    if consumed:
                        return PermissionDecision(
                            decision=PermissionDecisionType.ALLOW,
                            reason=f"Allowed by one-time grant '{matching_grant.id}'.",
                            rule_id="RULE_04_ONE_TIME_GRANT_ALLOWED",
                            grant_id=matching_grant.id,
                        )
                    # If atomic consume failed (already consumed in parallel), continue evaluation
                else:
                    return PermissionDecision(
                        decision=PermissionDecisionType.ALLOW,
                        reason=f"Allowed by active {matching_grant.grant_type.value} grant '{matching_grant.id}'.",
                        rule_id="RULE_04_GRANT_ALLOWED",
                        grant_id=matching_grant.id,
                    )

        # --- Rule 5: Static Confirmation Requirement ---
        if tool_def.requires_confirmation:
            return PermissionDecision(
                decision=PermissionDecisionType.ASK_USER,
                reason=f"Tool '{request.tool_id}' has requires_confirmation=True constraint.",
                rule_id="RULE_06_REQUIRES_CONFIRMATION",
            )

        # --- Rule 6: Autonomy Mode Evaluation ---
        mode = request.autonomy_mode
        risk = tool_def.risk_level

        if mode == AutonomyMode.MANUAL:
            if risk == RiskLevel.SAFE:
                return PermissionDecision(
                    decision=PermissionDecisionType.ALLOW,
                    reason="Allowed SAFE metric read in MANUAL autonomy mode.",
                    rule_id="RULE_07_MANUAL_SAFE_ALLOWED",
                )
            return PermissionDecision(
                decision=PermissionDecisionType.ASK_USER,
                reason=f"Operation with risk level {risk.value} requires user confirmation in MANUAL autonomy mode.",
                rule_id="RULE_07_MANUAL_ASK_USER",
            )

        elif mode == AutonomyMode.SEMI_AUTONOMOUS:
            if risk in (RiskLevel.SAFE, RiskLevel.LOW):
                return PermissionDecision(
                    decision=PermissionDecisionType.ALLOW,
                    reason=f"Allowed risk level {risk.value} in SEMI_AUTONOMOUS autonomy mode.",
                    rule_id="RULE_08_SEMI_AUTONOMOUS_ALLOWED",
                )
            elif risk in (RiskLevel.MEDIUM, RiskLevel.HIGH):
                return PermissionDecision(
                    decision=PermissionDecisionType.ASK_USER,
                    reason=f"Operation with risk level {risk.value} requires user confirmation in SEMI_AUTONOMOUS mode.",
                    rule_id="RULE_08_SEMI_AUTONOMOUS_ASK_USER",
                )
            else:  # CRITICAL
                return PermissionDecision(
                    decision=PermissionDecisionType.DENY,
                    reason="CRITICAL risk operation denied in SEMI_AUTONOMOUS mode without explicit grant.",
                    rule_id="RULE_08_SEMI_AUTONOMOUS_CRITICAL_DENY",
                )

        elif mode == AutonomyMode.AUTONOMOUS:
            if risk in (RiskLevel.SAFE, RiskLevel.LOW, RiskLevel.MEDIUM):
                return PermissionDecision(
                    decision=PermissionDecisionType.ALLOW,
                    reason=f"Allowed risk level {risk.value} in AUTONOMOUS autonomy mode.",
                    rule_id="RULE_09_AUTONOMOUS_ALLOWED",
                )
            elif risk == RiskLevel.HIGH:
                return PermissionDecision(
                    decision=PermissionDecisionType.ASK_USER,
                    reason="HIGH risk operation requires user confirmation in AUTONOMOUS mode.",
                    rule_id="RULE_09_AUTONOMOUS_HIGH_ASK_USER",
                )
            else:  # CRITICAL
                return PermissionDecision(
                    decision=PermissionDecisionType.DENY,
                    reason="CRITICAL risk operation denied in AUTONOMOUS mode without explicit grant.",
                    rule_id="RULE_09_AUTONOMOUS_CRITICAL_DENY",
                )

        # --- Rule 7: Fail-Closed Default ---
        return PermissionDecision(
            decision=PermissionDecisionType.DENY,
            reason="Operation not permitted by safety policy default.",
            rule_id="RULE_10_FAIL_CLOSED_DEFAULT",
        )
