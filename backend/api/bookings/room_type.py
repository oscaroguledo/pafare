from typing import List, Optional
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
from models.bookings.room_type import BedType
from schemas.bookings.room_type import RoomTypeCreate, RoomTypeUpdate, RoomTypeList, RoomTypeResponse
from services.bookings.room_type import RoomTypeService

logger = get_structured_logger(__name__)

router = APIRouter(prefix="/room-types", tags=["Room types"])


@router.get("/", summary="List room types", responses=errors(400, 422, 500))
async def list_room_types(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    search: Optional[str] = Query(None),
    bed: Optional[BedType] = Query(None),
    min_price: Optional[int] = Query(None, ge=0, description="Minor units per night"),
    max_price: Optional[int] = Query(None, ge=0, description="Minor units per night"),
    amenities: Optional[List[str]] = Query(None, description="Repeat to require several: ?amenities=wifi&amenities=tv"),
    db: AsyncSession = Depends(get_db),
):
    """List room types, cheapest first. Public."""
    try:
        filters = RoomTypeList(search=search, bed=bed, min_price=min_price, max_price=max_price, amenities=amenities)
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


@router.post("/", status_code=status.HTTP_201_CREATED, summary="Create a room type", responses=errors(401, 403, 409, 422, 500))
async def create_room_type(
    payload: RoomTypeCreate,
    current_user: User = Depends(require_permission(Scope.ROOM_TYPES_CREATE)),
    db: AsyncSession = Depends(get_db),
):
    """Create a room type (needs room_types:create). The name is unique, ignoring case."""
    try:
        room_type = await RoomTypeService(db).create(payload)
        logger.info(f"{current_user.email} created room type {room_type.name}")
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
    current_user: User = Depends(require_permission(Scope.ROOM_TYPES_EDIT)),
    db: AsyncSession = Depends(get_db),
):
    """Update the fields sent (needs room_types:edit). A new price applies to future bookings only."""
    try:
        room_type = await RoomTypeService(db).update(room_type_id, payload)
        logger.info(f"{current_user.email} updated room type {room_type_id}")
        return Response.success(data=RoomTypeResponse.model_validate(room_type), message="Room type updated")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Updating room type {room_type_id} failed")
        raise APIException(status_code=500, message="Could not update the room type")


@router.delete("/{room_type_id}/", summary="Delete a room type", responses=errors(401, 403, 404, 409, 500))
async def delete_room_type(
    room_type_id: UUID,
    current_user: User = Depends(require_permission(Scope.ROOM_TYPES_DELETE)),
    db: AsyncSession = Depends(get_db),
):
    """Delete a room type (needs room_types:delete). Refused while rooms use it."""
    try:
        await RoomTypeService(db).delete(room_type_id)
        logger.info(f"{current_user.email} deleted room type {room_type_id}")
        return Response.success(message="Room type deleted")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Deleting room type {room_type_id} failed")
        raise APIException(status_code=500, message="Could not delete the room type")
