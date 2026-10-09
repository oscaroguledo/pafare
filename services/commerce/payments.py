# Consolidated payment service with integrated failure handling
# This file includes all payment-related functionality including failure handling

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, String
from sqlalchemy.orm import selectinload
from fastapi import HTTPException
from models.commerce.payments import PaymentMethod, PaymentIntent, Transaction, PaymentFailureReason
from models.accounts.user import User
from uuid import UUID
from core.utils.uuid_utils import uuid7
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from core.config import settings
from core.logging import get_structured_logger
import stripe
import json
from decimal import Decimal
import time
import asyncio
from models.commerce.payments import CardBrand
from models.commerce.orders import Order
from services.commerce.payment_failure_handler import PaymentFailureHandler
from typing import Optional, List, Dict, Any

# Configure Stripe
stripe.api_key = getattr(settings, 'STRIPE_SECRET_KEY', '')
# Retry dropped connections and timeouts; requests carry idempotency keys, so a retry never charges twice.
stripe.max_network_retries = 2

logger = get_structured_logger(__name__)


class PaymentService:
    """Consolidated payment service with comprehensive payment management"""
    
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_method(
        self,
        user_id: UUID,
        stripe_payment_method_id: str,
        is_default: bool = False,
        payment_method_metadata: Optional[Dict] = None
    ) -> PaymentMethod:
        """Save a Stripe PaymentMethod for the user; card details are read from Stripe, never trusted from the client."""
        try:
            # Helper: normalize raw brand strings to DB enum values
            def _normalize_brand(raw_brand: str) -> str:
                if not raw_brand:
                    return CardBrand.UNKNOWN.value
                s = str(raw_brand).strip().lower()
                mapping = {
                    "visa": "visa",
                    "mastercard": "mastercard",
                    "master card": "mastercard",
                    "american express": "amex",
                    "amex": "amex",
                    "discover": "discover",
                    "jcb": "jcb",
                    "diners": "diners_club",
                    "diners_club": "diners_club",
                    "unionpay": "unionpay",
                    "verve": "verve",
                }
                return mapping.get(s, CardBrand.OTHER.value)

            # Handle modern payment method API
            if stripe_payment_method_id:
                # Get payment method details from Stripe
                stripe_pm = await asyncio.to_thread(stripe.PaymentMethod.retrieve, stripe_payment_method_id)

                user = await self.db.get(User, user_id)
                if not user:
                    raise HTTPException(status_code=404, detail="User not found")
                await self._attach_card(user, stripe_payment_method_id)

                # A customer's first card becomes their default, so renewals always have a card to charge.
                if not is_default:
                    has_default = await self.db.scalar(select(PaymentMethod.id).where(
                        PaymentMethod.user_id == user_id, PaymentMethod.is_default == True, PaymentMethod.is_active == True
                    ).limit(1))
                    is_default = has_default is None

                # If this is set as default, unset other defaults atomically
                if is_default:
                    # Get existing default payment methods with lock
                    existing_defaults = await self.db.execute(
                        select(PaymentMethod).where(
                            and_(PaymentMethod.user_id == user_id, PaymentMethod.is_default == True)
                        ).with_for_update()
                    )
                    
                    # Update them to not be default
                    for pm in existing_defaults.scalars().all():
                        pm.is_default = False

                # Prepare payment_method instance
                payment_method = PaymentMethod(
                    user_id=user_id,
                    type=stripe_pm.type,
                    provider="stripe",
                    stripe_payment_method_id=stripe_payment_method_id,
                    is_default=is_default,
                    is_active=True
                )
                # Determine brand value normalized to DB enum
                try:
                    brand_raw = stripe_pm.card.brand if getattr(stripe_pm, 'card', None) else None
                    brand_value = _normalize_brand(brand_raw)
                except Exception:
                    brand_value = CardBrand.UNKNOWN.value
                
                # Set card-specific details if it's a card
                if stripe_pm.type == "card" and stripe_pm.card:
                    payment_method.last_four = stripe_pm.card.last4
                    payment_method.expiry_month = stripe_pm.card.exp_month
                    payment_method.expiry_year = stripe_pm.card.exp_year
                    payment_method.brand = brand_value
            
            else:
                raise HTTPException(
                    status_code=400, 
                    detail="stripe_payment_method_id is required"
                )
            
            # Pre-check for existing payment method with same Stripe id to avoid unique constraint errors
            existing_pm = None
            pm_lookup_id = None
            pm_lookup_id = stripe_payment_method_id

            if pm_lookup_id:
                existing = await self.db.execute(
                    select(PaymentMethod).where(PaymentMethod.stripe_payment_method_id == pm_lookup_id)
                )
                existing_pm = existing.scalar_one_or_none()

            if existing_pm:
                # If existing belongs to same user, reuse and update default flag if requested
                if existing_pm.user_id == user_id:
                    if is_default:
                        ed = await self.db.execute(
                            select(PaymentMethod).where(
                                and_(PaymentMethod.user_id == user_id, PaymentMethod.is_default == True)
                            ).with_for_update()
                        )
                        for pm in ed.scalars().all():
                            pm.is_default = False
                        existing_pm.is_default = True
                        await self.db.commit()
                    return existing_pm
                # Owned by different user — conflict
                raise HTTPException(status_code=409, detail="Stripe payment method already associated with another account")

            # Attach metadata (e.g. cardholder name) before saving
            if payment_method_metadata:
                payment_method.payment_method_metadata = payment_method_metadata

            # Try to insert; if a payment method with same stripe id exists, return it instead
            try:
                self.db.add(payment_method)
                await self.db.commit()
                await self.db.refresh(payment_method)
                return payment_method
            except Exception as ie:
                # Handle integrity/unique-constraint failures robustly across dialects
                msg = str(ie).lower()
                if 'duplicate key' in msg or 'uniqueviol' in msg or 'unique constraint' in msg:
                    await self.db.rollback()
                    try:
                        existing = await self.db.execute(
                            select(PaymentMethod).where(PaymentMethod.stripe_payment_method_id == stripe_payment_method_id)
                        )
                        existing_pm = existing.scalar_one_or_none()
                        if existing_pm:
                            # If the existing payment method belongs to the same user, reuse it
                            if existing_pm.user_id == user_id:
                                if is_default:
                                    ed = await self.db.execute(
                                        select(PaymentMethod).where(
                                            and_(PaymentMethod.user_id == user_id, PaymentMethod.is_default == True)
                                        ).with_for_update()
                                    )
                                    for pm in ed.scalars().all():
                                        pm.is_default = False
                                    existing_pm.is_default = True
                                    await self.db.commit()
                                return existing_pm
                            # If it belongs to a different user, do not return it — signal conflict
                            raise HTTPException(status_code=409, detail="Stripe payment method already associated with another account")
                    except HTTPException:
                        # Let our own deliberate 409 above propagate - it must not be swallowed
                        # by the broad `except Exception: pass` below, which would otherwise leak a raw IntegrityError as a 500 instead.
                        raise
                    except Exception:
                        pass
                # If not an integrity/unique error or we couldn't handle it, re-raise
                raise
            
        except stripe.error.StripeError as e:
            raise HTTPException(status_code=400, detail=f"Stripe error: {str(e)}")
        except HTTPException:
            # Re-raise HTTPExceptions (like 409 conflicts) unchanged so callers receive correct status
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to create payment method: {str(e)}")

    async def get(self, payment_method_id: UUID, user_id: UUID) -> Optional[PaymentMethod]:
        """Get a specific payment method by ID - excludes soft-deleted methods, matching list()."""
        result = await self.db.execute(
            select(PaymentMethod).where(
                PaymentMethod.id == payment_method_id,
                PaymentMethod.user_id == user_id,
                PaymentMethod.is_active == True
            )
        )
        return result.scalar_one_or_none()

    async def list(self, user_id: UUID, page: int = 1, limit: int = 10, search: Optional[str] = None) -> Dict[str, Any]:
        """Get all payment methods for a user with pagination and search"""
        offset = (page - 1) * limit

        # Build base query with optional search
        base_conditions = [PaymentMethod.user_id == user_id, PaymentMethod.is_active == True]
        if search:
            search_pattern = f"%{search}%"
            base_conditions.append(
                or_(
                    PaymentMethod.last_four.ilike(search_pattern),
                    PaymentMethod.provider.cast(String).ilike(search_pattern),
                    PaymentMethod.type.cast(String).ilike(search_pattern),
                    PaymentMethod.brand.cast(String).ilike(search_pattern)
                )
            )

        result = await self.db.execute(
            select(PaymentMethod).where(and_(*base_conditions))
            .order_by(PaymentMethod.is_default.desc(), PaymentMethod.created_at.desc())
            .offset(offset).limit(limit)
        )
        payment_methods = result.scalars().all()

        # Get total count
        count_result = await self.db.execute(
            select(func.count()).select_from(PaymentMethod).where(and_(*base_conditions))
        )
        total = count_result.scalar() or 0

        return {
            "data": payment_methods,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit
            }
        }

    async def update(self, payment_method_id: UUID, user_id: UUID, update_data: Dict[str, Any]) -> Optional[PaymentMethod]:
        """Update a payment method"""
        try:
            result = await self.db.execute(
                select(PaymentMethod).where(
                    and_(
                        PaymentMethod.id == payment_method_id,
                        PaymentMethod.user_id == user_id,
                        PaymentMethod.is_active == True
                    )
                ).with_for_update()
            )
            payment_method = result.scalar_one_or_none()
            
            if not payment_method:
                return None
            
            allowed_fields = ['expiry_month', 'expiry_year']
            for field, value in update_data.items():
                if field in allowed_fields and hasattr(payment_method, field):
                    setattr(payment_method, field, value)
            
            await self.db.commit()
            await self.db.refresh(payment_method)
            return payment_method
            
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to update payment method: {str(e)}")

    async def delete(self, payment_method_id: UUID, user_id: UUID) -> bool:
        """Delete a payment method"""
        result = await self.db.execute(
            select(PaymentMethod).where(
                and_(
                    PaymentMethod.id == payment_method_id,
                    PaymentMethod.user_id == user_id
                )
            )
        )
        payment_method = result.scalar_one_or_none()
        
        if not payment_method:
            raise HTTPException(status_code=404, detail="Payment method not found")
        
        try:
            if payment_method.stripe_payment_method_id:
                try:
                    stripe_pm = await asyncio.to_thread(stripe.PaymentMethod.retrieve, payment_method.stripe_payment_method_id)
                    if getattr(stripe_pm, "customer", None):
                        await asyncio.to_thread(stripe.PaymentMethod.detach, payment_method.stripe_payment_method_id)
                except stripe.error.InvalidRequestError as detach_error:
                    message = str(detach_error).lower()
                    if "not attached" not in message:
                        raise
            
            payment_method.is_active = False
            if payment_method.is_default:
                # The newest remaining card takes over as default.
                payment_method.is_default = False
                successor = await self.db.scalar(
                    select(PaymentMethod).where(
                        PaymentMethod.user_id == user_id, PaymentMethod.is_active == True, PaymentMethod.id != payment_method.id
                    ).order_by(PaymentMethod.created_at.desc()).limit(1)
                )
                if successor:
                    successor.is_default = True
            await self.db.commit()
            return True

        except stripe.error.StripeError as e:
            raise HTTPException(status_code=400, detail=f"Stripe error: {str(e)}")

    async def set_default(self, payment_method_id: UUID, user_id: UUID) -> bool:
        """Set a payment method as default for the user"""
        try:
            result = await self.db.execute(
                select(PaymentMethod).where(
                    and_(
                        PaymentMethod.id == payment_method_id,
                        PaymentMethod.user_id == user_id,
                        PaymentMethod.is_active == True
                    )
                ).with_for_update()
            )
            new_default = result.scalar_one_or_none()
            
            if not new_default:
                return False
            
            existing_defaults = await self.db.execute(
                select(PaymentMethod).where(
                    and_(
                        PaymentMethod.user_id == user_id,
                        PaymentMethod.is_default == True,
                        PaymentMethod.is_active == True
                    )
                ).with_for_update()
            )
            
            for pm in existing_defaults.scalars().all():
                pm.is_default = False
            
            new_default.is_default = True
            await self.db.commit()
            return True
            
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to set default payment method: {str(e)}")


    async def confirm_intent(
        self,
        payment_intent_id: UUID,
        payment_method_id: str,
    ) -> PaymentIntent:
        """Confirm a payment intent on-session (the customer is present for any bank check)."""
        result = await self.db.execute(
            select(PaymentIntent).where(PaymentIntent.id == payment_intent_id).with_for_update()
        )
        payment_intent = result.scalar_one_or_none()
        
        if not payment_intent:
            raise HTTPException(status_code=404, detail="Payment intent not found")

        # Accept the app's saved-card id as well as a raw Stripe PaymentMethod id.
        try:
            saved = await self.db.get(PaymentMethod, UUID(str(payment_method_id)))
            if saved and saved.user_id == payment_intent.user_id:
                payment_method_id = saved.stripe_payment_method_id
        except ValueError:
            pass

        try:
            # Confirm with Stripe
            stripe_intent = await asyncio.to_thread(
                stripe.PaymentIntent.confirm,
                payment_intent.stripe_payment_intent_id,
                payment_method=payment_method_id
            )
            
            # Update our record atomically
            payment_intent.status = stripe_intent.status
            payment_intent.payment_method_id = payment_method_id
            
            if stripe_intent.status == "succeeded":
                await self._settle(payment_intent)
            else:
                if stripe_intent.status == "requires_action":
                    payment_intent.requires_action = True
                    payment_intent.client_secret = stripe_intent.client_secret
                await self.db.commit()
            await self.db.refresh(payment_intent)
            return payment_intent
            
        except stripe.error.StripeError as e:
            # Use comprehensive failure handler
            failure_handler = PaymentFailureHandler(self.db)
            
            # Update payment intent with basic failure info first
            payment_intent.status = "failed"
            payment_intent.failed_at = datetime.now(timezone.utc)
            payment_intent.failure_reason = str(e)
            
            await self.db.commit()
            # Handle failure comprehensively
            try:
                stripe_error_details = {
                    "code": getattr(e, 'code', None),
                    "decline_code": getattr(e, 'decline_code', None),
                    "type": getattr(e, 'type', None),
                    "message": str(e),
                    "param": getattr(e, 'param', None)
                }
                
                failure_result = await failure_handler.handle_failure(
                    payment_intent_id=payment_intent.id,
                    stripe_error=stripe_error_details,
                    failure_context={
                        "payment_method_id": payment_method_id,
                        "amount": payment_intent.amount_breakdown.get("total", 0),
                        "currency": payment_intent.currency
                    }
                )

            except Exception as handler_error:
                logger.error(f"Error in failure handler: {handler_error}")

            raise HTTPException(status_code=400, detail=f"Payment failed: {str(e)}")


    async def get_intent(self, payment_intent_id: UUID, user_id: UUID) -> Optional[PaymentIntent]:
        """Get a specific payment intent by ID"""
        result = await self.db.execute(
            select(PaymentIntent).where(
                PaymentIntent.id == payment_intent_id,
                PaymentIntent.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def list_intents(self, user_id: UUID, page: int = 1, limit: int = 20) -> Dict[str, Any]:
        """List payment intents for a user"""
        offset = (page - 1) * limit
        result = await self.db.execute(
            select(PaymentIntent).where(
                PaymentIntent.user_id == user_id
            ).offset(offset).limit(limit).order_by(PaymentIntent.created_at.desc())
        )
        intents = result.scalars().all()

        # Get total count
        count_result = await self.db.execute(
            select(func.count()).select_from(PaymentIntent).where(PaymentIntent.user_id == user_id)
        )
        total = count_result.scalar() or 0
        
        return {
            "items": intents,
            "total": total,
            "page": page,
            "limit": limit
        }


    async def sync_intent(self, intent: PaymentIntent) -> PaymentIntent:
        """Refresh an in-flight intent from Stripe; a paid subscription renewal reactivates the subscription."""
        stripe_intent = await asyncio.to_thread(stripe.PaymentIntent.retrieve, intent.stripe_payment_intent_id)
        intent.status = stripe_intent.status
        intent.requires_action = stripe_intent.status == "requires_action"
        if stripe_intent.status == "succeeded":
            await self._settle(intent)
        else:
            await self.db.commit()
        await self.db.refresh(intent)
        return intent

    async def refresh_order_payment(self, order_id: UUID, cancel_if_unfinished: bool = False) -> str:
        """Sync an order's latest PaymentIntent (and its transactions) with Stripe and return the Stripe status.
        cancel_if_unfinished cancels an intent still waiting on the customer. The caller commits."""
        intent = (await self.db.execute(
            select(PaymentIntent).where(PaymentIntent.order_id == order_id).order_by(PaymentIntent.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        if not intent:
            return "canceled"
        stripe_intent = await asyncio.to_thread(stripe.PaymentIntent.retrieve, intent.stripe_payment_intent_id)
        if cancel_if_unfinished and stripe_intent.status in ("requires_action", "requires_payment_method", "requires_confirmation"):
            stripe_intent = await asyncio.to_thread(stripe.PaymentIntent.cancel, intent.stripe_payment_intent_id)
        intent.status = stripe_intent.status
        intent.requires_action = stripe_intent.status == "requires_action"
        if stripe_intent.status == "succeeded" and not intent.confirmed_at:
            intent.confirmed_at = datetime.now(timezone.utc)
        transactions = (await self.db.execute(
            select(Transaction).where(Transaction.stripe_payment_intent_id == intent.stripe_payment_intent_id)
        )).scalars().all()
        for transaction in transactions:
            transaction.status = stripe_intent.status
        await self.db.flush()
        return stripe_intent.status

    async def _attach_card(self, user: User, stripe_payment_method_id: str) -> str:
        """Make sure the user has a Stripe customer holding this card; returns the customer id."""
        async def new_customer() -> None:
            customer = await asyncio.to_thread(
                stripe.Customer.create, email=getattr(user, "email", None), name=getattr(user, "full_name", None)
            )
            user.stripe_customer_id = customer.id
            await self.db.commit()

        if user.stripe_customer_id:
            try:
                # Stripe still returns a deleted customer, flagged "deleted", instead of erroring.
                if (await asyncio.to_thread(stripe.Customer.retrieve, user.stripe_customer_id)).get("deleted"):
                    await new_customer()
            except stripe.error.InvalidRequestError as e:
                if "No such customer" not in str(e):
                    raise
                await new_customer()
        else:
            await new_customer()

        try:
            await asyncio.to_thread(stripe.PaymentMethod.attach, stripe_payment_method_id, customer=user.stripe_customer_id)
        except stripe.error.InvalidRequestError as e:
            # Stripe refuses to re-attach a card the customer already holds; that's fine.
            message = str(e).lower()
            if "no such customer" in message:
                await new_customer()
                await asyncio.to_thread(stripe.PaymentMethod.attach, stripe_payment_method_id, customer=user.stripe_customer_id)
            elif "already" not in message:
                raise
        return user.stripe_customer_id

    async def charge_saved_card(self, order: Order, card: PaymentMethod, attempt: int) -> PaymentIntent:
        """Charge a saved card while the customer is away (subscription renewals); retries reuse the order's Stripe intent.
        Returns our intent row: 'succeeded', 'processing' or 'failed'. Other Stripe errors are raised. The caller commits."""
        intent = (await self.db.execute(
            select(PaymentIntent).where(PaymentIntent.order_id == order.id).order_by(PaymentIntent.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        if intent:
            # An earlier run may have been charged already (e.g. it stopped before saving the result).
            current = await asyncio.to_thread(stripe.PaymentIntent.retrieve, intent.stripe_payment_intent_id)
            if current.status in ("succeeded", "processing"):
                intent.status = current.status
                return intent

        user = await self.db.get(User, order.user_id)
        customer_id = await self._attach_card(user, card.stripe_payment_method_id)
        key = f"renewal_{order.id}_{attempt}"
        error = None
        try:
            if intent:
                stripe_intent = await asyncio.to_thread(
                    stripe.PaymentIntent.confirm, intent.stripe_payment_intent_id,
                    payment_method=card.stripe_payment_method_id, off_session=True, idempotency_key=key,
                )
            else:
                stripe_intent = await asyncio.to_thread(
                    stripe.PaymentIntent.create,
                    amount=int((Decimal(str(order.total_amount)) * 100).quantize(Decimal("1"))),
                    currency=order.currency.lower(), customer=customer_id,
                    payment_method=card.stripe_payment_method_id, off_session=True, confirm=True,
                    # Cards only, no redirects: the customer's "Pay again" confirms this same intent later.
                    automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
                    metadata={"order_id": str(order.id), "subscription_id": str(order.subscription_id)},
                    idempotency_key=key,
                )
        except stripe.error.CardError as e:
            if not e.error or not e.error.payment_intent:
                raise
            stripe_intent, error = e.error.payment_intent, e

        if not intent:
            intent = PaymentIntent(
                id=uuid7(), stripe_payment_intent_id=stripe_intent["id"], user_id=order.user_id,
                order_id=order.id, subscription_id=order.subscription_id,
                amount_breakdown={"total": float(order.total_amount), "currency": order.currency}, currency=order.currency,
            )
            self.db.add(intent)
        intent.payment_method_id = card.stripe_payment_method_id
        intent.payment_method_type = getattr(card.type, "value", card.type)
        if error:
            reason = PaymentFailureHandler(self.db)._map_failure_reason(
                {"code": error.code, "decline_code": getattr(error, "decline_code", None), "message": error.user_message or str(error)}
            )
            intent.status = "failed"
            intent.failed_at = datetime.now(timezone.utc)
            intent.failure_reason = reason.value
            intent.requires_action = reason == PaymentFailureReason.AUTHENTICATION_REQUIRED
        else:
            intent.status = stripe_intent["status"]
            intent.failed_at = intent.failure_reason = None
            intent.requires_action = False
        return intent

    async def _settle(self, intent: PaymentIntent) -> None:
        """Record a succeeded intent's payment once and confirm its order (a renewal also moves its subscription on). Commits."""
        # orders.py imports this module, so import it here.
        from services.commerce.orders import OrderService
        intent.status = "succeeded"
        intent.requires_action = False
        intent.confirmed_at = intent.confirmed_at or datetime.now(timezone.utc)
        payments = (await self.db.execute(select(Transaction).where(
            Transaction.stripe_payment_intent_id == intent.stripe_payment_intent_id, Transaction.transaction_type == "payment"
        ))).scalars().all()
        for transaction in payments:
            transaction.status = "succeeded"
        if not payments:
            self.db.add(Transaction(
                user_id=intent.user_id, order_id=intent.order_id, payment_intent_id=intent.id,
                stripe_payment_intent_id=intent.stripe_payment_intent_id,
                amount=intent.amount_breakdown.get("total", 0), currency=intent.currency,
                status="succeeded", transaction_type="payment", description="Payment processed successfully",
            ))
        order = await self.db.get(Order, intent.order_id) if intent.order_id else None
        if order:
            await OrderService(self.db)._mark_order_paid(order)
        else:
            await self.db.commit()

    async def process_idempotent(
        self,
        user_id: UUID,
        order_id: UUID,
        amount: float,
        payment_method_id: UUID,
        idempotency_key: str,
        request_id: Optional[str] = None,
        frontend_calculated_amount: Optional[float] = None  # Amount calculated by frontend
    ) -> Dict[str, Any]:
        """Process payment idempotently, validating frontend prices against backend calculations."""
        start_time = time.time()
        
        if not request_id:
            request_id = str(uuid7())
        
        try:
            # Validate frontend price against backend calculation
            if frontend_calculated_amount is not None:
                price_difference = abs(amount - frontend_calculated_amount)
                if price_difference > 0.01:  # Allow 1 cent tolerance for rounding
                    raise HTTPException(
                        status_code=400, 
                        detail=f"Price mismatch: frontend calculated {frontend_calculated_amount}, backend calculated {amount}"
                    )
            
            # Check for existing transaction with idempotency key
            existing_result = await self.db.execute(
                select(Transaction).where(
                    Transaction.idempotency_key == idempotency_key
                )
            )
            existing_transaction = existing_result.scalar_one_or_none()
            
            if existing_transaction:
                logger.info(f"Returning cached payment result for {idempotency_key}")
                return {
                    "status": existing_transaction.status,
                    "transaction_id": str(existing_transaction.id),
                    "cached": True,
                    "amount": existing_transaction.amount
                }
            
            # Get payment method with validation
            pm_result = await self.db.execute(
                select(PaymentMethod).where(
                    and_(
                        PaymentMethod.id == payment_method_id,
                        PaymentMethod.user_id == user_id,
                        PaymentMethod.is_active == True
                    )
                )
            )
            payment_method = pm_result.scalar_one_or_none()
            
            if not payment_method:
                raise HTTPException(status_code=404, detail="Payment method not found")

            user = await self.db.get(User, user_id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            await self._attach_card(user, payment_method.stripe_payment_method_id)

            # Create Stripe payment intent with idempotency key
            stripe_intent = await asyncio.to_thread(
                stripe.PaymentIntent.create,
                amount=int(amount * 100),  # Convert to cents
                currency=settings.STORE_CURRENCY,
                idempotency_key=idempotency_key,  # Stripe-level deduplication
                customer=user.stripe_customer_id,
                automatic_payment_methods={
                    "enabled": True,
                    "allow_redirects": "never"
                },
                metadata={
                    "order_id": str(order_id),
                    "user_id": str(user_id),
                    "request_id": request_id
                }
            )

            # Create PaymentIntent record for admin visibility
            payment_intent_record = PaymentIntent(
                id=uuid7(),
                stripe_payment_intent_id=stripe_intent.id,
                user_id=user_id,
                order_id=order_id,
                amount_breakdown={"total": amount, "currency": settings.STORE_CURRENCY},
                currency=settings.STORE_CURRENCY,
                status=stripe_intent.status,
                payment_method_id=payment_method.stripe_payment_method_id,
                payment_method_type=payment_method.type,
                requires_action=stripe_intent.status == "requires_action",
                client_secret=stripe_intent.client_secret,
                payment_intent_metadata={
                    "request_id": request_id,
                    "idempotency_key": idempotency_key
                }
            )
            self.db.add(payment_intent_record)
            
            # Confirm payment
            try:
                confirmed = await asyncio.to_thread(
                    stripe.PaymentIntent.confirm,
                    stripe_intent.id,
                    payment_method=payment_method.stripe_payment_method_id,
                    idempotency_key=f"{idempotency_key}:confirm"  # Separate idempotency for confirm
                )
            except stripe.error.StripeError as e:
                message = str(e).lower()
                if "previously used" in message and "may not be used again" in message:
                    payment_method.is_active = False
                    await self.db.commit()
                    raise HTTPException(
                        status_code=400,
                        detail="Payment method is no longer usable. Please add a new card and try again."
                    )
                raise
            
            # Create transaction record with idempotency key
            transaction = Transaction(
                user_id=user_id,
                order_id=order_id,
                payment_intent_id=None,
                stripe_payment_intent_id=stripe_intent.id,
                amount=amount,
                currency=settings.STORE_CURRENCY,
                status=confirmed.status,
                transaction_type="payment",
                idempotency_key=idempotency_key,  # Store for deduplication
                request_id=request_id,
                transaction_metadata=json.dumps({
                    "stripe_request_id": getattr(confirmed, 'request_id', None),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payment_method_type": str(payment_method.type),
                    "frontend_amount": frontend_calculated_amount,
                    "price_validated": frontend_calculated_amount is not None
                })
            )

            # Update PaymentIntent record status
            payment_intent_record.status = confirmed.status
            payment_intent_record.confirmed_at = datetime.now(timezone.utc) if confirmed.status == "succeeded" else None
            if confirmed.status == "requires_action":
                payment_intent_record.requires_action = True
            
            self.db.add(transaction)
            await self.db.commit()
            await self.db.refresh(transaction)
            
            return {
                "status": confirmed.status,
                "transaction_id": str(transaction.id),
                "cached": False,
                "amount": amount,
                "client_secret": confirmed.client_secret if confirmed.status == "requires_action" else None,
                "processing_time_ms": (time.time() - start_time) * 1000
            }
            
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error for idempotency key {idempotency_key}: {e}")
            raise HTTPException(status_code=400, detail=f"Payment failed: {str(e)}")

        except HTTPException:
            # Preserve intentional status codes raised above (price mismatch,
            # missing payment method) instead of masking them as a 500 below.
            raise
        except Exception as e:
            logger.error(f"Payment processing error for idempotency key {idempotency_key}: {e}")
            raise HTTPException(status_code=500, detail=f"Payment processing failed: {str(e)}")

    async def transactions(
        self,
        user_id: UUID,
        page: int = 1,
        limit: int = 20
    ) -> Dict[str, Any]:
        """Get user transaction history"""
        offset = (page - 1) * limit


        query = select(Transaction).where(Transaction.user_id == user_id).options(
            selectinload(Transaction.user)
        ).order_by(
            Transaction.created_at.desc()
        ).offset(offset).limit(limit)

        result = await self.db.execute(query)
        transactions = result.scalars().all()

        # Get total count
        count_result = await self.db.execute(
            select(func.count()).select_from(Transaction).where(Transaction.user_id == user_id)
        )
        total = count_result.scalar() or 0

        # Construct transaction data with customer_name and payment_method
        transaction_data = []
        for transaction in transactions:
            txn_dict = transaction.to_dict()

            # Add customer_name from user
            if transaction.user:
                customer_name = f"{transaction.user.firstname or ''} {transaction.user.lastname or ''}".strip()
                txn_dict['customer_name'] = customer_name if customer_name else transaction.user.email

            # Add payment_method from metadata
            payment_method = None
            if transaction.transaction_metadata:
                try:
                    metadata = json.loads(transaction.transaction_metadata)
                    if 'payment_method_type' in metadata:
                        payment_method = metadata['payment_method_type'].replace('PaymentType.', '')
                except (json.JSONDecodeError, KeyError):
                    pass
            txn_dict['payment_method'] = payment_method or 'Card'

            transaction_data.append(txn_dict)

        return {
            "transactions": transaction_data,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit
            }
        }

    async def all_transactions(
        self,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        status: Optional[str] = None,
        payment_method: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get all transactions (admin only)"""
        offset = (page - 1) * limit

        # Build base query with filters
        query = select(Transaction).options(
            selectinload(Transaction.user)
        )

        # Apply status filter
        if status:
            query = query.where(Transaction.status == status)

        # Apply date range filters
        if date_from:
            try:
                date_from_dt = datetime.fromisoformat(date_from)
                query = query.where(Transaction.created_at >= date_from_dt)
            except ValueError:
                pass

        if date_to:
            try:
                date_to_dt = datetime.fromisoformat(date_to)
                # Include the entire day by adding 1 day
                date_to_dt = date_to_dt + timedelta(days=1)
                query = query.where(Transaction.created_at < date_to_dt)
            except ValueError:
                pass

        # Apply search filter (customer name)
        if search:
            query = query.join(User, Transaction.user_id == User.id).where(
                (User.firstname.ilike(f'%{search}%')) |
                (User.lastname.ilike(f'%{search}%')) |
                (User.email.ilike(f'%{search}%'))
            )

        query = query.order_by(
            Transaction.created_at.desc()
        ).offset(offset).limit(limit)

        result = await self.db.execute(query)
        transactions = result.scalars().all()

        # Get total count with same filters
        count_query = select(func.count()).select_from(Transaction)
        if status:
            count_query = count_query.where(Transaction.status == status)
        if date_from:
            try:
                date_from_dt = datetime.fromisoformat(date_from)
                count_query = count_query.where(Transaction.created_at >= date_from_dt)
            except ValueError:
                pass
        if date_to:
            try:
                date_to_dt = datetime.fromisoformat(date_to)
                date_to_dt = date_to_dt + timedelta(days=1)
                count_query = count_query.where(Transaction.created_at < date_to_dt)
            except ValueError:
                pass
        if search:
            count_query = count_query.join(User, Transaction.user_id == User.id).where(
                (User.firstname.ilike(f'%{search}%')) |
                (User.lastname.ilike(f'%{search}%')) |
                (User.email.ilike(f'%{search}%'))
            )

        count_result = await self.db.execute(count_query)
        total = count_result.scalar() or 0

        # Construct transaction data with customer_name and payment_method
        transaction_data = []
        for transaction in transactions:
            txn_dict = transaction.to_dict()

            # Add customer_name from user
            if transaction.user:
                customer_name = f"{transaction.user.firstname or ''} {transaction.user.lastname or ''}".strip()
                txn_dict['customer_name'] = customer_name if customer_name else transaction.user.email

            # Add payment_method from metadata
            payment_method_value = None
            if transaction.transaction_metadata:
                try:
                    metadata = json.loads(transaction.transaction_metadata)
                    if 'payment_method_type' in metadata:
                        payment_method_value = metadata['payment_method_type'].replace('PaymentType.', '')
                except (json.JSONDecodeError, KeyError):
                    pass
            txn_dict['payment_method'] = payment_method_value or 'Card'

            # Apply payment method filter (client-side since it's in metadata)
            if payment_method and payment_method_value:
                if payment_method_value.lower() != payment_method.lower():
                    continue

            transaction_data.append(txn_dict)

        return {
            "transactions": transaction_data,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit
            }
        }


    async def get_transaction(self, transaction_id: UUID, user_id: UUID) -> Optional[Transaction]:
        """Get a specific transaction by ID"""
        result = await self.db.execute(
            select(Transaction).where(
                Transaction.id == transaction_id,
                Transaction.user_id == user_id
            )
        )
        return result.scalar_one_or_none()


    async def refund_order(self, order, amount: Decimal, idempotency_key: str, description: str) -> str:
        """Refund part or all of an order's payment through Stripe, record it, and return Stripe's refund id.
        Raises 400 with Stripe's reason when the refund can't be made; nothing is recorded then."""
        payment = (await self.db.execute(
            select(Transaction).where(
                Transaction.order_id == order.id,
                Transaction.transaction_type == "payment",
                Transaction.status == "succeeded",
            )
        )).scalars().first()
        if not payment or not payment.stripe_payment_intent_id:
            raise HTTPException(status_code=400, detail="This order has no card payment to refund")
        amount = Decimal(str(amount)).quantize(Decimal("0.01"))
        try:
            stripe_refund = await asyncio.to_thread(
                stripe.Refund.create,
                payment_intent=payment.stripe_payment_intent_id,
                amount=int(amount * 100),
                reason="requested_by_customer",
                metadata={"order_id": str(order.id)},
                idempotency_key=idempotency_key,
            )
        except stripe.error.StripeError as e:
            raise HTTPException(status_code=400, detail=f"Refund failed: {e.user_message or str(e)}")
        transaction = Transaction(
            user_id=order.user_id,
            order_id=order.id,
            payment_intent_id=payment.payment_intent_id,
            stripe_payment_intent_id=payment.stripe_payment_intent_id,
            amount=-amount,
            currency=payment.currency,
            status="succeeded",
            transaction_type="refund",
            description=description,
            transaction_metadata=json.dumps({"stripe_refund_id": stripe_refund.id}),
        )
        self.db.add(transaction)
        return stripe_refund.id

    async def refunded_total(self, order_id) -> Decimal:
        """How much of an order has been refunded so far."""
        total = await self.db.scalar(
            select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0)).where(
                Transaction.order_id == order_id,
                Transaction.transaction_type == "refund",
                Transaction.status == "succeeded",
            )
        )
        return Decimal(str(total or 0))

    # --- Payment failure handling methods ---


    async def _get_payment_intent_with_lock(self, payment_intent_id: UUID) -> Optional[PaymentIntent]:
        """Get payment intent with SELECT ... FOR UPDATE lock"""
        result = await self.db.execute(
            select(PaymentIntent)
            .where(PaymentIntent.id == payment_intent_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    def _determine_retry_strategy(
        self,
        failure_reason: PaymentFailureReason,
        payment_intent: PaymentIntent
    ) -> Dict[str, Any]:
        """Determine appropriate retry strategy based on failure reason"""
        retry_count = payment_intent.failure_metadata.get("retry_count", 0) if payment_intent.failure_metadata else 0
        
        # No retry for certain failure types
        if failure_reason in [
            PaymentFailureReason.FRAUD_SUSPECTED,
            PaymentFailureReason.INVALID_CARD,
            PaymentFailureReason.EXPIRED_CARD
        ]:
            return {
                "should_retry": False,
                "reason": "failure_type_not_retryable",
                "max_retries_reached": False
            }
        
        # Check max retries
        max_retries = 3
        if retry_count >= max_retries:
            return {
                "should_retry": False,
                "reason": "max_retries_reached",
                "max_retries_reached": True,
                "retry_count": retry_count
            }
        
        # Determine retry delay based on failure reason
        retry_delays = {
            PaymentFailureReason.INSUFFICIENT_FUNDS: [24, 72, 168],  # 1 day, 3 days, 1 week
            PaymentFailureReason.CARD_DECLINED: [1, 6, 24],  # 1 hour, 6 hours, 1 day
            PaymentFailureReason.PROCESSING_ERROR: [0.5, 2, 6],  # 30 min, 2 hours, 6 hours
            PaymentFailureReason.NETWORK_ERROR: [0.25, 1, 4],  # 15 min, 1 hour, 4 hours
            PaymentFailureReason.AUTHENTICATION_REQUIRED: [0, 0, 0],  # Immediate retry allowed
        }
        
        delay_hours = retry_delays.get(failure_reason, [1, 6, 24])
        next_retry_delay = delay_hours[min(retry_count, len(delay_hours) - 1)]
        
        return {
            "should_retry": True,
            "retry_count": retry_count,
            "max_retries": max_retries,
            "next_retry_in_hours": next_retry_delay,
            "next_retry_at": (datetime.now(timezone.utc) + timedelta(hours=next_retry_delay)).isoformat(),
            "retry_method": "automatic" if failure_reason in [
                PaymentFailureReason.PROCESSING_ERROR,
                PaymentFailureReason.NETWORK_ERROR
            ] else "manual"
        }

    def _get_user_friendly_message(self, failure_reason: PaymentFailureReason) -> str:
        """Get user-friendly message for payment failure"""
        messages = {
            PaymentFailureReason.INSUFFICIENT_FUNDS: "Your payment was declined due to insufficient funds. Please check your account balance and try again.",
            PaymentFailureReason.CARD_DECLINED: "Your card was declined. Please try a different payment method or contact your bank.",
            PaymentFailureReason.EXPIRED_CARD: "Your card has expired. Please update your payment method with a valid card.",
            PaymentFailureReason.INVALID_CARD: "There's an issue with your card details. Please check and update your payment information.",
            PaymentFailureReason.AUTHENTICATION_REQUIRED: "Additional authentication is required for this payment. Please complete the verification process.",
            PaymentFailureReason.PROCESSING_ERROR: "We encountered a temporary issue processing your payment. We'll retry automatically.",
            PaymentFailureReason.NETWORK_ERROR: "There was a network issue during payment processing. We'll retry automatically.",
            PaymentFailureReason.FRAUD_SUSPECTED: "This payment was flagged for security reasons. Please contact support for assistance.",
            PaymentFailureReason.LIMIT_EXCEEDED: "This payment exceeds your card's limit. Please try a smaller amount or different payment method.",
            PaymentFailureReason.UNKNOWN: "We encountered an issue processing your payment. Please try again or contact support."
        }
        return messages.get(failure_reason, messages[PaymentFailureReason.UNKNOWN])


    def _get_next_steps(self, failure_reason: PaymentFailureReason) -> List[str]:
        """Get recommended next steps for user"""
        steps = {
            PaymentFailureReason.INSUFFICIENT_FUNDS: [
                "Check your account balance",
                "Add funds to your account",
                "Try again once funds are available"
            ],
            PaymentFailureReason.CARD_DECLINED: [
                "Contact your bank to authorize the payment",
                "Try a different payment method",
                "Check if your card is blocked for online purchases"
            ],
            PaymentFailureReason.EXPIRED_CARD: [
                "Update your payment method",
                "Add a new valid card",
                "Remove the expired card"
            ],
            PaymentFailureReason.INVALID_CARD: [
                "Check your card number and details",
                "Update your payment information",
                "Try a different card"
            ],
            PaymentFailureReason.AUTHENTICATION_REQUIRED: [
                "Complete the authentication process",
                "Check for SMS or email verification",
                "Contact your bank if needed"
            ],
            PaymentFailureReason.PROCESSING_ERROR: [
                "Wait for automatic retry",
                "Try again in a few minutes",
                "Contact support if issue persists"
            ],
            PaymentFailureReason.FRAUD_SUSPECTED: [
                "Contact our support team",
                "Verify your identity",
                "Use a different payment method"
            ],
            PaymentFailureReason.LIMIT_EXCEEDED: [
                "Contact your bank to increase limits",
                "Try a smaller amount",
                "Use a different payment method"
            ]
        }
        return steps.get(failure_reason, [
            "Try again with a different payment method",
            "Contact support for assistance"
        ])

    async def retry(
        self,
        payment_intent_id: UUID,
        new_payment_method_id: Optional[UUID] = None
    ) -> Dict[str, Any]:
        """Retry a failed payment with optional new payment method"""
        try:
            # Get payment intent with lock
            payment_intent = await self._get_payment_intent_with_lock(payment_intent_id)
            
            if not payment_intent:
                raise HTTPException(
                    status_code=404,
                    detail="Payment intent not found"
                )
            
            if payment_intent.status != "failed":
                raise HTTPException(
                    status_code=400,
                    detail="Can only retry failed payments"
                )
            if payment_intent.order_id and not payment_intent.subscription_id:
                # A failed checkout already cancelled its order and released the stock.
                raise HTTPException(status_code=400, detail="This order was cancelled. Please place it again.")

            # A renewal can be paid with any card while its order is open; other payments follow the retry rules.
            failure_reason = PaymentFailureReason(payment_intent.failure_reason) if payment_intent.failure_reason else PaymentFailureReason.UNKNOWN
            retry_strategy = self._determine_retry_strategy(failure_reason, payment_intent)

            if not payment_intent.subscription_id and not retry_strategy["should_retry"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"Payment retry not allowed: {retry_strategy['reason']}"
                )
            
            # Update payment method if provided - payment_method_id stores the
            # Stripe string id, not our internal PaymentMethod row's UUID.
            if new_payment_method_id:
                pm_result = await self.db.execute(
                    select(PaymentMethod).where(
                        and_(
                            PaymentMethod.id == new_payment_method_id,
                            PaymentMethod.user_id == payment_intent.user_id,
                            PaymentMethod.is_active == True
                        )
                    )
                )
                new_payment_method = pm_result.scalar_one_or_none()
                if not new_payment_method:
                    raise HTTPException(status_code=404, detail="Payment method not found")
                payment_intent.payment_method_id = new_payment_method.stripe_payment_method_id
                payment_intent.payment_method_type = new_payment_method.type
            
            # Reset payment intent for retry
            payment_intent.status = "requires_payment_method"
            payment_intent.failed_at = None
            payment_intent.failure_reason = None
            
            # Update retry count - reassign a new dict, since mutating the existing one in
            # place doesn't register as a change on a plain JSON column.
            failure_metadata = dict(payment_intent.failure_metadata or {})
            failure_metadata["retry_count"] = failure_metadata.get("retry_count", 0) + 1
            failure_metadata["last_retry_at"] = datetime.now(timezone.utc).isoformat()
            payment_intent.failure_metadata = failure_metadata
            
            await self.db.commit()
            
            return {
                "payment_intent_id": str(payment_intent_id),
                "status": "ready_for_retry",
                "retry_count": payment_intent.failure_metadata["retry_count"]
            }
            
        except HTTPException:
            await self.db.rollback()
            raise
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error retrying payment {payment_intent_id}: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to retry payment: {str(e)}"
            )


    async def failure_status(
        self,
        payment_intent_id: UUID,
        user_id: UUID
    ) -> Dict[str, Any]:
        """Get detailed status of a failed payment"""
        # Get payment intent
        result = await self.db.execute(
            select(PaymentIntent).where(
                PaymentIntent.id == payment_intent_id,
                PaymentIntent.user_id == user_id
            )
        )
        payment_intent = result.scalar_one_or_none()
        
        if not payment_intent:
            raise HTTPException(
                status_code=404,
                detail="Payment intent not found"
            )
        
        if payment_intent.status != "failed":
            return {
                "payment_intent_id": str(payment_intent_id),
                "status": payment_intent.status,
                "is_failed": False
            }
        
        # Get retry strategy
        failure_reason = PaymentFailureReason(payment_intent.failure_reason) if payment_intent.failure_reason else PaymentFailureReason.UNKNOWN
        retry_strategy = self._determine_retry_strategy(failure_reason, payment_intent)
        
        return {
            "payment_intent_id": str(payment_intent_id),
            "status": payment_intent.status,
            "is_failed": True,
            "failure_reason": payment_intent.failure_reason,
            "failed_at": payment_intent.failed_at.isoformat() if payment_intent.failed_at else None,
            "failure_metadata": payment_intent.failure_metadata or {},
            "retry_strategy": retry_strategy,
            "user_message": self._get_user_friendly_message(failure_reason),
            "next_steps": self._get_next_steps(failure_reason),
            "order_id": str(payment_intent.order_id) if payment_intent.order_id else None,
            "subscription_id": str(payment_intent.subscription_id) if payment_intent.subscription_id else None,
            "amount": payment_intent.amount_breakdown.get("total", 0) if payment_intent.amount_breakdown else 0,
            "currency": payment_intent.currency
        }

    async def failed_payments(
        self,
        user_id: UUID,
        page: int = 1,
        limit: int = 10
    ) -> Dict[str, Any]:
        """Get user's failed payments with retry options"""
        try:
            offset = (page - 1) * limit
            
            # Get failed payment intents
            query = select(PaymentIntent).where(
                PaymentIntent.user_id == user_id,
                PaymentIntent.status == "failed"
            ).order_by(PaymentIntent.failed_at.desc()).offset(offset).limit(limit)
            
            result = await self.db.execute(query)
            failed_payments = result.scalars().all()
            
            # Get total count
            count_result = await self.db.execute(
                select(func.count(PaymentIntent.id)).where(
                    PaymentIntent.user_id == user_id,
                    PaymentIntent.status == "failed"
                )
            )
            total = count_result.scalar()
            
            # Process each failed payment
            processed_payments = []
            
            for payment in failed_payments:
                failure_reason = PaymentFailureReason(payment.failure_reason) if payment.failure_reason else PaymentFailureReason.UNKNOWN
                retry_strategy = self._determine_retry_strategy(failure_reason, payment)
                
                processed_payments.append({
                    "payment_intent_id": str(payment.id),
                    "order_id": str(payment.order_id) if payment.order_id else None,
                    "subscription_id": str(payment.subscription_id) if payment.subscription_id else None,
                    "amount": payment.amount_breakdown.get("total", 0) if payment.amount_breakdown else 0,
                    "currency": payment.currency,
                    "failure_reason": payment.failure_reason,
                    "failed_at": payment.failed_at.isoformat() if payment.failed_at else None,
                    "retry_strategy": retry_strategy,
                    "user_message": self._get_user_friendly_message(failure_reason),
                    "can_retry": bool(payment.subscription_id) or (retry_strategy["should_retry"] and not payment.order_id),
                    "retry_count": payment.failure_metadata.get("retry_count", 0) if payment.failure_metadata else 0
                })
            
            return {
                "failed_payments": processed_payments,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "pages": (total + limit - 1) // limit
                }
            }
            
        except Exception as e:
            logger.error(f"Error getting user failed payments: {e}")
            raise HTTPException(
                status_code=500,
                detail="Failed to get failed payments"
            )
