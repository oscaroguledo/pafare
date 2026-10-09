from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from typing import Optional, Set
from uuid import UUID
from core.utils.uuid_utils import uuid7
from models.bookings.booking import Booking, BookingStatus
from models.bookings.hotel import Hotel
from models.bookings.review import Review
from core.exceptions import APIException
from schemas.bookings.review import ReviewCreate, ReviewUpdate, ReviewList
from core.logging import get_structured_logger

logger = get_structured_logger(__name__)


class ReviewService:
    """Reviews. Two optional limits narrow what a method sees: `user_id` limits it to that guest's reviews (someone
    else's is reported as not found; leave it out for staff), and `hotel_ids` limits it to reviews of those hotels
    (None = all hotels; a review of another hotel is reported as not found)."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, user_id: UUID, data: ReviewCreate) -> Review:
        """Review a finished stay. The review starts unapproved.
        Raises 404 if the booking doesn't exist or isn't this guest's, 400 if the stay isn't checked out yet,
        409 if the booking already has a review."""
        row = (await self.db.execute(
            select(Booking, Hotel.name)
            .join(Hotel, Booking.hotel_id == Hotel.id)
            .where(Booking.id == data.booking_id, Booking.user_id == user_id)
        )).first()
        if not row:
            raise APIException(status_code=404, message="Booking not found")
        booking, hotel_name = row
        if booking.status != BookingStatus.CHECKED_OUT.value:
            raise APIException(status_code=400, message="Only a completed stay can be reviewed")
        if (await self.db.execute(select(Review.id).where(Review.booking_id == booking.id))).first():
            raise APIException(status_code=409, message="This booking already has a review")

        hotel_id = booking.hotel_id
        review = Review(id=uuid7(), booking_id=booking.id, rating=data.rating, comment=data.comment, is_approved=False)
        self.db.add(review)
        try:
            await self.db.commit()
        except IntegrityError:
            # Two requests for the same booking raced past the check above; the unique constraint caught it
            await self.db.rollback()
            raise APIException(status_code=409, message="This booking already has a review")
        await self.db.refresh(review)

        review.hotel_id, review.hotel_name, review.user_id = hotel_id, hotel_name, user_id
        return review

    async def get(self, review_id: UUID, user_id: Optional[UUID] = None, hotel_ids: Optional[Set[UUID]] = None) -> Review:
        """Get a review with its hotel and guest. Raises 404 if it doesn't exist (or isn't this guest's, or isn't for
        one of the staff member's hotels, when those limits are given)."""
        conditions = [Review.id == review_id]
        if user_id is not None:
            conditions.append(Booking.user_id == user_id)
        if hotel_ids is not None:
            conditions.append(Booking.hotel_id.in_(hotel_ids))
        row = (await self.db.execute(
            select(Review, Booking.hotel_id, Hotel.name, Booking.user_id)
            .join(Booking, Review.booking_id == Booking.id)
            .join(Hotel, Booking.hotel_id == Hotel.id)
            .where(*conditions)
        )).first()
        if not row:
            raise APIException(status_code=404, message="Review not found")
        review, hotel_id, hotel_name, guest_id = row
        review.hotel_id, review.hotel_name, review.user_id = hotel_id, hotel_name, guest_id

        return review

    async def list(self, filter: ReviewList, page: int = 1, limit: int = 10, hotel_ids: Optional[Set[UUID]] = None) -> dict:
        """Get a paginated, filtered list of reviews, newest first. When hotel_ids is given, only reviews of those hotels.
        Raises 400 if page is below 1 or limit is outside 1-100."""
        if page < 1:
            raise APIException(status_code=400, message="page must be 1 or greater")
        if not 1 <= limit <= 100:
            raise APIException(status_code=400, message="limit must be between 1 and 100")

        conditions = []
        if hotel_ids is not None:
            conditions.append(Booking.hotel_id.in_(hotel_ids))
        if filter.hotel_id:
            conditions.append(Booking.hotel_id == filter.hotel_id)
        if filter.user_id:
            conditions.append(Booking.user_id == filter.user_id)
        if filter.rating:
            conditions.append(Review.rating == filter.rating)
        if filter.is_approved is not None:
            conditions.append(Review.is_approved == filter.is_approved)

        total = (await self.db.execute(
            select(func.count()).select_from(Review).join(Booking, Review.booking_id == Booking.id).where(*conditions)
        )).scalar() or 0
        rows = (await self.db.execute(
            select(Review, Booking.hotel_id, Hotel.name, Booking.user_id)
            .join(Booking, Review.booking_id == Booking.id)
            .join(Hotel, Booking.hotel_id == Hotel.id)
            .where(*conditions)
            .order_by(Review.created_at.desc(), Review.id.desc())  # id breaks ties so pages never repeat or skip
            .offset((page - 1) * limit)
            .limit(limit)
        )).all()

        reviews = []
        for review, hotel_id, hotel_name, guest_id in rows:
            review.hotel_id, review.hotel_name, review.user_id = hotel_id, hotel_name, guest_id
            reviews.append(review)

        return {
            "reviews": reviews,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit,
            },
        }

    async def update(
        self, review_id: UUID, data: ReviewUpdate, user_id: Optional[UUID] = None, hotel_ids: Optional[Set[UUID]] = None,
    ) -> Review:
        """Edit a review's rating or comment (PATCH semantics). An edit sends it back for approval.
        Raises 404 if the review doesn't exist (or isn't this guest's), 400 if nothing was sent or rating is null."""
        review = await self.get(review_id, user_id, hotel_ids)

        changes = data.model_dump(exclude_unset=True)
        if not changes:
            raise APIException(status_code=400, message="No fields to update")
        if "rating" in changes and changes["rating"] is None:
            raise APIException(status_code=400, message="rating cannot be null")

        hotel_id, hotel_name, guest_id = review.hotel_id, review.hotel_name, review.user_id
        for field, value in changes.items():
            setattr(review, field, value)
        review.is_approved = False
        await self.db.commit()
        await self.db.refresh(review)

        review.hotel_id, review.hotel_name, review.user_id = hotel_id, hotel_name, guest_id
        return review

    async def moderate(self, review_id: UUID, is_approved: bool, hotel_ids: Optional[Set[UUID]] = None) -> Review:
        """Approve or hide a review (staff). Raises 404 if it doesn't exist (or isn't for one of the staff member's hotels)."""
        review = await self.get(review_id, None, hotel_ids)

        hotel_id, hotel_name, guest_id = review.hotel_id, review.hotel_name, review.user_id
        review.is_approved = is_approved
        await self.db.commit()
        await self.db.refresh(review)

        review.hotel_id, review.hotel_name, review.user_id = hotel_id, hotel_name, guest_id
        return review

    async def delete(self, review_id: UUID, user_id: Optional[UUID] = None, hotel_ids: Optional[Set[UUID]] = None) -> None:
        """Delete a review. Raises 404 if it doesn't exist (or isn't this guest's / for the staff member's hotels)."""
        review = await self.get(review_id, user_id, hotel_ids)

        await self.db.delete(review)
        await self.db.commit()

    async def hotel_rating(self, hotel_id: UUID) -> dict:
        """Average rating and number of a hotel's approved reviews; average is None when there are none.
        Raises 404 if the hotel doesn't exist."""
        if not (await self.db.execute(select(Hotel.id).where(Hotel.id == hotel_id))).first():
            raise APIException(status_code=404, message="Hotel not found")

        average, count = (await self.db.execute(
            select(func.avg(Review.rating), func.count(Review.id))
            .join(Booking, Review.booking_id == Booking.id)
            .where(Booking.hotel_id == hotel_id, Review.is_approved.is_(True))
        )).one()

        return {"hotel_id": hotel_id, "average": round(float(average), 2) if average is not None else None, "count": count}
