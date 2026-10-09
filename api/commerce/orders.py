from uuid import UUID
from fastapi import APIRouter, Depends, Query, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import Optional
from core.db import get_db
from core.dependencies import require_admin, require_auth
from core.exceptions import APIException
from core.logging import get_structured_logger
from core.utils.response import Response
from services.commerce.orders import OrderService
from models.accounts.user import User
from models.commerce.orders import Order as OrderModel
from schemas.commerce.orders import Checkout, Note
from services.commerce.shipping_tracking import ShippingTrackingService

router = APIRouter(prefix="/orders", tags=["Orders"])
logger = get_structured_logger(__name__)

# --- ORDERS - 5 Standard APIs ---


@router.get("/statistics/")
async def statistics(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get order statistics (admin only). Must be registered before /{order_id}/ -
    otherwise "statistics" gets captured as order_id and fails UUID validation."""
    try:
        order_service = OrderService(db)
        stats = await order_service.get_statistics(date_from=date_from, date_to=date_to)
        return Response.success(data=stats, message="Order statistics retrieved")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to fetch statistics: {str(e)}")


@router.get("/{order_id}/")
async def get(
    order_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Get a specific order. Admins can view any order, users can only view their own."""
    try:
        order_service = OrderService(db)
        is_admin = current_user.is_staff

        if is_admin:
            order = await order_service.get(order_id)
        else:
            order = await order_service.get(order_id, current_user.id)

        if not order:
            raise APIException(status_code=404, message="Order not found")

        order_dict = order.model_dump()
        if not is_admin:
            order_dict["internal_notes"] = None

        # Add customer information if user is loaded
        order_query = select(OrderModel).where(OrderModel.id == order_id).options(
            selectinload(OrderModel.user)
        )
        order_result = await db.execute(order_query)
        order_with_user = order_result.scalar_one_or_none()

        if order_with_user and order_with_user.user:
            order_dict["customer"] = {
                "id": str(order_with_user.user.id),
                "email": order_with_user.user.email,
                "name": f"{order_with_user.user.firstname} {order_with_user.user.lastname}" if order_with_user.user.firstname else order_with_user.user.email
            }

        return Response.success(data=order_dict, message="Order retrieved successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to fetch order: {str(e)}")


@router.get("/")
async def list(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    status_filter: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    sort_by: Optional[str] = Query(None),
    sort_order: Optional[str] = Query(None),
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """List orders. Returns all orders for admin, user's orders for regular users."""
    try:
        order_service = OrderService(db)
        is_admin = current_user.is_staff

        # Use consolidated list method: user_id=None for admin (all orders), user_id=current_user.id for users
        target_user_id = None if is_admin else current_user.id

        orders = await order_service.list(
            user_id=target_user_id,
            page=page,
            limit=limit,
            status=status_filter,
            search=search,
            date_from=date_from,
            date_to=date_to,
            sort_by=sort_by,
            sort_order=sort_order
        )
            
        if isinstance(orders, dict):
            if "orders" in orders and "pagination" in orders:
                return Response.success(data=orders.get("orders", []), pagination=orders.get("pagination", {}))
            if "data" in orders:
                pagination = {
                    "page": orders.get("page", page),
                    "limit": orders.get("limit", limit),
                    "total": orders.get("total", 0),
                    "pages": orders.get("pages", 1)
                }
                return Response.success(data=orders.get("data", []), pagination=pagination)
        return Response.success(data=orders)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to fetch orders: {str(e)}")


# --- KEPT ROUTES ---
@router.post("/checkout/validate/")
async def validate(
    request: Checkout,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Comprehensive checkout validation."""
    try:
        order_service = OrderService(db)
        validation_result = await order_service.validate_checkout(current_user.id, request)
        return Response.success(data=validation_result, message="Checkout validation completed")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Checkout validation error: {e}", exc_info=True)
        raise APIException(status_code=500, message=f"Checkout validation failed: {str(e)}")


@router.post("/checkout/")
async def checkout(
    request: Checkout,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Place order with comprehensive validation."""
    try:
        order_service = OrderService(db)
        order = await order_service.create(current_user.id, request, background_tasks)
        return Response.success(data=order, message="Order placed successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Order placement failed: {str(e)}")


@router.post("/{order_id}/complete-payment/")
async def complete_payment(
    order_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Finish checkout after the customer's bank verification (3-D Secure) in the browser."""
    try:
        order = await OrderService(db).complete_payment(order_id, current_user.id)
        return Response.success(data=order, message="Payment completed")
    except HTTPException as e:
        raise APIException(status_code=e.status_code, message=e.detail)


@router.patch("/{order_id}/cancel/")
async def cancel(
    order_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Cancel an order."""
    try:
        order_service = OrderService(db)
        order = await order_service.cancel(order_id, current_user.id)
        return Response.success(data=order, message="Order cancelled successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=400, message="Failed to cancel order")


@router.get("/{order_id}/invoice/")
async def get_invoice(
    order_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Get the order's invoice details; the frontend builds the PDF from them."""
    try:
        invoice = await OrderService(db).invoice(order_id, current_user.id, is_admin=current_user.is_staff)
        return Response.success(data=invoice)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to load invoice: {str(e)}")


# --- NOTES - Create, Get, List Only (Immutable Audit Records) ---
@router.post("/{order_id}/notes/")
async def create_note(
    order_id: UUID,
    request: Note,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Add a note: customers to their own order, staff as an internal note on any order."""
    try:
        order_service = OrderService(db)
        result = await order_service.add_note(order_id, current_user.id, request.note, is_admin=current_user.is_staff)
        return Response.success(data=result, message="Note added successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=400, message=f"Failed to add note: {str(e)}")


@router.get("/{order_id}/notes/")
async def list_notes(
    order_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """List all notes for an order."""
    try:
        order_service = OrderService(db)
        notes = await order_service.notes(order_id, current_user.id, is_admin=current_user.is_staff)
        return Response.success(data=notes, message="Notes retrieved successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to get notes: {str(e)}")


# --- ORDER TRACKING - Moved from shipping_tracking.py ---


# --- PUBLIC TRACKING - No authentication required ---
@router.get("/{order_id}/payments/")
async def get_order_payments(
    order_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """An order's payment attempts and transactions: the owner's order, or any order for staff."""
    try:
        order_service = OrderService(db)
        payments = await order_service.payments(order_id, current_user.id, is_admin=current_user.is_staff)
        return Response.success(data=payments, message="Payment information retrieved")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to fetch payments: {str(e)}")


@router.get("/{order_id}/shipments/")
async def get_order_shipments(
    order_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """An order's shipments with their tracking events: the owner's order, or any order for staff."""
    try:
        order_service = OrderService(db)
        is_admin = current_user.is_staff
        order = await order_service.get(order_id, None if is_admin else current_user.id)
        if not order:
            raise APIException(status_code=404, message="Order not found")

        shipping_service = ShippingTrackingService(db)
        shipments = await shipping_service.list_by_order(str(order_id))
        return Response.success(data=shipments, message="Shipments retrieved successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=500,
            message=f"Failed to get order shipments: {str(e)}"
        )


@router.get("/track/{order_id}/")
async def get_public_tracking(
    order_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get order tracking (public - no auth required). Accepts order ID (UUID) or order number."""
    try:
        order_service = OrderService(db)
        tracking = await order_service.tracking_public(order_id)
        return Response.success(data=tracking, message="Public tracking information retrieved")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to retrieve tracking: {str(e)}")


@router.patch("/{order_id}/status/")
async def update_status(
    order_id: str,
    request: dict,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Update order status (admin only)."""
    try:
        order_service = OrderService(db)
        updated_order = await order_service.update_status(
            order_id=order_id,
            status=request.get("status"),
            tracking_number=request.get("tracking_number"),
            carrier_name=request.get("carrier"),
            description=request.get("notes")
        )
        return Response.success(data=updated_order, message="Order status updated")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to update order status: {str(e)}")


