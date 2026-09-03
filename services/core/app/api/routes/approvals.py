"""Approval flow routes."""
from fastapi import APIRouter, Depends, HTTPException

from app.api.schemas.ai import ApprovalApproveRequest, ApprovalRejectRequest, ApprovalResponse
from app.dependencies import get_approval_service, get_tool_execution_service
from app.repositories.approval_repository import ApprovalRepository
from app.tools.approval_service import ApprovalError, ApprovalService
from app.tools.approval_models import ApprovalStatus, PendingApproval

router = APIRouter(prefix="/ai/approvals", tags=["Approvals"])


def _approval_to_response(approval: PendingApproval, execution_result: Any = None) -> ApprovalResponse:
    return ApprovalResponse(
        approval_id=approval.approval_id,
        status=approval.status.value,
        tool_id=approval.tool_id,
        action=approval.action,
        tool_call_id=approval.tool_call_id,
        arguments=approval.arguments,
        conversation_id=approval.conversation_id,
        request_id=approval.request_id,
        created_at=approval.created_at,
        expires_at=approval.expires_at,
        executed_at=approval.executed_at,
        decision_reason=approval.decision_reason,
        execution_result=execution_result,
    )


@router.post("/{approval_id}/approve", response_model=ApprovalResponse)
async def approve_approval(
    approval_id: str,
    request: ApprovalApproveRequest,
    service: ApprovalService = Depends(get_approval_service),
):
    try:
        updated, execution_result = await service.approve(
            approval_id=approval_id,
            decision_reason=request.decision_reason,
        )
        return _approval_to_response(updated, execution_result=execution_result.model_dump() if execution_result else None)
    except ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{approval_id}/reject", response_model=ApprovalResponse)
async def reject_approval(
    approval_id: str,
    request: ApprovalRejectRequest,
    service: ApprovalService = Depends(get_approval_service),
):
    try:
        updated = await service.reject(
            approval_id=approval_id,
            decision_reason=request.decision_reason,
        )
        return _approval_to_response(updated)
    except ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
