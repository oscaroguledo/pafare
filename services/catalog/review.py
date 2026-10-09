from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from typing import Optional
from models.catalog.review import Review
from models.catalog.product import Product
from models.accounts.user import User
from schemas.catalog.review import Create as ReviewCreate, Update as ReviewUpdate, Response as ReviewResponse
from core.exceptions import APIException
from core.utils.uuid_utils import uuid7
from uuid import UUID
from datetime import datetime, timezone
from sqlalchemy.orm import selectinload
from core.logging import get_structured_logger

logger = get_structured_logger(__name__)


class ReviewService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, review_data: ReviewCreate, user_id: UUID):
        """Create a new product review"""
        # Check if product exists
        product = await self.db.get(Product, review_data.product_id)
        if not product:
            raise APIException(status_code=404, message="Product not found")

        # Check if user has already reviewed this product
        existing_review = await self.db.execute(
            select(Review).where(
                (Review.product_id == review_data.product_id) & (Review.user_id == user_id))
        )
        if existing_review.scalars().first():
            raise APIException(
                status_code=400, message="You have already reviewed this product")

        # Create the review
        new_review = Review(
            id=uuid7(),
            user_id=user_id,
            product_id=review_data.product_id,
            rating=review_data.rating,
            comment=review_data.comment
        )
        self.db.add(new_review)
        await self.db.commit()
        await self.db.refresh(new_review)
        
        # Store the values before updating product rating
        review_id = str(new_review.id)
        rating = new_review.rating
        comment = new_review.comment
        created_at = new_review.created_at
        
        # Update product rating - don't fail if this has an issue
        try:
            await self._update_product_rating(review_data.product_id)
        except Exception as e:
            logger.warning(f"Could not update product rating for {review_data.product_id}: {type(e).__name__}: {str(e)}")

        # Return plain dict to avoid serialization issues
        return {
            "id": review_id,
            "rating": rating,
            "comment": comment,
            "created_at": created_at.isoformat() if created_at else None
        }

    def _parse_sort_by(self, sort_by: Optional[str]) -> tuple:
        """Parse sort_by parameter and return (field, order) tuple"""
        if not sort_by or '_' not in sort_by:
            return 'created_at', 'desc'
        
        # Split from the right to handle "created_at_desc" properly
        parts = sort_by.rsplit('_', 1)
        if len(parts) != 2:
            return 'created_at', 'desc'
        
        sort_field, sort_order = parts
        
        # Validate field exists on Review model
        if not hasattr(Review, sort_field):
            return 'created_at', 'desc'
        
        # Validate sort order
        if sort_order not in ['asc', 'desc']:
            return 'created_at', 'desc'
        
        return sort_field, sort_order

    async def list(self, product_id: Optional[UUID] = None, page: int = 1, limit: int = 10, min_rating: Optional[int] = None, max_rating: Optional[int] = None, sort_by: Optional[str] = None) -> dict:
        """Get all reviews with pagination and filtering. If product_id is provided, filter by product."""
        offset = (page - 1) * limit
        query = select(Review).options(
            selectinload(Review.user).load_only(
                User.id, User.firstname, User.lastname),
            selectinload(Review.product).load_only(
                Product.id, Product.name)
        )
        total_query = select(func.count()).select_from(Review)

        if product_id is not None:
            query = query.where(Review.product_id == product_id)
            total_query = total_query.where(Review.product_id == product_id)

        if min_rating is not None:
            query = query.filter(Review.rating >= min_rating)
            total_query = total_query.filter(Review.rating >= min_rating)
        if max_rating is not None:
            query = query.filter(Review.rating <= max_rating)
            total_query = total_query.filter(Review.rating <= max_rating)

        # Apply sorting
        sort_field, sort_order = self._parse_sort_by(sort_by)
        if sort_order == 'asc':
            query = query.order_by(getattr(Review, sort_field).asc())
        else:
            query = query.order_by(getattr(Review, sort_field).desc())

        total_reviews = (await self.db.execute(total_query)).scalar_one()
        reviews = (await self.db.execute(query.offset(offset).limit(limit))).scalars().all()

        return {
            "total": total_reviews,
            "page": page,
            "limit": limit,
            "data": [ReviewResponse.model_validate(r) for r in reviews]
        }

    async def get(self, review_id: UUID) -> Optional[Review]:
        """Get a review by ID, with its author and product."""
        result = await self.db.execute(
            select(Review).where(Review.id == review_id).options(
                selectinload(Review.user).load_only(User.id, User.firstname, User.lastname),
                selectinload(Review.product).load_only(Product.id, Product.name),
            )
        )
        return result.scalars().first()

    async def update(self, review_id: UUID, review_data: ReviewUpdate, user_id: UUID) -> Review:
        """Update a review"""
        review = await self.get(review_id)
        if not review:
            raise APIException(status_code=404, message="Review not found")

        if review.user_id != user_id:
            raise APIException(
                status_code=403, message="Not authorized to update this review")

        for key, value in review_data.model_dump(exclude_unset=True).items():
            setattr(review, key, value)
        review.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(review)

        # Update product rating
        await self._update_product_rating(review.product_id)

        return ReviewResponse.model_validate(review)

    async def delete(self, review_id: UUID, user_id: UUID, is_admin: bool = False):
        """Delete a review; only its author or staff may."""
        review = await self.get(review_id)
        if not review:
            raise APIException(status_code=404, message="Review not found")

        if review.user_id != user_id and not is_admin:
            raise APIException(
                status_code=403, message="Not authorized to delete this review")

        await self.db.delete(review)
        await self.db.commit()

        # Update product rating
        await self._update_product_rating(review.product_id)

    async def _update_product_rating(self, product_id: UUID):
        """Update product rating after review changes"""
        # Calculate new average rating and count
        try:
            result = await self.db.execute(
                select(func.avg(Review.rating), func.count(Review.id))
                .where(Review.product_id == product_id)
            )
            avg_rating, review_count = result.first()

            product = await self.db.get(Product, product_id)
            if product:
                product.rating_average = avg_rating if avg_rating is not None else 0.0
                product.rating_count = review_count if review_count is not None else 0
                product.review_count = review_count if review_count is not None else 0
                product.updated_at = datetime.now(timezone.utc)
                await self.db.commit()
                await self.db.refresh(product)
        except Exception as e:
            logger.error(f"Could not update product rating for {product_id}: {type(e).__name__}: {str(e)}")
            raise

