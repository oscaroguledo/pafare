from fastapi import APIRouter, Depends, Query, status, BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID
from datetime import datetime, timezone
from typing import Optional
from core.db import get_db, logger
from core.dependencies import require_auth
from core.utils.response import Response
from core.exceptions import APIException
from schemas.commerce.subscriptions import Create, Update, AddProducts, RemoveProducts, UpdateQuantity, DiscountApplication, ChangeFrequency, SkipShipment
from services.commerce.subscriptions import SubscriptionService
from models.accounts.user import User, Address
from models.catalog.product import ProductVariant
from models.commerce.subscriptions import Subscription
from models.commerce.orders import Order
from sqlalchemy import select, and_
from core.dependencies import require_admin, require_auth
from schemas.commerce.subscriptions import Create, Update, CostCalculation, AddProducts, RemoveProducts, UpdateQuantity, DiscountApplication, ChangeFrequency, SkipShipment
from services.commerce.subscriptions_scheduler import SubscriptionScheduler, renewal_lock
from core.config import settings

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.post("/trigger-order-processing/")
async def trigger_order_processing(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Run the renewal job now; it also runs every hour (admin only)."""
    try:
        async with renewal_lock(db) as acquired:
            if not acquired:
                raise APIException(status_code=status.HTTP_409_CONFLICT, message="Renewals are already running. Try again in a minute.")
            result = await SubscriptionScheduler(db).process_due_subscriptions()
        return Response.success(data=result, message="Subscription order processing triggered successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering subscription order processing: {e}")
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to trigger order processing: {str(e)}"
        )


@router.post("/trigger-notifications/")
async def trigger_notifications(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Send the upcoming-delivery reminders now (they also go out automatically) (admin only)."""
    try:
        sent = await SubscriptionScheduler(db).send_upcoming_reminders()
        return Response.success(data={"sent": sent}, message=f"Sent {sent} reminder email{'' if sent == 1 else 's'}")
    except Exception as e:
        logger.error(f"Error triggering subscription notifications: {e}")
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to trigger notifications: {str(e)}"
        )


@router.get("/plans/")
async def plans(
    db: AsyncSession = Depends(get_db)
):
    """Get available subscription plans (public endpoint)."""
    try:
        # Return subscription plan information
        # This is a simplified version - in a real app, you might have a SubscriptionPlan model
        plans = [
            {
                "id": "monthly",
                "name": "Monthly Plan",
                "description": "Monthly subscription with flexible delivery",
                "billing_cycle": "monthly",
                "features": [
                    "Flexible delivery scheduling",
                    "Skip or pause anytime",
                    "10% off all orders",
                    "Free shipping on orders over $50"
                ]
            },
            {
                "id": "quarterly",
                "name": "Quarterly Plan",
                "description": "Quarterly subscription with best value",
                "billing_cycle": "quarterly",
                "features": [
                    "Save 15% compared to monthly",
                    "Priority delivery scheduling",
                    "15% off all orders",
                    "Free shipping on all orders",
                    "Exclusive access to new products"
                ]
            },
            {
                "id": "yearly",
                "name": "Yearly Plan",
                "description": "Annual subscription with maximum savings",
                "billing_cycle": "yearly",
                "features": [
                    "Save 25% compared to monthly",
                    "VIP delivery scheduling",
                    "20% off all orders",
                    "Free express shipping",
                    "Exclusive access to new products",
                    "Dedicated customer support"
                ]
            }
        ]
        
        return Response.success(data=plans, message="Subscription plans retrieved successfully")
    except Exception as e:
        logger.error(f"Error fetching subscription plans: {e}")
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to fetch subscription plans: {str(e)}"
        )


@router.get("/due/")
async def list_due(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """List active subscriptions currently due for billing (admin only)."""
    try:
        subscription_service = SubscriptionService(db)
        due = await subscription_service.list_due()
        return Response.success(data=[s.to_dict() for s in due])
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to list due subscriptions: {str(e)}"
        )


@router.post("/calculate-cost/")
async def calculate(
    cost_request: CostCalculation,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
    ):
    """Calculate subscription cost with VAT before creating subscription."""
    try:
        subscription_service = SubscriptionService(db)
        
        # Get variants
        variant_result = await db.execute(
            select(ProductVariant).where(ProductVariant.id.in_(cost_request.variant_ids))
        )
        variants = variant_result.scalars().all()
        
        if len(variants) != len(cost_request.variant_ids):
            raise APIException(
                status_code=status.HTTP_400_BAD_REQUEST,
                message="Some product variants not found"
            )
        
        # Get customer address for tax calculation
        customer_address = None
        if cost_request.delivery_address_id:
            address_result = await db.execute(
                select(Address).where(
                    and_(Address.id == cost_request.delivery_address_id, Address.user_id == current_user.id)
                )
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

        pricing = await subscription_service._calculate_pricing(
            variants=variants,
            variant_quantities=cost_request.variant_quantities or {},
            customer_address=customer_address,
            currency=settings.STORE_CURRENCY,
            user_id=current_user.id,
            shipping_method_id=cost_request.shipping_method_id,
            discount_code=cost_request.discount_code,
        )
        return Response.success(data={**pricing, "currency": settings.STORE_CURRENCY})

    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error calculating subscription cost: {e}")
        raise APIException(
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"Failed to calculate subscription cost: {str(e)}"
        )


@router.post("/")
async def create(
    subscription_data: Create,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
    ):
    """Create a new subscription with product quantities and VAT calculation."""
    try:
        logger.info(f"API: Creating subscription, user_id={current_user.id if current_user else 'None'}, data={subscription_data}")
        subscription_service = SubscriptionService(db)
        # Extract data from request
        product_variant_ids = subscription_data.variant_ids or []
        variant_quantities = subscription_data.variant_quantities

        # A subscription needs at least one variant
        if not product_variant_ids:
            raise APIException(
                status_code=status.HTTP_400_BAD_REQUEST,
                message="variant_ids is required to create a subscription"
            )

        # Create subscription with quantities
        subscription = await subscription_service.create(
            user_id=current_user.id,
            name=subscription_data.name,
            variant_ids=product_variant_ids,
            variant_quantities=variant_quantities,
            delivery_address_id=subscription_data.delivery_address_id,
            billing_cycle=subscription_data.billing_cycle,
            current_period_start=subscription_data.current_period_start,
            shipping_method_id=subscription_data.shipping_method_id,
            discount_code=subscription_data.discount_code,
        )
        return Response.success(
            data=subscription.to_dict(include_products=True),
            message="Subscription created successfully! Orders will be placed automatically based on your billing cycle."
        )
    except APIException as e:
        raise e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating subscription: {e}")
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to create subscription: {str(e)}"
        )
@router.get("/")
async def list_subscriptions(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """List subscriptions. Returns all subscriptions for admin, user's subscriptions for regular users."""
    try:
        subscription_service = SubscriptionService(db)
        is_admin = current_user.is_staff
        
        if is_admin:
            result = await subscription_service.list(
                page=page, limit=limit, status=status_filter, search=search,
                date_from=date_from, date_to=date_to, sort_by=sort_by, sort_order=sort_order
            )
            if isinstance(result, dict) and "data" in result and "pagination" in result:
                return Response.success(data=result.get("data", []), pagination=result.get("pagination"))
            return Response.success(data=result)
        else:
            result = await subscription_service.list(
                user_id=current_user.id, page=page, limit=limit, status=status_filter
            )
            if isinstance(result, dict) and "data" in result and "pagination" in result:
                return Response.success(data=result.get("data", []), pagination=result.get("pagination"))
            return Response.success(data=result)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to get subscriptions: {str(e)}"
        )
@router.post("/{subscription_id}/products/")
async def add_products(
    subscription_id: UUID,
    request: AddProducts,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
    ):
    """Add products to an existing subscription."""
    try:
        subscription_service = SubscriptionService(db)
        subscription = await subscription_service.add_products(
            subscription_id, request.variant_ids, current_user.id
        )
        return Response.success(
            data=subscription.to_dict(include_products=True),
            message="Products added to subscription successfully"
        )
    except APIException as e:
        raise e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding products to subscription: {e}")
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to add products to subscription: {str(e)}"
        )
@router.delete("/{subscription_id}/products/")
async def remove_products(
    subscription_id: UUID,
    request: RemoveProducts,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
    ):
    """Remove products from an existing subscription."""
    try:
        subscription_service = SubscriptionService(db)
        subscription = await subscription_service.remove_products(
            subscription_id, request.variant_ids, current_user.id
        )
        return Response.success(
            data=subscription.to_dict(include_products=True),
            message="Products removed from subscription successfully"
        )
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing products from subscription: {e}")
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to remove products from subscription: {str(e)}"
        )
@router.patch("/{subscription_id}/products/quantity/")
async def update_quantity(
    subscription_id: UUID,
    request: UpdateQuantity,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Update the quantity of a specific variant in a subscription."""
    try:
        subscription_service = SubscriptionService(db)
        subscription = await subscription_service.set_quantity(
            subscription_id, request.variant_id, request.quantity, current_user.id
        )
        return Response.success(
            data=subscription.to_dict(include_products=True), 
            message=f"Variant quantity updated to {request.quantity} successfully"
        )
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating variant quantity: {e}")
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to update variant quantity: {str(e)}"
        )


@router.get("/{subscription_id}/")
async def get(
    subscription_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Get a specific subscription. Admins can view any subscription, users can only view their own."""
    try:
        subscription_service = SubscriptionService(db)
        is_admin = current_user.is_staff

        if is_admin:
            subscription = await subscription_service.get(subscription_id)
        else:
            subscription = await subscription_service.get(subscription_id, current_user.id)

        if not subscription:
            raise APIException(
                status_code=status.HTTP_404_NOT_FOUND,
                message="Subscription not found"
            )

        return Response.success(data=subscription.to_dict(include_products=True))
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to fetch subscription: {str(e)}"
        )
@router.patch("/{subscription_id}/")
async def update(
    subscription_id: UUID,
    subscription_data: Update,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Update a subscription."""
    try:
        subscription_service = SubscriptionService(db)
        # Pass data directly to the service method
        subscription = await subscription_service.update(
            subscription_id=subscription_id,
            user_id=current_user.id,
            name=subscription_data.name if hasattr(subscription_data, 'name') else None,
            variant_ids=subscription_data.variant_ids if hasattr(subscription_data, 'variant_ids') else None,
            # Pass other updateable fields from subscription_data
            delivery_address_id=subscription_data.delivery_address_id if hasattr(subscription_data, 'delivery_address_id') else None,
            shipping_method_id=subscription_data.shipping_method_id if hasattr(subscription_data, 'shipping_method_id') else None,
            auto_renew=subscription_data.auto_renew if hasattr(subscription_data, 'auto_renew') else None,
            current_period_start=subscription_data.current_period_start if hasattr(subscription_data, 'current_period_start') else None
            # Add other fields here as needed
        )
        return Response.success(data=subscription.to_dict(include_products=True), message="Subscription updated successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to update subscription: {str(e)}"
        )
@router.post("/{subscription_id}/cancel/")
async def cancel(
    subscription_id: UUID,
    reason: str = None,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Cancel a subscription (soft delete - status update only)."""
    try:
        subscription_service = SubscriptionService(db)
        subscription = await subscription_service.cancel(subscription_id, current_user.id, reason)
        return Response.success(
            data=subscription.to_dict(include_products=True), 
            message="Subscription cancelled successfully"
        )
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to cancel subscription: {str(e)}"
        )
@router.delete("/{subscription_id}/")
async def delete(
    subscription_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Delete a subscription."""
    try:
        subscription_service = SubscriptionService(db)
        await subscription_service.delete(subscription_id, current_user.id)
        return Response.success(message="Subscription deleted successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting subscription {subscription_id}: {e}", exc_info=True)
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to delete subscription: {str(e)}"
        )
@router.post("/{subscription_id}/process-shipment/")
async def process_shipment(
    subscription_id: UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Bill one active subscription and create its delivery order now; its schedule restarts from today (admin only)."""
    try:
        subscription = await db.get(Subscription, subscription_id)
        if not subscription:
            raise APIException(status_code=status.HTTP_404_NOT_FOUND, message="Subscription not found")
        if subscription.status != "active":
            raise APIException(status_code=status.HTTP_400_BAD_REQUEST, message="Can only process shipments for active subscriptions")
        now = datetime.now(timezone.utc)
        if not subscription.next_billing_date or subscription.next_billing_date > now:
            subscription.next_billing_date = now
            await db.commit()

        async with renewal_lock(db) as acquired:
            if not acquired:
                raise APIException(status_code=status.HTTP_409_CONFLICT, message="Renewals are already running. Try again in a minute.")
            outcome = await SubscriptionScheduler(db).process_subscription(subscription_id)
        order = (await db.execute(
            select(Order).where(Order.subscription_id == subscription_id).order_by(Order.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        if outcome == "paid":
            return Response.success(
                data={"subscription_id": str(subscription_id), "order_id": str(order.id), "order_number": order.order_number},
                message="Subscription shipment processed successfully"
            )
        await db.refresh(subscription)
        reasons = {
            "declined": f"The payment didn't go through: {subscription.last_payment_error}",
            "pending": "The payment is still processing; the order is confirmed when it clears.",
            "skipped": "Nothing in this subscription is in stock, so this delivery was skipped.",
        }
        raise APIException(
            status_code=status.HTTP_400_BAD_REQUEST if outcome in reasons else status.HTTP_503_SERVICE_UNAVAILABLE,
            message=reasons.get(outcome, "We couldn't reach the payment provider. Please try again in a moment.")
        )
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to process subscription shipment: {str(e)}"
        )


@router.post("/{subscription_id}/pause/")
async def pause(
    subscription_id: UUID,
    pause_reason: str = None,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Pause a subscription."""
    try:
        subscription_service = SubscriptionService(db)
        subscription = await subscription_service.pause(
            subscription_id, current_user.id, pause_reason
        )
        return Response.success(
            data=subscription.to_dict(include_products=True),
            message="Subscription paused successfully"
        )
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to pause subscription: {str(e)}"
        )
@router.post("/{subscription_id}/resume/")
async def resume(
    subscription_id: UUID,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Resume a paused subscription or activate a cancelled subscription."""
    try:
        subscription_service = SubscriptionService(db)
        subscription = await subscription_service.resume(
            subscription_id, current_user.id
        )
        action_message = "resumed" if subscription.status == "active" else "activated"
        return Response.success(
            data=subscription.to_dict(include_products=True),
            message=f"Subscription {action_message} successfully"
        )
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to resume/activate subscription: {str(e)}"
        )


@router.patch("/{subscription_id}/frequency/")
async def change_frequency(
    subscription_id: UUID,
    request: ChangeFrequency,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Change a subscription's billing frequency."""
    try:
        subscription_service = SubscriptionService(db)
        subscription = await subscription_service.change_frequency(
            subscription_id, current_user.id, request.frequency
        )
        return Response.success(
            data=subscription.to_dict(include_products=True),
            message="Subscription frequency updated successfully"
        )
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to change subscription frequency: {str(e)}"
        )


@router.post("/{subscription_id}/skip/")
async def skip(
    subscription_id: UUID,
    request: SkipShipment,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Skip the upcoming shipment, optionally to a specific date."""
    try:
        subscription_service = SubscriptionService(db)
        subscription = await subscription_service.skip_next_shipment(
            subscription_id, current_user.id, request.next_shipment_date
        )
        return Response.success(
            data=subscription.to_dict(include_products=True),
            message="Upcoming shipment skipped successfully"
        )
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to skip shipment: {str(e)}"
        )


@router.post("/{subscription_id}/unskip/")
async def unskip(
    subscription_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Undo the most recent skip, restoring the original next shipment date."""
    try:
        subscription_service = SubscriptionService(db)
        subscription = await subscription_service.unskip_next_shipment(
            subscription_id, current_user.id
        )
        return Response.success(
            data=subscription.to_dict(include_products=True),
            message="Shipment skip undone successfully"
        )
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to unskip shipment: {str(e)}"
        )


@router.post("/{subscription_id}/discounts/")
async def apply_discount(
    subscription_id: UUID,
    discount_request: DiscountApplication,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Apply a discount code to a subscription."""
    try:
        subscription_service = SubscriptionService(db)
        subscription = await subscription_service.apply_discount(
            subscription_id=subscription_id,
            user_id=current_user.id,
            discount_code=discount_request.discount_code
        )
        return Response.success(data=subscription.to_dict(include_products=True), message="Discount applied successfully")
    except HTTPException as e:
        raise APIException(
            status_code=e.status_code,
            message=e.detail
        )
    except Exception as e:
        logger.error(f"Error applying discount to subscription: {e}")
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to apply discount: {str(e)}"
        )
@router.delete("/{subscription_id}/discounts/{discount_id}/")
async def remove_discount(
    subscription_id: UUID,
    discount_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Remove a discount from a subscription."""
    try:
        subscription_service = SubscriptionService(db)
        subscription = await subscription_service.remove_discount(
            subscription_id=subscription_id,
            user_id=current_user.id,
            discount_id=discount_id
        )
        return Response.success(data=subscription.to_dict(include_products=True), message="Discount removed successfully")
    except HTTPException as e:
        raise APIException(
            status_code=e.status_code,
            message=e.detail
        )
    except Exception as e:
        logger.error(f"Error removing discount from subscription: {e}")
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to remove discount: {str(e)}"
        )
@router.get("/{subscription_id}/orders/")
async def orders(
    subscription_id: UUID,
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Get orders created from a subscription."""
    try:
        subscription_service = SubscriptionService(db)
        orders = await subscription_service.get_orders(
            subscription_id, current_user.id, page, limit
        )
        return Response.success(data=orders)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to fetch subscription orders: {str(e)}"
        )

