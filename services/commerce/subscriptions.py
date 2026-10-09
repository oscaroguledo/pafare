"""Subscription service: creation, updates, and pricing calculations."""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, delete, or_, func, update
from sqlalchemy.orm import selectinload
from fastapi import HTTPException
from models.commerce.subscriptions import Subscription, SubscriptionStatus, BillingCycle, SubscriptionProductAssociation, SubscriptionProduct
from models.commerce.orders import Order
from models.commerce.shipping import ShippingMethod
from models.catalog.product import ProductVariant
from models.catalog.variant_tracking import VariantTrackingEntry
from models.accounts.user import Address, User
from models.commerce.promocode import Promocode
from services.commerce.tax import TaxService
from services.commerce.promocode import PromocodeService
from uuid import UUID
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from decimal import Decimal
from dateutil.relativedelta import relativedelta
import calendar
from core.logging import get_structured_logger
from core.config import settings

logger = get_structured_logger(__name__)


def compute_period_end(start: datetime, billing_cycle: str) -> datetime:
    """End of a billing period given its start - the single source of truth for cycle
    math, shared by subscription creation, frequency changes, and the renewal scheduler."""
    if billing_cycle == BillingCycle.WEEKLY:
        return start + timedelta(weeks=1)
    if billing_cycle == BillingCycle.QUARTERLY:
        return start + relativedelta(months=3)
    if billing_cycle == BillingCycle.YEARLY:
        return start + relativedelta(years=1)
    return start + relativedelta(months=1)  # monthly (default)


def next_delivery(after: datetime, billing_cycle: str, day: Optional[int] = None) -> datetime:
    """One cycle after `after`. Monthly and longer cycles keep the subscription's day of the month
    when the month has it, so the 31st goes Jan 31 -> Feb 28 -> Mar 31 instead of drifting to the 28th."""
    end = compute_period_end(after, billing_cycle)
    if day and billing_cycle != BillingCycle.WEEKLY:
        end = end.replace(day=min(day, calendar.monthrange(end.year, end.month)[1]))
    return end


class SubscriptionService:
    """Simplified subscription service with dynamic pricing"""
    
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        user_id: UUID,
        name: str,
        variant_ids: List[str],
        variant_quantities: Optional[Dict[str, int]] = None,
        delivery_address_id: Optional[UUID] = None,
        shipping_method_id: Optional[UUID] = None,
        billing_cycle: str = "monthly",
        discount_code: Optional[str] = None,
        current_period_start: Optional[str] = None
    ) -> Subscription:
        """Create a new subscription"""
        logger.info(f"Creating subscription with user_id={user_id}, name={name}, variant_ids={variant_ids}")

        currency = settings.STORE_CURRENCY

        # Validate variants
        variant_uuids = [UUID(vid) for vid in variant_ids]
        logger.info(f"Looking up variants: {variant_uuids}")
        variant_result = await self.db.execute(
            select(ProductVariant)
            .where(ProductVariant.id.in_(variant_uuids))
            .options(selectinload(ProductVariant.product))
        )
        variants = variant_result.scalars().all()
        logger.info(f"Found variants: {variants}")

        if len(variants) != len(variant_uuids):
            raise HTTPException(status_code=400, detail="Some variants not found")

        # Get customer address for tax calculation
        customer_address = None
        if delivery_address_id:
            address = await self._owned_address(delivery_address_id, user_id)
            customer_address = {
                "street": address.street,
                "city": address.city,
                "state": address.state,
                "country": address.country,
                "post_code": address.post_code
            }

        # Calculate pricing at creation
        pricing = await self._calculate_pricing(
            variants,
            variant_quantities or {},
            customer_address,
            currency,
            user_id,
            shipping_method_id,
            discount_code
        )

        # The first delivery is on the chosen start date, or on the scheduler's next run.
        now = self._period_start(current_period_start) if current_period_start else datetime.now(timezone.utc)

        period_end = compute_period_end(now, billing_cycle)

        # Create subscription
        subscription = Subscription(
            user_id=user_id,
            name=name,
            status="active",
            currency=currency,
            billing_cycle=billing_cycle,
            auto_renew=True,
            current_period_start=now,
            current_period_end=period_end,
            next_billing_date=now,
            delivery_address_id=delivery_address_id,
            shipping_method_id=shipping_method_id,
            variant_ids=variant_ids,
            subscription_metadata={"variant_quantities": variant_quantities or {vid: 1 for vid in variant_ids}, "billing_day": now.day},
            # Historical prices at creation
            price_at_creation=pricing["total"],
            variant_prices_at_creation=pricing["variant_prices"],
            shipping_amount_at_creation=pricing["shipping"],
            tax_amount_at_creation=pricing["tax"],
            tax_rate_at_creation=pricing["tax_rate"],
            # Current prices (same as creation initially)
            current_variant_prices=pricing["variant_prices"],
            current_shipping_amount=pricing["shipping"],
            current_tax_amount=pricing["tax"],
            current_tax_rate=pricing["tax_rate"],
            current_subtotal=pricing["subtotal"],
            current_discount_amount=pricing["discount"],
            current_total=pricing["total"],
            # Discount
            discount_id=pricing.get("discount_id"),
            discount_type=pricing.get("discount_type"),
            discount_value=pricing.get("discount_value"),
            discount_code=pricing.get("discount_code")
        )

        self.db.add(subscription)
        await self.db.flush()

        # Add products to association table
        for variant in variants:
            association = SubscriptionProductAssociation(
                subscription_id=subscription.id,
                product_variant_id=variant.id
            )
            self.db.add(association)

        await self.db.commit()

        # Re-fetch with full eager loading so products are populated
        result = await self.db.execute(
            select(Subscription)
            .where(Subscription.id == subscription.id)
            .options(
                selectinload(Subscription.products).selectinload(ProductVariant.product),
                selectinload(Subscription.products).selectinload(ProductVariant.images),
                selectinload(Subscription.delivery_address),
                selectinload(Subscription.shipping_method),
            )
        )
        return result.scalar_one()

    async def _calculate_pricing(
        self,
        variants: List[ProductVariant],
        variant_quantities: Dict[str, int],
        customer_address: Optional[Dict],
        currency: str,
        user_id: UUID,
        shipping_method_id: Optional[UUID] = None,
        discount_code: Optional[str] = None
    ) -> Dict[str, Any]:
        """Calculate subscription pricing"""
        
        # Calculate variant prices
        variant_prices = []
        subtotal = Decimal('0.00')
        
        for variant in variants:
            qty = variant_quantities.get(str(variant.id), 1)
            price = Decimal(str(variant.current_price or 0))
            line_total = price * qty
            subtotal += line_total
            
            variant_prices.append({
                "id": str(variant.id),
                "price": float(price),
                "qty": qty
            })

        # Get shipping cost from database
        shipping_cost = await self._get_shipping_cost(shipping_method_id)

        # Apply the promocode with the same rules as one-off orders
        discount_amount = Decimal('0.00')
        discount_id = discount_type = discount_value = discount_code_used = None
        if discount_code:
            is_valid, _, promo = await PromocodeService(self.db).validate(discount_code, subtotal)
            if is_valid:
                discount_amount = PromocodeService.amount(promo, subtotal)
                discount_id, discount_type, discount_value, discount_code_used = promo.id, promo.discount_type, promo.value, promo.code

        # Tax after discount, same rule as one-off orders (services.commerce.tax)
        tax_rate = 0.0
        if customer_address:
            tax_rate = await TaxService(self.db).rate(customer_address.get('country'), customer_address.get('state'))
        tax_amount = TaxService.amount(subtotal + shipping_cost - discount_amount, tax_rate)

        # Calculate total
        total = subtotal + shipping_cost + tax_amount - discount_amount
        
        return {
            "variant_prices": variant_prices,
            "subtotal": float(subtotal),
            "shipping": float(shipping_cost),
            "tax": float(tax_amount),
            "tax_rate": float(tax_rate),
            "discount": float(discount_amount),
            "total": float(total),
            "discount_id": discount_id,
            "discount_type": discount_type,
            "discount_value": discount_value,
            "discount_code": discount_code_used
        }

    async def _get_shipping_cost(self, shipping_method_id: Optional[UUID] = None) -> Decimal:
        """Get shipping cost from database"""

        # If shipping_method_id is provided, get that specific method
        if shipping_method_id:
            result = await self.db.execute(
                select(ShippingMethod).where(ShippingMethod.id == shipping_method_id)
            )
            method = result.scalar_one_or_none()
            if method:
                return Decimal(str(method.price))

        # Fallback to cheapest active method
        result = await self.db.execute(
            select(ShippingMethod).where(ShippingMethod.is_active == True)
        )
        methods = result.scalars().all()

        if not methods:
            raise HTTPException(status_code=400, detail="No delivery methods are set up yet, so this can't be delivered.")
        return Decimal(str(min(methods, key=lambda m: m.price).price))

    async def get(self, subscription_id: UUID, user_id: Optional[UUID] = None) -> Optional[Subscription]:
        """Get subscription by ID"""
        query = select(Subscription).where(Subscription.id == subscription_id).options(
            selectinload(Subscription.products).selectinload(ProductVariant.product),
            selectinload(Subscription.products).selectinload(ProductVariant.images),
            selectinload(Subscription.delivery_address),
                selectinload(Subscription.shipping_method),
        )

        if user_id:
            query = query.where(Subscription.user_id == user_id)

        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def list(
        self, 
        user_id: Optional[UUID] = None, 
        status: Optional[str] = None,
        search: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        page: int = 1, 
        limit: int = 10
    ) -> Dict[str, Any]:
        """Get paginated subscriptions. If user_id is None, returns all subscriptions (admin)."""

        offset = (page - 1) * limit

        # Build base query
        if user_id:
            # User-specific query
            base_query = select(Subscription).where(Subscription.user_id == user_id).options(
                selectinload(Subscription.products).selectinload(ProductVariant.product),
                selectinload(Subscription.products).selectinload(ProductVariant.images),
                selectinload(Subscription.delivery_address),
                selectinload(Subscription.shipping_method),
            )
            count_query = select(func.count()).select_from(Subscription).where(Subscription.user_id == user_id)
        else:
            # Admin query — fetch subscriptions first, then join user info separately
            base_query = select(Subscription).options(
                selectinload(Subscription.products).selectinload(ProductVariant.product),
                selectinload(Subscription.products).selectinload(ProductVariant.images),
                selectinload(Subscription.user),
                selectinload(Subscription.delivery_address),
                selectinload(Subscription.shipping_method),
            )
            count_query = select(func.count()).select_from(Subscription)

        # Apply filters
        if status:
            base_query = base_query.where(Subscription.status == status)
            count_query = count_query.where(Subscription.status == status)

        # Apply search filter
        if search:
            search_term = f"%{search}%"
            if user_id:
                # User-specific search - search subscription fields
                search_condition = or_(
                    Subscription.name.ilike(search_term),
                    Subscription.payment_reference.ilike(search_term),
                    Subscription.payment_gateway.ilike(search_term),
                )
                base_query = base_query.where(search_condition)
                count_query = count_query.where(search_condition)
            else:
                # Admin search - search user fields
                search_condition = or_(
                    User.email.ilike(search_term),
                    User.firstname.ilike(search_term),
                    User.lastname.ilike(search_term),
                    Subscription.name.ilike(search_term),
                    Subscription.payment_reference.ilike(search_term),
                )
                base_query = base_query.join(User, Subscription.user_id == User.id).where(search_condition)
                count_query = select(func.count(Subscription.id)).select_from(Subscription).join(User, Subscription.user_id == User.id).where(search_condition)

        # Apply date filters
        if date_from:
            try:
                from_date = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
                base_query = base_query.where(Subscription.created_at >= from_date)
            except ValueError:
                pass

        if date_to:
            try:
                to_date = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
                base_query = base_query.where(Subscription.created_at <= to_date)
            except ValueError:
                pass

        # Apply sorting
        sort_column = Subscription.created_at
        if sort_by == "next_billing_date":
            sort_column = Subscription.next_billing_date
        elif sort_by == "status":
            sort_column = Subscription.status

        if sort_order == "asc":
            base_query = base_query.order_by(sort_column.asc())
        else:
            base_query = base_query.order_by(sort_column.desc())

        # Get total count
        total = await self.db.scalar(count_query) or 0

        # Execute query with pagination
        result = await self.db.execute(base_query.offset(offset).limit(limit))

        # Format results
        if user_id:
            subscriptions = result.scalars().all()
            subscriptions_data = [sub.to_dict(include_products=True) for sub in subscriptions]
        else:
            subscriptions = result.scalars().all()
            subscriptions_data = []
            for subscription in subscriptions:
                sub_dict = subscription.to_dict(include_products=True)
                if subscription.user:
                    sub_dict["user"] = {
                        "id": str(subscription.user.id),
                        "name": f"{subscription.user.firstname} {subscription.user.lastname}",
                        "email": subscription.user.email,
                    }
                subscriptions_data.append(sub_dict)

        return {
            "data": subscriptions_data,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": max(1, (total + limit - 1) // limit) if limit else 1
            }
        }

    async def update(
        self,
        subscription_id: UUID,
        user_id: UUID,
        name: Optional[str] = None,
        delivery_address_id: Optional[UUID] = None,
        shipping_method_id: Optional[UUID] = None,
        auto_renew: Optional[bool] = None,
        variant_ids: Optional[List[str]] = None,
        variant_quantities: Optional[Dict[str, int]] = None,
        current_period_start: Optional[str] = None
    ) -> Subscription:
        """Update subscription"""
        subscription = await self.get(subscription_id, user_id)
        
        if not subscription:
            raise HTTPException(status_code=404, detail="Subscription not found")
        
        if subscription.status not in ["active", "paused"]:
            raise HTTPException(status_code=400, detail="Cannot update inactive subscription")
        reprice = bool(delivery_address_id or shipping_method_id is not None or variant_ids or variant_quantities)

        if name:
            subscription.name = name

        if delivery_address_id:
            await self._owned_address(delivery_address_id, user_id)
            subscription.delivery_address_id = delivery_address_id

        if shipping_method_id is not None:
            subscription.shipping_method_id = shipping_method_id

        if auto_renew is not None:
            subscription.auto_renew = auto_renew

        if current_period_start:
            new_period_start = self._period_start(current_period_start)
            subscription.current_period_start = new_period_start

            # The next delivery moves to the new start date, and later ones follow its day of the month.
            subscription.current_period_end = compute_period_end(new_period_start, subscription.billing_cycle)
            subscription.next_billing_date = new_period_start
            subscription.subscription_metadata = {**(subscription.subscription_metadata or {}), "billing_day": new_period_start.day}

        if variant_ids:
            subscription.variant_ids = variant_ids

            # Update association table
            await self.db.execute(
                delete(SubscriptionProductAssociation).where(
                    SubscriptionProductAssociation.subscription_id == subscription.id
                )
            )

            variant_uuids = [UUID(vid) for vid in variant_ids]
            variant_result = await self.db.execute(
                select(ProductVariant).where(ProductVariant.id.in_(variant_uuids))
            )
            variants = variant_result.scalars().all()

            for variant in variants:
                association = SubscriptionProductAssociation(
                    subscription_id=subscription.id,
                    product_variant_id=variant.id
                )
                self.db.add(association)
        
        if variant_quantities:
            metadata = dict(subscription.subscription_metadata or {})
            metadata["variant_quantities"] = variant_quantities
            subscription.subscription_metadata = metadata

        if reprice:
            await self.recalc_pricing(subscription)
        await self.db.commit()
        # products is many-to-many; once loaded, a later selectinload (inside get())
        # won't re-query it even after the association rows just changed.
        self.db.expire(subscription, ["products"])
        return await self.get(subscription.id)

    async def cancel(self, subscription_id: UUID, user_id: UUID, reason: Optional[str] = None) -> Subscription:
        """Cancel subscription"""
        subscription = await self.get(subscription_id, user_id)
        
        if not subscription:
            raise HTTPException(status_code=404, detail="Subscription not found")
        
        subscription.status = SubscriptionStatus.CANCELLED.value
        subscription.cancelled_at = datetime.now(timezone.utc)
        subscription.auto_renew = False
        subscription.next_retry_date = None

        if reason:
            subscription.pause_reason = f"Cancelled: {reason}"

        await self._close_open_renewal(subscription, "subscription cancelled")
        return await self.get(subscription.id)

    async def pause(self, subscription_id: UUID, user_id: UUID, reason: Optional[str] = None) -> Subscription:
        """Pause subscription"""
        subscription = await self.get(subscription_id, user_id)
        
        if not subscription:
            raise HTTPException(status_code=404, detail="Subscription not found")
        
        if subscription.status not in (SubscriptionStatus.ACTIVE.value, SubscriptionStatus.PAYMENT_FAILED.value):
            raise HTTPException(status_code=400, detail="Can only pause active subscriptions")

        subscription.status = SubscriptionStatus.PAUSED.value
        subscription.paused_at = datetime.now(timezone.utc)
        subscription.pause_reason = reason
        subscription.next_retry_date = None

        await self._close_open_renewal(subscription, "subscription paused")
        return await self.get(subscription.id)

    async def _close_open_renewal(self, subscription: Subscription, reason: str) -> None:
        """Save the change and drop any renewal still waiting for payment. Commits."""
        # The scheduler module imports this one.
        from services.commerce.subscriptions_scheduler import close_open_renewal
        await self.db.flush()
        await close_open_renewal(self.db, subscription.id, reason)
        await self.db.commit()

    async def resume(self, subscription_id: UUID, user_id: UUID) -> Subscription:
        """Resume subscription"""
        subscription = await self.get(subscription_id, user_id)

        if not subscription:
            raise HTTPException(status_code=404, detail="Subscription not found")

        if subscription.status not in ["paused", "cancelled"]:
            raise HTTPException(status_code=400, detail="Can only resume paused or cancelled subscriptions")

        now = datetime.now(timezone.utc)
        subscription.status = SubscriptionStatus.ACTIVE.value
        subscription.paused_at = None
        subscription.pause_reason = None
        subscription.cancelled_at = None
        subscription.auto_renew = True
        subscription.payment_retry_count = 0
        subscription.next_retry_date = None
        subscription.last_payment_error = None
        # Keep a future delivery date; a missed one is delivered on the scheduler's next run.
        if not subscription.next_billing_date or subscription.next_billing_date < now:
            subscription.next_billing_date = now

        await self.db.commit()
        return await self.get(subscription.id)


    async def list_due(self, limit: int = 50) -> List[Subscription]:
        """List active subscriptions currently due for billing (admin)."""
        result = await self.db.execute(
            select(Subscription)
            .options(
                selectinload(Subscription.products).selectinload(ProductVariant.product),
                selectinload(Subscription.products).selectinload(ProductVariant.images),
                selectinload(Subscription.delivery_address),
                selectinload(Subscription.shipping_method),
            )
            .where(
                and_(
                    Subscription.status == "active",
                    Subscription.auto_renew == True,
                    Subscription.next_billing_date <= datetime.now(timezone.utc),
                )
            )
            .order_by(Subscription.next_billing_date.asc())
            .limit(limit)
        )
        return result.scalars().all()

    async def change_frequency(self, subscription_id: UUID, user_id: UUID, frequency: str) -> Subscription:
        """Change the billing cycle; the next delivery keeps its date and later ones follow the new cycle."""
        subscription = await self.get(subscription_id, user_id)
        if not subscription:
            raise HTTPException(status_code=404, detail="Subscription not found")

        subscription.billing_cycle = frequency
        period_start = subscription.current_period_start or datetime.now(timezone.utc)
        subscription.current_period_end = compute_period_end(period_start, frequency)

        await self.db.commit()
        return await self.get(subscription.id)

    async def skip_next_shipment(
        self, subscription_id: UUID, user_id: UUID, next_shipment_date: Optional[str] = None
    ) -> Subscription:
        """Push next_billing_date out, remembering the original date so it can be undone."""
        subscription = await self.get(subscription_id, user_id)
        if not subscription:
            raise HTTPException(status_code=404, detail="Subscription not found")

        if subscription.status != SubscriptionStatus.ACTIVE.value:
            raise HTTPException(status_code=400, detail="Only active subscriptions can skip a shipment")

        metadata = dict(subscription.subscription_metadata or {})
        # Keep the first pre-skip date so repeated skips still undo back to the real schedule.
        metadata.setdefault(
            "skipped_from_date",
            subscription.next_billing_date.isoformat() if subscription.next_billing_date else None,
        )
        subscription.subscription_metadata = metadata

        if next_shipment_date:
            chosen = datetime.fromisoformat(next_shipment_date)
            chosen = chosen if chosen.tzinfo else chosen.replace(tzinfo=timezone.utc)
            if chosen <= datetime.now(timezone.utc):
                raise HTTPException(status_code=400, detail="The next shipment date must be in the future")
            subscription.next_billing_date = chosen
        else:
            base = subscription.next_billing_date or datetime.now(timezone.utc)
            subscription.next_billing_date = next_delivery(base, subscription.billing_cycle, metadata.get("billing_day"))

        await self.db.commit()
        return await self.get(subscription.id)

    async def unskip_next_shipment(self, subscription_id: UUID, user_id: UUID) -> Subscription:
        """Restore next_billing_date to what it was before the last skip."""
        subscription = await self.get(subscription_id, user_id)
        if not subscription:
            raise HTTPException(status_code=404, detail="Subscription not found")

        metadata = dict(subscription.subscription_metadata or {})
        original = metadata.get("skipped_from_date")
        if not original:
            raise HTTPException(status_code=400, detail="Subscription has not been skipped")

        subscription.next_billing_date = datetime.fromisoformat(original)
        metadata.pop("skipped_from_date", None)
        subscription.subscription_metadata = metadata

        await self.db.commit()
        return await self.get(subscription.id)

    async def recalc_pricing(self, subscription: Subscription) -> Dict[str, Any]:
        """Recalculate current pricing for a subscription"""
        
        # Get current variants
        variant_uuids = [UUID(vid) for vid in subscription.variant_ids]
        variant_result = await self.db.execute(
            select(ProductVariant).where(ProductVariant.id.in_(variant_uuids))
        )
        variants = variant_result.scalars().all()
        
        # Get quantities
        variant_quantities = subscription.subscription_metadata.get("variant_quantities", {}) if subscription.subscription_metadata else {}
        
        # Get customer address
        customer_address = None
        if subscription.delivery_address_id:
            address_result = await self.db.execute(
                select(Address).where(Address.id == subscription.delivery_address_id)
            )
            address = address_result.scalar_one_or_none()
            if address:
                customer_address = {
                    "street": address.street,
                    "city": address.city,
                    "state": address.state,
                    "country": address.country,
                    "post_code": address.post_code
                }

        # Calculate current pricing
        pricing = await self._calculate_pricing(
            variants,
            variant_quantities,
            customer_address,
            subscription.currency or "CAD",
            subscription.user_id,
            subscription.shipping_method_id,
            subscription.discount_code
        )
        
        # Update current pricing fields
        subscription.current_variant_prices = pricing["variant_prices"]
        subscription.current_shipping_amount = pricing["shipping"]
        subscription.current_tax_amount = pricing["tax"]
        subscription.current_tax_rate = pricing["tax_rate"]
        subscription.current_subtotal = pricing["subtotal"]
        subscription.current_discount_amount = pricing["discount"]
        subscription.current_total = pricing["total"]

        await self.db.commit()

        return pricing

    async def apply_discount(self, subscription_id: UUID, user_id: UUID, discount_code: str) -> Subscription:
        """Apply a promocode to an existing subscription, reusing the same
        Promocode lookup _calculate_pricing already does at creation time."""
        subscription = await self.get(subscription_id, user_id=user_id)
        if not subscription:
            raise HTTPException(status_code=404, detail="Subscription not found")

        if subscription.status not in (SubscriptionStatus.ACTIVE.value, SubscriptionStatus.PAUSED.value):
            raise HTTPException(status_code=400, detail="Cannot apply a discount to a subscription in this status")

        previous_code = subscription.discount_code
        subscription.discount_code = discount_code
        pricing = await self.recalc_pricing(subscription)

        if not pricing.get("discount_id"):
            subscription.discount_code = previous_code
            await self.db.commit()
            raise HTTPException(status_code=400, detail="Invalid or inactive discount code")

        subscription.discount_id = pricing["discount_id"]
        subscription.discount_type = pricing["discount_type"]
        subscription.discount_value = pricing["discount_value"]
        await self.db.commit()
        await self.db.refresh(subscription)
        return subscription

    async def remove_discount(self, subscription_id: UUID, user_id: UUID, discount_id: UUID) -> Subscription:
        """Remove the currently-applied discount from a subscription."""
        subscription = await self.get(subscription_id, user_id=user_id)
        if not subscription:
            raise HTTPException(status_code=404, detail="Subscription not found")

        if subscription.discount_id != discount_id:
            raise HTTPException(status_code=404, detail="This discount is not applied to the subscription")

        subscription.discount_code = None
        subscription.discount_id = None
        subscription.discount_type = None
        subscription.discount_value = None
        await self.recalc_pricing(subscription)
        await self.db.refresh(subscription)
        return subscription

    # -------------------------------------------------------------------------

    async def delete(self, subscription_id: UUID, user_id: UUID) -> bool:
        # Fetch subscription without loading relationships for deletion
        result = await self.db.execute(
            select(Subscription).where(
                Subscription.id == subscription_id, Subscription.user_id == user_id
            )
        )
        subscription = result.scalar_one_or_none()
        if not subscription:
            raise HTTPException(status_code=404, detail="Subscription not found")
        await self._close_open_renewal(subscription, "subscription deleted")

        # Delete all child rows that lack DB-level CASCADE
        await self.db.execute(
            delete(SubscriptionProductAssociation).where(
                SubscriptionProductAssociation.subscription_id == subscription_id
            )
        )
        await self.db.execute(
            delete(SubscriptionProduct).where(
                SubscriptionProduct.subscription_id == subscription_id
            )
        )
        await self.db.execute(
            delete(VariantTrackingEntry).where(
                VariantTrackingEntry.subscription_id == subscription_id
            )
        )
        # Unlink orders — keep them but remove the subscription reference
        await self.db.execute(
            update(Order)
            .where(Order.subscription_id == subscription_id)
            .values(subscription_id=None)
        )

        await self.db.delete(subscription)
        await self.db.commit()
        return True

    async def add_products(self, subscription_id: UUID, variant_ids: List[UUID], user_id: UUID) -> Subscription:
        """Add products to a subscription and reprice it."""
        subscription = await self._editable(subscription_id, user_id)
        existing = {str(v) for v in (subscription.variant_ids or [])}
        new_ids = [vid for vid in dict.fromkeys(variant_ids) if str(vid) not in existing]
        if new_ids:
            found = await self.db.execute(
                select(ProductVariant.id).where(ProductVariant.id.in_(new_ids), ProductVariant.is_active.is_(True))
            )
            if len(found.scalars().all()) != len(new_ids):
                raise HTTPException(status_code=400, detail="One of those products isn't available any more")
            for vid in new_ids:
                self.db.add(SubscriptionProductAssociation(subscription_id=subscription.id, product_variant_id=vid))
            subscription.variant_ids = list(subscription.variant_ids or []) + [str(vid) for vid in new_ids]
            await self.recalc_pricing(subscription)
        self.db.expire(subscription, ["products"])
        return await self.get(subscription.id)

    async def remove_products(self, subscription_id: UUID, variant_ids: List[UUID], user_id: UUID) -> Subscription:
        """Remove products from a subscription and reprice it; at least one product must stay."""
        subscription = await self._editable(subscription_id, user_id)
        removing = {str(vid) for vid in variant_ids}
        remaining = [v for v in (subscription.variant_ids or []) if str(v) not in removing]
        if not remaining:
            raise HTTPException(status_code=400, detail="A subscription needs at least one product. Cancel it instead.")
        await self.db.execute(
            delete(SubscriptionProductAssociation).where(
                SubscriptionProductAssociation.subscription_id == subscription.id,
                SubscriptionProductAssociation.product_variant_id.in_(variant_ids),
            )
        )
        subscription.variant_ids = remaining
        meta = dict(subscription.subscription_metadata or {})
        quantities = {k: q for k, q in meta.get("variant_quantities", {}).items() if k not in removing}
        subscription.subscription_metadata = {**meta, "variant_quantities": quantities}
        await self.recalc_pricing(subscription)
        self.db.expire(subscription, ["products"])
        return await self.get(subscription.id)

    async def _owned_address(self, address_id: UUID, user_id: UUID) -> Address:
        """The customer's own address; anyone else's is rejected."""
        address = await self.db.scalar(select(Address).where(Address.id == address_id, Address.user_id == user_id))
        if not address:
            raise HTTPException(status_code=400, detail="Choose one of your saved addresses")
        return address

    @staticmethod
    def _period_start(value: str) -> datetime:
        """A chosen start date: today or later, never in the past."""
        start = datetime.fromisoformat(value)
        start = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
        if start.date() < datetime.now(timezone.utc).date():
            raise HTTPException(status_code=400, detail="The start date can't be in the past")
        return start

    async def _editable(self, subscription_id: UUID, user_id: UUID) -> Subscription:
        """The customer's subscription, if it can still be changed (not cancelled)."""
        subscription = await self.get(subscription_id, user_id)
        if not subscription:
            raise HTTPException(status_code=404, detail="Subscription not found")
        if subscription.status == SubscriptionStatus.CANCELLED.value:
            raise HTTPException(status_code=400, detail="A cancelled subscription can't be changed")
        return subscription

    async def set_quantity(self, subscription_id: UUID, variant_id: UUID, quantity: int, user_id: UUID) -> Subscription:
        subscription = await self.get(subscription_id, user_id)
        if not subscription:
            raise HTTPException(status_code=404, detail="Subscription not found")
        self._require_variant(subscription, variant_id)
        meta = dict(subscription.subscription_metadata or {})
        quantities = dict(meta.get("variant_quantities", {}))
        quantities[str(variant_id)] = quantity
        subscription.subscription_metadata = {**meta, "variant_quantities": quantities}
        await self.recalc_pricing(subscription)
        return await self.get(subscription.id)


    @staticmethod
    def _require_variant(subscription: Subscription, variant_id) -> None:
        if str(variant_id) not in [str(v) for v in (subscription.variant_ids or [])]:
            raise HTTPException(status_code=400, detail="That product is not part of this subscription")


    async def get_orders(self, subscription_id: UUID, user_id: UUID, page: int = 1, limit: int = 10) -> Dict[str, Any]:
        subscription = await self.get(subscription_id, user_id)
        if not subscription:
            raise HTTPException(status_code=404, detail="Subscription not found")
        total = await self.db.scalar(
            select(func.count()).select_from(Order).where(Order.subscription_id == subscription_id)
        ) or 0
        result = await self.db.execute(
            select(Order).where(Order.subscription_id == subscription_id)
            .order_by(Order.created_at.desc())
            .offset((page - 1) * limit).limit(limit)
        )
        orders = result.scalars().all()
        return {
            "orders": [o.to_dict() for o in orders],
            "total": total,
            "page": page,
            "limit": limit,
            "pages": max(1, (total + limit - 1) // limit)
        }


