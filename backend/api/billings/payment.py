from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from api.docs import errors
from core.db import get_db
from core.dependencies import Access, get_current_user, hotel_access, guest_or_hotel_access
from core.exceptions import APIException
from core.logging import get_structured_logger
from core.scopes import Scope
from core.utils.response import Response
from models.accounts.user import User
from models.billings.payment import PaymentStatus
from schemas.billings.payment import (
    PaymentCreate, PaymentConfirm, PaymentRefund, PaymentList, PaymentResponse, PaymentSummary,
)
from services.billings.payment import PaymentService

logger = get_structured_logger(__name__)

router = APIRouter(prefix="/payments", tags=["Payments"])

# Guests work on their own payments. A caller whose role holds payments:view is staff and reads the payments for the
# hotels they work on. A payment or booking that isn't yours (or isn't at your hotels) is reported as 404, so its
# existence isn't revealed.


@router.post(
    "/", status_code=status.HTTP_201_CREATED, summary="Pay for a booking",
    responses=errors(400, 401, 403, 404, 409, 422, 500),
)
async def create_payment(
    payload: PaymentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start a payment towards one of your bookings, in the booking's currency. Leave `amount` out to pay everything
    still owed. The payment starts pending until the payment provider confirms it."""
    try:
        payment = await PaymentService(db).create(current_user.id, payload)
        logger.info(f"{current_user.email} started a payment of {payment.amount} {payment.currency} for {payment.booking_code}")
        return Response.success(
            data=PaymentResponse.model_validate(payment), message="Payment started", status_code=status.HTTP_201_CREATED
        )
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Creating a payment failed")
        raise APIException(status_code=500, message="Could not start the payment")


@router.get("/", summary="List payments", responses=errors(400, 401, 403, 422, 500))
async def list_payments(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    user_id: Optional[UUID] = Query(None, description="Only has an effect for callers with payments:view"),
    booking_id: Optional[UUID] = Query(None),
    payment_status: Optional[PaymentStatus] = Query(None, alias="status"),
    access: Access = Depends(guest_or_hotel_access(Scope.PAYMENTS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Your payments, newest first. With payments:view, the payments for your hotels (narrow with user_id)."""
    try:
        filters = PaymentList(user_id=user_id, booking_id=booking_id, status=payment_status)
        result = await PaymentService(db).list(filters, page, limit, access.user_id, access.hotel_ids)
        payments = [PaymentResponse.model_validate(p) for p in result["payments"]]
        return Response.success(data=payments, pagination=result["pagination"])
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Listing payments failed")
        raise APIException(status_code=500, message="Could not load the payments")


@router.get("/booking/{booking_id}/summary/", summary="A booking's balance", responses=errors(401, 403, 404, 500))
async def booking_payment_summary(
    booking_id: UUID,
    access: Access = Depends(guest_or_hotel_access(Scope.PAYMENTS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """What one of your bookings costs and how much is paid (net of refunds), pending, refunded and still owed (any
    booking at your hotels, with payments:view)."""
    try:
        summary = await PaymentService(db).summary(booking_id, access.user_id, access.hotel_ids)
        return Response.success(data=PaymentSummary(**summary))
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Summary of booking {booking_id} failed")
        raise APIException(status_code=500, message="Could not load the balance")


@router.get("/{payment_id}/", summary="Get a payment", responses=errors(401, 403, 404, 500))
async def get_payment(
    payment_id: UUID,
    access: Access = Depends(guest_or_hotel_access(Scope.PAYMENTS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Get one of your payments (any at your hotels, with payments:view)."""
    try:
        payment = await PaymentService(db).get(payment_id, access.user_id, access.hotel_ids)
        return Response.success(data=PaymentResponse.model_validate(payment))
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Fetching payment {payment_id} failed")
        raise APIException(status_code=500, message="Could not load the payment")


@router.post("/{payment_id}/confirm/", summary="Confirm a payment", responses=errors(401, 403, 404, 409, 422, 500))
async def confirm_payment(
    payment_id: UUID,
    payload: PaymentConfirm,
    background_tasks: BackgroundTasks,
    access: Access = Depends(hotel_access(Scope.PAYMENTS_CONFIRM)),
    db: AsyncSession = Depends(get_db),
):
    """Record that the money arrived (needs payments:confirm, for a hotel you work on): pending becomes succeeded and
    paid_at is stamped. The provider's transaction id can be sent as `reference`; it must be unique. When the booking
    is then fully paid it is confirmed automatically and the guest is emailed."""
    try:
        payment = await PaymentService(db).confirm(payment_id, payload.reference, access.hotel_ids, background_tasks)
        logger.info(f"{access.user.email} confirmed payment {payment_id} for {payment.booking_code}")
        return Response.success(data=PaymentResponse.model_validate(payment), message="Payment confirmed")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Confirming payment {payment_id} failed")
        raise APIException(status_code=500, message="Could not confirm the payment")


@router.post("/{payment_id}/fail/", summary="Mark a payment as failed", responses=errors(401, 403, 404, 409, 500))
async def fail_payment(
    payment_id: UUID,
    access: Access = Depends(hotel_access(Scope.PAYMENTS_FAIL)),
    db: AsyncSession = Depends(get_db),
):
    """Record that a pending payment did not go through (needs payments:fail, for a hotel you work on). Its amount can be paid again."""
    try:
        payment = await PaymentService(db).fail(payment_id, access.hotel_ids)
        logger.info(f"{access.user.email} marked payment {payment_id} as failed")
        return Response.success(data=PaymentResponse.model_validate(payment), message="Payment marked as failed")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Failing payment {payment_id} failed")
        raise APIException(status_code=500, message="Could not mark the payment as failed")


@router.post("/{payment_id}/refund/", summary="Refund a payment", responses=errors(400, 401, 403, 404, 409, 422, 500))
async def refund_payment(
    payment_id: UUID,
    payload: Optional[PaymentRefund] = None,
    access: Access = Depends(hotel_access(Scope.PAYMENTS_REFUND)),
    db: AsyncSession = Depends(get_db),
):
    """Record a refund of a succeeded payment (needs payments:refund, for a hotel you work on): all that is left of it,
    or `{"amount": n}` for a part refund. Refunded money is owed again. This only records the refund; sending the money
    back happens with the payment provider."""
    try:
        payment = await PaymentService(db).refund(payment_id, payload.amount if payload else None, access.hotel_ids)
        logger.info(f"{access.user.email} refunded payment {payment_id}")
        return Response.success(data=PaymentResponse.model_validate(payment), message="Payment refunded")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Refunding payment {payment_id} failed")
        raise APIException(status_code=500, message="Could not refund the payment")
