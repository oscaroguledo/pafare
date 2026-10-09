from datetime import datetime, timezone
from typing import Optional, Set
from uuid import UUID

from fastapi import BackgroundTasks
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import APIException
from core.logging import get_structured_logger
from core.utils.uuid_utils import uuid7
from models.billings.payment import Payment, PaymentStatus
from models.bookings.booking import Booking, BookingStatus
from schemas.billings.payment import PaymentCreate, PaymentList
from services.bookings.booking import BookingService

logger = get_structured_logger(__name__)

# Bookings in these states can't be paid for any more.
UNPAYABLE_STATUSES = (BookingStatus.CANCELLED.value, BookingStatus.NO_SHOW.value)

# Payments whose money counts as received (a fully refunded one counts for nothing: amount - refunded_amount = 0)
RECEIVED_STATUSES = (PaymentStatus.SUCCEEDED.value, PaymentStatus.REFUNDED.value)

# Which status can follow which. Anything not listed is refused.
TRANSITIONS = {
    PaymentStatus.PENDING.value: {PaymentStatus.SUCCEEDED.value, PaymentStatus.FAILED.value},
    PaymentStatus.SUCCEEDED.value: {PaymentStatus.REFUNDED.value},
}


class PaymentService:
    """Payments towards bookings. Two optional limits narrow what a method sees: `user_id` limits it to that guest's
    payments (someone else's is reported as not found; leave it out for staff), and `hotel_ids` limits it to payments
    for bookings at those hotels (None = all hotels). There is no payment gateway here: `confirm`, `fail` and
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
            select(func.coalesce(func.sum(Payment.amount - Payment.refunded_amount), 0)).where(
                Payment.booking_id == booking.id,
                Payment.status.in_((PaymentStatus.PENDING.value, *RECEIVED_STATUSES)),
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

    async def get(self, payment_id: UUID, user_id: Optional[UUID] = None, hotel_ids: Optional[Set[UUID]] = None) -> Payment:
        """Get a payment with its booking code and the booking's status. Raises 404 if it doesn't exist (or isn't
        this guest's, or isn't for a booking at one of the staff member's hotels, when those limits are given)."""
        conditions = [Payment.id == payment_id]
        if user_id is not None:
            conditions.append(Payment.user_id == user_id)
        if hotel_ids is not None:
            conditions.append(Booking.hotel_id.in_(hotel_ids))
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

    async def list(
        self, filter: PaymentList, page: int = 1, limit: int = 10,
        user_id: Optional[UUID] = None, hotel_ids: Optional[Set[UUID]] = None,
    ) -> dict:
        """Get a paginated, filtered list of payments, newest first. When user_id is given, only that guest's; when
        hotel_ids is given, only payments for bookings at those hotels.
        Raises 400 if page is below 1 or limit is outside 1-100."""
        if page < 1:
            raise APIException(status_code=400, message="page must be 1 or greater")
        if not 1 <= limit <= 100:
            raise APIException(status_code=400, message="limit must be between 1 and 100")

        conditions = []
        owner = user_id if user_id is not None else filter.user_id
        if owner is not None:
            conditions.append(Payment.user_id == owner)
        if hotel_ids is not None:
            conditions.append(Booking.hotel_id.in_(hotel_ids))
        if filter.booking_id:
            conditions.append(Payment.booking_id == filter.booking_id)
        if filter.status:
            conditions.append(Payment.status == filter.status.value)

        total = (await self.db.execute(
            select(func.count()).select_from(Payment).join(Booking, Payment.booking_id == Booking.id).where(*conditions)
        )).scalar() or 0
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

    async def summary(self, booking_id: UUID, user_id: Optional[UUID] = None, hotel_ids: Optional[Set[UUID]] = None) -> dict:
        """What a booking costs and how much is paid (net of refunds), pending, refunded and still owed.
        Raises 404 if the booking doesn't exist (or isn't this guest's / at the staff member's hotels)."""
        conditions = [Booking.id == booking_id]
        if user_id is not None:
            conditions.append(Booking.user_id == user_id)
        if hotel_ids is not None:
            conditions.append(Booking.hotel_id.in_(hotel_ids))
        booking = (await self.db.execute(select(Booking).where(*conditions))).scalar_one_or_none()
        if not booking:
            raise APIException(status_code=404, message="Booking not found")

        rows = (await self.db.execute(
            select(Payment.status, func.coalesce(func.sum(Payment.amount), 0), func.coalesce(func.sum(Payment.refunded_amount), 0))
            .where(Payment.booking_id == booking_id)
            .group_by(Payment.status)
        )).all()
        paid = sum(amount - refunded for status, amount, refunded in rows if status in RECEIVED_STATUSES)
        refunded = sum(refunded for status, amount, refunded in rows if status in RECEIVED_STATUSES)
        pending = sum(amount for status, amount, refunded in rows if status == PaymentStatus.PENDING.value)

        return {
            "booking_id": booking_id,
            "currency": booking.currency,
            "total": booking.total_price,
            "paid": paid,
            "pending": pending,
            "refunded": refunded,
            "remaining": max(booking.total_price - paid - pending, 0),
        }

    async def confirm(
        self, payment_id: UUID, reference: Optional[str] = None, hotel_ids: Optional[Set[UUID]] = None,
        background_tasks: Optional[BackgroundTasks] = None,
    ) -> Payment:
        """Record that the provider received the money: pending -> succeeded, stamping paid_at. The provider's
        transaction id can be given here. If the booking's payments now cover its total and it is still pending, it
        becomes confirmed, in the same transaction, and the guest is emailed.
        Raises 404 if the payment doesn't exist, 409 if it isn't pending or the reference is already used."""
        payment = await self.get(payment_id, None, hotel_ids)
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
            select(func.coalesce(func.sum(Payment.amount - Payment.refunded_amount), 0)).where(
                Payment.booking_id == booking.id, Payment.status.in_(RECEIVED_STATUSES)
            )
        )).scalar()
        booking_id, just_confirmed = booking.id, False
        if booking.status == BookingStatus.PENDING.value and paid >= booking.total_price:
            booking.status = BookingStatus.CONFIRMED.value
            just_confirmed = True
        booking_status = booking.status

        await self.db.commit()
        await self.db.refresh(payment)

        if just_confirmed:
            bookings = BookingService(self.db)
            await bookings.queue_confirmation_email(await bookings.get(booking_id), background_tasks)
        payment.booking_code, payment.booking_status = booking_code, booking_status
        return payment

    async def fail(self, payment_id: UUID, hotel_ids: Optional[Set[UUID]] = None) -> Payment:
        """Record that the payment did not go through: pending -> failed, which frees its amount to be paid again.
        Raises 404 if the payment doesn't exist, 409 if it isn't pending."""
        payment = await self.get(payment_id, None, hotel_ids)
        if PaymentStatus.FAILED.value not in TRANSITIONS.get(payment.status, set()):
            raise APIException(status_code=409, message=f"A {payment.status} payment can't become failed")

        booking_code, booking_status = payment.booking_code, payment.booking_status
        payment.status = PaymentStatus.FAILED.value
        await self.db.commit()
        await self.db.refresh(payment)

        payment.booking_code, payment.booking_status = booking_code, booking_status
        return payment

    async def refund(self, payment_id: UUID, amount: Optional[int] = None, hotel_ids: Optional[Set[UUID]] = None) -> Payment:
        """Record a refund of a succeeded payment: all of what is left of it, or `amount` for a part refund. A part
        refund leaves the payment succeeded; once all of it is refunded it becomes refunded. Refunded money is owed
        again. This only records the refund; handing the money back happens with the payment provider.
        Raises 404 if the payment doesn't exist, 409 if it hasn't succeeded, 400 if the amount is more than is left."""
        payment = await self.get(payment_id, None, hotel_ids)
        if payment.status != PaymentStatus.SUCCEEDED.value:
            raise APIException(status_code=409, message=f"A {payment.status} payment can't be refunded")

        left = payment.amount - payment.refunded_amount
        if amount is None:
            amount = left
        if amount > left:
            raise APIException(status_code=400, message=f"Only {left} of this payment is left to refund")

        booking_code, booking_status = payment.booking_code, payment.booking_status
        payment.refunded_amount += amount
        if payment.refunded_amount == payment.amount:
            payment.status = PaymentStatus.REFUNDED.value
        await self.db.commit()
        await self.db.refresh(payment)

        payment.booking_code, payment.booking_status = booking_code, booking_status
        return payment
