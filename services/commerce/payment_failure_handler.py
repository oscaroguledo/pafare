"""Payment failure handling: standardized failure categorization and recovery metadata."""
from typing import Any, Dict, Optional
from datetime import datetime, timezone
from core.logging import get_structured_logger

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models.commerce.payments import PaymentIntent, PaymentFailureReason

logger = get_structured_logger(__name__)


class PaymentFailureHandler:
    """Handles payment failures by updating payment intent and returning guidance."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def handle_failure(
        self,
        payment_intent_id: str,
        stripe_error: Optional[Dict[str, Any]] = None,
        failure_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Handle payment failure and return user guidance"""
        try:
            result = await self.db.execute(
                select(PaymentIntent).where(PaymentIntent.id == payment_intent_id)
            )
            payment_intent = result.scalar_one_or_none()

            if not payment_intent:
                return {
                    "status": "not_found",
                    "user_message": "Payment record not found.",
                    "next_steps": "Please try again.",
                }

            reason = self._map_failure_reason(stripe_error)
            user_message, next_steps = self._user_guidance(reason)

            payment_intent.status = "failed"
            payment_intent.failed_at = datetime.now(timezone.utc)
            payment_intent.failure_reason = reason.value
            payment_intent.payment_intent_metadata = {
                **(payment_intent.payment_intent_metadata or {}),
                "stripe_error": stripe_error,
                "failure_context": failure_context,
                "failure_reason": reason.value,
            }

            await self.db.commit()

            return {
                "status": "failed",
                "failure_reason": reason.value,
                "user_message": user_message,
                "next_steps": next_steps,
            }
        except Exception as e:
            logger.error(f"Payment failure handling error: {e}")
            return {
                "status": "error",
                "user_message": "Payment failed. Please try again.",
                "next_steps": "Try a different card or contact support.",
            }

    def _map_failure_reason(self, stripe_error: Optional[Dict[str, Any]]) -> PaymentFailureReason:
        """Map Stripe error to payment failure reason"""
        if not stripe_error:
            return PaymentFailureReason.UNKNOWN

        code = (stripe_error.get("code") or "").lower()
        decline_code = (stripe_error.get("decline_code") or "").lower()
        message = (stripe_error.get("message") or "").lower()

        if "insufficient_funds" in decline_code or "insufficient" in message:
            return PaymentFailureReason.INSUFFICIENT_FUNDS
        if "expired" in code or "expired" in message:
            return PaymentFailureReason.EXPIRED_CARD
        if "authentication_required" in code or "authentication" in message:
            return PaymentFailureReason.AUTHENTICATION_REQUIRED
        if "fraud" in decline_code or "fraud" in message:
            return PaymentFailureReason.FRAUD_SUSPECTED
        if "limit" in decline_code or "limit" in message:
            return PaymentFailureReason.LIMIT_EXCEEDED
        if "network" in message:
            return PaymentFailureReason.NETWORK_ERROR
        if "processing" in message:
            return PaymentFailureReason.PROCESSING_ERROR
        if "declined" in decline_code or "declined" in message:
            return PaymentFailureReason.CARD_DECLINED
        if "invalid" in code or "invalid" in message:
            return PaymentFailureReason.INVALID_CARD

        return PaymentFailureReason.UNKNOWN

    def _user_guidance(self, reason: PaymentFailureReason) -> tuple[str, str]:
        """Get user-friendly message and next steps for failure reason"""
        mapping = {
            PaymentFailureReason.INSUFFICIENT_FUNDS: (
                "Payment failed due to insufficient funds.",
                "Try another card or use a different payment method."
            ),
            PaymentFailureReason.CARD_DECLINED: (
                "Your card was declined.",
                "Try another card or contact your bank."
            ),
            PaymentFailureReason.EXPIRED_CARD: (
                "Your card is expired.",
                "Use a valid card with a future expiry date."
            ),
            PaymentFailureReason.INVALID_CARD: (
                "The card details are invalid.",
                "Check the card number, expiry, and CVC."
            ),
            PaymentFailureReason.AUTHENTICATION_REQUIRED: (
                "Additional authentication is required.",
                "Complete the authentication or use another card."
            ),
            PaymentFailureReason.PROCESSING_ERROR: (
                "Payment processing failed.",
                "Please try again in a few minutes."
            ),
            PaymentFailureReason.NETWORK_ERROR: (
                "Network error during payment.",
                "Please try again shortly."
            ),
            PaymentFailureReason.FRAUD_SUSPECTED: (
                "Payment was blocked for security reasons.",
                "Use a different payment method or contact support."
            ),
            PaymentFailureReason.LIMIT_EXCEEDED: (
                "Payment exceeds card limits.",
                "Try another card or reduce the amount."
            ),
            PaymentFailureReason.UNKNOWN: (
                "Payment failed.",
                "Please try again or use a different card."
            ),
        }
        return mapping.get(reason, mapping[PaymentFailureReason.UNKNOWN])
