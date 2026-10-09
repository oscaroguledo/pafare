"""Customer emails: rendered in the customer's language from one catalog, sent through Brevo."""
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import BackgroundTasks

import aiohttp
from babel import Locale, UnknownLocaleError
from babel.dates import format_date, format_timedelta
from babel.numbers import format_currency, get_currency_precision
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup, escape

from core.config import settings
from core.logging import get_structured_logger

logger = get_structured_logger(__name__)

# Every email the app sends, and the template that draws it.
TEMPLATES: Dict[str, str] = {
    "verification": "account/verification.html",
    "password_reset": "account/password_reset.html",
    "booking_confirmation": "bookings/booking_confirmation.html",
    "booking_cancelled": "bookings/booking_cancelled.html",
}

RTL_LANGUAGES = {"ar", "ur", "fa", "ps", "ckb", "he"}

_HERE = Path(__file__).parent
CATALOG: Dict[str, Dict[str, Dict[str, str]]] = json.loads((_HERE / "email_translations.json").read_text(encoding="utf-8"))

_env = Environment(loader=FileSystemLoader(str(_HERE / "templates")), autoescape=True, undefined=StrictUndefined,
                   trim_blocks=True, lstrip_blocks=True)


def email_language(language: Optional[str]) -> str:
    """The supported language closest to the account's setting (fr-CA -> fr, zh-TW -> zh-Hant), else English."""
    tag = (language or "en").replace("_", "-")
    if tag in CATALOG:
        return tag
    lower = tag.lower()
    if lower.startswith("zh") and any(part in lower for part in ("hant", "tw", "hk", "mo")):
        return "zh-Hant"
    base = lower.split("-")[0]
    base = {"fil": "tl", "iw": "he", "prs": "fa"}.get(base, base)
    return base if base in CATALOG else "en"


def _babel_locale(language: str) -> Locale:
    try:
        return Locale.parse({"zh": "zh_Hans", "zh-Hant": "zh_Hant", "en": "en_CA", "fr": "fr_CA"}.get(language, language), sep="_")
    except (UnknownLocaleError, ValueError):
        return Locale.parse("en_CA")


@dataclass
class Translator:
    """Looks up one email's text; {placeholders} are filled with escaped values, the text's own markup is kept."""
    language: str
    kind: str

    def __call__(self, key: str, **values: Any) -> Markup:
        section = self.kind if key in CATALOG["en"].get(self.kind, {}) else "common"
        text = CATALOG.get(self.language, {}).get(section, {}).get(key) or CATALOG["en"][section][key]
        return Markup(text).format(**{name: escape(value) for name, value in values.items()})


def _formatters(language: str) -> Dict[str, Any]:
    locale = _babel_locale(language)
    return {
        "money": lambda amount, currency=settings.STORE_CURRENCY: format_currency(amount or 0, currency, locale=locale),
        # Amounts in the app are whole minor units (kobo, cents); show them in the currency's usual form
        "money_minor": lambda amount, currency: format_currency(
            Decimal(amount or 0) / (10 ** get_currency_precision(currency)), currency, locale=locale
        ),
        "long_date": lambda value: format_date(value if isinstance(value, (date, datetime)) else datetime.fromisoformat(str(value)), format="long", locale=locale),
        "duration": lambda hours: format_timedelta(timedelta(hours=hours), threshold=2, locale=locale),
    }


def render_email(kind: str, context: Dict[str, Any], language: Optional[str] = None) -> tuple[str, str]:
    """The (subject, html) of an email in the customer's language."""
    lang = email_language(language)
    t = Translator(lang, kind)
    formatters = _formatters(lang)
    page = {
        **context,
        "t": t,
        **formatters,
        "lang": lang,
        "dir": "rtl" if lang in RTL_LANGUAGES else "ltr",
        "start": "right" if lang in RTL_LANGUAGES else "left",
        "end": "left" if lang in RTL_LANGUAGES else "right",
        "store_name": settings.STORE_NAME,
        "support_email": settings.SUPPORT_EMAIL,
        "store_address": settings.STORE_ADDRESS,
        "frontend_url": settings.FRONTEND_URL.rstrip("/"),
        "year": datetime.now().year,
    }
    subject_key = context.get("subject_key", "subject")
    subject = str(t(subject_key, **context.get("subject_values", {}))).strip()
    html = _env.get_template(TEMPLATES[kind]).render(subject=subject, **page)
    return subject, html


async def send_email(to_email: str, kind: str, context: Dict[str, Any], language: Optional[str] = None) -> None:
    """Render an email in the customer's language and send it through Brevo; raises if Brevo refuses it."""
    subject, html = render_email(kind, context, language)
    from_raw = settings.BREVO_FROM_EMAIL
    if "<" in from_raw and ">" in from_raw:
        sender = {"name": from_raw.split("<")[0].strip(), "email": from_raw.split("<")[1].split(">")[0].strip()}
    else:
        sender = {"name": settings.STORE_NAME, "email": from_raw.strip()}

    payload = {"sender": sender, "to": [{"email": to_email.strip()}], "subject": subject, "htmlContent": html}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": settings.BREVO_API_KEY, "Content-Type": "application/json", "Accept": "application/json"},
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            if response.status not in (200, 201):
                raise RuntimeError(f"Brevo refused the {kind} email ({response.status}): {await response.text()}")
    logger.info(f"Sent {kind} email to {to_email} in {email_language(language)}")


class EmailService:
    """Sending the app's emails: sign-up and password-reset links, booking confirmations and cancellations.

    Mail goes out through Brevo when BREVO_API_KEY is set. Without a key, development just logs each email (with its
    link), so sign-up and password reset can be tried without an email account; in production a missing key is logged
    as an error. A failed send is logged and never fails the request that triggered it.
    """

    @staticmethod
    async def deliver(to_email: str, kind: str, context: Dict[str, Any], language: Optional[str] = None) -> None:
        """Send one email, or log it when no mail provider is configured. Never raises."""
        link = context.get("action_url", "")
        if not settings.BREVO_API_KEY:
            if settings.ENVIRONMENT.lower() == "production":
                logger.error(f"BREVO_API_KEY is not set: the {kind} email to {to_email} was not sent")
            else:
                logger.info(f"[dev] {kind} email to {to_email} not sent (no BREVO_API_KEY): {link}")
            return
        try:
            await send_email(to_email, kind, context, language)
        except Exception:
            logger.exception(f"Sending the {kind} email to {to_email} failed")

    @staticmethod
    def queue(background_tasks: Optional[BackgroundTasks], func, *args, **kwargs) -> None:
        """Run an email method after the response is sent. Without a background-tasks object, do nothing."""
        if background_tasks is not None:
            background_tasks.add_task(func, *args, **kwargs)

    @staticmethod
    async def send_verification(to_email: str, name: str, token: str, language: Optional[str], valid_hours: float = 0.25) -> None:
        """The link opens the API's verify-email route, which works without a frontend."""
        url = f"{settings.API_PUBLIC_URL.rstrip('/')}/v1/auth/verify-email/?token={token}"
        await EmailService.deliver(to_email, "verification", {"name": name, "action_url": url, "valid_hours": valid_hours}, language)

    @staticmethod
    async def send_password_reset(to_email: str, name: str, token: str, language: Optional[str], valid_hours: float = 0.25) -> None:
        """The link opens a reset page on the frontend, which posts the token and the new password to /auth/reset-password/."""
        url = f"{settings.FRONTEND_URL.rstrip('/')}/reset-password?token={token}"
        await EmailService.deliver(to_email, "password_reset", {"name": name, "action_url": url, "valid_hours": valid_hours}, language)

    @staticmethod
    async def send_booking_confirmed(
        to_email: str, name: str, language: Optional[str], *, booking_id, booking_code: str, hotel_name: str,
        room_label: str, check_in: date, check_out: date, guests: int, total: int, currency: str,
    ) -> None:
        context = {
            "name": name, "booking_code": booking_code, "hotel_name": hotel_name, "room_label": room_label,
            "check_in": check_in, "check_out": check_out, "nights": (check_out - check_in).days, "guests": guests,
            "total": total, "currency": currency,
            "action_url": f"{settings.FRONTEND_URL.rstrip('/')}/bookings/{booking_id}",
            "subject_values": {"code": booking_code},
        }
        await EmailService.deliver(to_email, "booking_confirmation", context, language)

    @staticmethod
    async def send_booking_cancelled(
        to_email: str, name: str, language: Optional[str], *, booking_id, booking_code: str, hotel_name: str,
        check_in: date, check_out: date, fee: int, refund: int, currency: str,
    ) -> None:
        context = {
            "name": name, "booking_code": booking_code, "hotel_name": hotel_name, "check_in": check_in,
            "check_out": check_out, "fee": fee, "refund": refund, "currency": currency,
            "action_url": f"{settings.FRONTEND_URL.rstrip('/')}/bookings/{booking_id}",
            "subject_values": {"code": booking_code},
        }
        await EmailService.deliver(to_email, "booking_cancelled", context, language)
