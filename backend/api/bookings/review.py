from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from api.docs import errors
from core.db import get_db
from core.dependencies import Access, get_current_user, get_optional_user, hotel_access, guest_or_hotel_access
from core.exceptions import APIException
from core.logging import get_structured_logger
from core.scopes import Scope
from core.utils.response import Response
from models.accounts.user import User
from schemas.bookings.review import ReviewCreate, ReviewUpdate, ReviewModeration, ReviewList, ReviewResponse
from services.accounts.permission import PermissionService
from services.bookings.review import ReviewService
from services.bookings.hotel import HotelService

logger = get_structured_logger(__name__)

router = APIRouter(prefix="/reviews", tags=["Reviews"])

# Fixed paths (/all/, /mine/) are declared before /{review_id}/ so they aren't read as ids.


@router.post(
    "/", status_code=status.HTTP_201_CREATED, summary="Review a stay",
    responses=errors(400, 401, 403, 404, 409, 422, 500),
)
async def create_review(
    payload: ReviewCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Review one of your own completed stays, once per booking. It stays hidden until staff approve it."""
    try:
        review = await ReviewService(db).create(current_user.id, payload)
        return Response.success(
            data=ReviewResponse.model_validate(review), message="Review submitted for approval", status_code=status.HTTP_201_CREATED
        )
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Creating a review failed")
        raise APIException(status_code=500, message="Could not submit the review")


@router.get("/", summary="List approved reviews", responses=errors(400, 422, 500))
async def list_reviews(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    hotel_id: Optional[UUID] = Query(None),
    rating: Optional[int] = Query(None, ge=1, le=5),
    db: AsyncSession = Depends(get_db),
):
    """Approved reviews, newest first. Public."""
    try:
        result = await ReviewService(db).list(ReviewList(hotel_id=hotel_id, rating=rating, is_approved=True), page, limit)
        reviews = [ReviewResponse.model_validate(r) for r in result["reviews"]]
        return Response.success(data=reviews, pagination=result["pagination"])
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Listing reviews failed")
        raise APIException(status_code=500, message="Could not load the reviews")


@router.get("/all/", summary="List every review", responses=errors(400, 401, 403, 422, 500))
async def list_all_reviews(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    hotel_id: Optional[UUID] = Query(None),
    user_id: Optional[UUID] = Query(None),
    rating: Optional[int] = Query(None, ge=1, le=5),
    is_approved: Optional[bool] = Query(None),
    access: Access = Depends(hotel_access(Scope.REVIEWS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Every review of your hotels, approved or not (needs reviews:view). Use is_approved=false for the moderation queue."""
    try:
        filters = ReviewList(hotel_id=hotel_id, user_id=user_id, rating=rating, is_approved=is_approved)
        result = await ReviewService(db).list(filters, page, limit, access.hotel_ids)
        reviews = [ReviewResponse.model_validate(r) for r in result["reviews"]]
        return Response.success(data=reviews, pagination=result["pagination"])
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Listing all reviews failed")
        raise APIException(status_code=500, message="Could not load the reviews")


@router.get("/mine/", summary="List my reviews", responses=errors(400, 401, 403, 500))
async def list_my_reviews(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Your own reviews, approved or not."""
    try:
        result = await ReviewService(db).list(ReviewList(user_id=current_user.id), page, limit)
        reviews = [ReviewResponse.model_validate(r) for r in result["reviews"]]
        return Response.success(data=reviews, pagination=result["pagination"])
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Listing my reviews failed")
        raise APIException(status_code=500, message="Could not load your reviews")


@router.get("/{review_id}/", summary="Get a review", responses=errors(401, 403, 404, 500))
async def get_review(
    review_id: UUID,
    current_user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Anyone can read an approved review. An unapproved one is only visible to its author and to staff with
    reviews:view at that hotel; everyone else gets a 404, so its existence isn't revealed."""
    try:
        review = await ReviewService(db).get(review_id)
        if not review.is_approved:
            allowed = current_user is not None and review.user_id == current_user.id
            if current_user is not None and not allowed and await PermissionService(db).has_permission(current_user.role_id, Scope.REVIEWS_VIEW):
                hotel_ids = await HotelService(db).hotel_ids_for(current_user)
                allowed = hotel_ids is None or review.hotel_id in hotel_ids
            if not allowed:
                raise APIException(status_code=404, message="Review not found")
        return Response.success(data=ReviewResponse.model_validate(review))
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Fetching review {review_id} failed")
        raise APIException(status_code=500, message="Could not load the review")


@router.patch("/{review_id}/", summary="Edit a review", responses=errors(400, 401, 403, 404, 422, 500))
async def update_review(
    review_id: UUID,
    payload: ReviewUpdate,
    access: Access = Depends(guest_or_hotel_access(Scope.REVIEWS_EDIT)),
    db: AsyncSession = Depends(get_db),
):
    """Edit your own review (any review of your hotels, with reviews:edit). Any edit sends it back for approval."""
    try:
        review = await ReviewService(db).update(review_id, payload, access.user_id, access.hotel_ids)
        return Response.success(data=ReviewResponse.model_validate(review), message="Review updated; it needs approval again")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Updating review {review_id} failed")
        raise APIException(status_code=500, message="Could not update the review")


@router.put("/{review_id}/approval/", summary="Approve or hide a review", responses=errors(401, 403, 404, 422, 500))
async def moderate_review(
    review_id: UUID,
    payload: ReviewModeration,
    access: Access = Depends(hotel_access(Scope.REVIEWS_MODERATE)),
    db: AsyncSession = Depends(get_db),
):
    """Approve (is_approved=true) or hide (false) a review (needs reviews:moderate, for a hotel you work on)."""
    try:
        review = await ReviewService(db).moderate(review_id, payload.is_approved, access.hotel_ids)
        logger.info(f"{access.user.email} {'approved' if payload.is_approved else 'hid'} review {review_id}")
        message = "Review approved" if payload.is_approved else "Review hidden"
        return Response.success(data=ReviewResponse.model_validate(review), message=message)
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Moderating review {review_id} failed")
        raise APIException(status_code=500, message="Could not moderate the review")


@router.delete("/{review_id}/", summary="Delete a review", responses=errors(401, 403, 404, 500))
async def delete_review(
    review_id: UUID,
    access: Access = Depends(guest_or_hotel_access(Scope.REVIEWS_DELETE)),
    db: AsyncSession = Depends(get_db),
):
    """Delete your own review (any review of your hotels, with reviews:delete)."""
    try:
        await ReviewService(db).delete(review_id, access.user_id, access.hotel_ids)
        return Response.success(message="Review deleted")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Deleting review {review_id} failed")
        raise APIException(status_code=500, message="Could not delete the review")
