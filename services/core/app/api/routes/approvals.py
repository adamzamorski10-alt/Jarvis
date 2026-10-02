"""Approval flow routes."""
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.schemas.ai import ApprovalApproveRequest, ApprovalRejectRequest, ApprovalResponse, PendingApprovalListResponse
from app.dependencies import get_approval_service, get_tool_execution_service, get_approval_repository
from app.identity.auth import require_authenticated
from app.repositories.approval_repository import ApprovalRepository
from app.tools.approval_service import ApprovalError, ApprovalService
from app.tools.approval_models import ApprovalStatus, PendingApproval
from pydantic import BaseModel

router = APIRouter(prefix="/ai/approvals", tags=["Approvals"])


class PendingApprovalCountResponse(BaseModel):
    count: int


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
    request: Request,
    approval_id: str,
    req: ApprovalApproveRequest,
    service: ApprovalService = Depends(get_approval_service),
):
    _ = require_authenticated(request)
    try:
        updated, execution_result = await service.approve(
            approval_id=approval_id,
            decision_reason=req.decision_reason,
        )
        return _approval_to_response(updated, execution_result=execution_result.model_dump() if execution_result else None)
    except ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{approval_id}/reject", response_model=ApprovalResponse)
async def reject_approval(
    request: Request,
    approval_id: str,
    req: ApprovalRejectRequest,
    service: ApprovalService = Depends(get_approval_service),
):
    _ = require_authenticated(request)
    try:
        updated = await service.reject(
            approval_id=approval_id,
            decision_reason=req.decision_reason,
        )
        return _approval_to_response(updated)
    except ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/count", response_model=PendingApprovalCountResponse)
async def count_pending_approvals(
    request: Request,
    repo: ApprovalRepository = Depends(get_approval_repository),
):
    _ = require_authenticated(request)
    count = await repo.count_pending()
    return PendingApprovalCountResponse(count=count)


@router.get("", response_model=PendingApprovalListResponse)
async def list_pending_approvals(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    repo: ApprovalRepository = Depends(get_approval_repository),
):
    _ = require_authenticated(request)
    approvals, total = await repo.list_pending(limit=limit, offset=offset)
    return PendingApprovalListResponse(
        approvals=[_approval_to_response(a) for a in approvals],
        total=total,
        limit=limit,
        offset=offset,
    )
