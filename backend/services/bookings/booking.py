import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Set
from uuid import UUID

from fastapi import BackgroundTasks

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import APIException
from core.logging import get_structured_logger
from core.utils.uuid_utils import uuid7
from models.accounts.user import User
from models.billings.payment import Payment, PaymentStatus
from models.bookings.booking import Booking, BookingStatus
from models.bookings.hotel import Hotel
from models.bookings.room import Room
from models.bookings.room_type import RoomType
from schemas.bookings.booking import BookingCreate, BookingUpdate, BookingList
from core.utils.messages.email import EmailService
from services.bookings.hotel import HotelService
from services.bookings.room_type import RoomTypeService

logger = get_structured_logger(__name__)


class BookingService:
    """Bookings. Two optional limits narrow what a method sees: `user_id` limits it to that guest's bookings (someone
    else's is reported as not found; leave it out for staff), and `hotel_ids` limits it to the hotels a staff member
    works on (None = all hotels; a booking at another hotel is reported as not found)."""

    # A booking in one of these states holds its room for its dates. Cancelled, no-show and finished stays don't.
    ACTIVE_STATUSES = (BookingStatus.PENDING.value, BookingStatus.CONFIRMED.value, BookingStatus.CHECKED_IN.value)

    # Which status can follow which. Anything not listed is refused.
    TRANSITIONS = {
        BookingStatus.PENDING.value: {BookingStatus.CONFIRMED.value, BookingStatus.CANCELLED.value},
        BookingStatus.CONFIRMED.value: {BookingStatus.CHECKED_IN.value, BookingStatus.CANCELLED.value, BookingStatus.NO_SHOW.value},
        BookingStatus.CHECKED_IN.value: {BookingStatus.CHECKED_OUT.value},
    }

    # Booking codes look like PH-K1G2N9: two letters from the city, a dash, six random characters. The alphabet leaves
    # out O/0 and I/1, so a code read out loud or copied by hand stays unambiguous.
    CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    CODE_SUFFIX_LENGTH = 6

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def city_prefix(place: Optional[str]) -> str:
        """Two letters for a place: the initials of its first two words ('Port Harcourt' -> 'PH'), or the first two
        letters of a single word ('Lagos' -> 'LA'). Accents are dropped ('Sao Paulo' -> 'SP'); 'XX' if there are no letters."""
        plain = unicodedata.normalize("NFKD", place or "").encode("ascii", "ignore").decode()
        words = re.findall(r"[A-Za-z]+", plain)
        if len(words) >= 2:
            prefix = words[0][0] + words[1][0]
        elif words:
            prefix = words[0][:2]
        else:
            prefix = ""
        return prefix.upper().ljust(2, "X")

    @staticmethod
    def generate_booking_code(place: Optional[str]) -> str:
        """A new random code for the city, e.g. 'PH-K1G2N9'. Not checked for uniqueness: `create` does that."""
        suffix = "".join(secrets.choice(BookingService.CODE_ALPHABET) for _ in range(BookingService.CODE_SUFFIX_LENGTH))
        return f"{BookingService.city_prefix(place)}-{suffix}"

    @staticmethod
    def cancellation_terms(booking: Booking, hotel: Hotel, paid: int, now: datetime) -> dict:
        """What cancelling a booking costs and returns right now, under the hotel's policy: free until
        free_cancellation_hours before check-in, after that the hotel keeps late_cancellation_fee_percent of the total
        (never more than what was paid). Check-in counts from the start of the check-in day, UTC."""
        check_in_at = datetime(booking.check_in.year, booking.check_in.month, booking.check_in.day, tzinfo=timezone.utc)
        free_until = check_in_at - timedelta(hours=hotel.free_cancellation_hours)
        within_free_window = now <= free_until
        policy_fee = 0 if within_free_window else RoomTypeService.money(
            Decimal(booking.total_price) * hotel.late_cancellation_fee_percent / 100
        )
        fee = min(policy_fee, paid)
        return {
            "booking_id": booking.id,
            "currency": booking.currency,
            "hours_until_check_in": int((check_in_at - now).total_seconds() // 3600),
            "free_until": free_until,
            "within_free_window": within_free_window,
            "paid": paid,
            "fee": fee,
            "refund": paid - fee,
        }

    async def create(
        self, user_id: UUID, data: BookingCreate, hotel_ids: Optional[Set[UUID]] = None,
    ) -> Booking:
        """Book a room for a guest. The server sets the hotel, currency, prices (each night at its rate, plus the
        hotel's tax), status and booking code. `hotel_ids` is only for staff booking on a guest's behalf.
        Raises 400 if check-in is in the past or more guests than the room sleeps, 404 if the room doesn't exist,
        403 if the room's hotel isn't one of the caller's, 409 if the room is booked for any of the nights."""
        if data.check_in < datetime.now(timezone.utc).date():
            raise APIException(status_code=400, message="check_in cannot be in the past")

        # Lock the room row so two guests booking the same room at the same time are handled one after the other
        row = (await self.db.execute(
            select(Room, Hotel, RoomType)
            .join(Hotel, Room.hotel_id == Hotel.id)
            .join(RoomType, Room.room_type_id == RoomType.id)
            .where(Room.id == data.room_id)
            .with_for_update(of=Room)
        )).first()
        if not row:
            raise APIException(status_code=404, message="Room not found")
        room, hotel, room_type = row
        HotelService.ensure_access(hotel.id, hotel_ids)
        if data.guests > room_type.max_guests:
            raise APIException(status_code=400, message=f"This room sleeps at most {room_type.max_guests} guests")

        if (await self.db.execute(
            select(Booking.id).where(
                Booking.room_id == room.id,
                Booking.status.in_(self.ACTIVE_STATUSES),
                Booking.check_in < data.check_out,
                Booking.check_out > data.check_in,
            )
        )).first():
            raise APIException(status_code=409, message="The room is not available for these dates")

        # A code is the city's letters plus six random characters. Draw until it is unused; the unique index on
        # booking_code is the final guarantee if two bookings ever draw the same code at the same moment.
        for _ in range(10):
            booking_code = self.generate_booking_code(hotel.city or hotel.name)
            if not (await self.db.execute(select(Booking.id).where(Booking.booking_code == booking_code))).first():
                break
        else:
            raise APIException(status_code=500, message="Could not generate a booking code; please try again")

        price = await RoomTypeService(self.db).stay_price(room_type, hotel, data.check_in, data.check_out)
        booking = Booking(
            id=uuid7(),
            hotel_id=hotel.id,
            user_id=user_id,
            room_id=room.id,
            check_in=data.check_in,
            check_out=data.check_out,
            check_in_time=data.check_in_time,
            check_out_time=data.check_out_time,
            guests=data.guests,
            status=BookingStatus.PENDING.value,
            base_price=price["base_price"],
            discount=0,
            tax_percent=price["tax_percent"],
            tax_amount=price["tax_amount"],
            total_price=price["total_price"],
            booking_code=booking_code,
            currency=hotel.currency,
        )
        hotel_name, room_number, room_type_name = hotel.name, room.number, room_type.name
        self.db.add(booking)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise APIException(status_code=409, message="The booking could not be completed; please try again")
        await self.db.refresh(booking)

        booking.hotel_name, booking.room_number, booking.room_type_name = hotel_name, room_number, room_type_name
        return booking

    async def get(self, booking_id: UUID, user_id: Optional[UUID] = None, hotel_ids: Optional[Set[UUID]] = None) -> Booking:
        """Get a booking with its hotel, room number and room type name. Raises 404 if it doesn't exist (or isn't
        this guest's, or isn't at one of the staff member's hotels, when those limits are given)."""
        conditions = [Booking.id == booking_id]
        if user_id is not None:
            conditions.append(Booking.user_id == user_id)
        if hotel_ids is not None:
            conditions.append(Booking.hotel_id.in_(hotel_ids))
        row = (await self.db.execute(
            select(Booking, Hotel.name, Room.number, RoomType.name)
            .join(Hotel, Booking.hotel_id == Hotel.id)
            .join(Room, Booking.room_id == Room.id)
            .join(RoomType, Room.room_type_id == RoomType.id)
            .where(*conditions)
        )).first()
        if not row:
            raise APIException(status_code=404, message="Booking not found")
        booking, hotel_name, room_number, room_type_name = row
        booking.hotel_name, booking.room_number, booking.room_type_name = hotel_name, room_number, room_type_name

        return booking

    async def list(
        self, filter: BookingList, page: int = 1, limit: int = 10,
        user_id: Optional[UUID] = None, hotel_ids: Optional[Set[UUID]] = None,
    ) -> dict:
        """Get a paginated, filtered list of bookings, newest first. When user_id is given, only that guest's; when
        hotel_ids is given, only bookings at those hotels.
        Raises 400 if page is below 1, limit is outside 1-100, or check_in_from is after check_in_to."""
        if page < 1:
            raise APIException(status_code=400, message="page must be 1 or greater")
        if not 1 <= limit <= 100:
            raise APIException(status_code=400, message="limit must be between 1 and 100")
        if filter.check_in_from and filter.check_in_to and filter.check_in_from > filter.check_in_to:
            raise APIException(status_code=400, message="check_in_from cannot be after check_in_to")

        conditions = []
        owner = user_id if user_id is not None else filter.user_id
        if owner is not None:
            conditions.append(Booking.user_id == owner)
        if hotel_ids is not None:
            conditions.append(Booking.hotel_id.in_(hotel_ids))
        if filter.hotel_id:
            conditions.append(Booking.hotel_id == filter.hotel_id)
        if filter.room_id:
            conditions.append(Booking.room_id == filter.room_id)
        if filter.status:
            conditions.append(Booking.status == filter.status.value)
        if filter.code:
            conditions.append(func.upper(Booking.booking_code) == filter.code.upper())
        if filter.check_in_from:
            conditions.append(Booking.check_in >= filter.check_in_from)
        if filter.check_in_to:
            conditions.append(Booking.check_in <= filter.check_in_to)

        total = (await self.db.execute(select(func.count()).select_from(Booking).where(*conditions))).scalar() or 0
        rows = (await self.db.execute(
            select(Booking, Hotel.name, Room.number, RoomType.name)
            .join(Hotel, Booking.hotel_id == Hotel.id)
            .join(Room, Booking.room_id == Room.id)
            .join(RoomType, Room.room_type_id == RoomType.id)
            .where(*conditions)
            .order_by(Booking.created_at.desc(), Booking.id.desc())  # id breaks ties so pages never repeat or skip
            .offset((page - 1) * limit)
            .limit(limit)
        )).all()

        bookings = []
        for booking, hotel_name, room_number, room_type_name in rows:
            booking.hotel_name, booking.room_number, booking.room_type_name = hotel_name, room_number, room_type_name
            bookings.append(booking)

        return {
            "bookings": bookings,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit,
            },
        }

    async def update(
        self, booking_id: UUID, data: BookingUpdate, user_id: Optional[UUID] = None, hotel_ids: Optional[Set[UUID]] = None,
    ) -> Booking:
        """Reschedule a booking or change its party size (PATCH semantics). If the dates change, the price is
        recalculated night by night at the current rates and the hotel's current tax, keeping the discount.
        Raises 404 if the booking doesn't exist, 400 if nothing was sent, a field is null, the new check-in is in the
        past or not before check-out, or the party is bigger than the room sleeps, 409 if the booking is no longer
        changeable or the room is taken."""
        booking = await self.get(booking_id, user_id, hotel_ids)
        if booking.status not in (BookingStatus.PENDING.value, BookingStatus.CONFIRMED.value):
            raise APIException(status_code=409, message=f"A {booking.status} booking can't be changed")

        changes = data.model_dump(exclude_unset=True)
        if not changes:
            raise APIException(status_code=400, message="No fields to update")
        for field in ("check_in", "check_out", "guests"):
            if field in changes and changes[field] is None:
                raise APIException(status_code=400, message=f"{field} cannot be null")

        check_in = changes.get("check_in", booking.check_in)
        check_out = changes.get("check_out", booking.check_out)
        if check_out <= check_in:
            raise APIException(status_code=400, message="check_out must be after check_in")
        if (check_out - check_in).days > 90:
            raise APIException(status_code=400, message="A stay can be at most 90 nights")
        dates_changed = (check_in, check_out) != (booking.check_in, booking.check_out)

        # Lock the room row so a competing booking for the same room waits for this change
        await self.db.execute(select(Room.id).where(Room.id == booking.room_id).with_for_update())
        room_type = (await self.db.execute(
            select(RoomType).join(Room, Room.room_type_id == RoomType.id).where(Room.id == booking.room_id)
        )).scalar_one()
        if changes.get("guests", booking.guests) > room_type.max_guests:
            raise APIException(status_code=400, message=f"This room sleeps at most {room_type.max_guests} guests")

        if dates_changed:
            if "check_in" in changes and check_in < datetime.now(timezone.utc).date():
                raise APIException(status_code=400, message="check_in cannot be in the past")
            if (await self.db.execute(
                select(Booking.id).where(
                    Booking.room_id == booking.room_id,
                    Booking.id != booking.id,
                    Booking.status.in_(self.ACTIVE_STATUSES),
                    Booking.check_in < check_out,
                    Booking.check_out > check_in,
                )
            )).first():
                raise APIException(status_code=409, message="The room is not available for these dates")
            hotel = (await self.db.execute(select(Hotel).where(Hotel.id == booking.hotel_id))).scalar_one()
            price = await RoomTypeService(self.db).stay_price(room_type, hotel, check_in, check_out, booking.discount)
            changes.update(
                base_price=price["base_price"], tax_percent=price["tax_percent"],
                tax_amount=price["tax_amount"], total_price=price["total_price"],
            )

        hotel_name, room_number, room_type_name = booking.hotel_name, booking.room_number, booking.room_type_name
        for field, value in changes.items():
            setattr(booking, field, value)
        await self.db.commit()
        await self.db.refresh(booking)

        booking.hotel_name, booking.room_number, booking.room_type_name = hotel_name, room_number, room_type_name
        return booking

    async def set_discount(self, booking_id: UUID, discount: int, hotel_ids: Optional[Set[UUID]] = None) -> Booking:
        """Set a percentage discount (0-100) on a booking (staff). The discount comes off the nights' total and tax is
        worked out on what is left, at the rate recorded on the booking.
        Raises 404 if the booking doesn't exist, 400 if the discount is outside 0-100, 409 if the booking is no longer open."""
        if not 0 <= discount <= 100:
            raise APIException(status_code=400, message="discount must be between 0 and 100")
        booking = await self.get(booking_id, None, hotel_ids)
        if booking.status not in self.ACTIVE_STATUSES:
            raise APIException(status_code=409, message=f"A {booking.status} booking can't be changed")

        hotel_name, room_number, room_type_name = booking.hotel_name, booking.room_number, booking.room_type_name
        after_discount = RoomTypeService.money(Decimal(booking.base_price) * (100 - discount) / 100)
        booking.discount = discount
        booking.tax_amount = RoomTypeService.money(Decimal(after_discount) * booking.tax_percent / 100)
        booking.total_price = after_discount + booking.tax_amount
        await self.db.commit()
        await self.db.refresh(booking)

        booking.hotel_name, booking.room_number, booking.room_type_name = hotel_name, room_number, room_type_name
        return booking

    async def change_status(
        self, booking_id: UUID, status: BookingStatus, user_id: Optional[UUID] = None,
        hotel_ids: Optional[Set[UUID]] = None, background_tasks: Optional[BackgroundTasks] = None,
    ) -> Booking:
        """Move a booking to a new status: pending -> confirmed or cancelled; confirmed -> checked in, cancelled or
        no-show; checked in -> checked out. Checking in or out stamps the time if none was set; confirming emails
        the guest; cancelling goes through `cancel` (fee and refund).
        Raises 404 if the booking doesn't exist, 409 if the move isn't allowed."""
        if status == BookingStatus.CANCELLED:
            return await self.cancel(booking_id, user_id, hotel_ids, False, background_tasks)

        booking = await self.get(booking_id, user_id, hotel_ids)
        if status.value not in self.TRANSITIONS.get(booking.status, set()):
            raise APIException(status_code=409, message=f"A {booking.status} booking can't become {status.value}")

        hotel_name, room_number, room_type_name = booking.hotel_name, booking.room_number, booking.room_type_name
        booking.status = status.value
        now = datetime.now(timezone.utc)
        if status == BookingStatus.CHECKED_IN and booking.check_in_time is None:
            booking.check_in_time = now
        if status == BookingStatus.CHECKED_OUT and booking.check_out_time is None:
            booking.check_out_time = now
        await self.db.commit()
        await self.db.refresh(booking)

        booking.hotel_name, booking.room_number, booking.room_type_name = hotel_name, room_number, room_type_name
        if status == BookingStatus.CONFIRMED:
            await self.queue_confirmation_email(booking, background_tasks)
        return booking

    async def cancellation_quote(
        self, booking_id: UUID, user_id: Optional[UUID] = None, hotel_ids: Optional[Set[UUID]] = None,
    ) -> dict:
        """What cancelling would cost and return right now (see cancellation_terms), without cancelling.
        Raises 404 if the booking doesn't exist, 409 if it can't be cancelled any more."""
        booking = await self.get(booking_id, user_id, hotel_ids)
        if booking.status not in (BookingStatus.PENDING.value, BookingStatus.CONFIRMED.value):
            raise APIException(status_code=409, message=f"A {booking.status} booking can't be cancelled")

        hotel = (await self.db.execute(select(Hotel).where(Hotel.id == booking.hotel_id))).scalar_one()
        paid = (await self.db.execute(
            select(func.coalesce(func.sum(Payment.amount - Payment.refunded_amount), 0)).where(
                Payment.booking_id == booking_id,
                Payment.status.in_((PaymentStatus.SUCCEEDED.value, PaymentStatus.REFUNDED.value)),
            )
        )).scalar()
        return self.cancellation_terms(booking, hotel, paid, datetime.now(timezone.utc))

    async def cancel(
        self, booking_id: UUID, user_id: Optional[UUID] = None, hotel_ids: Optional[Set[UUID]] = None,
        waive_fee: bool = False, background_tasks: Optional[BackgroundTasks] = None,
    ) -> Booking:
        """Cancel a pending or confirmed booking, which frees the room. Inside the hotel's free-cancellation window
        everything paid is refunded; after it the hotel keeps its late-cancellation share and the rest is refunded.
        Staff (no user_id) can waive the fee. Payments that were started but not confirmed are marked failed.
        Refunds are recorded here; handing the money back happens with the payment provider.
        Raises 404 if the booking doesn't exist, 409 if it can't be cancelled any more."""
        booking = await self.get(booking_id, user_id, hotel_ids)
        # Lock the booking row so two cancellations (or a cancellation and a payment) can't interleave
        await self.db.execute(select(Booking.id).where(Booking.id == booking_id).with_for_update())
        await self.db.refresh(booking)
        if booking.status not in (BookingStatus.PENDING.value, BookingStatus.CONFIRMED.value):
            raise APIException(status_code=409, message=f"A {booking.status} booking can't be cancelled")

        hotel = (await self.db.execute(select(Hotel).where(Hotel.id == booking.hotel_id))).scalar_one()
        payments = (await self.db.execute(
            select(Payment).where(Payment.booking_id == booking_id).order_by(Payment.paid_at.desc(), Payment.id.desc())
        )).scalars().all()
        paid = sum(p.amount - p.refunded_amount for p in payments if p.status in (PaymentStatus.SUCCEEDED.value, PaymentStatus.REFUNDED.value))
        terms = self.cancellation_terms(booking, hotel, paid, datetime.now(timezone.utc))
        fee = 0 if (waive_fee and user_id is None) else terms["fee"]
        refund = paid - fee

        left = refund
        for payment in payments:
            if payment.status == PaymentStatus.PENDING.value:
                payment.status = PaymentStatus.FAILED.value
            elif payment.status == PaymentStatus.SUCCEEDED.value and left > 0:
                take = min(left, payment.amount - payment.refunded_amount)
                payment.refunded_amount += take
                left -= take
                if payment.refunded_amount == payment.amount:
                    payment.status = PaymentStatus.REFUNDED.value

        hotel_name, room_number, room_type_name = booking.hotel_name, booking.room_number, booking.room_type_name
        booking.status = BookingStatus.CANCELLED.value
        booking.cancelled_at = datetime.now(timezone.utc)
        booking.cancellation_fee = fee
        booking.refund_amount = refund
        await self.db.commit()
        await self.db.refresh(booking)

        booking.hotel_name, booking.room_number, booking.room_type_name = hotel_name, room_number, room_type_name
        await self.queue_cancellation_email(booking, background_tasks)
        return booking

    async def queue_confirmation_email(self, booking: Booking, background_tasks: Optional[BackgroundTasks]) -> None:
        """Email the guest that their booking is confirmed. Does nothing without a background-tasks object."""
        if background_tasks is None:
            return
        guest = (await self.db.execute(select(User).where(User.id == booking.user_id))).scalar_one()
        EmailService.queue(
            background_tasks, EmailService.send_booking_confirmed, guest.email, guest.firstname.title(), guest.language,
            booking_id=booking.id, booking_code=booking.booking_code, hotel_name=booking.hotel_name,
            room_label=f"{booking.room_number} ({booking.room_type_name})", check_in=booking.check_in,
            check_out=booking.check_out, guests=booking.guests, total=booking.total_price, currency=booking.currency,
        )

    async def queue_cancellation_email(self, booking: Booking, background_tasks: Optional[BackgroundTasks]) -> None:
        """Email the guest that their booking is cancelled, with any fee and refund. Does nothing without a
        background-tasks object."""
        if background_tasks is None:
            return
        guest = (await self.db.execute(select(User).where(User.id == booking.user_id))).scalar_one()
        EmailService.queue(
            background_tasks, EmailService.send_booking_cancelled, guest.email, guest.firstname.title(), guest.language,
            booking_id=booking.id, booking_code=booking.booking_code, hotel_name=booking.hotel_name,
            check_in=booking.check_in, check_out=booking.check_out, fee=booking.cancellation_fee,
            refund=booking.refund_amount, currency=booking.currency,
        )
