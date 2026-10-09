# Consolidated payment routes with 5 standard APIs per entity

from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi import status as http_status  # alias: list_all_transactions has a `status` query param that shadows the module above
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from uuid import UUID

from core.db import get_db
from core.dependencies import require_admin, require_auth
from core.utils.response import Response
from core.exceptions import APIException
from models.accounts.user import User
from services.commerce.payments import PaymentService
from schemas.commerce.payments import MethodResponse, MethodCreate
from schemas.commerce.payments import MethodResponse, MethodCreate, IntentResponse, TxnResponse

router = APIRouter(prefix="/payments", tags=["payments"])


# --- PAYMENT METHODS - 5 Standard APIs ---
@router.post("/methods/")
async def create_method(
    payment_method_data: MethodCreate,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Create a new payment method"""
    try:
        service = PaymentService(db)
        payment_method = await service.create_method(
            user_id=current_user.id,
            stripe_payment_method_id=payment_method_data.stripe_payment_method_id,
            is_default=payment_method_data.is_default,
            payment_method_metadata=payment_method_data.payment_method_metadata,
        )
        return Response.success(data=MethodResponse.model_validate(payment_method), status_code=status.HTTP_201_CREATED, message="Payment method created successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"Failed to create payment method: {str(e)}")


@router.get("/methods/")
async def list_methods(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    search: Optional[str] = Query(None),
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """List all payment methods for user with pagination and search"""
    try:
        service = PaymentService(db)
        result = await service.list(current_user.id, page=page, limit=limit, search=search)
        if isinstance(result, dict) and "data" in result and "pagination" in result:
            return Response.success(data=result.get("data", []), pagination=result.get("pagination"), message="Payment methods retrieved successfully")
        return Response.success(data=result, message="Payment methods retrieved successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"Failed to list payment methods: {str(e)}")


@router.delete("/methods/{payment_method_id}/")
async def delete_method(
    payment_method_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Delete a payment method"""
    try:
        service = PaymentService(db)
        success = await service.delete(payment_method_id, current_user.id)
        if not success:
            raise APIException(status_code=404, message="Payment method not found")
        return Response.success(message="Payment method deleted successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"Failed to delete payment method: {str(e)}")


# --- PAYMENT INTENTS - 5 Standard APIs ---


# --- TRANSACTIONS - Read Only (system creates automatically) ---


@router.get("/intents/{payment_intent_id}/")
async def get_intent(
    payment_intent_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """One of your payment intents; one still in progress is re-checked with Stripe first."""
    try:
        service = PaymentService(db)
        intent = await service.get_intent(payment_intent_id, current_user.id)
        if not intent:
            raise APIException(status_code=404, message="Payment intent not found")
        if intent.status in ("requires_action", "processing", "requires_confirmation"):
            intent = await service.sync_intent(intent)
        return Response.success(data=IntentResponse.model_validate(intent), message="Payment intent retrieved successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"Failed to get payment intent: {str(e)}")


@router.get("/intents/")
async def list_intents(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """List payment intents for user"""
    try:
        service = PaymentService(db)
        result = await service.list_intents(current_user.id, page=page, limit=limit)
        if isinstance(result, dict) and "items" in result:
            pagination = {
                "page": result.get("page", page),
                "limit": result.get("limit", limit),
                "total": result.get("total", 0),
                "pages": (result.get("total", 0) + limit - 1) // limit
            }
            return Response.success(data=[IntentResponse.model_validate(i) for i in result.get("items", [])], pagination=pagination)
        return Response.success(data=result)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"Failed to list payment intents: {str(e)}")


@router.get("/transactions/{transaction_id}/")
async def get_transaction(
    transaction_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Get a specific transaction"""
    try:
        service = PaymentService(db)
        transaction = await service.get_transaction(transaction_id, current_user.id)
        if not transaction:
            raise APIException(status_code=404, message="Transaction not found")
        return Response.success(data=TxnResponse.model_validate(transaction), message="Transaction retrieved successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"Failed to get transaction: {str(e)}")


@router.get("/transactions/")
async def list_transactions(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """List transactions for user"""
    try:
        service = PaymentService(db)
        result = await service.transactions(current_user.id, page=page, limit=limit)
        return Response.success(data=result.get("transactions", []), pagination=result.get("pagination"))
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"Failed to list transactions: {str(e)}")


@router.get("/admin/transactions/")
async def list_all_transactions(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    payment_method: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """List all transactions (admin only)"""
    try:
        service = PaymentService(db)
        result = await service.all_transactions(
            page=page,
            limit=limit,
            search=search,
            status=status,
            payment_method=payment_method,
            date_from=date_from,
            date_to=date_to
        )
        return Response.success(data=result.get("transactions", []), pagination=result.get("pagination"))
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"Failed to list transactions: {str(e)}")


# --- REFUNDS - Create & List Only (immutable after processing) ---


# --- KEPT ROUTES - Additional functionality ---


@router.post("/intents/{payment_intent_id}/confirm/")
async def confirm_intent(
    payment_intent_id: UUID,
    payment_method_id: str,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Charge one of your payment intents with a saved card (or Stripe PaymentMethod id)."""
    try:
        service = PaymentService(db)
        if not await service.get_intent(payment_intent_id, current_user.id):
            raise APIException(status_code=404, message="Payment intent not found")
        payment_intent = await service.confirm_intent(
            payment_intent_id=payment_intent_id,
            payment_method_id=payment_method_id
        )
        return Response.success(data=IntentResponse.model_validate(payment_intent), message="Payment intent confirmed successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"Failed to confirm payment intent: {str(e)}")


@router.post("/methods/{payment_method_id}/default/")
async def set_default_method(
    payment_method_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Set a payment method as default"""
    try:
        service = PaymentService(db)
        success = await service.set_default(payment_method_id, current_user.id)
        if not success:
            raise APIException(status_code=404, message="Payment method not found")
        return Response.success(message="Payment method set as default successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"Failed to set default payment method: {str(e)}")


# --- FAILURE HANDLING - Kept routes ---


@router.get("/failures/{payment_intent_id}/status/")
async def failure_status(
    payment_intent_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Get detailed status of a failed payment"""
    try:
        service = PaymentService(db)
        failure_details = await service.failure_status(payment_intent_id, current_user.id)
        return Response.success(data=failure_details, message="Payment failure details retrieved")
    except HTTPException as e:
        raise APIException(status_code=e.status_code, message=e.detail)
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to get failure status: {str(e)}")


@router.post("/failures/{payment_intent_id}/retry/")
async def retry_payment(
    payment_intent_id: UUID,
    new_payment_method_id: Optional[UUID] = None,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Reset one of your failed subscription payments so it can be charged again (then confirm it)."""
    try:
        service = PaymentService(db)
        if not await service.get_intent(payment_intent_id, current_user.id):
            raise HTTPException(status_code=404, detail="Payment intent not found")
        retry_result = await service.retry(payment_intent_id, new_payment_method_id)
        return Response.success(data=retry_result, message="Payment retry initiated")
    except HTTPException as e:
        raise APIException(status_code=e.status_code, message=e.detail)
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to retry payment: {str(e)}")


@router.get("/failures/")
async def list_failures(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """List user's failed payments"""
    try:
        service = PaymentService(db)
        result = await service.failed_payments(current_user.id, page=page, limit=limit)
        if isinstance(result, dict) and "pagination" in result:
            return Response.success(data=result.get("failed_payments", []), pagination=result.get("pagination"))
        return Response.success(data=result)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"Failed to list failed payments: {str(e)}")