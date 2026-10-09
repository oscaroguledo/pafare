from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import APIException
from core.logging import get_structured_logger
from core.utils.uuid_utils import uuid7
from models.billings.payment import Payment, PaymentStatus
from models.bookings.booking import Booking, BookingStatus
from schemas.billings.payment import PaymentCreate, PaymentList

logger = get_structured_logger(__name__)

# Bookings in these states can't be paid for any more.
UNPAYABLE_STATUSES = (BookingStatus.CANCELLED.value, BookingStatus.NO_SHOW.value)

# Which status can follow which. Anything not listed is refused.
TRANSITIONS = {
    PaymentStatus.PENDING.value: {PaymentStatus.SUCCEEDED.value, PaymentStatus.FAILED.value},
    PaymentStatus.SUCCEEDED.value: {PaymentStatus.REFUNDED.value},
}


class PaymentService:
    """Payments towards bookings. Methods that take `user_id` only see that guest's payments (someone else's is
    reported as not found); leave it out for staff. There is no payment gateway here: `confirm`, `fail` and
    `refund` record what the provider (or staff) reports."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, user_id: UUID, data: PaymentCreate) -> Payment:
        """Start a payment towards one of the guest's own bookings, in the booking's currency. With no amount it
        covers everything still owed; the payment starts pending.
        Raises 404 if the booking doesn't exist or isn't this guest's, 409 if the booking is cancelled or already
        fully paid, 400 if the amount is more than what is still owed."""
        # Lock the booking row so two payments started at once can't both claim the same remaining balance
        booking = (await self.db.execute(
            select(Booking).where(Booking.id == data.booking_id, Booking.user_id == user_id).with_for_update()
        )).scalar_one_or_none()
        if not booking:
            raise APIException(status_code=404, message="Booking not found")
        if booking.status in UNPAYABLE_STATUSES:
            raise APIException(status_code=409, message=f"A {booking.status} booking can't be paid for")

        committed = (await self.db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.booking_id == booking.id,
                Payment.status.in_((PaymentStatus.SUCCEEDED.value, PaymentStatus.PENDING.value)),
            )
        )).scalar()
        remaining = booking.total_price - committed
        if remaining <= 0:
            raise APIException(status_code=409, message="This booking is already fully paid")
        amount = data.amount if data.amount is not None else remaining
        if amount > remaining:
            raise APIException(status_code=400, message=f"Amount is more than what is still owed ({remaining})")

        booking_code, booking_status, currency = booking.booking_code, booking.status, booking.currency
        payment = Payment(
            id=uuid7(), user_id=user_id, booking_id=booking.id, amount=amount,
            currency=currency, status=PaymentStatus.PENDING.value, card_id=data.card_id,
        )
        self.db.add(payment)
        await self.db.commit()
        await self.db.refresh(payment)

        payment.booking_code, payment.booking_status = booking_code, booking_status
        return payment

    async def get(self, payment_id: UUID, user_id: Optional[UUID] = None) -> Payment:
        """Get a payment with its booking code. Raises 404 if it doesn't exist
        (or isn't this guest's, when user_id is given)."""
        conditions = [Payment.id == payment_id]
        if user_id is not None:
            conditions.append(Payment.user_id == user_id)
        row = (await self.db.execute(
            select(Payment, Booking.booking_code, Booking.status)
            .join(Booking, Payment.booking_id == Booking.id)
            .where(*conditions)
        )).first()
        if not row:
            raise APIException(status_code=404, message="Payment not found")
        payment, booking_code, booking_status = row
        payment.booking_code, payment.booking_status = booking_code, booking_status

        return payment

    async def list(self, filter: PaymentList, page: int = 1, limit: int = 10, user_id: Optional[UUID] = None) -> dict:
        """Get a paginated, filtered list of payments, newest first. When user_id is given, only that guest's.
        Raises 400 if page is below 1 or limit is outside 1-100."""
        if page < 1:
            raise APIException(status_code=400, message="page must be 1 or greater")
        if not 1 <= limit <= 100:
            raise APIException(status_code=400, message="limit must be between 1 and 100")

        conditions = []
        owner = user_id if user_id is not None else filter.user_id
        if owner is not None:
            conditions.append(Payment.user_id == owner)
        if filter.booking_id:
            conditions.append(Payment.booking_id == filter.booking_id)
        if filter.status:
            conditions.append(Payment.status == filter.status.value)

        total = (await self.db.execute(select(func.count()).select_from(Payment).where(*conditions))).scalar() or 0
        rows = (await self.db.execute(
            select(Payment, Booking.booking_code, Booking.status)
            .join(Booking, Payment.booking_id == Booking.id)
            .where(*conditions)
            .order_by(Payment.created_at.desc(), Payment.id.desc())  # id breaks ties so pages never repeat or skip
            .offset((page - 1) * limit)
            .limit(limit)
        )).all()

        payments = []
        for payment, booking_code, booking_status in rows:
            payment.booking_code, payment.booking_status = booking_code, booking_status
            payments.append(payment)

        return {
            "payments": payments,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit,
            },
        }

    async def summary(self, booking_id: UUID, user_id: Optional[UUID] = None) -> dict:
        """What a booking costs and how much is paid, pending and still owed.
        Raises 404 if the booking doesn't exist (or isn't this guest's, when user_id is given)."""
        conditions = [Booking.id == booking_id]
        if user_id is not None:
            conditions.append(Booking.user_id == user_id)
        booking = (await self.db.execute(select(Booking).where(*conditions))).scalar_one_or_none()
        if not booking:
            raise APIException(status_code=404, message="Booking not found")

        totals = dict((await self.db.execute(
            select(Payment.status, func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.booking_id == booking_id)
            .group_by(Payment.status)
        )).all())
        paid = totals.get(PaymentStatus.SUCCEEDED.value, 0)
        pending = totals.get(PaymentStatus.PENDING.value, 0)

        return {
            "booking_id": booking_id,
            "currency": booking.currency,
            "total": booking.total_price,
            "paid": paid,
            "pending": pending,
            "remaining": max(booking.total_price - paid - pending, 0),
        }

    async def confirm(self, payment_id: UUID, reference: Optional[str] = None) -> Payment:
        """Record that the provider received the money: pending -> succeeded, stamping paid_at. The provider's
        transaction id can be given here. If the booking's succeeded payments now cover its total and it is still
        pending, it becomes confirmed, in the same transaction.
        Raises 404 if the payment doesn't exist, 409 if it isn't pending or the reference is already used."""
        payment = await self.get(payment_id)
        if PaymentStatus.SUCCEEDED.value not in TRANSITIONS.get(payment.status, set()):
            raise APIException(status_code=409, message=f"A {payment.status} payment can't become succeeded")

        booking_code = payment.booking_code
        payment.status = PaymentStatus.SUCCEEDED.value
        payment.paid_at = datetime.now(timezone.utc)
        if reference:
            payment.reference = reference
        try:
            await self.db.flush()
        except IntegrityError:
            await self.db.rollback()
            raise APIException(status_code=409, message="That provider reference is already used by another payment")

        # Lock the booking, then see whether it is now fully paid
        booking = (await self.db.execute(
            select(Booking).where(Booking.id == payment.booking_id).with_for_update()
        )).scalar_one()
        paid = (await self.db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.booking_id == booking.id, Payment.status == PaymentStatus.SUCCEEDED.value
            )
        )).scalar()
        if booking.status == BookingStatus.PENDING.value and paid >= booking.total_price:
            booking.status = BookingStatus.CONFIRMED.value
        booking_status = booking.status

        await self.db.commit()
        await self.db.refresh(payment)

        payment.booking_code, payment.booking_status = booking_code, booking_status
        return payment

    async def fail(self, payment_id: UUID) -> Payment:
        """Record that the payment did not go through: pending -> failed, which frees its amount to be paid again.
        Raises 404 if the payment doesn't exist, 409 if it isn't pending."""
        payment = await self.get(payment_id)
        if PaymentStatus.FAILED.value not in TRANSITIONS.get(payment.status, set()):
            raise APIException(status_code=409, message=f"A {payment.status} payment can't become failed")

        booking_code, booking_status = payment.booking_code, payment.booking_status
        payment.status = PaymentStatus.FAILED.value
        await self.db.commit()
        await self.db.refresh(payment)

        payment.booking_code, payment.booking_status = booking_code, booking_status
        return payment

    async def refund(self, payment_id: UUID) -> Payment:
        """Record a full refund: succeeded -> refunded, which makes its amount owed again.
        Raises 404 if the payment doesn't exist, 409 if it hasn't succeeded."""
        payment = await self.get(payment_id)
        if PaymentStatus.REFUNDED.value not in TRANSITIONS.get(payment.status, set()):
            raise APIException(status_code=409, message=f"A {payment.status} payment can't become refunded")

        booking_code, booking_status = payment.booking_code, payment.booking_status
        payment.status = PaymentStatus.REFUNDED.value
        await self.db.commit()
        await self.db.refresh(payment)

        payment.booking_code, payment.booking_status = booking_code, booking_status
        return payment
