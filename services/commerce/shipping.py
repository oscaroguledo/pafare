from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List, Optional, Dict, Any
from uuid import UUID
from core.utils.uuid_utils import uuid7
from models.commerce.shipping import ShippingMethod
from schemas.commerce.shipping import MethodCreate as ShippingMethodCreate, MethodUpdate as ShippingMethodUpdate
from core.exceptions import APIException
from core.logging import get_structured_logger

logger = get_structured_logger(__name__)


class ShippingService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, shipping_method_data: ShippingMethodCreate) -> ShippingMethod:
        """Create a new shipping method"""
        new_shipping_method = ShippingMethod(
            id=uuid7(),
            **shipping_method_data.model_dump(exclude_unset=True)
        )
        self.db.add(new_shipping_method)
        await self.db.commit()
        await self.db.refresh(new_shipping_method)
        return new_shipping_method

    async def get(self, shipping_method_id: UUID) -> Optional[ShippingMethod]:
        """Get a shipping method by ID"""
        result = await self.db.execute(select(ShippingMethod).where(ShippingMethod.id == shipping_method_id))
        return result.scalars().first()

    async def list(self, active_only: bool = False) -> List[ShippingMethod]:
        """List shipping methods. Set active_only=True to filter active methods."""
        query = select(ShippingMethod)
        if active_only:
            query = query.where(ShippingMethod.is_active == True)
        result = await self.db.execute(query)
        return result.scalars().all()

    async def get_all_methods(
        self,
        page: int = 1,
        limit: int = 10,
        is_active: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Get all shipping methods with pagination (admin only)."""
        # Build base query
        query = select(ShippingMethod)
        count_query = select(func.count(ShippingMethod.id))

        # Apply filters
        if is_active is not None:
            query = query.where(ShippingMethod.is_active == is_active)
            count_query = count_query.where(ShippingMethod.is_active == is_active)

        # Get total count
        total_result = await self.db.execute(count_query)
        total = total_result.scalar() or 0

        # Calculate pagination
        pages = (total + limit - 1) // limit if total > 0 else 0
        offset = (page - 1) * limit

        # Apply ordering and pagination
        query = query.order_by(ShippingMethod.created_at.desc())
        query = query.offset(offset).limit(limit)

        # Execute query
        result = await self.db.execute(query)
        items = result.scalars().all()

        return {
            "items": items,
            "total": total,
            "pages": pages,
            "page": page,
            "limit": limit
        }


    async def calc_cost(self, shipping_method_id: Optional[UUID] = None) -> float:
        """The selected method's price, or the cheapest active method's."""
        if shipping_method_id:
            method = await self.get(shipping_method_id)
            if method and method.is_active:
                logger.info(f"Using selected shipping method: {method.name} - ${method.price}")
                return float(method.price)

        # If no specific method or method not available, get cheapest available method
        active_methods = await self.list(active_only=True)
        if active_methods:
            cheapest = min(active_methods, key=lambda m: m.price)
            logger.info(f"Using cheapest available method: {cheapest.name} - ${cheapest.price}")
            return float(cheapest.price)
        
        # No shipping methods available
        logger.warning("No shipping methods available")
        return 0.0

    async def update(self, shipping_method_id: UUID, shipping_method_data: ShippingMethodUpdate) -> Optional[ShippingMethod]:
        """Update a shipping method"""
        shipping_method = await self.get(shipping_method_id)
        if not shipping_method:
            raise APIException(
                status_code=404, message="Shipping method not found")

        for key, value in shipping_method_data.model_dump(exclude_unset=True).items():
            setattr(shipping_method, key, value)

        await self.db.commit()
        await self.db.refresh(shipping_method)
        return shipping_method

    async def delete(self, shipping_method_id: UUID) -> bool:
        """Delete a shipping method"""
        shipping_method = await self.get(shipping_method_id)
        if not shipping_method:
            return False

        await self.db.delete(shipping_method)
        await self.db.commit()
        return True
