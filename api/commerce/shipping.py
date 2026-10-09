"""Shipping routes for managing shipping methods and calculating shipping costs."""

from fastapi import APIRouter, Depends, Query, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from uuid import UUID
from core.logging import get_structured_logger as get_logger

from core.db import get_db
from core.dependencies import get_current_auth_user, require_admin
from core.utils.response import Response
from core.exceptions import APIException
from models.accounts.user import User
from services.commerce.shipping import ShippingService
from schemas.commerce.shipping import MethodCreate, MethodUpdate, MethodInDB
from schemas.commerce.shipping import (
    MethodCreate,
    MethodUpdate,
    MethodInDB,
    Calculate
)

logger = get_logger(__name__)
router = APIRouter(prefix="/shipping", tags=["shipping"])


@router.get("/methods/")
async def list(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    is_active: Optional[bool] = Query(None),
    all_methods: bool = Query(False, description="Return all methods (admin only)"),
    current_user: Optional[User] = Depends(get_current_auth_user),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """List shipping methods. Users see active methods, admins can see all."""
    try:
        shipping_service = ShippingService(db)
        if all_methods:
            # Admin only - require authentication
            if current_user is None:
                raise APIException(status_code=401, message="Authentication required")
            _ = require_admin(current_user)
            methods_data = await shipping_service.get_all_methods(
                page=page, limit=limit, is_active=is_active
            )
            # Convert items to schema
            items = [MethodInDB.model_validate(item) for item in methods_data.get("items", [])]
            return Response.success(
                data=items,
                pagination={
                    "page": methods_data.get("page"),
                    "limit": methods_data.get("limit"),
                    "total": methods_data.get("total"),
                    "pages": methods_data.get("pages")
                }
            )
        # Regular users get active methods only
        methods = await shipping_service.list(active_only=True)
        methods_data = [MethodInDB.model_validate(method) for method in methods]
        return Response.success(data=methods_data)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting shipping methods: {e}")
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to get shipping methods: {str(e)}"
        )


@router.get("/methods/{method_id}/")
async def get(
    method_id: UUID,
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Get a specific shipping method."""
    try:
        shipping_service = ShippingService(db)
        method = await shipping_service.get(method_id)
        if not method:
            raise APIException(
                status_code=status.HTTP_404_NOT_FOUND,
                message="Shipping method not found"
            )
        method_data = MethodInDB.model_validate(method)
        return Response.success(
            data=method_data,
            message="Shipping method retrieved successfully"
        )
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting shipping method {method_id}: {e}")
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to get shipping method: {str(e)}"
        )


@router.post("/methods/")
async def create(
    method_data: MethodCreate,
    current_user = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Create a shipping method (Admin only)."""
    try:
        shipping_service = ShippingService(db)
        method = await shipping_service.create(method_data)
        method_data = MethodInDB.model_validate(method)
        return Response.success(
            data=method_data,
            message="Shipping method created successfully",
            status_code=status.HTTP_201_CREATED
        )
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating shipping method: {e}")
        raise APIException(
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"Failed to create shipping method: {str(e)}"
        )


@router.patch("/methods/{method_id}/")
async def patch(
    method_id: UUID,
    method_data: MethodUpdate,
    current_user = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Update a shipping method (Admin only)."""
    try:
        shipping_service = ShippingService(db)
        method = await shipping_service.update(method_id, method_data)
        if not method:
            raise APIException(
                status_code=status.HTTP_404_NOT_FOUND,
                message="Shipping method not found"
            )
        method_data = MethodInDB.model_validate(method)
        return Response.success(
            data=method_data,
            message="Shipping method updated successfully"
        )
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating shipping method {method_id}: {e}")
        raise APIException(
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"Failed to update shipping method: {str(e)}"
        )


@router.delete("/methods/{method_id}/")
async def delete(
    method_id: UUID,
    current_user = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Delete a shipping method (Admin only)."""
    try:
        shipping_service = ShippingService(db)
        await shipping_service.delete(method_id)
        return Response.success(message="Shipping method deleted successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting shipping method {method_id}: {e}")
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to delete shipping method: {str(e)}"
        )


# --- CALCULATE - Kept Route ---


@router.post("/calculate/")
async def calc_cost(
    body: Calculate,
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Delivery estimate: the chosen method's price, or the cheapest active method."""
    try:
        cost = await ShippingService(db).calc_cost(shipping_method_id=body.shipping_method_id)
        return Response.success(data={"shipping_cost": cost})
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error calculating shipping cost: {e}")
        raise APIException(
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"Failed to calculate shipping cost: {str(e)}"
        )