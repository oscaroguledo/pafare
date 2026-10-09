from datetime import date
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
from schemas.bookings.room import RoomCreate, RoomUpdate, RoomList, RoomResponse
from services.bookings.room import RoomService

logger = get_structured_logger(__name__)

router = APIRouter(prefix="/rooms", tags=["Rooms"])


@router.get("/", summary="List rooms", responses=errors(400, 422, 500))
async def list_rooms(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    hotel_id: Optional[UUID] = Query(None),
    room_type_id: Optional[UUID] = Query(None),
    search: Optional[str] = Query(None, description="Matches the room number"),
    available_from: Optional[date] = Query(None, description="First night; give together with available_to"),
    available_to: Optional[date] = Query(None, description="Check-out day; give together with available_from"),
    db: AsyncSession = Depends(get_db),
):
    """List rooms. With available_from and available_to, only rooms with no active booking on those nights. Public."""
    try:
        filters = RoomList(
            hotel_id=hotel_id, room_type_id=room_type_id, search=search,
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


@router.post("/", status_code=status.HTTP_201_CREATED, summary="Create a room", responses=errors(401, 403, 404, 409, 422, 500))
async def create_room(
    payload: RoomCreate,
    current_user: User = Depends(require_permission(Scope.ROOMS_CREATE)),
    db: AsyncSession = Depends(get_db),
):
    """Create a room in a hotel (needs rooms:create). The number is unique within the hotel."""
    try:
        room = await RoomService(db).create(payload)
        logger.info(f"{current_user.email} created room {room.number} in {room.hotel_name}")
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
    current_user: User = Depends(require_permission(Scope.ROOMS_EDIT)),
    db: AsyncSession = Depends(get_db),
):
    """Change a room's type or number (needs rooms:edit). A room never moves to another hotel."""
    try:
        room = await RoomService(db).update(room_id, payload)
        logger.info(f"{current_user.email} updated room {room_id}")
        return Response.success(data=RoomResponse.model_validate(room), message="Room updated")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Updating room {room_id} failed")
        raise APIException(status_code=500, message="Could not update the room")


@router.delete("/{room_id}/", summary="Delete a room", responses=errors(401, 403, 404, 409, 500))
async def delete_room(
    room_id: UUID,
    current_user: User = Depends(require_permission(Scope.ROOMS_DELETE)),
    db: AsyncSession = Depends(get_db),
):
    """Delete a room (needs rooms:delete). Refused if it has any booking, past ones included."""
    try:
        await RoomService(db).delete(room_id)
        logger.info(f"{current_user.email} deleted room {room_id}")
        return Response.success(message="Room deleted")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Deleting room {room_id} failed")
        raise APIException(status_code=500, message="Could not delete the room")
