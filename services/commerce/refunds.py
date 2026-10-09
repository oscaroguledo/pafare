"""Refunds: customers request them, staff approve or reject, and approval pays the money back through Stripe."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Any, List, Optional
from uuid import UUID
from core.utils.uuid_utils import uuid7
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
from fastapi import HTTPException

from models.commerce.refunds import Refund, RefundItem, RefundStatus, RefundReason, RefundType
from models.commerce.orders import Order, OrderItem, PaymentStatus
from models.catalog.product import ProductVariant
from schemas.commerce.refunds import Request as RefundRequest, Response as RefundResponse, ItemRequest as RefundItemRequest
from core.logging import get_structured_logger
from services.catalog.inventory import InventoryService
from services.commerce.payments import PaymentService
import random
import string

logger = get_structured_logger(__name__)


# Everything a refund response shows: its items (with product names), customer and order.
REFUND_DETAILS = (
    selectinload(Refund.refund_items).selectinload(RefundItem.order_item).selectinload(OrderItem.variant).selectinload(ProductVariant.product),
    selectinload(Refund.user),
    selectinload(Refund.order),
)

# Staff decisions allowed from each status; a failed Stripe refund can be approved again.
DECISIONS = {
    RefundStatus.REQUESTED: {RefundStatus.APPROVED, RefundStatus.REJECTED},
    RefundStatus.PENDING_REVIEW: {RefundStatus.APPROVED, RefundStatus.REJECTED},
    RefundStatus.FAILED: {RefundStatus.APPROVED, RefundStatus.REJECTED},
}


def _item_name(order_item: Optional[OrderItem]) -> str:
    variant = order_item.variant if order_item else None
    if not variant:
        return "Item"
    product = variant.product.name if variant.product else variant.name
    return product if variant.name in (None, "", product) else f"{product} ({variant.name})"


class RefundService:
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def request(
        self,
        user_id: UUID,
        order_id: UUID,
        refund_request: RefundRequest
    ) -> RefundResponse:
        """Request a refund, auto-approving it if eligible."""
        try:
            # Validate order and user
            order = await self._get_user_order(user_id, order_id)
            
            # Check refund eligibility
            eligibility = await self._check_refund_eligibility(order)
            if not eligibility["eligible"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"Order not eligible for refund: {eligibility['reason']}"
                )
            
            # Generate refund number
            refund_number = await self._generate_refund_number()
            
            # Calculate refund amounts
            refund_calculation = await self._calculate_refund_amounts(
                order, refund_request.items
            )
            
            # Create refund record
            refund = Refund(
                id=uuid7(), 
                order_id=order_id,
                user_id=user_id,
                refund_number=refund_number,
                status=RefundStatus.REQUESTED,
                refund_type=refund_calculation["refund_type"],
                reason=refund_request.reason,
                requested_amount=refund_calculation["total_amount"],
                currency=order.currency,
                customer_reason=refund_request.customer_reason,
                customer_notes=refund_request.customer_notes,
                requires_return=self._requires_return(refund_request.reason),
                refund_metadata={
                    "request_source": "customer_portal",
                    "order_age_days": (datetime.now(timezone.utc) - order.created_at).days,
                    "original_order_amount": float(order.total_amount)
                }
            )
            
            self.db.add(refund)
            await self.db.flush()  # Get refund ID
            
            # Create refund items
            for item_request in refund_request.items:
                refund_item = RefundItem(
                    refund_id=refund.id,
                    order_item_id=item_request.order_item_id,
                    quantity_to_refund=item_request.quantity,
                    unit_price=refund_calculation["items"][str(item_request.order_item_id)]["unit_price"],
                    total_refund_amount=refund_calculation["items"][str(item_request.order_item_id)]["total_amount"],
                    condition_notes=item_request.condition_notes
                )
                self.db.add(refund_item)
            
            await self.db.commit()
            # Clear-cut cases (e.g. a defective item) are approved and paid straight away.
            refund = await self._load(refund.id)
            if refund.is_eligible_for_auto_approval:
                refund.auto_approved = True
                await self._approve(refund, raise_on_failure=False)
            return await self._format_refund_response(await self._load(refund.id))
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to request refund: {e}")
            raise HTTPException(status_code=500, detail="Failed to process refund request")
    
    

    async def list(
        self,
        user_id: Optional[UUID] = None,
        status: Optional[RefundStatus] = None,
        page: int = 1,
        limit: int = 20,
        sort_by: str = "created_at",
        sort_order: str = "desc"
    ) -> Dict[str, Any]:
        """Get refund history with pagination. If user_id is None, returns all refunds (admin)."""
        try:
            offset = (page - 1) * limit
            
            # Build base query
            base_query = select(Refund)
            count_query = select(func.count()).select_from(Refund)
            if user_id:
                base_query = base_query.where(Refund.user_id == user_id)
                count_query = count_query.where(Refund.user_id == user_id)
            
            if status:
                base_query = base_query.where(Refund.status == status)
                count_query = count_query.where(Refund.status == status)
            
            # Apply sorting
            sort_column = Refund.created_at
            if sort_by == "amount":
                sort_column = Refund.requested_amount
            
            if sort_order == "asc":
                base_query = base_query.order_by(sort_column.asc())
            else:
                base_query = base_query.order_by(sort_column.desc())
            
            # Get total count
            total_result = await self.db.execute(count_query)
            total = total_result.scalar() or 0
            
            # Get paginated results
            query = base_query.options(*REFUND_DETAILS).limit(limit).offset(offset)
            
            result = await self.db.execute(query)
            refunds = result.scalars().all()
            
            # Format response
            items = [await self._format_refund_response(refund, is_admin=user_id is None) for refund in refunds]
            
            return {
                "items": items,
                "total": total,
                "page": page,
                "limit": limit,
                "pages": (total + limit - 1) // limit if limit > 0 else 0
            }
            
        except Exception as e:
            logger.error(f"Failed to get refunds: {e}")
            raise HTTPException(status_code=500, detail="Failed to retrieve refunds")
    
    async def get(self, refund_id: UUID, user_id: Optional[UUID] = None) -> RefundResponse:
        """Get detailed refund information. If user_id is None, admin access (no user filter)."""
        try:
            query = select(Refund).where(Refund.id == refund_id).options(*REFUND_DETAILS)
            
            # Apply user filter if provided (user access)
            if user_id:
                query = query.where(Refund.user_id == user_id)
            
            result = await self.db.execute(query)
            refund = result.scalar_one_or_none()
            
            if not refund:
                raise HTTPException(status_code=404, detail="Refund not found")
            
            return await self._format_refund_response(refund, is_admin=user_id is None)
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to get refund details: {e}")
            raise HTTPException(status_code=500, detail="Failed to retrieve refund details")
    
    async def update_status(self, refund_id: UUID, status: str, admin_notes: Optional[str] = None, staff_id: Optional[UUID] = None) -> RefundResponse:
        """Staff decision: approve (pays the refund through Stripe) or reject."""
        refund = await self._load(refund_id)
        if not refund:
            raise HTTPException(status_code=404, detail="Refund not found")
        try:
            decision = RefundStatus(status.lower())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid refund status: {status}")
        if decision not in DECISIONS.get(refund.status, set()):
            raise HTTPException(status_code=400, detail=f"A {refund.status.value.replace('_', ' ')} refund can't be {decision.value}")

        if admin_notes:
            refund.admin_notes = admin_notes
        refund.reviewed_by = staff_id
        refund.reviewed_at = datetime.now(timezone.utc)
        if decision == RefundStatus.REJECTED:
            refund.status = RefundStatus.REJECTED
            await self.db.commit()
        else:
            await self._approve(refund, raise_on_failure=True, staff_id=staff_id)
        return await self._format_refund_response(await self._load(refund.id), is_admin=True)

    async def _approve(self, refund: Refund, raise_on_failure: bool, staff_id: Optional[UUID] = None) -> None:
        """Pay the approved amount back to the customer's card and complete the refund.
        If Stripe refuses, the refund is marked failed with the reason so staff can try again."""
        order = await self.db.get(Order, refund.order_id)
        payments = PaymentService(self.db)
        remaining = Decimal(str(order.total_amount)) - await payments.refunded_total(order.id)
        amount = min(Decimal(str(refund.approved_amount or refund.requested_amount)), remaining)
        now = datetime.now(timezone.utc)
        refund.approved_amount = amount
        refund.approved_at = refund.approved_at or now
        try:
            if amount <= 0:
                raise HTTPException(status_code=400, detail="This order has already been refunded in full")
            refund.stripe_refund_id = await payments.refund_order(
                order, amount, f"refund-{refund.id}-{refund.approved_at.timestamp():.0f}", f"Refund {refund.refund_number}"
            )
        except HTTPException as e:
            refund.status = RefundStatus.FAILED
            refund.stripe_status = "failed"
            refund.admin_notes = "\n".join(filter(None, [refund.admin_notes, str(e.detail)]))
            await self.db.commit()
            if raise_on_failure:
                raise
            return
        refund.status = RefundStatus.COMPLETED
        refund.stripe_status = "succeeded"
        refund.processed_amount = amount
        refund.processed_by = staff_id
        refund.processed_at = refund.completed_at = now
        if remaining - amount <= 0:
            order.payment_status = PaymentStatus.REFUNDED
        await self._restore_inventory_for_refund(refund)
        await self.db.commit()

    async def _load(self, refund_id: UUID) -> Optional[Refund]:
        result = await self.db.execute(
            select(Refund).where(Refund.id == refund_id).options(*REFUND_DETAILS)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def _get_user_order(self, user_id: UUID, order_id: UUID) -> Order:
        """Get and validate user's order"""
        order = await self.db.execute(
            select(Order)
            .where(and_(Order.id == order_id, Order.user_id == user_id))
            .options(selectinload(Order.items))
        )
        order = order.scalar_one_or_none()
        
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        
        return order
    
    async def _check_refund_eligibility(self, order: Order) -> Dict[str, Any]:
        """Check if order is eligible for refund"""
        # Check order status
        if order.order_status not in ["confirmed", "shipped", "delivered"]:
            return {
                "eligible": False,
                "reason": "Order must be confirmed, shipped, or delivered to request refund"
            }
        
        if order.payment_status != PaymentStatus.PAID:
            return {"eligible": False, "reason": "Only paid orders that haven't been refunded in full can be refunded"}

        # Check if order is too old (90 days limit)
        order_age = (datetime.now(timezone.utc) - order.created_at).days
        if order_age > 90:
            return {
                "eligible": False,
                "reason": "Refund window has expired (90 days limit)"
            }
        
        # Check if refund already exists
        existing_refund = await self.db.execute(
            select(Refund)
            .where(
                and_(
                    Refund.order_id == order.id,
                    Refund.status.in_([
                        RefundStatus.REQUESTED,
                        RefundStatus.PENDING_REVIEW,
                        RefundStatus.APPROVED,
                        RefundStatus.PROCESSING,
                        RefundStatus.FAILED,
                    ])
                )
            )
        )
        
        if existing_refund.scalar_one_or_none():
            return {
                "eligible": False,
                "reason": "A refund request already exists for this order"
            }
        
        return {"eligible": True, "reason": None}
    
    async def _calculate_refund_amounts(self, order: Order, refund_items: List[RefundItemRequest]) -> Dict[str, Any]:
        """What the customer gets back, from what they actually paid (after any promo, with tax).
        Every item returned: whatever hasn't been refunded yet. Some items: their share of the paid goods."""
        order_items = {str(item.id): item for item in order.items}
        items: Dict[str, Dict[str, float]] = {}
        for request in refund_items:
            order_item = order_items.get(str(request.order_item_id))
            if not order_item:
                raise HTTPException(status_code=400, detail=f"Order item {request.order_item_id} not found")
            if request.quantity > order_item.quantity:
                raise HTTPException(status_code=400, detail="Cannot refund more items than ordered")
            items[str(request.order_item_id)] = {
                "unit_price": float(order_item.price_per_unit),
                "total_amount": float(order_item.price_per_unit) * request.quantity,
            }

        cent = Decimal("0.01")
        paid = Decimal(str(order.total_amount))
        remaining = paid - await PaymentService(self.db).refunded_total(order.id)
        if remaining <= 0:
            raise HTTPException(status_code=400, detail="This order has already been refunded in full")

        everything = len(items) == len(order.items) and all(
            r.quantity == order_items[str(r.order_item_id)].quantity for r in refund_items
        )
        if everything:
            amount = remaining
        else:
            goods_paid = paid - Decimal(str(order.shipping_cost or 0))
            share = Decimal(str(sum(i["total_amount"] for i in items.values()))) / Decimal(str(order.subtotal))
            amount = min((goods_paid * share).quantize(cent), remaining)
        return {
            "items": items,
            "total_amount": float(amount),
            "refund_type": RefundType.FULL_REFUND if everything else RefundType.PARTIAL_REFUND,
        }

    async def _generate_refund_number(self) -> str:
        """Generate unique refund number"""
        while True:
            # Generate REF-XXXXXXXX format
            suffix = ''.join(random.choices(string.digits, k=8))
            refund_number = f"REF-{suffix}"
            
            # Check if already exists
            existing = await self.db.execute(
                select(Refund).where(Refund.refund_number == refund_number)
            )
            
            if not existing.scalar_one_or_none():
                return refund_number
    
    def _requires_return(self, reason: RefundReason) -> bool:
        """Determine if refund requires item return"""
        no_return_reasons = [
            RefundReason.DEFECTIVE_PRODUCT,
            RefundReason.DAMAGED_IN_SHIPPING,
            RefundReason.WRONG_ITEM,
            RefundReason.MISSING_PARTS
        ]
        return reason not in no_return_reasons
    
    async def _restore_inventory_for_refund(self, refund: Refund):
        """Restore inventory when refund is confirmed"""
        try:
            inventory_service = InventoryService(self.db)
            
            # Get refund items with order item details
            refund_items = await self.db.execute(
                select(RefundItem)
                .options(selectinload(RefundItem.order_item))
                .where(RefundItem.refund_id == refund.id)
            )
            refund_items = refund_items.scalars().all()
            
            for refund_item in refund_items:
                try:
                    # Restore inventory for each refunded item
                    await inventory_service.increment(
                        variant_id=refund_item.order_item.variant_id,
                        quantity=refund_item.quantity_to_refund,
                        location_id=None,  # Will be determined by service
                        order_id=refund.order_id,
                        user_id=refund.user_id
                    )
                    
                    logger.info(f"Restored {refund_item.quantity_to_refund} units of variant {refund_item.order_item.variant_id} for refund {refund.refund_number}")
                    
                except Exception as restore_error:
                    logger.error(f"Failed to restore inventory for refund item {refund_item.id}: {restore_error}")
                    # Continue with other items even if one fails
                    
        except Exception as e:
            logger.error(f"Failed to restore inventory for refund {refund.id}: {e}")
            # Don't fail the refund if inventory restoration fails
    
    

    async def _format_refund_response(self, refund: Refund, is_admin: bool = False) -> RefundResponse:
        """Format refund for API response"""
        return RefundResponse(
            id=refund.id,
            order_id=refund.order_id,
            refund_number=refund.refund_number,
            status=refund.status,
            refund_type=refund.refund_type,
            reason=refund.reason,
            requested_amount=refund.requested_amount,
            approved_amount=refund.approved_amount,
            processed_amount=refund.processed_amount,
            currency=refund.currency,
            customer_reason=refund.customer_reason,
            customer_notes=refund.customer_notes,
            auto_approved=refund.auto_approved,
            requires_return=refund.requires_return,
            return_shipping_paid=refund.return_shipping_paid,
            requested_at=refund.requested_at,
            approved_at=refund.approved_at,
            processed_at=refund.processed_at,
            completed_at=refund.completed_at,
            items=[
                {
                    "order_item_id": item.order_item_id,
                    "name": _item_name(item.order_item),
                    "quantity": item.quantity_to_refund,
                    "amount": item.total_refund_amount,
                    "condition_notes": item.condition_notes
                }
                for item in refund.refund_items
            ] if refund.refund_items else [],
            timeline=self._generate_refund_timeline(refund),
            # Staff-only: who asked, and internal notes.
            customer=({"name": f"{refund.user.firstname} {refund.user.lastname}".strip(), "email": refund.user.email}
                      if is_admin and refund.user else None),
            admin_notes=refund.admin_notes if is_admin else None,
        )
    
    def _generate_refund_timeline(self, refund: Refund) -> List[Dict[str, Any]]:
        """Generate refund timeline for customer"""
        timeline = []
        
        if refund.requested_at:
            timeline.append({
                "status": "requested",
                "title": "Refund Requested",
                "description": "Your refund request has been submitted",
                "timestamp": refund.requested_at.isoformat(),
                "completed": True
            })
        
        if refund.auto_approved:
            timeline.append({
                "status": "approved",
                "title": "Automatically Approved",
                "description": "Your refund was automatically approved and is being processed",
                "timestamp": refund.approved_at.isoformat() if refund.approved_at else None,
                "completed": True
            })
        elif refund.status in [RefundStatus.PENDING_REVIEW, RefundStatus.APPROVED]:
            timeline.append({
                "status": "review",
                "title": "Under Review",
                "description": "Our team is reviewing your refund request",
                "timestamp": None,
                "completed": refund.status != RefundStatus.PENDING_REVIEW
            })
        
        if refund.processed_at:
            timeline.append({
                "status": "processing",
                "title": "Processing Refund",
                "description": f"Refund of {refund.processed_amount:.2f} {refund.currency} sent to your card",
                "timestamp": refund.processed_at.isoformat(),
                "completed": True
            })
        
        if refund.completed_at:
            timeline.append({
                "status": "completed",
                "title": "Refund Completed",
                "description": "Your refund has been processed and should appear in your account within 3-5 business days",
                "timestamp": refund.completed_at.isoformat(),
                "completed": True
            })
        
        return timeline