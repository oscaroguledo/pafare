"""Subscription renewals: each due subscription gets a delivery order, paid with the customer's saved card."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import UUID

import stripe
from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import selectinload

from core.config import settings
from core.logging import get_structured_logger
from core.utils.uuid_utils import uuid7
from models.accounts.user import Address, User
from models.catalog.product import ProductVariant
from models.commerce.orders import FulfillmentStatus, Order, OrderSource, OrderStatus, PaymentStatus
from models.commerce.payments import PaymentFailureReason, PaymentIntent, PaymentMethod
from models.commerce.subscriptions import Subscription, SubscriptionStatus
from services.accounts.email import send_subscription_payment_failed, send_subscription_reminder
from services.commerce.orders import OrderService
from services.commerce.payments import PaymentService
from services.commerce.subscriptions import SubscriptionService, next_delivery

logger = get_structured_logger(__name__)

# A declined renewal is tried again after these delays; the next decline pauses the subscription.
RETRY_DELAYS = (timedelta(hours=6), timedelta(hours=24))
# Subscribers hear about a delivery (and its charge) this long before it.
REMINDER_LEAD = timedelta(days=3)
# Held while renewals run, so two app processes never bill at the same time.
RENEWAL_LOCK_ID = 7_214_001
ACTIVE = SubscriptionStatus.ACTIVE.value
PAYMENT_FAILED = SubscriptionStatus.PAYMENT_FAILED.value


@asynccontextmanager
async def renewal_lock(db: AsyncSession):
    """Yield True if this process may run renewals now. A session-level advisory lock on its own
    connection, so the commits made while billing don't release it."""
    engine = db.bind if isinstance(db.bind, AsyncEngine) else db.bind.engine
    async with engine.connect() as conn:
        acquired = await conn.scalar(text("SELECT pg_try_advisory_lock(:id)"), {"id": RENEWAL_LOCK_ID})
        try:
            yield acquired
        finally:
            if acquired:
                await conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": RENEWAL_LOCK_ID})


# Renewals run at set hours, so anything due within this window is taken now rather than a run later
# (e.g. a retry set for 14:00:30 by the 08:00:30 run is billed by the 14:00 run).
DUE_WINDOW = timedelta(minutes=15)


def _due(now: datetime):
    cutoff = now + DUE_WINDOW
    return and_(
        Subscription.auto_renew.is_(True),
        or_(
            and_(Subscription.status == ACTIVE, Subscription.next_billing_date <= cutoff),
            and_(Subscription.status == PAYMENT_FAILED, Subscription.next_retry_date <= cutoff),
        ),
    )


def _advance(subscription: Subscription, now: datetime, delivered: bool) -> None:
    """Move to the next delivery date on the subscription's own schedule, never into the past."""
    metadata = dict(subscription.subscription_metadata or {})
    start = subscription.next_billing_date or now
    day = metadata.setdefault("billing_day", start.day)
    end = next_delivery(start, subscription.billing_cycle, day)
    while end <= now:
        end = next_delivery(end, subscription.billing_cycle, day)
    subscription.current_period_start = start
    subscription.current_period_end = end
    subscription.next_billing_date = end
    metadata.pop("skipped_from_date", None)
    if delivered:
        metadata["orders_created_count"] = metadata.get("orders_created_count", 0) + 1
        metadata["last_order_created"] = now.isoformat()
    subscription.subscription_metadata = metadata


async def renewal_paid(db: AsyncSession, order: Order) -> None:
    """A renewal order was paid (by the scheduler, the customer or a webhook): reactivate and schedule the next one."""
    subscription = await db.get(Subscription, order.subscription_id)
    if not subscription or subscription.status == SubscriptionStatus.CANCELLED.value:
        return
    now = datetime.now(timezone.utc)
    subscription.status = ACTIVE
    subscription.payment_retry_count = 0
    subscription.next_retry_date = None
    subscription.last_payment_error = None
    subscription.last_payment_attempt = now
    subscription.paused_at = subscription.pause_reason = None
    _advance(subscription, now, delivered=True)


async def open_renewal(db: AsyncSession, subscription_id: UUID) -> Optional[Order]:
    """The subscription's renewal order that is still waiting for payment, if any."""
    return (await db.execute(
        select(Order).where(Order.subscription_id == subscription_id, Order.payment_status == PaymentStatus.PENDING)
        .order_by(Order.created_at.desc()).limit(1)
    )).scalar_one_or_none()


async def close_open_renewal(db: AsyncSession, subscription_id: UUID, reason: str) -> None:
    """Drop an unpaid renewal: stop offering it for payment and give its stock back. Commits."""
    order = await open_renewal(db, subscription_id)
    if not order:
        return
    intents = (await db.execute(select(PaymentIntent).where(PaymentIntent.order_id == order.id))).scalars().all()
    for intent in intents:
        intent.status = "canceled"
        try:
            await asyncio.to_thread(stripe.PaymentIntent.cancel, intent.stripe_payment_intent_id)
        except stripe.error.StripeError as e:
            # Already final at Stripe (e.g. canceled); our row is what the customer sees.
            logger.warning(f"Could not cancel Stripe intent {intent.stripe_payment_intent_id}: {e}")
    await OrderService(db)._release_unpaid_order(order, reason)


class SubscriptionScheduler:
    """Bills due subscriptions and retries declined renewals."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.orders = OrderService(db)

    async def process_due_subscriptions(self) -> Dict[str, Any]:
        """Renew every due subscription; one failure never stops the rest."""
        now = datetime.now(timezone.utc)
        ids = (await self.db.execute(select(Subscription.id).where(_due(now)))).scalars().all()
        counts = {"paid": 0, "declined": 0, "pending": 0, "skipped": 0, "error": 0}
        for subscription_id in ids:
            try:
                outcome = await self.process_subscription(subscription_id)
            except Exception as e:
                await self.db.rollback()
                # A rollback leaves every loaded object stale; the next subscription starts clean.
                self.db.expunge_all()
                logger.exception(f"Renewal of subscription {subscription_id} failed: {e}")
                outcome = "error"
            counts[outcome] += 1
        return {
            "processed_count": counts["paid"],
            "failed_count": counts["declined"] + counts["error"],
            "total_due": len(ids),
            **counts,
        }

    async def send_upcoming_reminders(self) -> int:
        """Email each subscriber once before their next delivery; returns how many were sent."""
        now = datetime.now(timezone.utc)
        subscriptions = (await self.db.execute(select(Subscription).where(
            Subscription.status == ACTIVE, Subscription.auto_renew.is_(True),
            Subscription.next_billing_date > now, Subscription.next_billing_date <= now + REMINDER_LEAD,
        ))).scalars().all()
        sent = 0
        for subscription in subscriptions:
            metadata = dict(subscription.subscription_metadata or {})
            delivery = subscription.next_billing_date.isoformat()
            if metadata.get("reminded_for") == delivery:
                continue
            user = await self.db.get(User, subscription.user_id)
            try:
                await send_subscription_reminder(
                    user.email, user.firstname, user.language, str(subscription.id), subscription.name,
                    subscription.next_billing_date, float(subscription.current_total or 0), subscription.currency,
                )
            except Exception as e:
                logger.error(f"Failed to send renewal reminder for subscription {subscription.id}: {e}")
                continue
            metadata["reminded_for"] = delivery
            subscription.subscription_metadata = metadata
            await self.db.commit()
            sent += 1
        return sent

    async def process_subscription(self, subscription_id: UUID) -> str:
        """Renew one subscription: 'paid', 'declined', 'pending' (bank still processing), 'skipped' or 'error'."""
        now = datetime.now(timezone.utc)
        subscription = (await self.db.execute(
            select(Subscription).where(Subscription.id == subscription_id, _due(now))
            .options(selectinload(Subscription.shipping_method)).with_for_update(skip_locked=True)
        )).scalar_one_or_none()
        if not subscription:
            return "skipped"

        order = await open_renewal(self.db, subscription.id) or await self._new_order(subscription)
        if not order:
            _advance(subscription, now, delivered=False)
            await self.db.commit()
            logger.warning(f"Subscription {subscription.id} skipped a delivery: nothing in stock")
            return "skipped"

        # The default card, else the newest one (older accounts may have none marked default).
        card = await self.db.scalar(select(PaymentMethod).where(
            PaymentMethod.user_id == subscription.user_id, PaymentMethod.is_active.is_(True)
        ).order_by(PaymentMethod.is_default.desc(), PaymentMethod.created_at.desc()).limit(1))
        if not card:
            return await self._declined(subscription, "There's no saved card to charge. Add a card to keep your deliveries coming.")

        try:
            intent = await PaymentService(self.db).charge_saved_card(order, card, subscription.payment_retry_count or 0)
        except stripe.error.StripeError as e:
            # Not a decline (e.g. Stripe unreachable): keep the order and try again on the next run.
            await self.db.commit()
            logger.error(f"Could not charge renewal {order.order_number}: {e}")
            return "error"

        if intent.status == "succeeded":
            await PaymentService(self.db)._settle(intent)
            await self.orders._send_confirmation_email(order.id)
            logger.info(f"Subscription {subscription.id} renewed with order {order.order_number}")
            return "paid"
        if intent.status == "processing":
            # The bank settles later and the webhook finishes the renewal; until then each run just re-checks it.
            await self.db.commit()
            return "pending"
        reason = PaymentFailureReason(intent.failure_reason or PaymentFailureReason.UNKNOWN.value)
        return await self._declined(subscription, PaymentService(self.db)._get_user_friendly_message(reason))

    async def _new_order(self, subscription: Subscription) -> Optional[Order]:
        """Create this period's delivery order with everything in stock and reserve that stock. Commits."""
        quantities = (subscription.subscription_metadata or {}).get("variant_quantities", {})
        variants = (await self.db.execute(
            select(ProductVariant).where(ProductVariant.id.in_([UUID(v) for v in subscription.variant_ids or []]))
            .options(selectinload(ProductVariant.product), selectinload(ProductVariant.inventory))
        )).scalars().all()
        lines = [
            (v, int(quantities.get(str(v.id), 1))) for v in variants
            if v.is_active and v.product and v.product.is_active
            and v.inventory and v.inventory.quantity_available >= int(quantities.get(str(v.id), 1))
        ]
        if not lines:
            return None

        address = await self.db.get(Address, subscription.delivery_address_id) if subscription.delivery_address_id else None
        address_dict = {
            "street": address.street, "city": address.city, "state": address.state,
            "country": address.country, "post_code": address.post_code,
        } if address else {}
        pricing = await SubscriptionService(self.db)._calculate_pricing(
            [v for v, _ in lines], quantities, address_dict or None, subscription.currency or settings.STORE_CURRENCY,
            subscription.user_id, subscription.shipping_method_id, subscription.discount_code,
        )
        prices = {p["id"]: p["price"] for p in pricing["variant_prices"]}

        order_id = uuid7()
        order = Order(
            id=order_id,
            order_number=f"SUB-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{order_id.hex[-12:].upper()}",
            user_id=subscription.user_id,
            subscription_id=subscription.id,
            order_status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            fulfillment_status=FulfillmentStatus.UNFULFILLED,
            source=OrderSource.API,
            subtotal=pricing["subtotal"],
            shipping_cost=pricing["shipping"],
            tax_amount=pricing["tax"],
            tax_rate=pricing["tax_rate"],
            discount_amount=pricing["discount"],
            total_amount=pricing["total"],
            currency=subscription.currency or settings.STORE_CURRENCY,
            shipping_method=subscription.shipping_method.name if subscription.shipping_method else "standard",
            shipping_address=dict(address_dict),
            billing_address=dict(address_dict),
        )
        self.db.add(order)
        await self.db.flush()
        for variant, quantity in lines:
            await self.orders._reserve_line(order, variant, quantity, prices[str(variant.id)])
        # Committed before charging, so a crash mid-charge finds this order (and its Stripe intent) again.
        await self.db.commit()
        return order

    async def _declined(self, subscription: Subscription, message: str) -> str:
        """Record a failed renewal attempt: retry later, or pause after the last retry. Emails the customer."""
        now = datetime.now(timezone.utc)
        attempt = (subscription.payment_retry_count or 0) + 1
        subscription.payment_retry_count = attempt
        subscription.last_payment_attempt = now
        subscription.last_payment_error = message
        if attempt <= len(RETRY_DELAYS):
            subscription.status = PAYMENT_FAILED
            subscription.next_retry_date = now + RETRY_DELAYS[attempt - 1]
            await self.db.commit()
        else:
            subscription.status = SubscriptionStatus.PAUSED.value
            subscription.paused_at = now
            subscription.pause_reason = f"Payment failed {attempt} times: {message}"
            subscription.next_retry_date = None
            await self.db.flush()
            await close_open_renewal(self.db, subscription.id, "subscription paused after failed payments")
        logger.warning(f"Renewal payment for subscription {subscription.id} failed (attempt {attempt}): {message}")

        try:
            user = await self.db.get(User, subscription.user_id)
            await send_subscription_payment_failed(
                user.email, user.firstname, user.language, str(subscription.id), subscription.name,
                message, paused=attempt > len(RETRY_DELAYS),
            )
        except Exception as e:
            logger.error(f"Failed to send payment failure email for subscription {subscription.id}: {e}")
        return "declined"
