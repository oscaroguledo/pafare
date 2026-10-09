from datetime import date
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
from models.bookings.booking import BookingStatus
from schemas.bookings.booking import (
    BookingCreate, BookingUpdate, BookingDiscount, BookingCancel, BookingList, BookingResponse, CancellationQuote,
)
from services.bookings.booking import BookingService

logger = get_structured_logger(__name__)

router = APIRouter(prefix="/bookings", tags=["Bookings"])

# Guests always work on their own bookings. A caller whose role holds the matching scope is staff and works on the
# bookings at the hotels they are assigned to (or at every hotel, with hotels:access_all). A booking that isn't yours,
# or isn't at one of your hotels, is reported as 404, so its existence isn't revealed.


@router.post(
    "/", status_code=status.HTTP_201_CREATED, summary="Book a room",
    responses=errors(400, 401, 403, 404, 409, 422, 500),
)
async def create_booking(
    payload: BookingCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Book a room for yourself. Each night is priced at its rate (seasonal and weekday rules) and the hotel's tax is
    added, in the hotel's currency; the booking starts pending. 400 if more guests than the room sleeps, 409 if the
    room is taken on any of the nights."""
    try:
        booking = await BookingService(db).create(current_user.id, payload)
        logger.info(f"{current_user.email} booked {booking.booking_code}")
        return Response.success(
            data=BookingResponse.model_validate(booking), message="Booking created", status_code=status.HTTP_201_CREATED
        )
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Creating a booking failed")
        raise APIException(status_code=500, message="Could not create the booking")


@router.post(
    "/for/{user_id}/", status_code=status.HTTP_201_CREATED, summary="Book a room for a guest",
    responses=errors(400, 401, 403, 404, 409, 422, 500),
)
async def create_booking_for_guest(
    user_id: UUID,
    payload: BookingCreate,
    access: Access = Depends(hotel_access(Scope.BOOKINGS_CREATE)),
    db: AsyncSession = Depends(get_db),
):
    """Book a room on behalf of a guest (needs bookings:create, for a room at a hotel you work on)."""
    try:
        booking = await BookingService(db).create(user_id, payload, access.hotel_ids)
        logger.info(f"{access.user.email} booked {booking.booking_code} for user {user_id}")
        return Response.success(
            data=BookingResponse.model_validate(booking), message="Booking created", status_code=status.HTTP_201_CREATED
        )
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Creating a booking for {user_id} failed")
        raise APIException(status_code=500, message="Could not create the booking")


@router.get("/", summary="List bookings", responses=errors(400, 401, 403, 422, 500))
async def list_bookings(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    user_id: Optional[UUID] = Query(None, description="Only has an effect for staff"),
    hotel_id: Optional[UUID] = Query(None),
    room_id: Optional[UUID] = Query(None),
    booking_status: Optional[BookingStatus] = Query(None, alias="status"),
    code: Optional[str] = Query(None, description="Booking code"),
    check_in_from: Optional[date] = Query(None),
    check_in_to: Optional[date] = Query(None),
    access: Access = Depends(guest_or_hotel_access(Scope.BOOKINGS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Your bookings, newest first. With bookings:view, the bookings at your hotels (narrow with user_id or hotel_id)."""
    try:
        filters = BookingList(
            user_id=user_id, hotel_id=hotel_id, room_id=room_id, status=booking_status, code=code,
            check_in_from=check_in_from, check_in_to=check_in_to,
        )
        result = await BookingService(db).list(filters, page, limit, access.user_id, access.hotel_ids)
        bookings = [BookingResponse.model_validate(b) for b in result["bookings"]]
        return Response.success(data=bookings, pagination=result["pagination"])
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Listing bookings failed")
        raise APIException(status_code=500, message="Could not load the bookings")


@router.get("/{booking_id}/", summary="Get a booking", responses=errors(401, 403, 404, 500))
async def get_booking(
    booking_id: UUID,
    access: Access = Depends(guest_or_hotel_access(Scope.BOOKINGS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Get one of your bookings (any at your hotels, with bookings:view)."""
    try:
        booking = await BookingService(db).get(booking_id, access.user_id, access.hotel_ids)
        return Response.success(data=BookingResponse.model_validate(booking))
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Fetching booking {booking_id} failed")
        raise APIException(status_code=500, message="Could not load the booking")


@router.patch("/{booking_id}/", summary="Reschedule a booking", responses=errors(400, 401, 403, 404, 409, 422, 500))
async def update_booking(
    booking_id: UUID,
    payload: BookingUpdate,
    access: Access = Depends(guest_or_hotel_access(Scope.BOOKINGS_EDIT)),
    db: AsyncSession = Depends(get_db),
):
    """Change the dates or guest count of one of your bookings (any at your hotels, with bookings:edit). Only pending
    or confirmed bookings can change. New dates are re-checked for availability and re-priced at today's rates."""
    try:
        booking = await BookingService(db).update(booking_id, payload, access.user_id, access.hotel_ids)
        return Response.success(data=BookingResponse.model_validate(booking), message="Booking updated")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Updating booking {booking_id} failed")
        raise APIException(status_code=500, message="Could not update the booking")


@router.get("/{booking_id}/cancellation/", summary="What cancelling would cost", responses=errors(401, 403, 404, 409, 500))
async def cancellation_quote(
    booking_id: UUID,
    access: Access = Depends(guest_or_hotel_access(Scope.BOOKINGS_CANCEL)),
    db: AsyncSession = Depends(get_db),
):
    """The fee and refund if you cancelled now, under the hotel's policy, without cancelling. Cancelling is free until
    the hotel's free-cancellation deadline; after it the hotel keeps a share of the total."""
    try:
        quote = await BookingService(db).cancellation_quote(booking_id, access.user_id, access.hotel_ids)
        return Response.success(data=CancellationQuote.model_validate(quote))
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Cancellation quote for booking {booking_id} failed")
        raise APIException(status_code=500, message="Could not work out the cancellation terms")


@router.post("/{booking_id}/cancel/", summary="Cancel a booking", responses=errors(401, 403, 404, 409, 422, 500))
async def cancel_booking(
    booking_id: UUID,
    background_tasks: BackgroundTasks,
    payload: Optional[BookingCancel] = None,
    access: Access = Depends(guest_or_hotel_access(Scope.BOOKINGS_CANCEL)),
    db: AsyncSession = Depends(get_db),
):
    """Cancel one of your pending or confirmed bookings (any at your hotels, with bookings:cancel). Frees the room.
    Inside the free-cancellation window you get everything back; after it the hotel keeps its late-cancellation share.
    Staff may send `{"waive_fee": true}`. The guest is emailed the fee and refund."""
    try:
        waive_fee = bool(payload and payload.waive_fee)
        booking = await BookingService(db).cancel(booking_id, access.user_id, access.hotel_ids, waive_fee, background_tasks)
        logger.info(f"{access.user.email} cancelled {booking.booking_code} (fee {booking.cancellation_fee}, refund {booking.refund_amount})")
        return Response.success(data=BookingResponse.model_validate(booking), message="Booking cancelled")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Cancelling booking {booking_id} failed")
        raise APIException(status_code=500, message="Could not cancel the booking")


@router.post("/{booking_id}/confirm/", summary="Confirm a booking", responses=errors(401, 403, 404, 409, 500))
async def confirm_booking(
    booking_id: UUID,
    background_tasks: BackgroundTasks,
    access: Access = Depends(hotel_access(Scope.BOOKINGS_CONFIRM)),
    db: AsyncSession = Depends(get_db),
):
    """Confirm a pending booking (needs bookings:confirm, for a hotel you work on). The guest is emailed. A booking
    is also confirmed automatically once it is fully paid."""
    try:
        booking = await BookingService(db).change_status(
            booking_id, BookingStatus.CONFIRMED, None, access.hotel_ids, background_tasks
        )
        logger.info(f"{access.user.email} confirmed {booking.booking_code}")
        return Response.success(data=BookingResponse.model_validate(booking), message="Booking confirmed")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Confirming booking {booking_id} failed")
        raise APIException(status_code=500, message="Could not confirm the booking")


@router.post("/{booking_id}/check-in/", summary="Check a guest in", responses=errors(401, 403, 404, 409, 500))
async def check_in_booking(
    booking_id: UUID,
    access: Access = Depends(hotel_access(Scope.BOOKINGS_CHECK_IN)),
    db: AsyncSession = Depends(get_db),
):
    """Check in a confirmed booking (needs bookings:check_in, for a hotel you work on). Stamps the check-in time if none was set."""
    try:
        booking = await BookingService(db).change_status(booking_id, BookingStatus.CHECKED_IN, None, access.hotel_ids)
        logger.info(f"{access.user.email} checked in {booking.booking_code}")
        return Response.success(data=BookingResponse.model_validate(booking), message="Guest checked in")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Checking in booking {booking_id} failed")
        raise APIException(status_code=500, message="Could not check the guest in")


@router.post("/{booking_id}/check-out/", summary="Check a guest out", responses=errors(401, 403, 404, 409, 500))
async def check_out_booking(
    booking_id: UUID,
    access: Access = Depends(hotel_access(Scope.BOOKINGS_CHECK_OUT)),
    db: AsyncSession = Depends(get_db),
):
    """Check out a checked-in booking (needs bookings:check_out, for a hotel you work on). The guest can then review the stay."""
    try:
        booking = await BookingService(db).change_status(booking_id, BookingStatus.CHECKED_OUT, None, access.hotel_ids)
        logger.info(f"{access.user.email} checked out {booking.booking_code}")
        return Response.success(data=BookingResponse.model_validate(booking), message="Guest checked out")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Checking out booking {booking_id} failed")
        raise APIException(status_code=500, message="Could not check the guest out")


@router.post("/{booking_id}/no-show/", summary="Mark a no-show", responses=errors(401, 403, 404, 409, 500))
async def no_show_booking(
    booking_id: UUID,
    access: Access = Depends(hotel_access(Scope.BOOKINGS_NO_SHOW)),
    db: AsyncSession = Depends(get_db),
):
    """Mark a confirmed booking as a no-show (needs bookings:no_show, for a hotel you work on). Frees the room."""
    try:
        booking = await BookingService(db).change_status(booking_id, BookingStatus.NO_SHOW, None, access.hotel_ids)
        logger.info(f"{access.user.email} marked {booking.booking_code} as a no-show")
        return Response.success(data=BookingResponse.model_validate(booking), message="Booking marked as no-show")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Marking booking {booking_id} as a no-show failed")
        raise APIException(status_code=500, message="Could not mark the booking as a no-show")


@router.put("/{booking_id}/discount/", summary="Set a discount", responses=errors(400, 401, 403, 404, 409, 422, 500))
async def set_booking_discount(
    booking_id: UUID,
    payload: BookingDiscount,
    access: Access = Depends(hotel_access(Scope.BOOKINGS_DISCOUNT)),
    db: AsyncSession = Depends(get_db),
):
    """Set a percentage discount (0-100) and recalculate tax and total (needs bookings:discount, for a hotel you work on). Open bookings only."""
    try:
        booking = await BookingService(db).set_discount(booking_id, payload.discount, access.hotel_ids)
        logger.info(f"{access.user.email} set a {payload.discount}% discount on {booking.booking_code}")
        return Response.success(data=BookingResponse.model_validate(booking), message="Discount applied")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Setting a discount on booking {booking_id} failed")
        raise APIException(status_code=500, message="Could not apply the discount")
