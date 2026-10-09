"""Admin tax rate management; checkout applies these rates through services.commerce.tax."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from typing import Optional, List
from uuid import UUID

from core.db import get_db
from core.dependencies import require_admin
from core.utils.response import Response
from core.exceptions import APIException
from core.logging import get_structured_logger
from schemas.commerce.tax import BulkRateUpdate, RateCreate, RateUpdate, RateResponse
from models.commerce.tax_rates import TaxRate

logger = get_structured_logger(__name__)
router = APIRouter(prefix="/tax", tags=["tax"])


# --- Admin tax rates management routes ---


@router.get("/rates/")
async def list_rates(
    country_code: Optional[str] = Query(None),
    country_name: Optional[str] = Query(None),
    province_code: Optional[str] = Query(None),
    province_name: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: Optional[str] = Query(None),
    sort_order: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    current_user = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """List all tax rates with filtering and pagination (Admin only)."""
    return await _list_tax_rates_internal(
        country_code, country_name, province_code, province_name, is_active, search, sort_by, sort_order, page, per_page, db
    )


async def _list_tax_rates_internal(
    country_code: Optional[str],
    country_name: Optional[str],
    province_code: Optional[str],
    province_name: Optional[str],
    is_active: Optional[bool],
    search: Optional[str],
    sort_by: Optional[str],
    sort_order: Optional[str],
    page: int,
    per_page: int,
    db: AsyncSession
):
    """Internal function to list tax rates."""
    try:
        # Build query
        query = select(TaxRate)
        count_query = select(func.count(TaxRate.id))
        
        # Apply filters
        conditions = []
        if country_code:
            conditions.append(TaxRate.country_code == country_code.upper())
        if country_name:
            conditions.append(TaxRate.country_name.ilike(f"%{country_name}%"))
        if province_code:
            conditions.append(TaxRate.province_code == province_code.upper())
        if province_name:
            conditions.append(TaxRate.province_name.ilike(f"%{province_name}%"))
        if is_active is not None:
            conditions.append(TaxRate.is_active == is_active)
        if search:
            search_term = f"%{search}%"
            conditions.append(
                or_(
                    TaxRate.country_name.ilike(search_term),
                    TaxRate.province_name.ilike(search_term),
                    TaxRate.tax_name.ilike(search_term)
                )
            )
        
        if conditions:
            query = query.where(and_(*conditions))
            count_query = count_query.where(and_(*conditions))
        
        # Add dynamic sorting
        sort_field = sort_by or "created_at"
        sort_dir = sort_order or "desc"
        
        if sort_field == "created_at":
            if sort_dir == "asc":
                query = query.order_by(TaxRate.created_at.asc())
            else:
                query = query.order_by(TaxRate.created_at.desc())
        elif sort_field == "country_name":
            if sort_dir == "asc":
                query = query.order_by(TaxRate.country_name.asc())
            else:
                query = query.order_by(TaxRate.country_name.desc())
        elif sort_field == "tax_rate":
            if sort_dir == "asc":
                query = query.order_by(TaxRate.tax_rate.asc())
            else:
                query = query.order_by(TaxRate.tax_rate.desc())
        elif sort_field == "tax_name":
            if sort_dir == "asc":
                query = query.order_by(TaxRate.tax_name.asc().nulls_last())
            else:
                query = query.order_by(TaxRate.tax_name.desc().nulls_last())
        else:
            # Default sorting
            query = query.order_by(TaxRate.country_name, TaxRate.province_name)
        
        # Apply pagination
        offset = (page - 1) * per_page
        query = query.offset(offset).limit(per_page)
        
        # Execute queries
        result = await db.execute(query)
        tax_rates = result.scalars().all()
        
        total = await db.scalar(count_query) or 0
        pages = (total + per_page - 1) // per_page
        
        # Convert to response format
        response_data = []
        for rate in tax_rates:
            response_data.append(RateResponse(
                id=rate.id,
                country_code=rate.country_code,
                country_name=rate.country_name,
                province_code=rate.province_code,
                province_name=rate.province_name,
                tax_rate=rate.tax_rate,
                tax_percentage=rate.tax_rate * 100,
                tax_name=rate.tax_name,
                is_active=rate.is_active,
                created_at=rate.created_at.isoformat(),
                updated_at=rate.updated_at.isoformat() if rate.updated_at else None
            ))
        
        return Response.success(data=response_data, pagination={
            "page": page,
            "limit": per_page,
            "total": total,
            "pages": pages
        })
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch tax rates: {str(e)}"
        )


@router.get("/countries/")
async def countries(
    db: AsyncSession = Depends(get_db)
):
    """Get list of countries that have tax rates configured."""
    try:
        result = await db.execute(
            select(
                TaxRate.country_code,
                TaxRate.country_name,
                func.count(TaxRate.id).label('rate_count')
            )
            .group_by(TaxRate.country_code, TaxRate.country_name)
            .order_by(TaxRate.country_name)
        )
        
        countries = []
        for row in result:
            countries.append({
                "country_code": row.country_code,
                "country_name": row.country_name,
                "rate_count": row.rate_count
            })
        
        return Response.success(data=countries)
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch countries: {str(e)}"
        )


@router.get("/tax-types/")
async def tax_types(
    db: AsyncSession = Depends(get_db)
):
    """Get list of all tax types currently in use"""
    try:
        result = await db.execute(
            select(TaxRate.tax_name, func.count(TaxRate.id).label('usage_count'))
            .where(TaxRate.tax_name.isnot(None))
            .group_by(TaxRate.tax_name)
            .order_by(func.count(TaxRate.id).desc(), TaxRate.tax_name)
        )
        
        tax_types = []
        for row in result:
            tax_types.append({
                "value": row.tax_name,
                "label": row.tax_name,
                "usage_count": row.usage_count
            })
        
        return Response.success(data=tax_types)
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch tax types: {str(e)}"
        )


@router.get("/rates/{tax_rate_id}/", response_model=RateResponse)
async def get_rate(
    tax_rate_id: UUID,
    current_user = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get a specific tax rate by ID (Admin only)."""
    try:
        result = await db.execute(
            select(TaxRate).where(TaxRate.id == tax_rate_id)
        )
        tax_rate = result.scalar_one_or_none()
        
        if not tax_rate:
            raise HTTPException(status_code=404, detail="Tax rate not found")
        
        return RateResponse(
            id=tax_rate.id,
            country_code=tax_rate.country_code,
            country_name=tax_rate.country_name,
            province_code=tax_rate.province_code,
            province_name=tax_rate.province_name,
            tax_rate=tax_rate.tax_rate,
            tax_percentage=tax_rate.tax_rate * 100,
            tax_name=tax_rate.tax_name,
            is_active=tax_rate.is_active,
            created_at=tax_rate.created_at.isoformat(),
            updated_at=tax_rate.updated_at.isoformat() if tax_rate.updated_at else None
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get tax rate: {str(e)}"
        )


@router.post("/rates/", response_model=RateResponse, status_code=status.HTTP_201_CREATED)
async def create_rate(
    data: RateCreate,
    current_user = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Create a new tax rate (Admin only)."""
    try:
        # Check if tax rate already exists for this location
        existing_query = select(TaxRate).where(
            and_(
                TaxRate.country_code == data.country_code.upper(),
                TaxRate.province_code == (data.province_code.upper() if data.province_code else None)
            )
        )
        result = await db.execute(existing_query)
        existing = result.scalar_one_or_none()
        
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Tax rate already exists for {data.country_code}" + 
                        (f"-{data.province_code}" if data.province_code else "")
            )
        
        # Create new tax rate
        tax_rate = TaxRate(
            country_code=data.country_code.upper(),
            country_name=data.country_name,
            province_code=data.province_code.upper() if data.province_code else None,
            province_name=data.province_name,
            tax_rate=data.tax_rate,
            tax_name=data.tax_name,
            is_active=data.is_active
        )
        
        db.add(tax_rate)
        await db.commit()
        await db.refresh(tax_rate)
        
        return RateResponse(
            id=tax_rate.id,
            country_code=tax_rate.country_code,
            country_name=tax_rate.country_name,
            province_code=tax_rate.province_code,
            province_name=tax_rate.province_name,
            tax_rate=tax_rate.tax_rate,
            tax_percentage=tax_rate.tax_rate * 100,
            tax_name=tax_rate.tax_name,
            is_active=tax_rate.is_active,
            created_at=tax_rate.created_at.isoformat(),
            updated_at=tax_rate.updated_at.isoformat() if tax_rate.updated_at else None
        )
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create tax rate: {str(e)}"
        )


@router.patch("/rates/{tax_rate_id}/", response_model=RateResponse)
async def update_rate(
    tax_rate_id: UUID,
    data: RateUpdate,
    current_user = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Update an existing tax rate (Admin only)."""
    try:
        result = await db.execute(
            select(TaxRate).where(TaxRate.id == tax_rate_id)
        )
        tax_rate = result.scalar_one_or_none()
        
        if not tax_rate:
            raise HTTPException(status_code=404, detail="Tax rate not found")
        
        # Update fields
        if data.country_name is not None:
            tax_rate.country_name = data.country_name
        if data.province_name is not None:
            tax_rate.province_name = data.province_name
        if data.tax_rate is not None:
            tax_rate.tax_rate = data.tax_rate
        if data.tax_name is not None:
            tax_rate.tax_name = data.tax_name
        if data.is_active is not None:
            tax_rate.is_active = data.is_active
        
        await db.commit()
        await db.refresh(tax_rate)
        
        return RateResponse(
            id=tax_rate.id,
            country_code=tax_rate.country_code,
            country_name=tax_rate.country_name,
            province_code=tax_rate.province_code,
            province_name=tax_rate.province_name,
            tax_rate=tax_rate.tax_rate,
            tax_percentage=tax_rate.tax_rate * 100,
            tax_name=tax_rate.tax_name,
            is_active=tax_rate.is_active,
            created_at=tax_rate.created_at.isoformat(),
            updated_at=tax_rate.updated_at.isoformat() if tax_rate.updated_at else None
        )
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update tax rate: {str(e)}"
        )


@router.delete("/rates/{tax_rate_id}/")
async def delete_rate(
    tax_rate_id: UUID,
    current_user = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Delete a tax rate (Admin only)."""
    try:
        result = await db.execute(
            select(TaxRate).where(TaxRate.id == tax_rate_id)
        )
        tax_rate = result.scalar_one_or_none()
        
        if not tax_rate:
            raise HTTPException(status_code=404, detail="Tax rate not found")
        
        await db.delete(tax_rate)
        await db.commit()
        
        return Response.success(message="Tax rate deleted successfully")
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete tax rate: {str(e)}"
        )


@router.post("/rates/bulk-update/")
async def bulk_update(
    updates: List[BulkRateUpdate],
    current_user = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Update several tax rates at once, e.g. switch them on or off (Admin only)."""
    rates = {r.id: r for r in (await db.execute(select(TaxRate).where(TaxRate.id.in_([u.id for u in updates])))).scalars()}
    missing = [str(u.id) for u in updates if u.id not in rates]
    if missing:
        raise APIException(status_code=status.HTTP_404_NOT_FOUND, message=f"Tax rates not found: {', '.join(missing)}")
    for update in updates:
        for field, value in update.model_dump(exclude={"id"}, exclude_unset=True).items():
            setattr(rates[update.id], field, value)
    await db.commit()
    return Response.success(data={"updated_count": len(updates)}, message=f"Updated {len(updates)} tax rates")
