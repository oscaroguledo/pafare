from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Tuple
from uuid import UUID
from datetime import datetime, timezone
from core.utils.uuid_utils import uuid7
from models.commerce.promocode import Promocode
from schemas.commerce.promos import Create as PromocodeCreate, Update as PromocodeUpdate
from core.exceptions import APIException


class PromocodeService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, promocode_data: PromocodeCreate) -> Promocode:
        """Create a new promocode"""
        existing = await self.db.scalar(select(Promocode).where(Promocode.code == promocode_data.code))
        if existing:
            raise APIException(status_code=400, message=f"Promocode '{promocode_data.code}' already exists")

        new_promocode = Promocode(
            id=uuid7(),
            **promocode_data.model_dump(exclude_unset=True)
        )
        self.db.add(new_promocode)
        await self.db.commit()
        await self.db.refresh(new_promocode)
        return new_promocode

    async def get(
        self,
        promocode_id: Optional[UUID] = None,
        code: Optional[str] = None,
        active_only: bool = False
    ) -> Optional[Promocode]:
        """Get promocode by ID or code. If active_only=True, filter for active promocodes."""
        if not promocode_id and not code:
            raise ValueError("Either promocode_id or code must be provided")
        
        if code:
            query = select(Promocode).where(Promocode.code == code)
            if active_only:
                query = query.where(Promocode.is_active == True)
            result = await self.db.execute(query)
            return result.scalars().first()
        else:
            result = await self.db.execute(select(Promocode).where(Promocode.id == promocode_id))
            return result.scalars().first()

    async def list(
        self,
        page: int = 1,
        limit: int = 10,
        is_active: Optional[bool] = None
    ) -> Tuple[List[Promocode], int]:
        """List all promocodes with pagination"""
        query = select(Promocode)
        count_query = select(func.count()).select_from(Promocode)

        if is_active is not None:
            query = query.where(Promocode.is_active == is_active)
            count_query = count_query.where(Promocode.is_active == is_active)

        total_result = await self.db.execute(count_query)
        total = total_result.scalar() or 0

        query = query.order_by(Promocode.created_at.desc()).offset((page - 1) * limit).limit(limit)
        result = await self.db.execute(query)
        return result.scalars().all(), total

    async def update(self, promocode_id: UUID, promocode_data: PromocodeUpdate) -> Optional[Promocode]:
        """Update a promocode"""
        promocode = await self.get(promocode_id)
        if not promocode:
            raise APIException(status_code=404, message="Promocode not found")

        for key, value in promocode_data.model_dump(exclude_unset=True).items():
            setattr(promocode, key, value)

        await self.db.commit()
        await self.db.refresh(promocode)
        return promocode

    async def delete(self, promocode_id: UUID) -> bool:
        """Delete a promocode"""
        promocode = await self.get(promocode_id)
        if not promocode:
            return False

        await self.db.delete(promocode)
        await self.db.commit()
        return True

    
    async def validate(self, code: str, subtotal: Optional[Decimal] = None) -> tuple[bool, Optional[str], Optional[Promocode]]:
        """Validate a promocode (optionally against an order subtotal) and return (is_valid, error_message, promocode)."""
        promocode = await self.get(code=code.strip().upper(), active_only=True)
        
        if not promocode:
            return False, "Promocode not found", None
        
        if not promocode.is_active:
            return False, "Promocode is not active", None
        
        current_time = datetime.now(timezone.utc)
        
        # Check if promocode has started
        if promocode.valid_from and promocode.valid_from > current_time:
            return False, "Promocode is not yet valid", None
        
        # Check if promocode has expired
        if promocode.valid_until and promocode.valid_until <= current_time:
            return False, "Promocode has expired", None
        
        # Check usage limit
        if promocode.usage_limit and promocode.used_count >= promocode.usage_limit:
            return False, "Promocode usage limit reached", None

        minimum = promocode.minimum_order_amount
        if subtotal is not None and minimum and subtotal < Decimal(str(minimum)):
            return False, f"This code needs an order of at least {Decimal(str(minimum)):.2f}", None

        return True, None, promocode

    @staticmethod
    def amount(promocode: Promocode, subtotal: Decimal) -> Decimal:
        """Discount on a subtotal: a percentage (capped by maximum_discount_amount) or a fixed amount, never above the subtotal."""
        value = Decimal(str(promocode.value))
        if promocode.discount_type == "percentage":
            discount = (subtotal * value / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if promocode.maximum_discount_amount:
                discount = min(discount, Decimal(str(promocode.maximum_discount_amount)))
        else:
            discount = value
        return min(discount, subtotal)
