"""Standalone address endpoints at /v1/addresses."""
from fastapi import APIRouter, Depends, status, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from typing import Optional

from core.db import get_db
from core.utils.response import Response
from core.exceptions import APIException
from core.dependencies import require_auth
from models.accounts.user import User
from schemas.accounts.user import AddressCreate, AddressUpdate
from services.accounts.address import AddressService
from core.logging import get_structured_logger as get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/addresses", tags=["Addresses"])


@router.post("/")
async def create(
    payload: AddressCreate,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Create address for current user."""
    try:
        service = AddressService(db)
        address = await service.create(user_id=current_user.id, **payload.model_dump(exclude_none=True))
        return Response.success(data=address, message="Address created successfully")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating address: {e}")
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to create address: {str(e)}"
        )


@router.get("/{address_id}/")
async def get(
    address_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Get a specific address for current user."""
    try:
        service = AddressService(db)
        address = await service.get(address_id)
        if not address or address.user_id != current_user.id:
            raise APIException(status_code=status.HTTP_404_NOT_FOUND, message="Address not found")
        return Response.success(data=address, message="Address retrieved successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to get address: {str(e)}"
        )


@router.get("/")
async def list(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    search: Optional[str] = Query(None),
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """List all addresses for current user with pagination and search."""
    try:
        service = AddressService(db)
        result = await service.list(current_user.id, page=page, limit=limit, search=search)
        if isinstance(result, dict) and "data" in result and "pagination" in result:
            return Response.success(data=result.get("data", []), pagination=result.get("pagination"), message="Addresses retrieved successfully")
        return Response.success(data=result, message="Addresses retrieved successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to list addresses: {str(e)}"
        )


@router.patch("/{address_id}/")
async def patch(
    address_id: UUID,
    payload: AddressUpdate,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Partially update address for current user."""
    try:
        service = AddressService(db)
        address = await service.get(address_id)
        if not address or address.user_id != current_user.id:
            raise APIException(status_code=status.HTTP_404_NOT_FOUND, message="Address not found")
        updated = await service.update(address_id, current_user.id, **payload.model_dump(exclude_unset=True, exclude_none=True))
        return Response.success(data=updated, message="Address updated successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to update address: {str(e)}"
        )


@router.delete("/{address_id}/")
async def delete(
    address_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Delete address for current user."""
    try:
        service = AddressService(db)
        address = await service.get(address_id)
        if not address or address.user_id != current_user.id:
            raise APIException(status_code=status.HTTP_404_NOT_FOUND, message="Address not found")
        deleted = await service.delete(address_id, current_user.id)
        if not deleted:
            raise APIException(status_code=status.HTTP_404_NOT_FOUND, message="Address not found")
        return Response.success(message="Address deleted successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to delete address: {str(e)}"
        )
