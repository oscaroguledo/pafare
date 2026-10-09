from datetime import date
from typing import List, Optional
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
from models.bookings.room_type import BedType
from schemas.bookings.booking import BookingQuote
from schemas.bookings.room import (
    RoomCreate, RoomUpdate, RoomList, RoomResponse, SearchQuery, SearchResult, SearchSort,
)
from services.bookings.room import RoomService
from services.bookings.room_type import RoomTypeService

logger = get_structured_logger(__name__)

router = APIRouter(prefix="/rooms", tags=["Rooms"])


@router.get("/", summary="List rooms", responses=errors(400, 422, 500))
async def list_rooms(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    hotel_id: Optional[UUID] = Query(None),
    room_type_id: Optional[UUID] = Query(None),
    search: Optional[str] = Query(None, description="Matches the room number"),
    guests: Optional[int] = Query(None, ge=1, description="Only rooms that sleep at least this many"),
    available_from: Optional[date] = Query(None, description="First night; give together with available_to"),
    available_to: Optional[date] = Query(None, description="Check-out day; give together with available_from"),
    db: AsyncSession = Depends(get_db),
):
    """List rooms. With available_from and available_to, only rooms with no active booking on those nights. Public."""
    try:
        filters = RoomList(
            hotel_id=hotel_id, room_type_id=room_type_id, search=search, guests=guests,
            available_from=available_from, available_to=available_to,
        )
        result = await RoomService(db).list(filters, page, limit)
        rooms = [RoomResponse.model_validate(r) for r in result["rooms"]]
        return Response.success(data=rooms, pagination=result["pagination"])
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Listing rooms failed")
        raise APIException(status_code=500, message="Could not load the rooms")

@router.get("/search/", summary="Find rooms to book", responses=errors(400, 422, 500))
async def search_rooms(
    check_in: date = Query(...),
    check_out: date = Query(...),
    guests: int = Query(1, ge=1, le=20),
    city: Optional[str] = Query(None),
    country: Optional[str] = Query(None),
    hotel_id: Optional[UUID] = Query(None),
    bed: Optional[BedType] = Query(None),
    amenities: Optional[List[str]] = Query(None, description="Repeat to require several: ?amenities=wifi&amenities=tv"),
    currency: Optional[Currency] = Query(None),
    min_price: Optional[int] = Query(None, ge=0, description="Per night on average, minor units, before tax"),
    max_price: Optional[int] = Query(None, ge=0, description="Per night on average, minor units, before tax"),
    sort: SearchSort = Query(SearchSort.PRICE_ASC),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Room types that are free for the whole stay, each with how many rooms are free, one room to book, the hotel's
    rating and the price of the stay night by night (seasonal and weekday rates) plus the hotel's tax. Public.
    Prices are in each hotel's own currency; use `currency` to compare like with like."""
    try:
        query = SearchQuery(
            check_in=check_in, check_out=check_out, guests=guests, city=city, country=country, hotel_id=hotel_id,
            bed=bed, amenities=amenities, currency=currency, min_price=min_price, max_price=max_price, sort=sort,
        )
        result = await RoomService(db).search(query, page, limit)
        results = [SearchResult.model_validate(r) for r in result["results"]]
        return Response.success(data=results, pagination=result["pagination"])
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Room search failed")
        raise APIException(status_code=500, message="Could not search for rooms")


@router.get("/{room_id}/", summary="Get a room", responses=errors(404, 500))
async def get_room(room_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get a room. Public."""
    try:
        room = await RoomService(db).get(room_id)
        return Response.success(data=RoomResponse.model_validate(room))
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Fetching room {room_id} failed")
        raise APIException(status_code=500, message="Could not load the room")


@router.post("/", status_code=status.HTTP_201_CREATED, summary="Create a room", responses=errors(400, 401, 403, 404, 409, 422, 500))
async def create_room(
    payload: RoomCreate,
    access: Access = Depends(hotel_access(Scope.ROOMS_CREATE)),
    db: AsyncSession = Depends(get_db),
):
    """Create a room in a hotel (needs rooms:create, for a hotel you work on). The number is unique within the hotel and
    the room type must belong to the same hotel."""
    try:
        room = await RoomService(db).create(payload, access.hotel_ids)
        logger.info(f"{access.user.email} created room {room.number} in {room.hotel_name}")
        return Response.success(data=RoomResponse.model_validate(room), message="Room created", status_code=status.HTTP_201_CREATED)
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Creating a room failed")
        raise APIException(status_code=500, message="Could not create the room")


@router.patch("/{room_id}/", summary="Update a room", responses=errors(400, 401, 403, 404, 409, 422, 500))
async def update_room(
    room_id: UUID,
    payload: RoomUpdate,
    access: Access = Depends(hotel_access(Scope.ROOMS_EDIT)),
    db: AsyncSession = Depends(get_db),
):
    """Change a room's type or number (needs rooms:edit, for a hotel you work on). A room never moves to another hotel."""
    try:
        room = await RoomService(db).update(room_id, payload, access.hotel_ids)
        logger.info(f"{access.user.email} updated room {room_id}")
        return Response.success(data=RoomResponse.model_validate(room), message="Room updated")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Updating room {room_id} failed")
        raise APIException(status_code=500, message="Could not update the room")


@router.delete("/{room_id}/", summary="Delete a room", responses=errors(401, 403, 404, 409, 500))
async def delete_room(
    room_id: UUID,
    access: Access = Depends(hotel_access(Scope.ROOMS_DELETE)),
    db: AsyncSession = Depends(get_db),
):
    """Delete a room (needs rooms:delete, for a hotel you work on). Refused if it has any booking, past ones included."""
    try:
        await RoomService(db).delete(room_id, access.hotel_ids)
        logger.info(f"{access.user.email} deleted room {room_id}")
        return Response.success(message="Room deleted")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Deleting room {room_id} failed")
        raise APIException(status_code=500, message="Could not delete the room")

@router.get("/{room_id}/quote/", summary="Price a stay", responses=errors(400, 404, 422, 500))
async def quote_stay(
    room_id: UUID,
    check_in: date = Query(...),
    check_out: date = Query(...),
    guests: int = Query(1, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    """What a stay in this room costs, night by night, with the hotel's tax. This is exactly what booking it will
    charge today. Public."""
    try:
        quote = await RoomTypeService(db).quote(room_id, check_in, check_out, guests)
        return Response.success(data=BookingQuote.model_validate(quote))
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Pricing a stay failed")
        raise APIException(status_code=500, message="Could not price the stay")
