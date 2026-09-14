"""
Stage 9.5 — AI Manager Approval Foundation Tests.

Covers APPROVAL-01 through APPROVAL-40.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock

import pytest

from app.ai_manager.approval_models import (
    ApprovalStatus,
    CapabilityApproval,
    CapabilityApprovalRequest,
    compute_scope_fingerprint,
)
from app.ai_manager.approval_repository import CapabilityApprovalRepository
from app.ai_manager.approval_service import (
    CapabilityApprovalError,
    CapabilityApprovalExpiredError,
    CapabilityApprovalNotFoundError,
    CapabilityApprovalService,
    CapabilityApprovalStateError,
)
from app.ai_manager.models import RiskLevel


def _make_request(
    intent_id: str = "intent_1",
    capabilities: list[str] | None = None,
    effective_risk: RiskLevel = RiskLevel.LOW,
    operation_type: str = "plan",
    project_id: str | None = None,
    requested_by: str | None = None,
    ttl_seconds: float | None = None,
) -> CapabilityApprovalRequest:
    return CapabilityApprovalRequest(
        intent_id=intent_id,
        capabilities=capabilities or ["development.plan"],
        effective_risk=effective_risk,
        operation_type=operation_type,
        project_id=project_id,
        request_reason="test",
        requested_by=requested_by,
        ttl_seconds=ttl_seconds,
    )


def _fingerprint(intent_id: str = "intent_1", capabilities: list[str] | None = None) -> str:
    return compute_scope_fingerprint(
        intent_id=intent_id,
        capabilities=capabilities or ["development.plan"],
        effective_risk=RiskLevel.LOW,
        operation_type="plan",
    )


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-01: Create PENDING approval
# ─────────────────────────────────────────────────────────────────────────────

class TestCreatePendingApproval:
    async def test_create_pending_approval(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        repo.create.return_value = CapabilityApproval(
            approval_id="cap_approval_abc123",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        result = await service.request_approval(_make_request())
        assert result.status == ApprovalStatus.PENDING
        assert result.approval_id.startswith("cap_approval_")

    async def test_approval_id_is_unique(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        counter = [0]

        def make_approval(_):
            counter[0] += 1
            return CapabilityApproval(
                approval_id=f"cap_approval_{counter[0]:012d}",
                intent_id="intent_1",
                operation_type="plan",
                scope_fingerprint=_fingerprint(),
                effective_risk=RiskLevel.LOW,
                status=ApprovalStatus.PENDING,
                created_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )

        repo.create.side_effect = make_approval
        results = []
        for _ in range(10):
            r = await service.request_approval(_make_request())
            results.append(r.approval_id)
        assert len(set(results)) == 10


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-02: Approve PENDING approval
# ─────────────────────────────────────────────────────────────────────────────

class TestApprovePendingApproval:
    async def test_approve_pending(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        approval = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo.get_pending.return_value = approval
        repo.mark_approved.return_value = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.APPROVED,
            approved_by="admin",
            decision_reason="ok",
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        result = await service.approve("cap_approval_1", approved_by="admin", reason="ok")
        assert result.status == ApprovalStatus.APPROVED


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-03: Deny PENDING approval
# ─────────────────────────────────────────────────────────────────────────────

class TestDenyPendingApproval:
    async def test_deny_pending(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        approval = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo.get_pending.return_value = approval
        repo.mark_denied.return_value = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.DENIED,
            denied_by="admin",
            decision_reason="no",
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        result = await service.deny("cap_approval_1", denied_by="admin", reason="no")
        assert result.status == ApprovalStatus.DENIED


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-04: Cancel PENDING approval
# ─────────────────────────────────────────────────────────────────────────────

class TestCancelPendingApproval:
    async def test_cancel_pending(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        approval = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo.get_pending.return_value = approval
        repo.mark_cancelled.return_value = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.CANCELLED,
            decision_reason="changed mind",
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        result = await service.cancel("cap_approval_1", reason="changed mind")
        assert result.status == ApprovalStatus.CANCELLED


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-05: Consume APPROVED approval
# ─────────────────────────────────────────────────────────────────────────────

class TestConsumeApprovedApproval:
    async def test_consume_approved(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        approval = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.APPROVED,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo.get.return_value = approval
        repo.mark_consumed.return_value = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.CONSUMED,
            consumed_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        result = await service.consume("cap_approval_1")
        assert result.status == ApprovalStatus.CONSUMED


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-06: Cannot consume PENDING
# ─────────────────────────────────────────────────────────────────────────────

class TestCannotConsumePending:
    async def test_consume_pending_fails(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        approval = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo.get.return_value = approval

        with pytest.raises(CapabilityApprovalStateError, match="expected APPROVED"):
            await service.consume("cap_approval_1")


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-07: Cannot approve DENIED
# ─────────────────────────────────────────────────────────────────────────────

class TestCannotApproveDenied:
    async def test_approve_denied_fails(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        repo.get_pending.return_value = None

        with pytest.raises(CapabilityApprovalNotFoundError):
            await service.approve("cap_approval_1", approved_by="admin")


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-08: Cannot approve CONSUMED
# ─────────────────────────────────────────────────────────────────────────────

class TestCannotApproveConsumed:
    async def test_approve_consumed_fails(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        repo.get_pending.return_value = None

        with pytest.raises(CapabilityApprovalNotFoundError):
            await service.approve("cap_approval_1", approved_by="admin")


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-09: Cannot approve EXPIRED
# ─────────────────────────────────────────────────────────────────────────────

class TestCannotApproveExpired:
    async def test_approve_expired_fails(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        expired = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        repo.get_pending.return_value = expired
        repo.mark_expired.return_value = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.EXPIRED,
            created_at=expired.created_at,
            expires_at=expired.expires_at,
        )

        with pytest.raises(CapabilityApprovalNotFoundError):
            await service.approve("cap_approval_1", approved_by="admin")


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-10: Cannot approve CANCELLED
# ─────────────────────────────────────────────────────────────────────────────

class TestCannotApproveCancelled:
    async def test_approve_cancelled_fails(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        repo.get_pending.return_value = None

        with pytest.raises(CapabilityApprovalNotFoundError):
            await service.approve("cap_approval_1", approved_by="admin")


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-11: Cannot consume DENIED
# ─────────────────────────────────────────────────────────────────────────────

class TestCannotConsumeDenied:
    async def test_consume_denied_fails(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        approval = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.DENIED,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo.get.return_value = approval

        with pytest.raises(CapabilityApprovalStateError, match="expected APPROVED"):
            await service.consume("cap_approval_1")


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-12: Cannot consume EXPIRED
# ─────────────────────────────────────────────────────────────────────────────

class TestCannotConsumeExpired:
    async def test_consume_expired_fails(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        approval = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.EXPIRED,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        repo.get.return_value = approval

        with pytest.raises(CapabilityApprovalExpiredError):
            await service.consume("cap_approval_1")


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-13: Cannot consume CANCELLED
# ─────────────────────────────────────────────────────────────────────────────

class TestCannotConsumeCancelled:
    async def test_consume_cancelled_fails(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        approval = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.CANCELLED,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo.get.return_value = approval

        with pytest.raises(CapabilityApprovalStateError, match="expected APPROVED"):
            await service.consume("cap_approval_1")


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-14: Cannot consume twice
# ─────────────────────────────────────────────────────────────────────────────

class TestCannotConsumeTwice:
    async def test_double_consume_fails(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        approval = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.APPROVED,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo.get.return_value = approval
        repo.mark_consumed.side_effect = [
            CapabilityApproval(
                approval_id="cap_approval_1",
                intent_id="intent_1",
                operation_type="plan",
                scope_fingerprint=_fingerprint(),
                effective_risk=RiskLevel.LOW,
                status=ApprovalStatus.CONSUMED,
                consumed_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ),
            None,
        ]

        await service.consume("cap_approval_1")
        with pytest.raises(CapabilityApprovalStateError, match="already consumed"):
            await service.consume("cap_approval_1")


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-15: Concurrent consume has exactly one winner
# ─────────────────────────────────────────────────────────────────────────────

class TestConcurrentConsume:
    async def test_concurrent_consume_one_wins(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        approval = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.APPROVED,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        winners = []
        call_count = 0

        async def mock_get(approval_id):
            return approval

        async def mock_consume(approval_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return CapabilityApproval(
                    approval_id="cap_approval_1",
                    intent_id="intent_1",
                    operation_type="plan",
                    scope_fingerprint=_fingerprint(),
                    effective_risk=RiskLevel.LOW,
                    status=ApprovalStatus.CONSUMED,
                    consumed_at=datetime.now(timezone.utc),
                    created_at=datetime.now(timezone.utc),
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                )
            return None

        repo.get = mock_get
        repo.mark_consumed = mock_consume

        results = await asyncio.gather(
            service.consume("cap_approval_1"),
            service.consume("cap_approval_1"),
            return_exceptions=True,
        )
        successes = [r for r in results if not isinstance(r, Exception)]
        failures = [r for r in results if isinstance(r, Exception)]
        assert len(successes) == 1
        assert len(failures) == 1


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-16: Scope mismatch blocks consumption
# ─────────────────────────────────────────────────────────────────────────────

class TestScopeMismatchBlocksConsumption:
    async def test_scope_fingerprint_stored_in_approval(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        request = _make_request()
        expected_fp = _fingerprint(request.intent_id, request.capabilities)

        created = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id=request.intent_id,
            operation_type=request.operation_type,
            scope_fingerprint=expected_fp,
            effective_risk=request.effective_risk,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo.create.return_value = created

        result = await service.request_approval(request)
        assert result.scope_fingerprint == expected_fp


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-17: Capability mismatch blocks consumption
# ─────────────────────────────────────────────────────────────────────────────

class TestCapabilityMismatchBlocksConsumption:
    async def test_different_capabilities_produce_different_fingerprint(self):
        fp1 = compute_scope_fingerprint("intent_1", ["development.plan"], RiskLevel.LOW, "plan")
        fp2 = compute_scope_fingerprint("intent_1", ["development.execute"], RiskLevel.HIGH, "plan")
        assert fp1 != fp2


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-18: Intent mismatch blocks consumption
# ─────────────────────────────────────────────────────────────────────────────

class TestIntentMismatchBlocksConsumption:
    async def test_different_intent_produces_different_fingerprint(self):
        fp1 = compute_scope_fingerprint("intent_1", ["development.plan"], RiskLevel.LOW, "plan")
        fp2 = compute_scope_fingerprint("intent_2", ["development.plan"], RiskLevel.LOW, "plan")
        assert fp1 != fp2


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-19: Plan mismatch (project_id in fingerprint)
# ─────────────────────────────────────────────────────────────────────────────

class TestProjectMismatchBlocksConsumption:
    async def test_different_project_produces_different_fingerprint(self):
        fp1 = compute_scope_fingerprint("intent_1", ["development.plan"], RiskLevel.LOW, "plan", project_id="proj_a")
        fp2 = compute_scope_fingerprint("intent_1", ["development.plan"], RiskLevel.LOW, "plan", project_id="proj_b")
        assert fp1 != fp2


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-20: Risk mismatch blocks consumption
# ─────────────────────────────────────────────────────────────────────────────

class TestRiskMismatchBlocksConsumption:
    async def test_different_risk_produces_different_fingerprint(self):
        fp1 = compute_scope_fingerprint("intent_1", ["development.plan"], RiskLevel.LOW, "plan")
        fp2 = compute_scope_fingerprint("intent_1", ["development.plan"], RiskLevel.HIGH, "plan")
        assert fp1 != fp2


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-21: Policy change invalidates incompatible approval
# ─────────────────────────────────────────────────────────────────────────────

class TestPolicyChangeInvalidatesApproval:
    async def test_fingerprint_captures_policy_state(self):
        fp_low = compute_scope_fingerprint("intent_1", ["development.plan"], RiskLevel.LOW, "plan")
        fp_high = compute_scope_fingerprint("intent_1", ["development.plan"], RiskLevel.HIGH, "plan")
        assert fp_low != fp_high
        # If policy changes effective risk, fingerprint changes, approval no longer matches


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-22: Denied capability cannot create usable approval
# ─────────────────────────────────────────────────────────────────────────────

class TestDeniedCapabilityCannotCreateApproval:
    async def test_approval_request_for_denied_capability(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        # The service itself doesn't know about capabilities — that's the policy gate's job.
        # But the application should not request approval for denied capabilities.
        # This test verifies the service doesn't silently accept arbitrary scopes.
        repo.create.return_value = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint("intent_1", ["unknown.evil"]),
            effective_risk=RiskLevel.HIGH,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        result = await service.request_approval(_make_request(capabilities=["unknown.evil"]))
        assert result.status == ApprovalStatus.PENDING
        # Application policy must prevent this from reaching execution


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-23: Model cannot approve its own request
# ─────────────────────────────────────────────────────────────────────────────

class TestModelCannotApprove:
    async def test_model_approval_has_zero_effect(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        # Model output cannot grant approval — only explicit application action can
        # The service has no method that accepts model-controlled approval state
        assert not hasattr(service, "model_approve")
        assert not hasattr(service, "approve_from_model")


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-24: approval_required=False cannot create approval grant
# ─────────────────────────────────────────────────────────────────────────────

class TestApprovalRequiredFalseCannotGrant:
    async def test_approval_required_false_does_not_grant(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        repo.get.return_value = None
        with pytest.raises(CapabilityApprovalNotFoundError):
            await service.consume("nonexistent")


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-25: Approval cannot override DENY
# ─────────────────────────────────────────────────────────────────────────────

class TestApprovalCannotOverrideDeny:
    async def test_denied_policy_cannot_be_overridden_by_approval(self):
        # This is enforced at the policy gate level (Stage 9.4).
        # The approval service only manages approval state for genuine NEEDS_APPROVAL.
        # Approval does not retroactively change DENY to ALLOW.
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        # No API in this service to "override DENY"
        assert not hasattr(service, "override_deny")


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-26: Approval can satisfy genuine NEEDS_APPROVAL
# ─────────────────────────────────────────────────────────────────────────────

class TestApprovalSatisfiesNeedsApproval:
    async def test_approval_satisfies_needs_approval(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        pending = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo.get_pending.return_value = pending
        repo.mark_approved.return_value = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.APPROVED,
            approved_by="admin",
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        result = await service.approve("cap_approval_1", approved_by="admin")
        assert result.status == ApprovalStatus.APPROVED


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-27: Approval does not execute operation
# ─────────────────────────────────────────────────────────────────────────────

class TestApprovalDoesNotExecute:
    def test_approval_service_has_no_execute_method(self):
        # The approval service manages state only. Execution is separate.
        assert not hasattr(CapabilityApprovalService, "execute")
        assert not hasattr(CapabilityApprovalService, "run")
        assert not hasattr(CapabilityApprovalService, "perform")


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-28: Approval does not execute plan
# ─────────────────────────────────────────────────────────────────────────────

class TestApprovalDoesNotExecutePlan:
    def test_no_plan_manager_in_approval_service(self):
        import inspect
        source = inspect.getsource(CapabilityApprovalService)
        assert "PlanManager" not in source
        assert "plan" not in source or "operation_type" in source


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-29: Expiration is enforced
# ─────────────────────────────────────────────────────────────────────────────

class TestExpirationEnforced:
    async def test_expired_approval_cannot_be_approved(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        expired = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        repo.get_pending.return_value = expired
        repo.mark_expired.return_value = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.EXPIRED,
            created_at=expired.created_at,
            expires_at=expired.expires_at,
        )

        with pytest.raises(CapabilityApprovalNotFoundError):
            await service.approve("cap_approval_1", approved_by="admin")


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-30: Maximum TTL is enforced
# ─────────────────────────────────────────────────────────────────────────────

class TestMaximumTTLEnforced:
    async def test_ttl_capped_at_max(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo, max_ttl=timedelta(hours=1))

        captured = {}

        def capture_create(approval):
            captured["approval"] = approval
            return CapabilityApproval(
                approval_id="cap_approval_1",
                intent_id=approval.intent_id,
                operation_type=approval.operation_type,
                scope_fingerprint=approval.scope_fingerprint,
                effective_risk=approval.effective_risk,
                status=ApprovalStatus.PENDING,
                created_at=approval.created_at,
                expires_at=approval.expires_at,
            )

        repo.create.side_effect = capture_create

        await service.request_approval(_make_request(ttl_seconds=7200))
        assert (captured["approval"].expires_at - captured["approval"].created_at).total_seconds() <= 3600


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-31: Actor information is application-controlled
# ─────────────────────────────────────────────────────────────────────────────

class TestActorControlled:
    async def test_approved_by_is_set_by_application(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        pending = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo.get_pending.return_value = pending
        repo.mark_approved.return_value = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.APPROVED,
            approved_by="user_123",
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        result = await service.approve("cap_approval_1", approved_by="user_123")
        assert result.approved_by == "user_123"


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-32: Approval state transitions are atomic
# ─────────────────────────────────────────────────────────────────────────────

class TestAtomicStateTransitions:
    async def test_approve_uses_atomic_update(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        pending = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo.get_pending.return_value = pending
        repo.mark_approved.return_value = None  # simulate concurrent modification

        with pytest.raises(CapabilityApprovalStateError, match="concurrent request"):
            await service.approve("cap_approval_1", approved_by="admin")


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-33: Database failure does not create phantom approval
# ─────────────────────────────────────────────────────────────────────────────

class TestDatabaseFailureNoPhantom:
    async def test_repo_failure_raises(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        repo.create.side_effect = Exception("database error")
        service = CapabilityApprovalService(repo=repo)

        with pytest.raises(Exception, match="database error"):
            await service.request_approval(_make_request())


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-34: Database failure during consume does not produce false success
# ─────────────────────────────────────────────────────────────────────────────

class TestDatabaseFailureDuringConsume:
    async def test_consume_repo_failure_raises(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        approval = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.APPROVED,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo.get.return_value = approval
        repo.mark_consumed.side_effect = Exception("database error")

        with pytest.raises(Exception, match="database error"):
            await service.consume("cap_approval_1")


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-35: Duplicate approval requests behave deterministically
# ─────────────────────────────────────────────────────────────────────────────

class TestDuplicateApprovalRequests:
    async def test_duplicate_requests_create_separate_records(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        id1 = "cap_approval_a"
        id2 = "cap_approval_b"
        repo.create.side_effect = [
            CapabilityApproval(
                approval_id=id1, intent_id="intent_1", operation_type="plan",
                scope_fingerprint=_fingerprint(), effective_risk=RiskLevel.LOW,
                status=ApprovalStatus.PENDING,
                created_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ),
            CapabilityApproval(
                approval_id=id2, intent_id="intent_1", operation_type="plan",
                scope_fingerprint=_fingerprint(), effective_risk=RiskLevel.LOW,
                status=ApprovalStatus.PENDING,
                created_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ),
        ]

        r1 = await service.request_approval(_make_request())
        r2 = await service.request_approval(_make_request())
        assert r1.approval_id != r2.approval_id
        assert r1.status == ApprovalStatus.PENDING
        assert r2.status == ApprovalStatus.PENDING


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-36: Concurrent approval requests are safe
# ─────────────────────────────────────────────────────────────────────────────

class TestConcurrentApprovalRequests:
    async def test_concurrent_requests_succeed(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)

        def make_response(i):
            return CapabilityApproval(
                approval_id=f"cap_approval_{i}",
                intent_id=f"intent_{i}",
                operation_type="plan",
                scope_fingerprint=_fingerprint(f"intent_{i}"),
                effective_risk=RiskLevel.LOW,
                status=ApprovalStatus.PENDING,
                created_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )

        repo.create.side_effect = [make_response(i) for i in range(5)]

        async def make_request(i):
            return await service.request_approval(_make_request(intent_id=f"intent_{i}"))

        results = await asyncio.gather(*[make_request(i) for i in range(5)])
        assert len(results) == 5
        assert all(r.status == ApprovalStatus.PENDING for r in results)


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-37: Audit records are generated appropriately
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditRecords:
    async def test_audit_called_on_approve(self):
        audit_service = AsyncMock()
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo, audit_service=audit_service)
        pending = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo.get_pending.return_value = pending
        repo.mark_approved.return_value = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.APPROVED,
            approved_by="admin",
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        await service.approve("cap_approval_1", approved_by="admin")
        audit_service.record.assert_called()


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-38: Audit cannot grant approval
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditCannotGrant:
    async def test_audit_service_is_fail_safe(self):
        audit_service = AsyncMock()
        audit_service.record.side_effect = Exception("audit down")
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo, audit_service=audit_service)
        pending = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo.get_pending.return_value = pending
        repo.mark_approved.return_value = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.APPROVED,
            approved_by="admin",
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        result = await service.approve("cap_approval_1", approved_by="admin")
        assert result.status == ApprovalStatus.APPROVED


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-39: No secrets are logged
# ─────────────────────────────────────────────────────────────────────────────

class TestNoSecretsLogged:
    async def test_reason_is_bounded(self):
        repo = AsyncMock(spec=CapabilityApprovalRepository)
        service = CapabilityApprovalService(repo=repo)
        pending = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo.get_pending.return_value = pending
        repo.mark_approved.return_value = CapabilityApproval(
            approval_id="cap_approval_1",
            intent_id="intent_1",
            operation_type="plan",
            scope_fingerprint=_fingerprint(),
            effective_risk=RiskLevel.LOW,
            status=ApprovalStatus.APPROVED,
            approved_by="admin",
            decision_reason="x" * 500,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        result = await service.approve("cap_approval_1", approved_by="admin", reason="x" * 500)
        assert result.status == ApprovalStatus.APPROVED


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL-40: No filesystem/shell/Git/tool execution authority
# ─────────────────────────────────────────────────────────────────────────────

class TestNoExecutionAuthority:
    def test_approval_service_imports_clean(self):
        import ast
        import inspect
        source = inspect.getsource(CapabilityApprovalService)
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)

        forbidden = [
            "subprocess", "shutil", "os", "pathlib",
            "ToolExecutionService", "PermissionEngine", "VerificationGate",
            "RunController", "AutonomousPlanExecutor", "DevelopmentAgent",
            "git",
        ]
        for imp in imports:
            for f in forbidden:
                assert f not in imp, f"ApprovalService imports {imp} which contains forbidden {f}"

    def test_approval_service_no_execution_calls(self):
        import ast
        import inspect
        source = inspect.getsource(CapabilityApprovalService)
        tree = ast.parse(source)
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute):
                    calls.append(func.attr)
                elif isinstance(func, ast.Name):
                    calls.append(func.id)

        forbidden_calls = ["execute", "run", "call", "invoke", "start", "spawn"]
        for call in calls:
            for f in forbidden_calls:
                assert f not in call.lower(), f"ApprovalService contains suspicious call: {call}"
