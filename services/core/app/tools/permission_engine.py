"""
Permission Engine for AI Operator.
Evaluates PermissionEvaluationRequests against registered tools, safety policies,
autonomy modes, and persistent PermissionGrants.

Enforces Fail-Closed security guarantee for permission decisions.
Audit I/O is fail-soft and never changes the computed decision.
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.repositories.approval_repository import ApprovalRepository
    from app.repositories.grant_repository import GrantRepository
    from app.tools.approval_models import ApprovalStatus, ApprovalVerificationToken
    from app.tools.audit_service import AuditLogService
    from app.tools.models import ToolDefinition

from app.core.logging import get_logger
from app.tools.approval_models import compute_tool_definition_fingerprint
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

_OVERRIDABLE_ASK_USER_RULES = frozenset(
    {
        "RULE_06_REQUIRES_CONFIRMATION",
        "RULE_07_MANUAL_ASK_USER",
        "RULE_08_SEMI_AUTONOMOUS_ASK_USER",
        "RULE_09_AUTONOMOUS_HIGH_ASK_USER",
    }
)


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
        approval_repo: "ApprovalRepository | None" = None,
    ) -> None:
        self._registry = registry or default_tool_registry
        self._grant_repo = grant_repo
        self._audit_service = audit_service
        self._approval_repo = approval_repo

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

        # Defensive CRITICAL check: CRITICAL risk is ALWAYS DENY, regardless of
        # any grant, approval, autonomy mode, or confirmation override.
        tool_def = self._registry.get(request.tool_id)
        if tool_def is not None and tool_def.risk_level == RiskLevel.CRITICAL:
            decision = PermissionDecision(
                decision=PermissionDecisionType.DENY,
                reason="CRITICAL risk operations are absolutely denied under all conditions.",
                rule_id="RULE_05_CRITICAL_RISK_ABSOLUTE_DENY",
            )

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
            decision = PermissionDecision(
                decision=PermissionDecisionType.DENY,
                reason=f"Tool '{request.tool_id}' is not registered in ToolRegistry.",
                rule_id="RULE_01_TOOL_NOT_FOUND",
            )
            return await self._evaluate_approval_override(request, decision)

        # --- Rule 2: Tool Enabled State ---
        if not self._registry.is_enabled(request.tool_id):
            decision = PermissionDecision(
                decision=PermissionDecisionType.DENY,
                reason=f"Tool '{request.tool_id}' is currently disabled.",
                rule_id="RULE_02_TOOL_DISABLED",
            )
            return await self._evaluate_approval_override(request, decision)

        # --- Rule 3: Input / Action Validation ---
        if not request.action or not request.action.strip():
            decision = PermissionDecision(
                decision=PermissionDecisionType.DENY,
                reason="Invalid or missing action in permission request.",
                rule_id="RULE_03_INVALID_ACTION",
            )
            return await self._evaluate_approval_override(request, decision)

        # --- Rule 4: Permission Grants Evaluation ---
        if repo is not None:
            matching_grant = await repo.find_matching_grant(
                tool_id=request.tool_id,
                action=request.action,
            )

            if matching_grant is not None:
                # Check CRITICAL risk restriction: CRITICAL risk level requires persistent grant
                if tool_def.risk_level == RiskLevel.CRITICAL and matching_grant.grant_type != GrantType.PERSISTENT:
                    decision = PermissionDecision(
                        decision=PermissionDecisionType.ASK_USER,
                        reason="CRITICAL risk operations require explicit persistent user grant or user confirmation.",
                        rule_id="RULE_05_CRITICAL_RISK_RESTRICTION",
                        grant_id=matching_grant.id,
                    )
                    return await self._evaluate_approval_override(request, decision)

                # Attempt one-time grant atomic consumption if one_time
                if matching_grant.grant_type == GrantType.ONE_TIME:
                    consumed = await repo.consume_one_time_grant_atomically(matching_grant.id)
                    if consumed:
                        decision = PermissionDecision(
                            decision=PermissionDecisionType.ALLOW,
                            reason=f"Allowed by one-time grant '{matching_grant.id}'.",
                            rule_id="RULE_04_ONE_TIME_GRANT_ALLOWED",
                            grant_id=matching_grant.id,
                        )
                        return await self._evaluate_approval_override(request, decision)
                    # If atomic consume failed (already consumed in parallel), continue evaluation
                else:
                    decision = PermissionDecision(
                        decision=PermissionDecisionType.ALLOW,
                        reason=f"Allowed by active {matching_grant.grant_type.value} grant '{matching_grant.id}'.",
                        rule_id="RULE_04_GRANT_ALLOWED",
                        grant_id=matching_grant.id,
                    )
                    return await self._evaluate_approval_override(request, decision)

        # --- Rule 5: Static Confirmation Requirement ---
        if tool_def.requires_confirmation:
            decision = PermissionDecision(
                decision=PermissionDecisionType.ASK_USER,
                reason=f"Tool '{request.tool_id}' has requires_confirmation=True constraint.",
                rule_id="RULE_06_REQUIRES_CONFIRMATION",
            )
            return await self._evaluate_approval_override(request, decision)

        # --- Rule 6: Autonomy Mode Evaluation ---
        mode = request.autonomy_mode
        risk = tool_def.risk_level

        if mode == AutonomyMode.MANUAL:
            if risk == RiskLevel.SAFE:
                decision = PermissionDecision(
                    decision=PermissionDecisionType.ALLOW,
                    reason="Allowed SAFE metric read in MANUAL autonomy mode.",
                    rule_id="RULE_07_MANUAL_SAFE_ALLOWED",
                )
                return await self._evaluate_approval_override(request, decision)
            decision = PermissionDecision(
                decision=PermissionDecisionType.ASK_USER,
                reason=f"Operation with risk level {risk.value} requires user confirmation in MANUAL autonomy mode.",
                rule_id="RULE_07_MANUAL_ASK_USER",
            )
            return await self._evaluate_approval_override(request, decision)

        elif mode == AutonomyMode.SEMI_AUTONOMOUS:
            if risk in (RiskLevel.SAFE, RiskLevel.LOW):
                decision = PermissionDecision(
                    decision=PermissionDecisionType.ALLOW,
                    reason=f"Allowed risk level {risk.value} in SEMI_AUTONOMOUS autonomy mode.",
                    rule_id="RULE_08_SEMI_AUTONOMOUS_ALLOWED",
                )
                return await self._evaluate_approval_override(request, decision)
            elif risk in (RiskLevel.MEDIUM, RiskLevel.HIGH):
                decision = PermissionDecision(
                    decision=PermissionDecisionType.ASK_USER,
                    reason=f"Operation with risk level {risk.value} requires user confirmation in SEMI_AUTONOMOUS mode.",
                    rule_id="RULE_08_SEMI_AUTONOMOUS_ASK_USER",
                )
                return await self._evaluate_approval_override(request, decision)
            else:  # CRITICAL
                decision = PermissionDecision(
                    decision=PermissionDecisionType.DENY,
                    reason="CRITICAL risk operation denied in SEMI_AUTONOMOUS mode without explicit grant.",
                    rule_id="RULE_08_SEMI_AUTONOMOUS_CRITICAL_DENY",
                )
                return await self._evaluate_approval_override(request, decision)

        elif mode == AutonomyMode.AUTONOMOUS:
            if risk in (RiskLevel.SAFE, RiskLevel.LOW, RiskLevel.MEDIUM):
                decision = PermissionDecision(
                    decision=PermissionDecisionType.ALLOW,
                    reason=f"Allowed risk level {risk.value} in AUTONOMOUS autonomy mode.",
                    rule_id="RULE_09_AUTONOMOUS_ALLOWED",
                )
                return await self._evaluate_approval_override(request, decision)
            elif risk == RiskLevel.HIGH:
                decision = PermissionDecision(
                    decision=PermissionDecisionType.ASK_USER,
                    reason="HIGH risk operation requires user confirmation in AUTONOMOUS mode.",
                    rule_id="RULE_09_AUTONOMOUS_HIGH_ASK_USER",
                )
                return await self._evaluate_approval_override(request, decision)
            else:  # CRITICAL
                decision = PermissionDecision(
                    decision=PermissionDecisionType.DENY,
                    reason="CRITICAL risk operation denied in AUTONOMOUS mode without explicit grant.",
                    rule_id="RULE_09_AUTONOMOUS_CRITICAL_DENY",
                )
                return await self._evaluate_approval_override(request, decision)

        # --- Rule 7: Fail-Closed Default ---
        decision = PermissionDecision(
            decision=PermissionDecisionType.DENY,
            reason="Operation not permitted by safety policy default.",
            rule_id="RULE_10_FAIL_CLOSED_DEFAULT",
        )

        return await self._evaluate_approval_override(request, decision)

    async def _evaluate_approval_override(
        self,
        request: PermissionEvaluationRequest,
        decision: PermissionDecision,
    ) -> PermissionDecision:
        """
        Evaluate explicit user approval context.

        ONLY called AFTER rules 1-10.
        Can ONLY upgrade ASK_USER to ALLOW.
        CANNOT override DENY.
        CRITICAL risk is ALWAYS DENY, even with valid approval context.
        """
        # Defensive CRITICAL check: CRITICAL can NEVER be authorized via approval,
        # regardless of the original decision type (DENY, ASK_USER, or ALLOW).
        tool_def = self._registry.get(request.tool_id)
        if tool_def is not None and tool_def.risk_level == RiskLevel.CRITICAL:
            return PermissionDecision(
                decision=PermissionDecisionType.DENY,
                reason="CRITICAL risk operations can never be authorized through approval.",
                rule_id="RULE_11_CRITICAL_APPROVAL_DENIED",
            )

        if request.approval_context is None:
            return decision

        if self._approval_repo is None:
            return PermissionDecision(
                decision=PermissionDecisionType.DENY,
                reason="Approval context provided but approval repository not configured.",
                rule_id="RULE_APPROVAL_MISCONFIGURED",
            )

        approval = self._approval_repo.get(request.approval_context.approval_id)

        if approval is None:
            return PermissionDecision(
                decision=PermissionDecisionType.DENY,
                reason="Approval context references non-existent approval.",
                rule_id="RULE_APPROVAL_INVALID",
            )

        if approval.status != ApprovalStatus.APPROVED:
            return PermissionDecision(
                decision=PermissionDecisionType.DENY,
                reason=f"Approval is not in APPROVED state (current: {approval.status}).",
                rule_id="RULE_APPROVAL_NOT_APPROVED",
            )

        if approval.is_expired():
            return PermissionDecision(
                decision=PermissionDecisionType.DENY,
                reason="Approval has expired.",
                rule_id="RULE_APPROVAL_EXPIRED",
            )

        # tool_id binding
        if approval.tool_id != request.tool_id:
            return PermissionDecision(
                decision=PermissionDecisionType.DENY,
                reason=f"Approval tool_id mismatch. Expected: {approval.tool_id}, got: {request.tool_id}",
                rule_id="RULE_APPROVAL_TOOL_MISMATCH",
            )

        # action binding
        if approval.action != request.action:
            return PermissionDecision(
                decision=PermissionDecisionType.DENY,
                reason=f"Approval action mismatch. Expected: {approval.action}, got: {request.action}",
                rule_id="RULE_APPROVAL_ACTION_MISMATCH",
            )

        # tool_call_id binding
        current_tool_call_id = request.context.get("tool_call_id") if request.context else None
        if approval.tool_call_id != current_tool_call_id:
            return PermissionDecision(
                decision=PermissionDecisionType.DENY,
                reason=f"Approval tool_call_id mismatch. Expected: {approval.tool_call_id}, got: {current_tool_call_id}",
                rule_id="RULE_APPROVAL_TOOL_CALL_ID_MISMATCH",
            )

        # arguments_hash binding
        canonical_args = json.dumps(request.arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        computed_hash = hashlib.sha256(canonical_args.encode("utf-8")).hexdigest()
        if approval.arguments_hash != computed_hash:
            return PermissionDecision(
                decision=PermissionDecisionType.DENY,
                reason="Approval arguments hash mismatch.",
                rule_id="RULE_APPROVAL_ARGS_MISMATCH",
            )

        # request_id binding
        stored_request_id = approval.request_id
        current_request_id = request.context.get("request_id") if request.context else None
        if stored_request_id is not None and current_request_id != stored_request_id:
            return PermissionDecision(
                decision=PermissionDecisionType.DENY,
                reason=f"Approval request_id mismatch. Expected: {stored_request_id}, got: {current_request_id}",
                rule_id="RULE_APPROVAL_REQUEST_ID_MISMATCH",
            )

        # tool definition fingerprint binding
        if approval.tool_definition_fingerprint is not None and tool_def is not None:
            current_fingerprint = compute_tool_definition_fingerprint(tool_def)
            if current_fingerprint != approval.tool_definition_fingerprint:
                return PermissionDecision(
                    decision=PermissionDecisionType.DENY,
                    reason="Tool definition has changed since approval was created.",
                    rule_id="RULE_APPROVAL_TOOL_DEFINITION_CHANGED",
                )

        # Override ASK_USER -> ALLOW (only for overridable rules; CRITICAL is
        # already denied at the top of this method)
        if decision.decision == PermissionDecisionType.ASK_USER and decision.rule_id in _OVERRIDABLE_ASK_USER_RULES:
            return PermissionDecision(
                decision=PermissionDecisionType.ALLOW,
                reason=f"Approved by user via approval '{approval.approval_id}'. Original: {decision.reason}",
                rule_id="RULE_APPROVAL_OVERRIDE",
                grant_id=None,
            )

        return decision

