from fastapi import APIRouter, Depends, Query, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from uuid import UUID
from core.db import get_db
from core.dependencies import require_auth
from core.utils.response import Response
from core.exceptions import APIException
from core.logging import get_structured_logger
from schemas.catalog.review import Create, Update, Response as ReviewResponse
from services.catalog.review import ReviewService
from models.accounts.user import User

logger = get_structured_logger(__name__)

router = APIRouter(prefix="/reviews", tags=["Reviews"])


@router.get("/")
async def list(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    product_id: Optional[UUID] = Query(None, description="Filter by product ID"),
    min_rating: Optional[int] = Query(None, ge=1, le=5),
    max_rating: Optional[int] = Query(None, ge=1, le=5),
    sort_by: Optional[str] = Query("created_at_desc", pattern="^(created_at|rating)_(asc|desc)$"),
    db: AsyncSession = Depends(get_db)
):
    """Get all reviews with optional filtering and sorting."""
    try:
        review_service = ReviewService(db)
        
        result = await review_service.list(
            product_id=product_id, page=page, limit=limit, min_rating=min_rating, max_rating=max_rating, sort_by=sort_by
        )
        
        if isinstance(result, dict) and "data" in result:
            pagination = {
                "page": result.get("page", page),
                "limit": result.get("limit", limit),
                "total": result.get("total", 0),
                "pages": (result.get("total", 0) + limit - 1) // limit
            }
            return Response.success(data=result.get("data", []), pagination=pagination)
        return Response.success(data=result)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to fetch reviews: {str(e)}"
        )


@router.post("/")
async def create(
    review_data: Create,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Create a new review for a product."""
    try:
        review_service = ReviewService(db)
        review = await review_service.create(review_data, current_user.id)
        return Response.success(data=review, message="Review created successfully")
    except APIException:
        raise
    except Exception as e:
        logger.exception(f"Failed to create review for product {review_data.product_id}")
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to create review: {str(e)}"
        )


@router.get("/{review_id}/")
async def get(
    review_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """Get a specific review by ID."""
    try:
        review_service = ReviewService(db)
        review = await review_service.get(review_id)
        if not review:
            raise APIException(
                status_code=status.HTTP_404_NOT_FOUND,
                message="Review not found"
            )
        return Response.success(data=ReviewResponse.model_validate(review), message="Review retrieved successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to fetch review: {str(e)}"
        )


@router.patch("/{review_id}/")
async def update(
    review_id: UUID,
    review_data: Update,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Update a review (owner only)."""
    try:
        review_service = ReviewService(db)
        review = await review_service.update(review_id, review_data, current_user.id)
        return Response.success(data=review, message="Review updated successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to update review: {str(e)}"
        )


@router.delete("/{review_id}/")
async def delete(
    review_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Delete a review: its author, or staff removing it from the shop."""
    try:
        review_service = ReviewService(db)
        await review_service.delete(review_id, current_user.id, is_admin=current_user.is_staff)
        return Response.success(message="Review deleted successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to delete review: {str(e)}"
        )


@router.get("/product/{product_id}/")
async def for_product(
    product_id: UUID,
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    min_rating: Optional[int] = Query(None, ge=1, le=5),
    max_rating: Optional[int] = Query(None, ge=1, le=5),
    sort_by: Optional[str] = Query(
        None, pattern="^(created_at|rating)_(asc|desc)$"),
    db: AsyncSession = Depends(get_db)
):
    """Get all reviews for a specific product with optional filtering and sorting."""
    try:
        review_service = ReviewService(db)
        result = await review_service.list(
            product_id=product_id, page=page, limit=limit, min_rating=min_rating, max_rating=max_rating, sort_by=sort_by
        )
        if isinstance(result, dict) and "data" in result:
            pagination = {
                "page": result.get("page", page),
                "limit": result.get("limit", limit),
                "total": result.get("total", 0),
                "pages": (result.get("total", 0) + limit - 1) // limit
            }
            return Response.success(data=result.get("data", []), pagination=pagination)
        return Response.success(data=result)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to fetch reviews for product: {str(e)}"
        )


