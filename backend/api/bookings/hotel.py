from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from api.docs import errors
from core.db import get_db
from core.dependencies import require_permission
from core.exceptions import APIException
from core.logging import get_structured_logger
from core.scopes import Scope
from core.utils.response import Response
from models.accounts.user import User
from models.base import Currency
from schemas.bookings.hotel import HotelCreate, HotelUpdate, HotelList, HotelResponse
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
    current_user: User = Depends(require_permission(Scope.HOTELS_CREATE)),
    db: AsyncSession = Depends(get_db),
):
    """Create a hotel (needs hotels:create). Prices at the hotel are in `currency`, in minor units."""
    try:
        hotel = await HotelService(db).create(payload)
        logger.info(f"{current_user.email} created hotel {hotel.name}")
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
    current_user: User = Depends(require_permission(Scope.HOTELS_EDIT)),
    db: AsyncSession = Depends(get_db),
):
    """Update the fields sent (needs hotels:edit). The currency can't change once the hotel has bookings."""
    try:
        hotel = await HotelService(db).update(hotel_id, payload)
        logger.info(f"{current_user.email} updated hotel {hotel_id}")
        return Response.success(data=HotelResponse.model_validate(hotel), message="Hotel updated")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Updating hotel {hotel_id} failed")
        raise APIException(status_code=500, message="Could not update the hotel")


@router.delete("/{hotel_id}/", summary="Delete a hotel", responses=errors(401, 403, 404, 409, 500))
async def delete_hotel(
    hotel_id: UUID,
    current_user: User = Depends(require_permission(Scope.HOTELS_DELETE)),
    db: AsyncSession = Depends(get_db),
):
    """Delete a hotel (needs hotels:delete). Refused while it has rooms or bookings."""
    try:
        await HotelService(db).delete(hotel_id)
        logger.info(f"{current_user.email} deleted hotel {hotel_id}")
        return Response.success(message="Hotel deleted")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Deleting hotel {hotel_id} failed")
        raise APIException(status_code=500, message="Could not delete the hotel")
