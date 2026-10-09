"""Order service: complete order lifecycle with backend-only price calculations."""
import re
import traceback

import stripe
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, delete, String, text, func
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, BackgroundTasks
from core.exceptions import APIException
from models.commerce.orders import Order, OrderItem, TrackingEvent, OrderStatus, FulfillmentStatus, PaymentStatus, OrderSource
from services.accounts.email import queue, send_order_confirmation, send_order_delivered, send_shipping_update
from models.commerce.cart import Cart, CartItem
from models.accounts.user import User, Address
from models.catalog.product import ProductVariant
from models.commerce.shipping import ShippingMethod
from models.commerce.payments import PaymentMethod
from schemas.commerce.orders import Response as OrderResponse, ItemResponse as OrderItemResponse, Checkout as CheckoutRequest
from schemas.catalog.inventory import AdjustmentCreate as StockAdjustmentCreate
from services.commerce.cart import CartService
from services.commerce.payments import PaymentService
from services.catalog.inventory import InventoryService 
from services.commerce.tax import TaxService
from services.commerce.promocode import PromocodeService
from models.commerce.promocode import Promocode
from services.commerce.shipping import ShippingService
from uuid import UUID
from core.utils.uuid_utils import uuid7
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from decimal import Decimal, ROUND_HALF_UP
from core.logging import get_structured_logger

from schemas.common.service_types import PricingCalculationResult
from core.config import settings

logger = get_structured_logger(__name__)



# Where staff can move an order next; refunds after delivery go through the refund flow.
NEXT_STATUSES = {
    # A pending order is unpaid; payment confirms it, so staff can only cancel it.
    OrderStatus.PENDING: {OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.PROCESSING: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED},
}



def _item_name(item) -> str:
    """Product and option name, or just the product when the option has the same name."""
    variant = item.variant
    if not variant:
        return "—"
    product = variant.product.name if variant.product else ""
    return f"{product} – {variant.name}" if product and variant.name and variant.name != product else (product or variant.name)

def address_lines(address) -> list:
    """A saved address as printable lines: street, "city province postal code", country."""
    if not isinstance(address, dict):
        return [str(address)] if address else []
    city_line = " ".join(p for p in [address.get("city"), address.get("state"), address.get("post_code")] if p)
    return [line for line in [address.get("street"), city_line, address.get("country")] if line]

class OrderService:
    """Order service with backend-only pricing: TOTAL = SUM(qty×backend_price) + shipping + tax - discount.
    Frontend prices are never trusted; mismatches are recalculated from the DB and logged for audit."""
    
    def __init__(self, db: AsyncSession, lock_service=None):
        self.db = db
        self.inventory_service = InventoryService(db, lock_service)
        self.tax_service = TaxService(db)
        self.shipping_service = ShippingService(db)

    @staticmethod
    def _orderable_items(cart_items: List[CartItem]) -> List[CartItem]:
        """Cart items that are actually purchasable now; out-of-stock (quantity 0) or
        deactivated variants stay in the cart but are skipped at checkout, not blocked."""
        return [item for item in cart_items if item.quantity > 0 and item.variant and item.variant.is_active]

    async def calc_pricing(
        self,
        cart_items: List[CartItem],
        shipping_address: Address,
        shipping_method_id: UUID,
        discount_code: Optional[str] = None
    ) -> PricingCalculationResult:
        """The authoritative pricing calculation (subtotal/shipping/tax/discount); never trust frontend prices."""
        logger.info(f"Calculating comprehensive pricing for {len(cart_items)} items")
        
        # Step 1: Calculate subtotal from product variant sale prices
        subtotal = Decimal('0.00')
        item_breakdown = []
        
        for item in cart_items:
            # Get current price from variant (sale_price if available, otherwise base_price)
            variant_price = Decimal(str(item.variant.sale_price or item.variant.base_price))
            item_total = variant_price * Decimal(str(item.quantity))
            subtotal += item_total
            
            item_breakdown.append({
                'variant_id': str(item.variant.id),
                'variant_name': item.variant.name,
                'quantity': item.quantity,
                'unit_price': float(variant_price),
                'total_price': float(item_total),
                'on_sale': item.variant.sale_price is not None,
                'original_price': float(item.variant.base_price) if item.variant.sale_price else None
            })
        
        logger.info(f"Calculated subtotal: ${subtotal}")
        
        # Step 2: Calculate shipping cost
        shipping_cost = Decimal('0.00')
        shipping_method = await self.shipping_service.get(shipping_method_id)
        if shipping_method and shipping_method.is_active:
            shipping_cost = Decimal(str(shipping_method.price))
            logger.info(f"Shipping cost: ${shipping_cost} ({shipping_method.name})")
        
        # Step 3: Apply the promocode, if it's valid for this subtotal
        discount_amount = Decimal('0.00')
        discount_info = None
        if discount_code:
            is_valid, _, promocode = await PromocodeService(self.db).validate(discount_code, subtotal)
            if is_valid:
                discount_amount = PromocodeService.amount(promocode, subtotal)
                discount_info = {
                    'id': str(promocode.id),
                    'code': promocode.code,
                    'type': promocode.discount_type,
                    'value': float(promocode.value),
                    'amount': float(discount_amount)
                }

        # Step 4: Tax on what the customer actually pays for goods and delivery, at the shipping address's rate
        tax_rate = await self.tax_service.rate(shipping_address.country, shipping_address.state)
        tax_amount = TaxService.amount(subtotal + shipping_cost - discount_amount, tax_rate)

        # Step 5: Calculate final total
        total_amount = (subtotal + shipping_cost + tax_amount - discount_amount).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        
        # Ensure total is never negative
        if total_amount < Decimal('0.00'):
            total_amount = Decimal('0.00')
        
        logger.info(f"Final total: ${total_amount}")
        
        # Create detailed breakdown
        breakdown = {
            'items': item_breakdown,
            'subtotal': float(subtotal),
            'shipping': {
                'method_id': str(shipping_method_id),
                'method_name': shipping_method.name if shipping_method else None,
                'cost': float(shipping_cost)
            },
            'tax': {
                'rate': tax_rate,
                'amount': float(tax_amount),
                'location': f"{shipping_address.country}-{shipping_address.state}"
            },
            'discount': discount_info,
            'total': float(total_amount),
            'currency': settings.STORE_CURRENCY,
            'calculated_at': datetime.now(timezone.utc).isoformat()
        }
        
        return PricingCalculationResult(
            subtotal=subtotal,
            shipping_cost=shipping_cost,
            tax_amount=tax_amount,
            tax_rate=float(tax_rate),
            discount_amount=discount_amount,
            total_amount=total_amount,
            currency=settings.STORE_CURRENCY,
            breakdown=breakdown
        )

    async def validate_checkout(
        self,
        user_id: UUID,
        request: CheckoutRequest
    ) -> Dict[str, Any]:
        """Comprehensive checkout validation with detailed error reporting."""
        def _clean_result(r: dict) -> dict:
            """Ensure result is fully JSON-serializable."""
            return {
                'valid': bool(r.get('valid', False)),
                'can_proceed': bool(r.get('can_proceed', False)),
                'errors': [
                    e if isinstance(e, dict) else {'message': str(e)}
                    for e in r.get('errors', [])
                ],
                'warnings': r.get('warnings', []),
                'pricing': r.get('pricing'),
            }

        validation_result = {
            'valid': True,
            'can_proceed': True,
            'errors': [],
            'warnings': [],
            'pricing': None
        }
        
        try:
            # Step 1: Validate cart exists and has items
            cart_service = CartService(self.db)
            cart_validation = await cart_service.validate_cart(user_id)
            
            logger.info(f"Cart validation result: valid={cart_validation.get('valid')}, can_checkout={cart_validation.get('can_checkout')}")
            if cart_validation.get('issues'):
                for issue in cart_validation.get('issues'):
                    logger.info(f"Cart issue: {issue}")
            
            # A cart with one out-of-stock item alongside otherwise-valid items can still
            # check out (that item is simply skipped) - only block when nothing is purchasable.
            if not cart_validation.get('can_checkout', False):
                validation_result['valid'] = False
                validation_result['can_proceed'] = False
                validation_result['errors'].extend(cart_validation.get('issues', []))
                return _clean_result(validation_result)

            cart = cart_validation['cart']
            orderable_items = self._orderable_items(cart.items) if cart and hasattr(cart, 'items') else []

            if not orderable_items:
                validation_result['valid'] = False
                validation_result['can_proceed'] = False
                validation_result['errors'].append({
                    'type': 'cart_validation',
                    'severity': 'error',
                    'message': 'Cart has no items available to checkout'
                })
                return _clean_result(validation_result)
            
            # Step 2: Validate shipping address
            shipping_address_result = await self.db.execute(
                select(Address).where(
                    and_(Address.id == request.shipping_address_id, Address.user_id == user_id)
                )
            )
            shipping_address = shipping_address_result.scalar_one_or_none()
            
            if not shipping_address:
                validation_result['valid'] = False
                validation_result['can_proceed'] = False
                validation_result['errors'].append({
                    'field': 'shipping_address_id',
                    'message': 'Invalid shipping address'
                })
                return _clean_result(validation_result)
            
            # Step 3: Validate shipping method
            shipping_method = await self.shipping_service.get(request.shipping_method_id)
            if not shipping_method or not shipping_method.is_active:
                validation_result['valid'] = False
                validation_result['can_proceed'] = False
                validation_result['errors'].append({
                    'field': 'shipping_method_id',
                    'message': 'Invalid or inactive shipping method'
                })
                return _clean_result(validation_result)
            
            # Step 4: Validate payment method
            payment_method_result = await self.db.execute(
                select(PaymentMethod).where(
                    and_(PaymentMethod.id == request.payment_method_id, PaymentMethod.user_id == user_id)
                )
            )
            payment_method = payment_method_result.scalar_one_or_none()
            
            if not payment_method:
                validation_result['valid'] = False
                validation_result['can_proceed'] = False
                validation_result['errors'].append({
                    'field': 'payment_method_id',
                    'message': 'Invalid payment method'
                })
                return _clean_result(validation_result)
            
            # Step 5: Calculate comprehensive pricing
            pricing = await self.calc_pricing(
                orderable_items,
                shipping_address,
                request.shipping_method_id,
                request.discount_code
            )
            validation_result['pricing'] = pricing['breakdown']
            
            # Step 6: Validate frontend price if provided
            if hasattr(request, 'frontend_calculated_total') and request.frontend_calculated_total:
                frontend_total = Decimal(str(request.frontend_calculated_total))
                backend_total = pricing['total_amount']
                price_difference = abs(frontend_total - backend_total)
                
                # Allow small rounding differences (up to 1 cent)
                if price_difference > Decimal('0.01'):
                    validation_result['warnings'].append({
                        'type': 'price_mismatch',
                        'message': f'Price mismatch detected. Frontend: ${frontend_total}, Backend: ${backend_total}',
                        'frontend_total': float(frontend_total),
                        'backend_total': float(backend_total),
                        'difference': float(price_difference)
                    })
            
            logger.info(f"Checkout validation completed for user {user_id}: {validation_result['valid']}")
            return _clean_result(validation_result)
            
        except Exception as e:
            logger.error(f"Checkout validation failed for user {user_id}: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            validation_result['valid'] = False
            validation_result['can_proceed'] = False
            validation_result['errors'].append({
                'type': 'system_error',
                'message': f'Checkout validation failed: {str(e)}'
            })
            return _clean_result(validation_result)

    async def create(
        self,
        user_id: UUID,
        request: CheckoutRequest,
        background_tasks: BackgroundTasks,
        idempotency_key: Optional[str] = None
    ) -> OrderResponse:
        """Create an order with full validation; the single path both checkout endpoints call."""
        logger.info(f"Processing order for user {user_id}")
        
        # Step 1: Comprehensive validation
        validation_result = await self.validate_checkout(user_id, request)
        
        if not validation_result['can_proceed']:
            raise HTTPException(
                status_code=400,
                detail={
                    'message': 'Checkout validation failed',
                    'errors': validation_result['errors'],
                    'warnings': validation_result['warnings']
                }
            )
        
        # Step 2: Get cart from database to access items for order creation
        # We need to fetch the cart fresh from the database in case items were updated
        cart_result = await self.db.execute(
            select(Cart).where(Cart.user_id == user_id).options(
                selectinload(Cart.items).selectinload(CartItem.variant)
            )
        )
        cart = cart_result.scalar_one_or_none()
        
        # Validate cart still has items (defensive check)
        if not cart:
            raise HTTPException(
                status_code=400,
                detail="Cart not found"
            )
        
        orderable_items = self._orderable_items(cart.items)
        if not orderable_items:
            raise HTTPException(
                status_code=400,
                detail="Cart has no items available to checkout"
            )

        pricing = validation_result['pricing']
        
        # Step 2: Get addresses and shipping method
        shipping_address_result = await self.db.execute(
            select(Address).where(Address.id == request.shipping_address_id)
        )
        shipping_address = shipping_address_result.scalar_one()
        
        shipping_method_result = await self.db.execute(
            select(ShippingMethod).where(ShippingMethod.id == request.shipping_method_id)
        )
        shipping_method = shipping_method_result.scalar_one()
        
        
        order_id = uuid7()
        # UUID7's leading hex chars are a shared timestamp, not random - slice from the tail so concurrent checkouts don't collide.
        order_number = f"ORD-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{order_id.hex[-12:].upper()}"
        address = {
            'street': shipping_address.street,
            'city': shipping_address.city,
            'state': shipping_address.state,
            'country': shipping_address.country,
            'post_code': shipping_address.post_code
        }
        order = Order(
            id=order_id,
            order_number=order_number,
            user_id=user_id,
            order_status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            fulfillment_status=FulfillmentStatus.UNFULFILLED,
            subtotal=pricing['subtotal'],
            shipping_cost=pricing['shipping']['cost'],
            tax_amount=pricing['tax']['amount'],
            tax_rate=pricing['tax']['rate'],
            discount_amount=pricing['discount']['amount'] if pricing['discount'] else 0,
            promocode_id=UUID(pricing['discount']['id']) if pricing['discount'] else None,
            total_amount=pricing['total'],
            currency=pricing['currency'],
            shipping_method=shipping_method.name,
            billing_address=address,
            shipping_address=dict(address),
            customer_notes=request.notes,
            source=OrderSource(request.source),
        )
        self.db.add(order)
        await self.db.flush()

        # Reserve stock (and the promo redemption) BEFORE charging, so a losing race for the last unit fails
        # cleanly instead of charging the card; _release_unpaid_order gives both back if payment doesn't complete.
        order_items = [
            await self._reserve_line(order, item.variant, item.quantity, item.variant.sale_price or item.variant.base_price)
            for item in orderable_items
        ]
        if order.promocode_id:
            promocode = await self.db.get(Promocode, order.promocode_id)
            promocode.used_count = (promocode.used_count or 0) + 1

        try:
            payment = await PaymentService(self.db).process_idempotent(
                user_id=user_id,
                order_id=order_id,
                amount=pricing['total'],
                payment_method_id=request.payment_method_id,
                idempotency_key=f"payment_{order_id}_{idempotency_key}" if idempotency_key else f"payment_{order_id}",
                request_id=str(uuid7()),
                frontend_calculated_amount=request.frontend_calculated_total
            )
        except Exception as e:
            # The charge can commit part-way (e.g. a new Stripe customer), so undo what reached the database too.
            await self.db.rollback()
            persisted = await self.db.get(Order, order_id)
            if persisted:
                await self._release_unpaid_order(persisted, "payment failed")
            if isinstance(e, HTTPException):
                raise
            logger.exception(f"Checkout payment error for order {order_number}: {e}")
            raise HTTPException(status_code=500, detail="We couldn't take your payment right now. Please try again.")

        if payment["status"] == "succeeded":
            await self._mark_order_paid(order)
            if background_tasks:
                background_tasks.add_task(self._send_confirmation_email, order.id)
            else:
                await self._send_confirmation_email(order.id)
        elif payment["status"] == "requires_action":
            await self.db.commit()
            logger.info(f"Order {order_number} is waiting for the customer's bank verification")
        else:
            await self._release_unpaid_order(order, f"payment {payment['status']}")
            raise HTTPException(status_code=400, detail="Your payment was not completed. Please try another card.")

        response = OrderResponse(
            id=order.id,
            order_number=order.order_number,
            user_id=user_id,
            order_status=order.order_status,
            payment_status=order.payment_status,
            fulfillment_status=order.fulfillment_status,
            total_amount=float(order.total_amount),
            subtotal=float(order.subtotal or 0),
            tax_amount=float(order.tax_amount or 0),
            tax_rate=float(order.tax_rate or 0),
            shipping_cost=float(order.shipping_cost or 0),
            discount_amount=float(order.discount_amount or 0),
            currency=order.currency,
            source=order.source,
            shipping_method=order.shipping_method,
            shipping_address=order.shipping_address,
            billing_address=order.billing_address,
            customer_notes=order.customer_notes,
            items=[
                OrderItemResponse(
                    id=item.id, variant_id=item.variant_id, quantity=item.quantity,
                    price_per_unit=float(item.price_per_unit), total_price=float(item.total_price), variant=None
                ) for item in order_items
            ],
            created_at=datetime.now(timezone.utc),
            requires_action=payment["status"] == "requires_action",
            client_secret=payment.get("client_secret") if payment["status"] == "requires_action" else None,
        )
        logger.info("Order placed", metadata={
            "order_id": str(order.id), "order_number": order_number, "user_id": str(user_id),
            "total_amount": float(order.total_amount), "payment_status": payment["status"], "milestone": "revenue"
        })
        return response

    async def _reserve_line(self, order: Order, variant: ProductVariant, quantity: int, price) -> OrderItem:
        """Add an order line and take its stock; _release_unpaid_order gives it back."""
        item = OrderItem(
            id=uuid7(), order_id=order.id, variant_id=variant.id, quantity=quantity,
            price_per_unit=price, total_price=price * quantity
        )
        self.db.add(item)
        variant.purchase_count = (variant.purchase_count or 0) + quantity
        await self.inventory_service.adjust_stock(
            StockAdjustmentCreate(
                variant_id=variant.id, quantity_change=-quantity,
                reason=f"Order placed: {order.order_number}", notes=f"Reserved for order {order.order_number}"
            ),
            adjusted_by_user_id=order.user_id,
            commit=False
        )
        return item

    async def _mark_order_paid(self, order: Order) -> None:
        """Confirm a paid order and clear its items from the customer's cart. Idempotent."""
        if order.payment_status == PaymentStatus.PAID:
            return
        order.order_status = OrderStatus.CONFIRMED
        order.payment_status = PaymentStatus.PAID
        order.confirmed_at = datetime.now(timezone.utc)
        if order.subscription_id:
            # A paid renewal moves its subscription to the next delivery; the scheduler module imports this one.
            from services.commerce.subscriptions_scheduler import renewal_paid
            await renewal_paid(self.db, order)
            await self.db.commit()
            return
        await self.db.commit()
        # Best-effort: the payment is taken, so a failed cart cleanup must never undo the order.
        try:
            variant_ids = select(OrderItem.variant_id).where(OrderItem.order_id == order.id)
            cart_ids = select(Cart.id).where(Cart.user_id == order.user_id)
            await self.db.execute(delete(CartItem).where(CartItem.cart_id.in_(cart_ids), CartItem.variant_id.in_(variant_ids)))
            await self.db.commit()
        except Exception as e:
            logger.error(f"Failed to clear ordered items from cart for order {order.order_number}: {e}")

    async def _release_unpaid_order(self, order: Order, reason: str) -> None:
        """Cancel an order whose payment didn't complete: return its stock and promo redemption. Idempotent."""
        if order.payment_status != PaymentStatus.PENDING:
            return
        await self.db.flush()  # the stock lock re-reads rows, so pending reservations must be written first
        items = (await self.db.execute(select(OrderItem).where(OrderItem.order_id == order.id))).scalars().all()
        for item in items:
            await self.inventory_service.adjust_stock(
                StockAdjustmentCreate(
                    variant_id=item.variant_id, quantity_change=item.quantity,
                    reason=f"Order not paid: {order.order_number}", notes=reason
                ),
                commit=False
            )
            variant = await self.db.get(ProductVariant, item.variant_id)
            if variant:
                variant.purchase_count = max((variant.purchase_count or 0) - item.quantity, 0)
        if order.promocode_id:
            promocode = await self.db.get(Promocode, order.promocode_id)
            if promocode:
                promocode.used_count = max((promocode.used_count or 0) - 1, 0)
        order.order_status = OrderStatus.CANCELLED
        order.payment_status = PaymentStatus.FAILED
        order.cancelled_at = datetime.now(timezone.utc)
        await self.db.commit()
        logger.info(f"Released unpaid order {order.order_number}: {reason}")

    async def complete_payment(self, order_id: UUID, user_id: UUID) -> OrderResponse:
        """After the customer finishes their bank's verification, settle the order from Stripe's final answer."""
        order = (await self.db.execute(
            select(Order).where(Order.id == order_id, Order.user_id == user_id).with_for_update()
        )).scalar_one_or_none()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        if order.payment_status == PaymentStatus.PENDING:
            try:
                status = await PaymentService(self.db).refresh_order_payment(order.id)
            except stripe.error.StripeError:
                # Nothing is lost: the order stays pending until this is retried, the webhook arrives or it expires.
                raise HTTPException(status_code=503, detail="We couldn't reach the payment provider. Please try again in a moment.")
            if status == "succeeded":
                await self._mark_order_paid(order)
                await self._send_confirmation_email(order.id)
            elif status in ("requires_payment_method", "canceled"):
                await self._release_unpaid_order(order, "bank verification failed")
                raise HTTPException(status_code=400, detail="Your bank didn't approve this payment. Please try another card.")
            else:
                raise HTTPException(status_code=409, detail="Your bank is still verifying this payment. Please try again in a moment.")
        elif order.payment_status != PaymentStatus.PAID:
            raise HTTPException(status_code=400, detail="This order's payment was not completed.")
        return await self.get(order.id, user_id)

    async def expire_unverified_orders(self, older_than_minutes: int = 30) -> int:
        """Release orders whose bank verification was abandoned; returns how many were released."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
        orders = (await self.db.execute(
            # Unpaid renewals belong to the subscription scheduler, which retries them.
            select(Order).where(
                Order.payment_status == PaymentStatus.PENDING, Order.created_at < cutoff, Order.subscription_id.is_(None)
            )
        )).scalars().all()
        payments = PaymentService(self.db)
        for order in orders:
            status = await payments.refresh_order_payment(order.id, cancel_if_unfinished=True)
            if status == "succeeded":
                await self._mark_order_paid(order)
            else:
                await self._release_unpaid_order(order, "bank verification abandoned")
        return len(orders)

    async def _send_confirmation_email(self, order_id: UUID) -> None:
        """Best-effort order confirmation email; never affects the order itself."""
        try:
            order = (await self.db.execute(
                select(Order).where(Order.id == order_id).options(
                    selectinload(Order.user),
                    selectinload(Order.items).selectinload(OrderItem.variant).selectinload(ProductVariant.product),
                )
            )).scalar_one()
            if not order.user or not order.user.email:
                return
            await send_order_confirmation(
                order.user.email, order.user.firstname, order.user.language, str(order.id), order.order_number,
                items=[
                    {"name": _item_name(item), "quantity": item.quantity, "total": float(item.total_price or 0)}
                    for item in order.items
                ],
                total=float(order.total_amount or 0), currency=order.currency, address=address_lines(order.shipping_address),
            )
        except Exception as e:
            logger.error(f"Failed to send confirmation email for order {order_id}: {e}")

    async def list(
        self,
        user_id: Optional[UUID] = None,
        page: int = 1,
        limit: int = 10,
        status: Optional[str] = None,
        search: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get paginated list of orders. If user_id is None, returns all orders (admin)."""

        try:
            offset = (page - 1) * limit

            # Build base query
            base_query = select(Order).options(
                selectinload(Order.items).selectinload(OrderItem.variant).selectinload(ProductVariant.images),
                selectinload(Order.items).selectinload(OrderItem.variant).selectinload(ProductVariant.product),
                selectinload(Order.user)
            )
            count_query = select(func.count()).select_from(Order)

            # Apply filters
            conditions = []

            if user_id:
                conditions.append(Order.user_id == user_id)

            if status:
                conditions.append(Order.order_status == status)

            if search:
                term = f"%{search.strip()}%"
                conditions.append(or_(
                    Order.id.cast(String).ilike(term),
                    Order.order_number.ilike(term),
                    Order.user.has(or_(User.email.ilike(term), User.firstname.ilike(term), User.lastname.ilike(term))),
                ))

            if date_from:
                try:
                    from_date = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
                    conditions.append(Order.created_at >= from_date)
                except ValueError:
                    pass

            if date_to:
                try:
                    to_date = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
                    conditions.append(Order.created_at <= to_date)
                except ValueError:
                    pass

            if min_price is not None:
                conditions.append(Order.total_amount >= min_price)
            if max_price is not None:
                conditions.append(Order.total_amount <= max_price)

            # Apply all conditions
            if conditions:
                base_query = base_query.where(and_(*conditions))
                count_query = count_query.where(and_(*conditions))

            # Apply sorting
            sort_column = Order.created_at
            if sort_by == "total":
                sort_column = Order.total_amount
            elif sort_by == "status":
                sort_column = Order.order_status

            if sort_order == "asc":
                base_query = base_query.order_by(sort_column.asc())
            else:
                base_query = base_query.order_by(sort_column.desc())

            # Get total count
            total = await self.db.scalar(count_query) or 0

            # Get paginated results
            result = await self.db.execute(base_query.offset(offset).limit(limit))
            orders = result.scalars().all()

            formatted_orders = []
            for order in orders:
                order_response = await self._format_order_response(order)
                order_dict = order_response.model_dump()
                if order.user:
                    order_dict["customer"] = {
                        "id": str(order.user.id),
                        "email": order.user.email,
                        "name": f"{order.user.firstname} {order.user.lastname}"
                    }
                formatted_orders.append(order_dict)

            return {
                "orders": formatted_orders,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "pages": (total + limit - 1) // limit if limit > 0 else 1
                }
            }
        except Exception as e:
            logger.error(f"Error in list method: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise

    async def get(self, order_id: UUID, user_id: Optional[UUID] = None) -> Optional[OrderResponse]:
        """Get a specific order by ID. If user_id is provided, only return that user's order."""
        try:
            # Build query - filter by user_id if provided (for regular users), no filter for admins
            if user_id:
                query = select(Order).where(and_(Order.id == order_id, Order.user_id == user_id)).options(
                    selectinload(Order.items).selectinload(OrderItem.variant).selectinload(ProductVariant.images),
                    selectinload(Order.items).selectinload(OrderItem.variant).selectinload(ProductVariant.product),
                    selectinload(Order.user)
                )
            else:
                query = select(Order).where(Order.id == order_id).options(
                    selectinload(Order.items).selectinload(OrderItem.variant).selectinload(ProductVariant.images),
                    selectinload(Order.items).selectinload(OrderItem.variant).selectinload(ProductVariant.product),
                    selectinload(Order.user)
                )

            result = await self.db.execute(query)
            order = result.scalar_one_or_none()

            if not order:
                return None

            return await self._format_order_response(order)
        except Exception as e:
            logger.error(f"Error in get method for order {order_id}: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise

    async def cancel(self, order_id: UUID, user_id: UUID) -> OrderResponse:
        """Customer cancellation: pending or confirmed orders only."""
        order = (await self.db.execute(
            select(Order).where(and_(Order.id == order_id, Order.user_id == user_id)).with_for_update()
        )).scalar_one_or_none()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        if order.order_status not in [OrderStatus.PENDING, OrderStatus.CONFIRMED]:
            raise HTTPException(status_code=400, detail="Order cannot be cancelled")
        await self._cancel(order, user_id, "Order cancelled by customer")
        # Reload with items and variants: the response needs them and async sessions can't lazy-load.
        return await self.get(order.id, user_id)

    async def _cancel(self, order: Order, actor_id: Optional[UUID], description: str) -> None:
        """Refund what was paid, put the stock back and close the order, all or nothing."""
        try:
            if order.payment_status == PaymentStatus.PAID:
                await self._refund_for_cancellation(order)
                order.payment_status = PaymentStatus.REFUNDED
            order.order_status = OrderStatus.CANCELLED
            order.fulfillment_status = FulfillmentStatus.CANCELLED
            order.cancelled_at = datetime.now(tz=timezone.utc)

            items = (await self.db.execute(
                select(OrderItem).where(OrderItem.order_id == order.id).options(
                    selectinload(OrderItem.variant).selectinload(ProductVariant.inventory)
                )
            )).scalars().all()
            for item in items:
                if not item.variant or not item.variant.inventory:
                    logger.warning(f"No inventory found for variant {item.variant_id} during order cancellation.")
                    continue
                await self.inventory_service.increment(
                    variant_id=item.variant.id,
                    quantity=item.quantity,
                    location_id=item.variant.inventory.location_id,
                    order_id=order.id,
                    user_id=actor_id,
                )
            self.db.add(TrackingEvent(order_id=order.id, status="cancelled", description=description, location="System"))
            await self.db.commit()
            await self.db.refresh(order)
        except HTTPException:
            await self.db.rollback()
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(status_code=500, detail=f"Order cancellation failed: {str(e)}")

    async def _refund_for_cancellation(self, order: Order) -> None:
        """Refund what's left of a paid order via Stripe; raises 400 so a failed refund leaves the order uncancelled."""
        payments = PaymentService(self.db)
        remaining = Decimal(str(order.total_amount)) - await payments.refunded_total(order.id)
        if remaining > 0:
            await payments.refund_order(order, remaining, f"order-cancel-{order.id}", "Refund for cancelled order")

    async def update_status(
        self, 
        order_id: UUID, 
        status: str, 
        tracking_number: Optional[str] = None,
        carrier_name: Optional[str] = None,
        location: Optional[str] = None,
        description: Optional[str] = None,
        background_tasks = None
    ) -> Order:
        """Update order status (admin function)"""
        query = select(Order).where(Order.id == order_id)
        result = await self.db.execute(query)
        order = result.scalar_one_or_none()

        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        try:
            status_enum = OrderStatus(status.lower())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid order status: {status}")
        if status_enum not in NEXT_STATUSES.get(order.order_status, set()):
            raise HTTPException(status_code=400, detail=f"A {order.order_status.value} order can't be marked {status_enum.value}")
        if status_enum == OrderStatus.CANCELLED:
            await self._cancel(order, None, description or "Order cancelled by the shop")
            return order
        if order.payment_status != PaymentStatus.PAID:
            raise HTTPException(status_code=400, detail="This order hasn't been paid yet")
        order.order_status = status_enum

        # Set lifecycle timestamps
        now = datetime.now(tz=timezone.utc)
        if status_enum == OrderStatus.CONFIRMED and not order.confirmed_at:
            order.confirmed_at = now
        elif status_enum == OrderStatus.SHIPPED:
            order.shipped_at = now
            if not order.confirmed_at:
                order.confirmed_at = now
        elif status_enum == OrderStatus.DELIVERED:
            order.delivered_at = now
            if not order.shipped_at:
                order.shipped_at = now
            if not order.confirmed_at:
                order.confirmed_at = now
        elif status_enum == OrderStatus.CANCELLED:
            order.cancelled_at = now

        # Auto-update fulfillment_status
        fulfillment_map = {
            OrderStatus.PENDING: FulfillmentStatus.UNFULFILLED,
            OrderStatus.CONFIRMED: FulfillmentStatus.UNFULFILLED,
            OrderStatus.PROCESSING: FulfillmentStatus.UNFULFILLED,
            OrderStatus.SHIPPED: FulfillmentStatus.PARTIAL,
            OrderStatus.DELIVERED: FulfillmentStatus.FULFILLED,
            OrderStatus.CANCELLED: FulfillmentStatus.CANCELLED,
            OrderStatus.REFUNDED: FulfillmentStatus.CANCELLED,
        }
        order.fulfillment_status = fulfillment_map.get(status_enum, order.fulfillment_status)

        if tracking_number:
            order.tracking_number = tracking_number

        # Auto-populate carrier from ShippingMethod if not provided
        if not carrier_name and order.shipping_method:
            shipping_method_result = await self.db.execute(
                select(ShippingMethod).where(ShippingMethod.name == order.shipping_method)
            )
            # name isn't unique - several methods can share one (e.g. admin-recreated
            # "Standard"), so take the first match instead of scalar_one_or_none().
            shipping_method = shipping_method_result.scalars().first()
            if shipping_method and shipping_method.carrier:
                carrier_name = shipping_method.carrier

        if carrier_name:
            order.carrier = carrier_name

        # Generate appropriate description based on status
        if not description:
            status_descriptions = {
                'pending': 'Order placed and awaiting confirmation',
                'confirmed': 'Order confirmed and payment processed',
                'processing': 'Order is being prepared for shipment',
                'shipped': 'Package has been shipped and is in transit',
                'out_for_delivery': 'Package is out for delivery',
                'delivered': 'Package has been successfully delivered',
                'cancelled': 'Order has been cancelled'
            }
            description = status_descriptions.get(status, f"Order status updated to {status}")

        # Determine location based on status if not provided
        if not location:
            location_map = {
                'pending': 'System',
                'confirmed': 'Processing Center',
                'processing': 'Warehouse',
                'shipped': 'In Transit',
                'out_for_delivery': 'Local Distribution Center',
                'delivered': 'Delivery Address',
                'cancelled': 'System'
            }
            location = location_map.get(status, 'Fulfillment Center')

        # Add tracking event
        tracking_event = TrackingEvent(
            order_id=order.id,
            status=status,
            description=description,
            location=location
        )
        self.db.add(tracking_event)

        await self.db.commit()
        await self.db.refresh(order)
        
        # Send shipping update email for shipped/delivered orders
        if status in ['shipped', 'delivered'] and background_tasks:
            # Get user details for email
            user_result = await self.db.execute(
                select(User).where(User.id == order.user_id)
            )
            user = user_result.scalar_one_or_none()
            
            if user and user.email:
                if status == 'shipped':
                    queue(background_tasks, send_shipping_update, user.email, user.firstname, user.language, str(order.id),
                          order.order_number, order.carrier, order.tracking_number, None)
                else:
                    queue(background_tasks, send_order_delivered, user.email, user.firstname, user.language, str(order.id),
                          order.order_number, order.delivered_at or datetime.now(timezone.utc), address_lines(order.shipping_address))
        
        return order

    async def _format_order_response(self, order: Order) -> OrderResponse:
        """Format order for response"""
        items = []
        calculated_subtotal = 0
        
        for item in order.items:
            # Include variant details with images
            variant_data = None
            if item.variant:
                variant_data = {
                    "id": str(item.variant.id),
                    "name": item.variant.name,
                    "product_name": item.variant.product.name if item.variant.product else None,
                    "product_id": str(item.variant.product_id) if item.variant.product_id else None,
                    "sku": item.variant.sku,
                    "images": [
                        {
                            "id": str(img.id),
                            "url": img.url,
                            "is_primary": img.is_primary,
                            "sort_order": img.sort_order
                        }
                        for img in item.variant.images
                    ] if item.variant.images else []
                }
            else:
                logger.warning(f"Order item {item.id} has no variant associated")
            
            # Add to calculated subtotal
            calculated_subtotal += item.total_price or 0
            
            items.append(OrderItemResponse(
                id=item.id,
                variant_id=item.variant.id if item.variant else item.variant_id,
                quantity=item.quantity,
                price_per_unit=float(item.price_per_unit) if item.price_per_unit else 0.0,
                total_price=float(item.total_price) if item.total_price else 0.0,
                variant=variant_data
            ))

        # Use calculated subtotal if order subtotal is missing or zero
        display_subtotal = order.subtotal if order.subtotal and order.subtotal > 0 else calculated_subtotal

        # CRITICAL FIX: Validate and correct order total before returning to frontend
        expected_total = display_subtotal + (order.shipping_cost or 0) + (order.tax_amount or 0) - (order.discount_amount or 0)
        
        # If the stored total is wrong, log it and use the calculated total
        if abs(expected_total - order.total_amount) > 0.01:
            logger.warning(f"🔧 ORDER TOTAL CORRECTION for order {order.id}:")
            logger.warning(f"   Stored total:    ${order.total_amount:.2f}")
            logger.warning(f"   Calculated total: ${expected_total:.2f}")
            logger.warning(f"   Subtotal:        ${display_subtotal:.2f}")
            logger.warning(f"   Shipping:        ${order.shipping_cost or 0:.2f}")
            logger.warning(f"   Tax:             ${order.tax_amount or 0:.2f}")
            logger.warning(f"   Discount:        ${order.discount_amount or 0:.2f}")
            
            # Use the calculated total instead of the stored (incorrect) total
            corrected_total = expected_total
            
            # Optionally update the database with the correct total
            try:
                order.total_amount = corrected_total
                await self.db.commit()
                # updated_at is DB-computed and stale after this UPDATE regardless
                # of expire_on_commit, so refresh before the plain reads below.
                await self.db.refresh(order)
                logger.info(f"✅ Updated order {order.id} total in database to ${corrected_total:.2f}")
            except Exception as e:
                logger.error(f"Failed to update order total in database: {e}")
                # Continue with the corrected total even if DB update fails
        else:
            corrected_total = order.total_amount

        # Calculate estimated delivery
        estimated_delivery = None
        current_status = (order.order_status.value if hasattr(order.order_status, 'value') else str(order.order_status or '')).lower()
        if current_status in ["confirmed", "shipped"]:
            estimated_days = 5  # Default, could be from shipping method
            estimated_delivery = (order.created_at + timedelta(days=estimated_days)).isoformat()

        # Generate tracking URL if tracking number and shipping method exist
        tracking_url = None
        if order.tracking_number and order.shipping_method:
            shipping_method_result = await self.db.execute(
                select(ShippingMethod).where(ShippingMethod.name == order.shipping_method)
            )
            # name isn't unique - take the first match instead of scalar_one_or_none().
            shipping_method = shipping_method_result.scalars().first()
            if shipping_method and shipping_method.tracking_url_template:
                tracking_url = shipping_method.tracking_url_template.replace('{tracking_number}', order.tracking_number)

        return OrderResponse(
            id=order.id,
            order_number=order.order_number,
            user_id=order.user_id,
            order_status=order.order_status,
            payment_status=order.payment_status,
            fulfillment_status=order.fulfillment_status,
            total_amount=float(corrected_total),  # Use corrected total instead of order.total_amount
            subtotal=float(display_subtotal) if display_subtotal else None,
            tax_amount=float(order.tax_amount) if order.tax_amount else None,
            tax_rate=float(order.tax_rate) if order.tax_rate is not None else None,
            shipping_cost=float(order.shipping_cost) if order.shipping_cost else None,
            discount_amount=float(order.discount_amount) if order.discount_amount else None,
            currency=order.currency,  # Use order's currency
            source=order.source,
            shipping_method=order.shipping_method,
            tracking_number=order.tracking_number,
            carrier=order.carrier,
            tracking_url=tracking_url,
            estimated_delivery=estimated_delivery,
            shipping_address=order.shipping_address,
            billing_address=order.billing_address,
            customer_notes=order.customer_notes,
            internal_notes=order.internal_notes,
            items=items,
            confirmed_at=order.confirmed_at,
            shipped_at=order.shipped_at,
            delivered_at=order.delivered_at,
            cancelled_at=order.cancelled_at,
            created_at=order.created_at if order.created_at else datetime.now(timezone.utc),
            updated_at=order.updated_at
        )


    async def tracking(self, order_id: UUID, user_id: UUID) -> Dict[str, Any]:
        """Get order tracking information for authenticated user"""
        try:
            # Get order with tracking events
            query = select(Order).where(
                and_(Order.id == order_id, Order.user_id == user_id)
            ).options(
                selectinload(Order.tracking_events)
            )
            
            result = await self.db.execute(query)
            order = result.scalar_one_or_none()
            
            if not order:
                raise APIException(status_code=404, message="Order not found")
            
            # Format tracking events
            tracking_events = []
            for event in order.tracking_events:
                tracking_events.append({
                    "id": str(event.id),
                    "status": event.status,
                    "description": event.description,
                    "location": event.location,
                    "timestamp": event.created_at.isoformat() if event.created_at else None
                })
            
            # Sort events by timestamp (newest first)
            tracking_events.sort(key=lambda x: x["timestamp"] or "", reverse=True)
            
            return {
                "order_id": str(order.id),
                "order_number": order.order_number,
                "status": order.order_status,
                "tracking_number": order.tracking_number,
                "carrier": order.carrier,
                "estimated_delivery": self._calculate_estimated_delivery(order),
                "tracking_events": tracking_events,
                "shipping_address": order.shipping_address
            }
            
        except APIException:
            raise
        except Exception as e:
            logger.error(f"Failed to get order tracking for order {order_id}: {e}")
            raise APIException(
                status_code=500,
                message="Failed to retrieve tracking information"
            )

    async def payments(self, order_id: UUID, user_id: UUID, is_admin: bool = False) -> Dict[str, Any]:
        """An order's payment attempts and transactions: the owner's order, or any order for staff."""
        try:
            query = select(Order).where(
                Order.id == order_id if is_admin else and_(Order.id == order_id, Order.user_id == user_id)
            ).options(
                selectinload(Order.payment_intents),
                selectinload(Order.transactions)
            )

            result = await self.db.execute(query)
            order = result.scalar_one_or_none()

            if not order:
                raise APIException(status_code=404, message="Order not found")

            return {
                "order_id": str(order.id),
                "order_number": order.order_number,
                "payment_status": order.payment_status,
                # The client secret only matters while a payment is being made, never on a record view.
                "payment_intents": [{**intent.to_dict(), "client_secret": None} for intent in order.payment_intents],
                "transactions": [txn.to_dict() for txn in order.transactions],
            }

        except APIException:
            raise
        except Exception as e:
            logger.error(f"Failed to get payments for order {order_id}: {e}")
            raise APIException(
                status_code=500,
                message="Failed to retrieve payment information"
            )

    async def tracking_public(self, order_id: str) -> Dict[str, Any]:
        """Get order tracking information without authentication (public endpoint)"""
        try:
            # Try to parse as UUID first, if fails treat as order_number
            try:
                order_uuid = UUID(order_id)
                query = select(Order).where(Order.id == order_uuid).options(
                    selectinload(Order.tracking_events)
                )
            except ValueError:
                # Not a UUID, treat as order_number
                query = select(Order).where(Order.order_number == order_id).options(
                    selectinload(Order.tracking_events)
                )

            result = await self.db.execute(query)
            order = result.scalar_one_or_none()

            if not order:
                raise HTTPException(status_code=404, detail="Order not found")
            
            # Only return limited information for public access
            tracking_events = []
            for event in order.tracking_events:
                # Only include public-safe tracking events
                if event.status in ["confirmed", "processing", "shipped", "out_for_delivery", "delivered"]:
                    tracking_events.append({
                        "status": event.status,
                        "description": event.description,
                        "location": event.location,
                        "timestamp": event.created_at.isoformat() if event.created_at else None
                    })
            
            # Sort events by timestamp (newest first)
            tracking_events.sort(key=lambda x: x["timestamp"] or "", reverse=True)
            
            return {
                "order_number": order.order_number,
                "status": order.order_status,
                "tracking_number": order.tracking_number,
                "carrier": order.carrier,
                "estimated_delivery": self._calculate_estimated_delivery(order),
                "tracking_events": tracking_events
                # Note: No shipping address or sensitive info for public access
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to get public order tracking for order {order_id}: {e}")
            raise HTTPException(
                status_code=404,
                detail="Order not found or tracking information unavailable"
            )

    async def reorder(self, order_id: UUID, user_id: UUID) -> OrderResponse:
        """Create a new order from an existing order"""
        try:
            # Get the original order with items
            query = select(Order).where(
                and_(Order.id == order_id, Order.user_id == user_id)
            ).options(
                selectinload(Order.items).selectinload(OrderItem.variant)
            )
            
            result = await self.db.execute(query)
            original_order = result.scalar_one_or_none()
            
            if not original_order:
                raise HTTPException(status_code=404, detail="Original order not found")

            # Snapshot before clear_cart(): it expires every object in the session,
            # which would strand this loop trying to lazy-load items later.
            original_items = [(item.variant_id, item.quantity) for item in original_order.items]

            # Clear user's current cart
            cart_service = CartService(self.db)
            await cart_service.clear_cart(user_id)

            # Add items from original order to cart
            for variant_id, quantity in original_items:
                # Check if variant still exists and is available
                variant_query = select(ProductVariant).where(ProductVariant.id == variant_id)
                variant_result = await self.db.execute(variant_query)
                variant = variant_result.scalar_one_or_none()

                if variant and variant.is_active:
                    # Check stock availability
                    stock_check = await self.inventory_service.check_stock(
                        variant_id=variant_id,
                        quantity=quantity
                    )

                    # Add to cart with available quantity
                    quantity_to_add = min(quantity, stock_check.get("current_stock", 0))
                    if quantity_to_add > 0:
                        await cart_service.add_to_cart(
                            user_id=user_id,
                            variant_id=variant_id,
                            quantity=quantity_to_add
                        )
            
            # Get updated cart
            cart = await cart_service.get_or_create(user_id)
            
            if not cart.items:
                raise HTTPException(
                    status_code=400,
                    detail="No items from the original order are currently available"
                )
            
            return {
                "message": "Items added to cart successfully",
                "cart_items": len(cart.items),
                "original_order_id": str(order_id),
                "cart_id": str(cart.id)
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to reorder from order {order_id}: {e}")
            raise HTTPException(
                status_code=500,
                detail="Failed to create reorder"
            )

    async def invoice(self, order_id: UUID, user_id: UUID, is_admin: bool = False) -> Dict[str, Any]:
        """Invoice details for an order, which the frontend renders as a PDF; customers get their own orders, admins any order."""
        query = select(Order).where(
            Order.id == order_id if is_admin else and_(Order.id == order_id, Order.user_id == user_id)
        ).options(
            selectinload(Order.items).selectinload(OrderItem.variant).selectinload(ProductVariant.product),
            selectinload(Order.user)
        )
        order = (await self.db.execute(query)).scalar_one_or_none()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        user = order.user
        customer_name = (f"{user.firstname or ''} {user.lastname or ''}".strip() or user.email) if user else "Customer"
        money = lambda amount: float(amount or 0)
        return {
            "invoice_ref": f"INV-{order.order_number}",
            "order_id": str(order.id),
            "order_number": order.order_number,
            "issued_at": order.created_at.isoformat(),
            "payment_status": getattr(order.payment_status, "value", order.payment_status),
            "currency": order.currency,
            "seller": {
                "name": settings.STORE_NAME,
                "address_lines": [line.strip() for line in settings.STORE_ADDRESS.split("|") if line.strip()],
            },
            "customer": {"name": customer_name, "email": user.email if user else None, "phone": user.phone if user else None},
            "shipping_address_lines": address_lines(order.shipping_address),
            "items": [
                {
                    "name": item.variant.product.name if item.variant and item.variant.product else "Product",
                    "description": item.variant.name if item.variant else "",
                    "quantity": item.quantity,
                    "unit_price": money(item.price_per_unit),
                    "total": money(item.total_price),
                }
                for item in order.items
            ],
            "subtotal": money(order.subtotal),
            "discount": money(order.discount_amount),
            "tax_rate": money(order.tax_rate),
            "tax_amount": money(order.tax_amount),
            "shipping": money(order.shipping_cost),
            "total": money(order.total_amount),
        }

    async def add_note(self, order_id: UUID, user_id: UUID, note: str, is_admin: bool = False) -> Dict[str, List[Dict[str, str]]]:
        """Append a timestamped note: customers to their own order's customer notes, staff to any order's internal notes."""
        query = select(Order).where(Order.id == order_id)
        if not is_admin:
            query = query.where(Order.user_id == user_id)
        order = (await self.db.execute(query)).scalar_one_or_none()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        entry = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}] {note.strip()}"
        field = "internal_notes" if is_admin else "customer_notes"
        current = getattr(order, field)
        setattr(order, field, f"{current}\n\n{entry}" if current else entry)
        await self.db.commit()
        return {
            "customer": self._parse_notes(order.customer_notes),
            "internal": self._parse_notes(order.internal_notes) if is_admin else [],
        }

    @staticmethod
    def _parse_notes(text: Optional[str]) -> List[Dict[str, str]]:
        """'[YYYY-MM-DD HH:MM] text' blocks -> [{timestamp, note}], oldest first."""
        entries = re.findall(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})(?::\d{2})?\] (.*?)(?=\n\n\[\d{4}-\d{2}-\d{2} |\Z)", text or "", re.DOTALL)
        return [{"timestamp": ts, "note": body.strip()} for ts, body in entries]

    async def notes(self, order_id: UUID, user_id: UUID, is_admin: bool = False) -> Dict[str, List[Dict[str, str]]]:
        """An order's notes as entries; internal (staff) notes only for staff."""
        query = select(Order).where(Order.id == order_id)
        if not is_admin:
            query = query.where(Order.user_id == user_id)
        order = (await self.db.execute(query)).scalar_one_or_none()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        return {
            "customer": self._parse_notes(order.customer_notes),
            "internal": self._parse_notes(order.internal_notes) if is_admin else [],
        }


    def _calculate_estimated_delivery(self, order: Order) -> Optional[str]:
        """Calculate estimated delivery date based on order status and shipping method"""
        try:
            if order.delivered_at:
                return order.delivered_at.isoformat()
            
            if order.order_status in ["cancelled", "refunded"]:
                return None
            
            # Base delivery estimate on shipping method and current status
            base_days = 5  # Default delivery time
            
            # Adjust based on shipping method
            if order.shipping_method:
                shipping_method_lower = order.shipping_method.lower()
                if "express" in shipping_method_lower or "overnight" in shipping_method_lower:
                    base_days = 1
                elif "priority" in shipping_method_lower or "2-day" in shipping_method_lower:
                    base_days = 2
                elif "standard" in shipping_method_lower:
                    base_days = 5
                elif "economy" in shipping_method_lower:
                    base_days = 7
            
            # Calculate from appropriate date
            if order.shipped_at:
                estimated_date = order.shipped_at + timedelta(days=base_days)
            elif order.confirmed_at:
                estimated_date = order.confirmed_at + timedelta(days=base_days + 2)  # Add processing time
            else:
                estimated_date = order.created_at + timedelta(days=base_days + 3)  # Add confirmation + processing time
            
            return estimated_date.isoformat()
            
        except Exception as e:
            logger.error(f"Failed to calculate estimated delivery for order {order.id}: {e}")
            return None
    async def get_statistics(self, date_from: Optional[str] = None, date_to: Optional[str] = None) -> dict:
        """Get order statistics (admin only)."""

        # Build base query
        query = select(Order)

        if date_from:
            try:
                from_date = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
                query = query.where(Order.created_at >= from_date)
            except ValueError:
                pass

        if date_to:
            try:
                to_date = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
                query = query.where(Order.created_at <= to_date)
            except ValueError:
                pass

        # Get counts by status
        status_query = select(Order.order_status, func.count(Order.id)).group_by(Order.order_status)
        if date_from:
            try:
                from_date = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
                status_query = status_query.where(Order.created_at >= from_date)
            except ValueError:
                pass
        if date_to:
            try:
                to_date = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
                status_query = status_query.where(Order.created_at <= to_date)
            except ValueError:
                pass

        status_result = await self.db.execute(status_query)
        status_counts = {status: count for status, count in status_result.all()}

        # Get total revenue
        revenue_query = select(func.sum(Order.total_amount)).where(Order.order_status.notin_(["cancelled", "refunded"]))
        if date_from:
            try:
                from_date = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
                revenue_query = revenue_query.where(Order.created_at >= from_date)
            except ValueError:
                pass
        if date_to:
            try:
                to_date = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
                revenue_query = revenue_query.where(Order.created_at <= to_date)
            except ValueError:
                pass

        revenue_result = await self.db.execute(revenue_query)
        total_revenue = revenue_result.scalar() or 0

        # Get total orders count
        count_query = select(func.count(Order.id))
        if date_from:
            try:
                from_date = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
                count_query = count_query.where(Order.created_at >= from_date)
            except ValueError:
                pass
        if date_to:
            try:
                to_date = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
                count_query = count_query.where(Order.created_at <= to_date)
            except ValueError:
                pass

        count_result = await self.db.execute(count_query)
        total_orders = count_result.scalar() or 0

        return {
            "total_orders": total_orders,
            "total_revenue": float(total_revenue),
            "status_breakdown": status_counts,
            "date_range": {
                "from": date_from,
                "to": date_to
            }
        }


    

