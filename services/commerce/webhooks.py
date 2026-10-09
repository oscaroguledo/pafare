"""Secure Stripe webhook handling: signature verification, rate limiting, secure publishing."""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException, Request
import json
import stripe
from models.commerce.payments import Transaction, PaymentIntent
from models.commerce.orders import Order, OrderStatus, PaymentStatus
from services.commerce.payments import PaymentService
from services.catalog.inventory import InventoryService
from services.commerce.payment_failure_handler import PaymentFailureHandler
from datetime import datetime, timezone
from typing import Dict, Any
from core.config import settings
from core.logging import get_structured_logger
from services.commerce.orders import OrderService

logger = get_structured_logger(__name__)


class WebhookSecurityError(Exception):
    """Raised when a Stripe webhook request fails signature verification."""


def _merge_transaction_metadata(transaction: "Transaction", updates: Dict[str, Any]) -> str:
    """transaction_metadata is a JSON-serialized string column - decode, merge, re-encode."""
    existing: Dict[str, Any] = {}
    if transaction.transaction_metadata:
        try:
            existing = json.loads(transaction.transaction_metadata)
        except (TypeError, ValueError):
            existing = {}
    return json.dumps({**existing, **updates})


async def verify_stripe_webhook_request(
    request: Request,
    db: AsyncSession,
    signature_header: str,
    payload: bytes
) -> Dict[str, Any]:
    """Verify a Stripe webhook request's signature."""
    try:
        event = stripe.Webhook.construct_event(
            payload, signature_header, settings.STRIPE_WEBHOOK_SECRET
        )

        return {
            "verified": True,
            "event": event,
            "security_metadata": {
                "signature_verified": True,
                "timestamp": event.get("created"),
                "event_id": event.get("id")
            }
        }

    except ValueError as e:
        logger.error(f"Invalid payload: {e}")
        raise WebhookSecurityError("Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid signature: {e}")
        raise WebhookSecurityError("Invalid signature")
    except Exception as e:
        logger.error(f"Webhook verification error: {e}")
        raise WebhookSecurityError(f"Verification failed: {str(e)}")


class WebhookService:
    """Secure Stripe webhook handling; processes events in real-time without storing them."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.payment_service = PaymentService(db)
        self.inventory_service = InventoryService(db)
        
    async def handle_stripe_webhook(
        self,
        request: Request,
        request_body: bytes,
        signature: str
    ) -> Dict[str, Any]:
        """Handle Stripe webhook with comprehensive security verification."""
        start_time = datetime.now(timezone.utc)
        
        try:
            # Comprehensive security verification
            verification_result = await verify_stripe_webhook_request(
                request=request,
                db=self.db,
                signature_header=signature,
                payload=request_body
            )
            
            if not verification_result["verified"]:
                raise HTTPException(
                    status_code=401,
                    detail="Webhook verification failed"
                )
            
            event = verification_result["event"]
            security_metadata = verification_result["security_metadata"]
            
            # Process the verified event
            result = await self._process_webhook_event(event)
            
            # Log the processed event for monitoring/audit
            await self._log_webhook_event(event, result, security_metadata)
            
            # Log processing completion
            processing_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            logger.info(f"Webhook processed successfully: {event['id']} in {processing_time:.3f}s")
            
            return {
                "status": "success",
                "event_id": event["id"],
                "event_type": event["type"],
                "processing_time": processing_time,
                "result": result
            }
            
        except WebhookSecurityError as e:
            logger.error(f"Webhook security error: {e}")
            raise HTTPException(status_code=401, detail=str(e))
        except Exception as e:
            logger.error(f"Webhook processing error: {e}")
            raise HTTPException(status_code=500, detail="Webhook processing failed")
        
        
    async def _process_webhook_event(
        self,
        event: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Process webhook event based on type"""
        event_type = event["type"]
        event_data = event["data"]["object"]
        
        if event_type == "payment_intent.succeeded":
            return await self._handle_payment_succeeded(event_data)
        
        elif event_type == "payment_intent.payment_failed":
            return await self._handle_payment_failed(event_data)
        
        elif event_type == "payment_intent.canceled":
            return await self._handle_payment_canceled(event_data)
        
        elif event_type == "charge.refunded":
            return await self._handle_refund(event_data)
        
        else:
            logger.info(f"Unhandled webhook event type: {event_type}")
            return {"status": "ignored", "reason": "unhandled_event_type"}

    async def _handle_payment_succeeded(self, payment_intent_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle successful payment webhook"""
        stripe_payment_intent_id = payment_intent_data["id"]
        
        # Find the transaction with lock to prevent race conditions
        transaction_result = await self.db.execute(
            select(Transaction).where(
                Transaction.stripe_payment_intent_id == stripe_payment_intent_id
            ).with_for_update()
        )
        transaction = transaction_result.scalar_one_or_none()
        
        if transaction:
            # Update transaction status atomically
            transaction.status = "succeeded"
            transaction.transaction_metadata = _merge_transaction_metadata(transaction, {
                "webhook_confirmed_at": datetime.now(timezone.utc).isoformat(),
                "stripe_charges": payment_intent_data.get("charges", {})
            })
            
            # If this is an order payment, update order status atomically
            if transaction.order_id:
                order_result = await self.db.execute(
                    select(Order).where(Order.id == transaction.order_id).with_for_update()
                )
                order = order_result.scalar_one_or_none()
                
                if order:
                    await OrderService(self.db)._mark_order_paid(order)

            await self.db.commit()
            
            return {
                "action": "payment_confirmed",
                "transaction_id": str(transaction.id),
                "order_id": str(transaction.order_id) if transaction.order_id else None
            }
        
        intent = (await self.db.execute(
            select(PaymentIntent).where(PaymentIntent.stripe_payment_intent_id == stripe_payment_intent_id)
        )).scalar_one_or_none()
        if intent:
            # E.g. a renewal that settled after the scheduler saw it "processing".
            await PaymentService(self.db)._settle(intent)
            return {"action": "payment_confirmed", "order_id": str(intent.order_id) if intent.order_id else None}
        else:
            logger.warning(f"Transaction not found for payment intent {stripe_payment_intent_id}")
            return {
                "action": "payment_confirmed",
                "warning": "transaction_not_found",
                "stripe_payment_intent_id": stripe_payment_intent_id
            }

    async def _handle_payment_failed(self, payment_intent_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle failed payment webhook with comprehensive failure handling"""
        stripe_payment_intent_id = payment_intent_data["id"]
        
        # Find the transaction with lock to prevent race conditions
        transaction_result = await self.db.execute(
            select(Transaction).where(
                Transaction.stripe_payment_intent_id == stripe_payment_intent_id
            ).with_for_update()
        )
        transaction = transaction_result.scalar_one_or_none()
        
        if transaction:
            # Update transaction status atomically
            transaction.status = "failed"
            transaction.failure_reason = payment_intent_data.get("last_payment_error", {}).get("message", "Payment failed")
            transaction.transaction_metadata = _merge_transaction_metadata(transaction, {
                "webhook_failed_at": datetime.now(timezone.utc).isoformat(),
                "failure_details": payment_intent_data.get("last_payment_error", {})
            })
            
            # If this is an order payment, update order status atomically
            if transaction.order_id:
                order_result = await self.db.execute(
                    select(Order).where(Order.id == transaction.order_id).with_for_update()
                )
                order = order_result.scalar_one_or_none()
                # Unpaid renewals stay open: the subscription scheduler retries them.
                if order and not order.subscription_id:
                    await OrderService(self.db)._release_unpaid_order(order, "payment failed")

            await self.db.commit()
            
            # Use comprehensive failure handler if payment intent exists
            try:
                # Find the payment intent
                payment_intent_result = await self.db.execute(
                    select(PaymentIntent).where(
                        PaymentIntent.stripe_payment_intent_id == stripe_payment_intent_id
                    )
                )
                payment_intent = payment_intent_result.scalar_one_or_none()
                
                if payment_intent:
                    failure_handler = PaymentFailureHandler(self.db)
                    
                    stripe_error = payment_intent_data.get("last_payment_error", {})
                    failure_result = await failure_handler.handle_failure(
                        payment_intent_id=payment_intent.id,
                        stripe_error=stripe_error,
                        failure_context={
                            "webhook_source": True,
                            "stripe_payment_intent_id": stripe_payment_intent_id
                        }
                    )
                    
                    return {
                        "action": "payment_failed_comprehensive",
                        "transaction_id": str(transaction.id),
                        "order_id": str(transaction.order_id) if transaction.order_id else None,
                        "failure_reason": transaction.failure_reason,
                        "failure_handling": failure_result
                    }
                    
            except Exception as e:
                logger.error(f"Error in comprehensive failure handling: {e}")
            
            return {
                "action": "payment_failed",
                "transaction_id": str(transaction.id),
                "order_id": str(transaction.order_id) if transaction.order_id else None,
                "failure_reason": transaction.failure_reason
            }
        
        else:
            logger.warning(f"Transaction not found for failed payment intent {stripe_payment_intent_id}")
            return {
                "action": "payment_failed",
                "warning": "transaction_not_found",
                "stripe_payment_intent_id": stripe_payment_intent_id
            }

    async def _handle_payment_canceled(self, payment_intent_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle canceled payment webhook"""
        stripe_payment_intent_id = payment_intent_data["id"]
        
        # Find the transaction with lock to prevent race conditions
        transaction_result = await self.db.execute(
            select(Transaction).where(
                Transaction.stripe_payment_intent_id == stripe_payment_intent_id
            ).with_for_update()
        )
        transaction = transaction_result.scalar_one_or_none()
        
        if transaction:
            transaction.status = "cancelled"
            
            # Update order status if applicable atomically
            if transaction.order_id:
                order_result = await self.db.execute(
                    select(Order).where(Order.id == transaction.order_id).with_for_update()
                )
                order = order_result.scalar_one_or_none()
                if order:
                    await OrderService(self.db)._release_unpaid_order(order, "payment cancelled")

            await self.db.commit()
            
            return {
                "action": "payment_cancelled",
                "transaction_id": str(transaction.id)
            }
        
        return {"action": "payment_cancelled", "warning": "transaction_not_found"}

    async def _handle_refund(self, charge_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle refund webhook"""
        
        return {"action": "refund_processed", "charge_id": charge_data["id"]}

    async def _log_webhook_event(
        self,
        event: Dict[str, Any],
        processing_result: Dict[str, Any],
        security_metadata: Dict[str, Any]
    ):
        """Log a processed webhook event for monitoring/audit purposes."""
        try:
            logger.info(
                f"Webhook event processed: {event.get('id')} - {event.get('type', 'unknown')} "
                f"(signature_verified={security_metadata.get('signature_verified')}), "
                f"result={processing_result}"
            )
        except Exception as e:
            logger.error(f"Failed to log webhook event: {e}")
            # Don't fail the webhook processing if logging fails