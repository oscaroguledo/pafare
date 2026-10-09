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
from models.bookings.room_type import BedType
from schemas.bookings.room_type import (
    RoomRateCreate, RoomRateUpdate, RoomRateResponse, RoomTypeCreate, RoomTypeUpdate, RoomTypeList, RoomTypeResponse,
)
from services.bookings.room_type import RoomTypeService

logger = get_structured_logger(__name__)

router = APIRouter(prefix="/room-types", tags=["Room types"])


@router.get("/", summary="List room types", responses=errors(400, 422, 500))
async def list_room_types(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    hotel_id: Optional[UUID] = Query(None),
    search: Optional[str] = Query(None),
    bed: Optional[BedType] = Query(None),
    min_guests: Optional[int] = Query(None, ge=1, description="Room types that sleep at least this many"),
    min_price: Optional[int] = Query(None, ge=0, description="Minor units per night"),
    max_price: Optional[int] = Query(None, ge=0, description="Minor units per night"),
    amenities: Optional[List[str]] = Query(None, description="Repeat to require several: ?amenities=wifi&amenities=tv"),
    db: AsyncSession = Depends(get_db),
):
    """List room types, cheapest first. Public."""
    try:
        filters = RoomTypeList(
            hotel_id=hotel_id, search=search, bed=bed, min_guests=min_guests,
            min_price=min_price, max_price=max_price, amenities=amenities,
        )
        result = await RoomTypeService(db).list(filters, page, limit)
        room_types = [RoomTypeResponse.model_validate(r) for r in result["room_types"]]
        return Response.success(data=room_types, pagination=result["pagination"])
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Listing room types failed")
        raise APIException(status_code=500, message="Could not load the room types")


@router.get("/{room_type_id}/", summary="Get a room type", responses=errors(404, 500))
async def get_room_type(room_type_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get a room type. Public."""
    try:
        room_type = await RoomTypeService(db).get(room_type_id)
        return Response.success(data=RoomTypeResponse.model_validate(room_type))
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Fetching room type {room_type_id} failed")
        raise APIException(status_code=500, message="Could not load the room type")


@router.post("/", status_code=status.HTTP_201_CREATED, summary="Create a room type", responses=errors(401, 403, 404, 409, 422, 500))
async def create_room_type(
    payload: RoomTypeCreate,
    access: Access = Depends(hotel_access(Scope.ROOM_TYPES_CREATE)),
    db: AsyncSession = Depends(get_db),
):
    """Create a room type at a hotel (needs room_types:create, for a hotel you work on). The name is unique within the
    hotel, ignoring case."""
    try:
        room_type = await RoomTypeService(db).create(payload, access.hotel_ids)
        logger.info(f"{access.user.email} created room type {room_type.name}")
        return Response.success(
            data=RoomTypeResponse.model_validate(room_type), message="Room type created", status_code=status.HTTP_201_CREATED
        )
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Creating a room type failed")
        raise APIException(status_code=500, message="Could not create the room type")


@router.patch("/{room_type_id}/", summary="Update a room type", responses=errors(400, 401, 403, 404, 409, 422, 500))
async def update_room_type(
    room_type_id: UUID,
    payload: RoomTypeUpdate,
    access: Access = Depends(hotel_access(Scope.ROOM_TYPES_EDIT)),
    db: AsyncSession = Depends(get_db),
):
    """Update the fields sent (needs room_types:edit, for a hotel you work on). A new price applies to future bookings only."""
    try:
        room_type = await RoomTypeService(db).update(room_type_id, payload, access.hotel_ids)
        logger.info(f"{access.user.email} updated room type {room_type_id}")
        return Response.success(data=RoomTypeResponse.model_validate(room_type), message="Room type updated")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Updating room type {room_type_id} failed")
        raise APIException(status_code=500, message="Could not update the room type")


@router.delete("/{room_type_id}/", summary="Delete a room type", responses=errors(401, 403, 404, 409, 500))
async def delete_room_type(
    room_type_id: UUID,
    access: Access = Depends(hotel_access(Scope.ROOM_TYPES_DELETE)),
    db: AsyncSession = Depends(get_db),
):
    """Delete a room type and its price rules (needs room_types:delete, for a hotel you work on). Refused while rooms use it."""
    try:
        await RoomTypeService(db).delete(room_type_id, access.hotel_ids)
        logger.info(f"{access.user.email} deleted room type {room_type_id}")
        return Response.success(message="Room type deleted")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Deleting room type {room_type_id} failed")
        raise APIException(status_code=500, message="Could not delete the room type")


# ---- price rules (seasonal and weekday rates) ----

@router.get("/{room_type_id}/rates/", summary="A room type's price rules", responses=errors(404, 500))
async def list_room_rates(room_type_id: UUID, db: AsyncSession = Depends(get_db)):
    """Seasonal and weekday price rules for a room type, highest priority first. Public."""
    try:
        rates = await RoomTypeService(db).list_rates(room_type_id)
        return Response.success(data=[RoomRateResponse.model_validate(r) for r in rates])
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Listing rates of room type {room_type_id} failed")
        raise APIException(status_code=500, message="Could not load the price rules")


@router.post(
    "/{room_type_id}/rates/", status_code=status.HTTP_201_CREATED, summary="Add a price rule",
    responses=errors(401, 403, 404, 422, 500),
)
async def create_room_rate(
    room_type_id: UUID,
    payload: RoomRateCreate,
    access: Access = Depends(hotel_access(Scope.ROOM_RATES_CREATE)),
    db: AsyncSession = Depends(get_db),
):
    """Add a price rule to a room type (needs room_rates:create, for a hotel you work on). Give dates, weekdays or
    both; a night uses the matching rule with the highest priority, or the standard price if none matches. Rules apply
    to bookings made from now on."""
    try:
        rate = await RoomTypeService(db).create_rate(room_type_id, payload, access.hotel_ids)
        logger.info(f"{access.user.email} added price rule {rate.name} to room type {room_type_id}")
        return Response.success(data=RoomRateResponse.model_validate(rate), message="Price rule added", status_code=status.HTTP_201_CREATED)
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Adding a price rule to room type {room_type_id} failed")
        raise APIException(status_code=500, message="Could not add the price rule")


@router.patch("/{room_type_id}/rates/{rate_id}/", summary="Update a price rule", responses=errors(400, 401, 403, 404, 422, 500))
async def update_room_rate(
    room_type_id: UUID,
    rate_id: UUID,
    payload: RoomRateUpdate,
    access: Access = Depends(hotel_access(Scope.ROOM_RATES_EDIT)),
    db: AsyncSession = Depends(get_db),
):
    """Change a price rule (needs room_rates:edit, for a hotel you work on). Send a date or weekdays field as null to
    clear it. Existing bookings keep the price they were made at."""
    try:
        rate = await RoomTypeService(db).update_rate(room_type_id, rate_id, payload, access.hotel_ids)
        logger.info(f"{access.user.email} updated price rule {rate_id}")
        return Response.success(data=RoomRateResponse.model_validate(rate), message="Price rule updated")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Updating price rule {rate_id} failed")
        raise APIException(status_code=500, message="Could not update the price rule")


@router.delete("/{room_type_id}/rates/{rate_id}/", summary="Delete a price rule", responses=errors(401, 403, 404, 500))
async def delete_room_rate(
    room_type_id: UUID,
    rate_id: UUID,
    access: Access = Depends(hotel_access(Scope.ROOM_RATES_DELETE)),
    db: AsyncSession = Depends(get_db),
):
    """Delete a price rule (needs room_rates:delete, for a hotel you work on). Existing bookings keep their price."""
    try:
        await RoomTypeService(db).delete_rate(room_type_id, rate_id, access.hotel_ids)
        logger.info(f"{access.user.email} deleted price rule {rate_id}")
        return Response.success(message="Price rule deleted")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Deleting price rule {rate_id} failed")
        raise APIException(status_code=500, message="Could not delete the price rule")
