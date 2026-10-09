"""Refunds API - Unified routes with role-based access."""
from fastapi import APIRouter, Depends, Query, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from uuid import UUID

from core.db import get_db
from core.dependencies import require_admin, require_auth
from core.utils.response import Response
from models.commerce.refunds import RefundStatus

from schemas.commerce.refunds import Request, UpdateRefundStatus
from services.commerce.refunds import RefundService
from core.exceptions import APIException

router = APIRouter(prefix="/refunds", tags=["refunds"])


@router.get("/")
async def list(
    refund_status: Optional[RefundStatus] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    current_user = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """List refunds. Returns all refunds for admin, user's refunds for regular users."""
    try:
        refund_service = RefundService(db)
        
        # Check if user is admin
        is_admin = current_user.is_staff
        
        if is_admin:
            # Admin gets all refunds
            result = await refund_service.list(
                status=refund_status,
                page=page,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order
            )
        else:
            # Regular user gets only their refunds
            result = await refund_service.list(
                user_id=current_user.id,
                status=refund_status,
                page=page,
                limit=limit
            )
        
        if isinstance(result, dict) and "items" in result:
            pagination = {
                "page": result.get("page", page),
                "limit": result.get("limit", limit),
                "total": result.get("total", 0),
                "pages": result.get("pages", 1)
            }
            return Response.success(data=result.get("items", []), pagination=pagination)
        return Response.success(data=result)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to retrieve refunds: {str(e)}"
        )


@router.get("/{refund_id}/")
async def get(
    refund_id: UUID,
    current_user = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Get refund by ID. Admins can view any refund, users can only view their own."""
    try:
        refund_service = RefundService(db)
        is_admin = current_user.is_staff
        
        if is_admin:
            refund = await refund_service.get(refund_id)
        else:
            refund = await refund_service.get(refund_id, current_user.id)
            
        if not refund:
            raise APIException(
                status_code=status.HTTP_404_NOT_FOUND,
                message="Refund not found"
            )
        return Response.success(data=refund)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to retrieve refund: {str(e)}"
        )


@router.post("/orders/{order_id}/request/")
async def request(
    order_id: UUID,
    refund_request: Request,
    current_user = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Request refund for an order."""
    try:
        refund_service = RefundService(db)
        refund = await refund_service.request(
            user_id=current_user.id,
            order_id=order_id,
            refund_request=refund_request
        )
        return Response.success(data=refund, message="Refund request submitted")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"Failed to request refund: {str(e)}"
        )


@router.put("/{refund_id}/status/")
async def update_status(
    refund_id: UUID,
    payload: UpdateRefundStatus,
    current_user = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Update refund status (admin only)."""
    try:
        refund_service = RefundService(db)
        refund = await refund_service.update_status(
            refund_id=refund_id,
            status=payload.status,
            admin_notes=payload.admin_notes,
            staff_id=current_user.id,
        )
        return Response.success(data=refund, message="Refund status updated successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to update refund status: {str(e)}"
        )


