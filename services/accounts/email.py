"""The emails the store sends customers, each in the customer's own language."""
from datetime import date, datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import BackgroundTasks

from core.config import settings
from core.logging import get_structured_logger
from core.utils.messages.email import send_email

logger = get_structured_logger(__name__)


def queue(background_tasks: BackgroundTasks, send: Callable[..., Awaitable[None]], *args: Any, **kwargs: Any) -> None:
    """Send after the response; a failed send is logged, never raised into the request that queued it."""
    async def run() -> None:
        try:
            await send(*args, **kwargs)
        except Exception as e:
            logger.error(f"Email {getattr(send, '__name__', 'send')} failed: {e}")
    background_tasks.add_task(run)


def _url(path: str) -> str:
    return f"{settings.FRONTEND_URL.rstrip('/')}{path}"


async def send_verification(email: str, name: Optional[str], token: str, language: Optional[str]) -> None:
    await send_email(email, "verification", {
        "name": name, "action_url": _url(f"/verify-email?token={token}"), "valid_hours": 24,
    }, language)


async def send_password_reset(email: str, name: Optional[str], token: str, language: Optional[str], valid_hours: int) -> None:
    await send_email(email, "password_reset", {
        "name": name, "action_url": _url(f"/reset-password?token={token}"), "valid_hours": valid_hours,
    }, language)


async def send_order_confirmation(
    email: str, name: Optional[str], language: Optional[str], order_id: str, order_number: str,
    items: List[Dict[str, Any]], total: float, currency: str, address: List[str],
) -> None:
    """items: [{"name", "quantity", "total"}]; address: printable lines."""
    await send_email(email, "order_confirmation", {
        "name": name, "order_number": order_number, "items": items, "total": total, "currency": currency,
        "address": address, "action_url": _url(f"/account/orders/{order_id}"),
        "subject_values": {"number": order_number},
    }, language)


async def send_shipping_update(
    email: str, name: Optional[str], language: Optional[str], order_id: str, order_number: str,
    carrier: Optional[str], tracking_number: Optional[str], estimated_delivery: Optional[date],
) -> None:
    await send_email(email, "shipping_update", {
        "name": name, "order_number": order_number, "carrier": carrier, "tracking_number": tracking_number,
        "estimated_delivery": estimated_delivery, "action_url": _url(f"/track-order/{order_number}"),
        "subject_values": {"number": order_number},
    }, language)


async def send_order_delivered(
    email: str, name: Optional[str], language: Optional[str], order_id: str, order_number: str,
    delivered_on: datetime, address: List[str],
) -> None:
    await send_email(email, "order_delivered", {
        "name": name, "order_number": order_number, "delivered_on": delivered_on, "address": address,
        "action_url": _url(f"/account/orders/{order_id}"),
        "subject_values": {"number": order_number},
    }, language)


async def send_subscription_reminder(
    email: str, name: Optional[str], language: Optional[str], subscription_id: str, subscription_name: str,
    delivery_date: date, amount: float, currency: str,
) -> None:
    await send_email(email, "subscription_reminder", {
        "name": name, "subscription_name": subscription_name, "delivery_date": delivery_date, "amount": amount,
        "currency": currency, "action_url": _url(f"/account/subscriptions/{subscription_id}"),
        "subject_values": {"name": subscription_name},
    }, language)


async def send_subscription_payment_failed(
    email: str, name: Optional[str], language: Optional[str], subscription_id: str, subscription_name: str,
    reason: Optional[str], paused: bool,
) -> None:
    """Before the pause the renewal can still be paid; after it the customer fixes their card and resumes."""
    await send_email(email, "subscription_payment_failed", {
        "name": name, "subscription_name": subscription_name, "reason": reason, "paused": paused,
        "action_url": _url("/account/payment-methods" if paused else "/account/payments"),
        "subscription_url": _url(f"/account/subscriptions/{subscription_id}"),
        "subject_key": "subjectPaused" if paused else "subject", "subject_values": {"name": subscription_name},
    }, language)
