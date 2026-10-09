"""Tax rate lookup: the single place orders, subscriptions and the cart get their tax from."""
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, or_, select
from models.commerce.tax_rates import TaxRate


class TaxService:
    """Service for tax rate lookups and calculations"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def info(self, country: Optional[str], province: Optional[str] = None) -> dict:
        """Active rate for a location; country and province may each be an ISO code or a full name.
        A province rate wins over the country-wide one; no match means no tax."""
        record = None
        if country:
            country_match = or_(func.upper(TaxRate.country_code) == country.strip().upper(),
                                func.lower(TaxRate.country_name) == country.strip().lower())
            area_match = TaxRate.province_code.is_(None)
            if province:
                area_match = or_(area_match,
                                 func.upper(TaxRate.province_code) == province.strip().upper(),
                                 func.lower(TaxRate.province_name) == province.strip().lower())
            record = (await self.db.execute(
                select(TaxRate)
                .where(TaxRate.is_active.is_(True), country_match, area_match)
                .order_by(TaxRate.province_code.is_(None))
                .limit(1)
            )).scalar_one_or_none()
        if not record:
            return {"country_code": None, "country_name": country, "province_code": None,
                    "province_name": province, "tax_rate": 0.0, "tax_name": None}
        return {"country_code": record.country_code, "country_name": record.country_name,
                "province_code": record.province_code, "province_name": record.province_name,
                "tax_rate": float(record.tax_rate), "tax_name": record.tax_name}

    async def rate(self, country: Optional[str], province: Optional[str] = None) -> float:
        """Tax rate as a decimal (0.13 for 13%) for a location."""
        return (await self.info(country, province))["tax_rate"]

    @staticmethod
    def amount(taxable: Decimal, rate: float) -> Decimal:
        """Tax on a taxable amount, rounded to cents; never negative."""
        return (max(taxable, Decimal("0")) * Decimal(str(rate))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
