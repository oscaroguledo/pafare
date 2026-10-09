from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from api.docs import errors
from core.db import get_db
from core.dependencies import Access, hotel_access
from core.exceptions import APIException
from core.logging import get_structured_logger
from core.scopes import Scope
from core.utils.response import Response
from models.base import Currency
from schemas.bookings.hotel import (
    HotelCreate, HotelUpdate, HotelList, HotelResponse, HotelStaffAssign, HotelStaffResponse,
)
from schemas.bookings.review import HotelRating
from services.bookings.hotel import HotelService
from services.bookings.review import ReviewService

logger = get_structured_logger(__name__)

router = APIRouter(prefix="/hotels", tags=["Hotels"])


@router.get("/", summary="List hotels", responses=errors(400, 422, 500))
async def list_hotels(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    search: Optional[str] = Query(None, description="Matches name, city or country"),
    city: Optional[str] = Query(None),
    country: Optional[str] = Query(None),
    currency: Optional[Currency] = Query(None, description="ISO 4217 code"),
    db: AsyncSession = Depends(get_db),
):
    """List hotels by name. Public."""
    try:
        filters = HotelList(search=search, city=city, country=country, currency=currency)
        result = await HotelService(db).list(filters, page, limit)
        hotels = [HotelResponse.model_validate(h) for h in result["hotels"]]
        return Response.success(data=hotels, pagination=result["pagination"])
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Listing hotels failed")
        raise APIException(status_code=500, message="Could not load the hotels")


@router.get("/{hotel_id}/", summary="Get a hotel", responses=errors(404, 500))
async def get_hotel(hotel_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get a hotel. Public."""
    try:
        hotel = await HotelService(db).get(hotel_id)
        return Response.success(data=HotelResponse.model_validate(hotel))
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Fetching hotel {hotel_id} failed")
        raise APIException(status_code=500, message="Could not load the hotel")


@router.get("/{hotel_id}/rating/", summary="A hotel's rating", responses=errors(404, 500))
async def get_hotel_rating(hotel_id: UUID, db: AsyncSession = Depends(get_db)):
    """Average rating and number of approved reviews (average is null with no reviews). Public."""
    try:
        rating = await ReviewService(db).hotel_rating(hotel_id)
        return Response.success(data=HotelRating(**rating))
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Rating of hotel {hotel_id} failed")
        raise APIException(status_code=500, message="Could not load the rating")


@router.post("/", status_code=status.HTTP_201_CREATED, summary="Create a hotel", responses=errors(401, 403, 422, 500))
async def create_hotel(
    payload: HotelCreate,
    access: Access = Depends(hotel_access(Scope.HOTELS_CREATE)),
    db: AsyncSession = Depends(get_db),
):
    """Create a hotel (needs hotels:create). Prices at the hotel are in `currency`, in minor units. Staff limited to
    their own hotels are assigned to the one they create, so they can manage it."""
    try:
        creator_id = access.user.id if access.hotel_ids is not None else None
        hotel = await HotelService(db).create(payload, creator_id)
        logger.info(f"{access.user.email} created hotel {hotel.name}")
        return Response.success(data=HotelResponse.model_validate(hotel), message="Hotel created", status_code=status.HTTP_201_CREATED)
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Creating a hotel failed")
        raise APIException(status_code=500, message="Could not create the hotel")


@router.patch("/{hotel_id}/", summary="Update a hotel", responses=errors(400, 401, 403, 404, 409, 422, 500))
async def update_hotel(
    hotel_id: UUID,
    payload: HotelUpdate,
    access: Access = Depends(hotel_access(Scope.HOTELS_EDIT)),
    db: AsyncSession = Depends(get_db),
):
    """Update the fields sent (needs hotels:edit, for a hotel you work on). The currency can't change once the hotel
    has bookings. A new tax rate or cancellation policy applies to future bookings only."""
    try:
        hotel = await HotelService(db).update(hotel_id, payload, access.hotel_ids)
        logger.info(f"{access.user.email} updated hotel {hotel_id}")
        return Response.success(data=HotelResponse.model_validate(hotel), message="Hotel updated")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Updating hotel {hotel_id} failed")
        raise APIException(status_code=500, message="Could not update the hotel")


@router.delete("/{hotel_id}/", summary="Delete a hotel", responses=errors(401, 403, 404, 409, 500))
async def delete_hotel(
    hotel_id: UUID,
    access: Access = Depends(hotel_access(Scope.HOTELS_DELETE)),
    db: AsyncSession = Depends(get_db),
):
    """Delete a hotel (needs hotels:delete, for a hotel you work on). Refused while it has rooms, room types or bookings."""
    try:
        await HotelService(db).delete(hotel_id, access.hotel_ids)
        logger.info(f"{access.user.email} deleted hotel {hotel_id}")
        return Response.success(message="Hotel deleted")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Deleting hotel {hotel_id} failed")
        raise APIException(status_code=500, message="Could not delete the hotel")


@router.get("/{hotel_id}/staff/", summary="A hotel's staff", responses=errors(401, 403, 404, 500))
async def list_hotel_staff(
    hotel_id: UUID,
    access: Access = Depends(hotel_access(Scope.HOTELS_ASSIGN_STAFF)),
    db: AsyncSession = Depends(get_db),
):
    """The staff assigned to a hotel (needs hotels:assign_staff, for a hotel you work on)."""
    try:
        staff = await HotelService(db).list_staff(hotel_id, access.hotel_ids)
        return Response.success(data=[HotelStaffResponse(**member) for member in staff])
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Listing staff of hotel {hotel_id} failed")
        raise APIException(status_code=500, message="Could not load the hotel's staff")


@router.post("/{hotel_id}/staff/", status_code=status.HTTP_201_CREATED, summary="Assign staff to a hotel", responses=errors(401, 403, 404, 422, 500))
async def assign_hotel_staff(
    hotel_id: UUID,
    payload: HotelStaffAssign,
    access: Access = Depends(hotel_access(Scope.HOTELS_ASSIGN_STAFF)),
    db: AsyncSession = Depends(get_db),
):
    """Let a staff member work on this hotel (needs hotels:assign_staff, for a hotel you work on). What they may do
    there is still decided by their role's permissions. Assigning someone already assigned is harmless."""
    try:
        member = await HotelService(db).assign_staff(hotel_id, payload.user_id, access.hotel_ids)
        logger.info(f"{access.user.email} assigned user {payload.user_id} to hotel {hotel_id}")
        return Response.success(data=HotelStaffResponse(**member), message="Staff member assigned", status_code=status.HTTP_201_CREATED)
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Assigning staff to hotel {hotel_id} failed")
        raise APIException(status_code=500, message="Could not assign the staff member")


@router.delete("/{hotel_id}/staff/{user_id}/", summary="Remove staff from a hotel", responses=errors(401, 403, 404, 500))
async def unassign_hotel_staff(
    hotel_id: UUID,
    user_id: UUID,
    access: Access = Depends(hotel_access(Scope.HOTELS_ASSIGN_STAFF)),
    db: AsyncSession = Depends(get_db),
):
    """Stop a staff member working on this hotel (needs hotels:assign_staff, for a hotel you work on)."""
    try:
        await HotelService(db).unassign_staff(hotel_id, user_id, access.hotel_ids)
        logger.info(f"{access.user.email} removed user {user_id} from hotel {hotel_id}")
        return Response.success(message="Staff member removed")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Removing staff from hotel {hotel_id} failed")
        raise APIException(status_code=500, message="Could not remove the staff member")
