from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from core.db import get_db
from core.dependencies import require_admin
from core.utils.response import Response
from core.exceptions import APIException
from models.accounts.user import User
from schemas.catalog.category import Create, Update, Response as CategoryResponse
from services.catalog.category import CategoryService

router = APIRouter(prefix="/categories", tags=["Categories"])


@router.get("/")
async def list(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    active_only: bool = Query(False),
    db: AsyncSession = Depends(get_db)
):
    """List categories (flat), with pagination."""
    try:
        category_service = CategoryService(db)
        categories, total = await category_service.list(page=page, limit=limit, active_only=active_only)

        pagination = {
            "page": page,
            "limit": limit,
            "total": total,
            "pages": (total + limit - 1) // limit if total > 0 else 0
        }
        return Response.success(
            data=[CategoryResponse.model_validate(c) for c in categories],
            pagination=pagination
        )
    except APIException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to fetch categories: {str(e)}"
        )


@router.get("/{category_id}/")
async def get(
    category_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """Get a single category by id."""
    category_service = CategoryService(db)
    category = await category_service.get(category_id)
    if not category:
        raise APIException(status_code=status.HTTP_404_NOT_FOUND, message="Category not found")
    return Response.success(data=CategoryResponse.model_validate(category))


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create(
    category_data: Create,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Create a new category (admin only)."""
    category_service = CategoryService(db)
    category = await category_service.create(category_data)
    return Response.success(
        data=CategoryResponse.model_validate(category),
        message="Category created successfully",
        status_code=status.HTTP_201_CREATED
    )


@router.patch("/{category_id}/")
async def update(
    category_id: UUID,
    category_data: Update,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Update a category (admin only)."""
    category_service = CategoryService(db)
    category = await category_service.update(category_id, category_data)
    if not category:
        raise APIException(status_code=status.HTTP_404_NOT_FOUND, message="Category not found")
    return Response.success(data=CategoryResponse.model_validate(category), message="Category updated successfully")


@router.delete("/{category_id}/")
async def delete(
    category_id: UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Delete a category (admin only). Fails while products still use it."""
    category_service = CategoryService(db)
    deleted = await category_service.delete(category_id)
    if not deleted:
        raise APIException(status_code=status.HTTP_404_NOT_FOUND, message="Category not found")
    return Response.success(message="Category deleted successfully")
