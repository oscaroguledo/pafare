from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional, List, Tuple
from uuid import UUID

from core.utils.uuid_utils import uuid7
from models.commerce.carriers import Carrier
from models.commerce.shipping_tracking import ShippingProvider
from schemas.commerce.carrier import Create as CarrierCreate, Update as CarrierUpdate
from core.exceptions import APIException


class CarrierService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, carrier_id: UUID) -> Optional[Carrier]:
        result = await self.db.execute(select(Carrier).where(Carrier.id == carrier_id))
        return result.scalar_one_or_none()

    async def get_by_code(self, code: str, active_only: bool = False) -> Optional[Carrier]:
        query = select(Carrier).where(Carrier.code == code)
        if active_only:
            query = query.where(Carrier.is_active.is_(True))
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def list(self, page: int = 1, limit: int = 50, active_only: bool = False) -> Tuple[List[Carrier], int]:
        query = select(Carrier)
        count_query = select(func.count(Carrier.id))
        if active_only:
            query = query.where(Carrier.is_active.is_(True))
            count_query = count_query.where(Carrier.is_active.is_(True))

        total = await self.db.scalar(count_query) or 0
        query = query.order_by(Carrier.name).offset((page - 1) * limit).limit(limit)
        result = await self.db.execute(query)
        return result.scalars().all(), total

    async def create(self, data: CarrierCreate) -> Carrier:
        if await self.get_by_code(data.code):
            raise APIException(status_code=400, message="A carrier with this code already exists")

        carrier = Carrier(id=uuid7(), **data.model_dump())
        self.db.add(carrier)
        await self.db.commit()
        await self.db.refresh(carrier)
        return carrier

    async def update(self, carrier_id: UUID, data: CarrierUpdate) -> Optional[Carrier]:
        carrier = await self.get(carrier_id)
        if not carrier:
            return None

        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(carrier, field, value)

        await self.db.commit()
        await self.db.refresh(carrier)
        return carrier

    async def delete(self, carrier_id: UUID) -> bool:
        carrier = await self.get(carrier_id)
        if not carrier:
            return False

        provider_count = await self.db.scalar(
            select(func.count(ShippingProvider.id)).where(ShippingProvider.carrier_id == carrier_id)
        )
        if provider_count:
            raise APIException(
                status_code=400,
                message=f"Cannot delete carrier: {provider_count} shipping provider(s) still reference it"
            )

        await self.db.delete(carrier)
        await self.db.commit()
        return True
