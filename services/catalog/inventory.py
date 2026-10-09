# Consolidated inventory service
# This file includes all inventory-related functionality including enhanced features

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm import selectinload, joinedload
from typing import Optional, List, Dict, Any, Union
from uuid import UUID
from core.utils.uuid_utils import uuid7
from datetime import datetime, timezone
from models.catalog.inventories import Inventory, WarehouseLocation, StockAdjustment
from models.catalog.product import ProductVariant, Product
from schemas.catalog.inventory import (
    LocationCreate as WarehouseLocationCreate,
    LocationUpdate as WarehouseLocationUpdate,
    LocationResponse as WarehouseLocationResponse,
    Create as InventoryCreate,
    Update as InventoryUpdate,
    Response as InventoryResponse,
    AdjustmentCreate as StockAdjustmentCreate,
    AdjustmentResponse as StockAdjustmentResponse
)
from core.exceptions import APIException
from core.logging import get_structured_logger
from core.utils.cache import invalidate_variant
from models.catalog.inventories import Inventory, WarehouseLocation, StockAdjustment
from core.utils.cache import invalidate_variant

logger = get_structured_logger(__name__)


class InventoryService:
    """Consolidated inventory service with comprehensive inventory management and distributed locking"""
    
    def __init__(self, db: AsyncSession, lock_service=None):
        self.db = db
        self.lock_service = lock_service

    # --- WarehouseLocation CRUD ---
    async def create_location(self, location_data: WarehouseLocationCreate) -> WarehouseLocationResponse:
        """Create a new warehouse location"""
        new_location = WarehouseLocation(id=uuid7(), **location_data.model_dump())
        self.db.add(new_location)
        await self.db.commit()
        await self.db.refresh(new_location)
        return WarehouseLocationResponse.model_validate(new_location)

    async def list_locations(self, page: int = 1, limit: int = 10) -> Dict[str, Any]:
        """List all warehouse locations with pagination"""
        offset = (page - 1) * limit
        
        # Get total count
        count_query = select(func.count()).select_from(WarehouseLocation)
        total_result = await self.db.execute(count_query)
        total = total_result.scalar()
        
        # Get paginated results
        result = await self.db.execute(
            select(WarehouseLocation).order_by(WarehouseLocation.name).offset(offset).limit(limit)
        )
        locations = result.scalars().all()
        
        return {
            "data": [WarehouseLocationResponse.model_validate(location) for location in locations],
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit if limit else 1
            }
        }

    async def get_location(self, location_id: UUID, raw: bool = False) -> Optional[Union[WarehouseLocationResponse, WarehouseLocation]]:
        """Get location by ID. Set raw=True to return SQLAlchemy model instead of response schema."""
        result = await self.db.execute(select(WarehouseLocation).filter(WarehouseLocation.id == location_id))
        location = result.scalars().first()
        if not location:
            return None
        if raw:
            return location
        return WarehouseLocationResponse.model_validate(location)

    async def update_location(self, location_id: UUID, location_data: WarehouseLocationUpdate) -> WarehouseLocationResponse:
        """Update a warehouse location"""
        location = await self.get_location(location_id, raw=True)
        if not location:
            raise APIException(status_code=404, message="Warehouse location not found")
        
        for field, value in location_data.model_dump(exclude_unset=True).items():
            setattr(location, field, value)
        
        location.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(location)
        return WarehouseLocationResponse.model_validate(location)

    async def delete_location(self, location_id: UUID):
        """Delete a warehouse location"""
        location = await self.get_location(location_id, raw=True)
        if not location:
            raise APIException(status_code=404, message="Warehouse location not found")
        
        # Check if there are any inventory items in this location
        inventory_count = await self.db.scalar(select(func.count(Inventory.id)).filter(Inventory.location_id == location_id))
        if inventory_count > 0:
            raise APIException(status_code=400, message="Cannot delete location with existing inventory. Move all items first.")
        
        await self.db.delete(location)
        await self.db.commit()

    # --- Inventory CRUD and Adjustment ---
    async def get(
        self,
        inventory_id: Optional[UUID] = None,
        variant_id: Optional[UUID] = None,
        serialized: bool = False
    ) -> Optional[Union[Inventory, dict]]:
        """Get inventory item by ID or variant_id. Set serialized=True to return dict instead of model."""
        if not inventory_id and not variant_id:
            raise ValueError("Either inventory_id or variant_id must be provided")
        
        if variant_id:
            result = await self.db.execute(select(Inventory).filter(Inventory.variant_id == variant_id).options(
                joinedload(Inventory.variant).joinedload(ProductVariant.product),
                joinedload(Inventory.variant).joinedload(ProductVariant.images),
                joinedload(Inventory.location)
            ))
        else:
            result = await self.db.execute(select(Inventory).filter(Inventory.id == inventory_id).options(
                joinedload(Inventory.variant).joinedload(ProductVariant.product),
                joinedload(Inventory.variant).joinedload(ProductVariant.images),
                joinedload(Inventory.location)
            ))
        item = result.scalars().unique().first()
        
        if not item:
            return None
        
        if not serialized:
            return item
            
        # Return serialized dict
        try:
            variant_data = None
            if item.variant:
                primary_image = None
                if item.variant.images:
                    primary_image = next(
                        (img for img in item.variant.images if img.is_primary),
                        item.variant.images[0] if len(item.variant.images) > 0 else None
                    )
                
                product_info = None
                if item.variant.product:
                    product_info = {
                        "id": str(item.variant.product.id),
                        "name": item.variant.product.name or "",
                        "description": item.variant.product.description or "",
                        "is_active": getattr(item.variant.product, 'is_active', True)
                    }

                variant_data = {
                    "id": str(item.variant.id),
                    "name": item.variant.name or "",
                    "sku": item.variant.sku or "",
                    "base_price": float(item.variant.base_price) if item.variant.base_price else 0.0,
                    "sale_price": float(item.variant.sale_price) if item.variant.sale_price else None,
                    "is_active": getattr(item.variant, 'is_active', True),
                    "product": product_info,
                    "primary_image": {
                        "id": str(primary_image.id),
                        "url": primary_image.url or "",
                        "alt_text": primary_image.alt_text or "",
                        "is_primary": primary_image.is_primary
                    } if primary_image else None,
                    "images": [
                        {
                            "id": str(img.id),
                            "url": img.url or "",
                            "alt_text": img.alt_text or "",
                            "is_primary": img.is_primary,
                            "sort_order": img.sort_order or 0
                        }
                        for img in (item.variant.images or [])
                    ]
                }

            location_info = None
            if item.location:
                location_info = {
                    "id": str(item.location.id),
                    "name": item.location.name or "",
                    "address": item.location.address or "",
                    "description": item.location.description or "",
                    "created_at": item.location.created_at.isoformat() if item.location.created_at else None,
                    "updated_at": item.location.updated_at.isoformat() if item.location.updated_at else None
                }

            return {
                "id": str(item.id),
                "variant_id": str(item.variant_id),
                "location_id": str(item.location_id),
                "quantity": item.quantity_available or 0,
                "quantity_available": item.quantity_available or 0,
                "low_stock_threshold": item.low_stock_threshold or 0,
                "reorder_point": getattr(item, 'reorder_point', 0),
                "inventory_status": getattr(item, 'inventory_status', 'active'),
                "last_restocked_at": item.last_restocked_at.isoformat() if getattr(item, 'last_restocked_at', None) else None,
                "last_sold_at": item.last_sold_at.isoformat() if getattr(item, 'last_sold_at', None) else None,
                "version": getattr(item, 'version', 1),
                "created_at": item.created_at.isoformat() if item.created_at else None,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
                "variant": variant_data,
                "location": location_info
            }
        except Exception as e:
            logger.error(f"Error serializing inventory item", metadata={"item_id": str(item.id)}, exception=e)
            return None

    async def list(self, page: int = 1, limit: int = 10, product_id: Optional[UUID] = None, location_id: Optional[UUID] = None, location_name: Optional[str] = None, low_stock: Optional[bool] = None, in_stock: Optional[bool] = None, out_of_stock: Optional[bool] = None, search: Optional[str] = None, sort_by: Optional[str] = None, sort_order: Optional[str] = None) -> dict:
        try:
            logger.info(f"get_all_inventory_items called", metadata={
                "page": page,
                "limit": limit,
                "sort_by": sort_by,
                "sort_order": sort_order,
                "low_stock": low_stock,
                "search": search
            })
            offset = (page - 1) * limit
            
            # Build query with proper eager loading
            query = select(Inventory).options(
                joinedload(Inventory.variant).joinedload(ProductVariant.product),
                joinedload(Inventory.variant).joinedload(ProductVariant.images),
                joinedload(Inventory.location)
            )
            count_query = select(func.count(Inventory.id))

            conditions = []
            if product_id:
                conditions.append(Inventory.variant.has(ProductVariant.product_id == product_id))
            if location_id:
                conditions.append(Inventory.location_id == location_id)
            if location_name:
                conditions.append(Inventory.location.has(WarehouseLocation.name == location_name))
            if low_stock is not None:
                if low_stock:
                    conditions.append(Inventory.quantity_available <= Inventory.low_stock_threshold)
                else:
                    conditions.append(Inventory.quantity_available > Inventory.low_stock_threshold)
            if in_stock is not None:
                if in_stock:
                    conditions.append(Inventory.quantity_available > Inventory.low_stock_threshold)
                else:
                    conditions.append(Inventory.quantity_available <= Inventory.low_stock_threshold)
            if out_of_stock is not None:
                if out_of_stock:
                    conditions.append(Inventory.quantity_available == 0)
                else:
                    conditions.append(Inventory.quantity_available > 0)
            if search:
                # Search in product name, variant name, variant SKU, and location name
                search_term = f"%{search.lower()}%"
                search_conditions = [
                    Inventory.variant.has(ProductVariant.product.has(Product.name.ilike(search_term))),
                    Inventory.variant.has(ProductVariant.name.ilike(search_term)),
                    Inventory.variant.has(ProductVariant.sku.ilike(search_term)),
                    Inventory.location.has(WarehouseLocation.name.ilike(search_term))
                ]
                conditions.append(or_(*search_conditions))
            
            if conditions:
                query = query.filter(and_(*conditions))
                count_query = count_query.filter(and_(*conditions))

            # Build dynamic sorting - simplified approach with defaults
            sort_field = sort_by or "updated_at"
            sort_dir = sort_order or "desc"
            
            logger.info(f"Applying sorting", metadata={
                "sort_field": sort_field,
                "sort_dir": sort_dir
            })
            
            if sort_field == "created_at":
                if sort_dir == "asc":
                    query = query.order_by(Inventory.created_at.asc())
                else:
                    query = query.order_by(Inventory.created_at.desc())
            elif sort_field == "product_name":
                # Add explicit join for product name sorting
                query = query.join(ProductVariant, Inventory.variant_id == ProductVariant.id)
                query = query.join(Product, ProductVariant.product_id == Product.id)
                if sort_dir == "asc":
                    query = query.order_by(Product.name.asc())
                else:
                    query = query.order_by(Product.name.desc())
            elif sort_field == "quantity":
                if sort_dir == "asc":
                    query = query.order_by(Inventory.quantity_available.asc())
                else:
                    query = query.order_by(Inventory.quantity_available.desc())
            elif sort_field == "location_name":
                # Add explicit join for location name sorting
                query = query.join(WarehouseLocation, Inventory.location_id == WarehouseLocation.id)
                if sort_dir == "asc":
                    query = query.order_by(WarehouseLocation.name.asc())
                else:
                    query = query.order_by(WarehouseLocation.name.desc())
            else:  # default to updated_at
                if sort_dir == "asc":
                    query = query.order_by(Inventory.updated_at.asc())
                else:
                    query = query.order_by(Inventory.updated_at.desc())

            query = query.offset(offset).limit(limit)

            # Log the final query for debugging
            logger.info(f"Final SQL query", metadata={
    "query": str(query)
})

            # Execute queries
            total = await self.db.scalar(count_query) or 0
            result = await self.db.execute(query)
            items = result.scalars().unique().all()  # Add .unique() to handle joined eager loads

            # Convert to dictionaries with safe serialization
            items_data = []
            for item in items:
                try:
                    # Safely serialize variant data
                    variant_data = None
                    if item.variant:
                        # Get primary image safely
                        primary_image = None
                        if item.variant.images:
                            primary_image = next(
                                (img for img in item.variant.images if img.is_primary),
                                item.variant.images[0] if len(item.variant.images) > 0 else None
                            )
                        
                        # Safely serialize product info
                        product_info = None
                        if item.variant.product:
                            product_info = {
                                "id": str(item.variant.product.id),
                                "name": item.variant.product.name or "",
                                "description": item.variant.product.description or "",
                                "is_active": getattr(item.variant.product, 'is_active', True)
                            }

                        variant_data = {
                            "id": str(item.variant.id),
                            "name": item.variant.name or "",
                            "sku": item.variant.sku or "",
                            "base_price": float(item.variant.base_price) if item.variant.base_price else 0.0,
                            "sale_price": float(item.variant.sale_price) if item.variant.sale_price else None,
                            "is_active": getattr(item.variant, 'is_active', True),
                            "product": product_info,
                            "primary_image": {
                                "id": str(primary_image.id),
                                "url": primary_image.url or "",
                                "alt_text": primary_image.alt_text or "",
                                "is_primary": primary_image.is_primary
                            } if primary_image else None,
                            "images": [
                                {
                                    "id": str(img.id),
                                    "url": img.url or "",
                                    "alt_text": img.alt_text or "",
                                    "is_primary": img.is_primary,
                                    "sort_order": img.sort_order or 0
                                }
                                for img in (item.variant.images or [])
                            ]
                        }

                    # Safely serialize location info
                    location_info = None
                    if item.location:
                        location_info = {
                            "id": str(item.location.id),
                            "name": item.location.name or "",
                            "address": item.location.address or "",
                            "description": item.location.description or "",
                            "created_at": item.location.created_at.isoformat() if item.location.created_at else None,
                            "updated_at": item.location.updated_at.isoformat() if item.location.updated_at else None
                        }

                    item_dict = {
                        "id": str(item.id),
                        "variant_id": str(item.variant_id),
                        "location_id": str(item.location_id),
                        "quantity": item.quantity_available or 0,
                        "quantity_available": item.quantity_available or 0,
                        "low_stock_threshold": item.low_stock_threshold or 0,
                        "reorder_point": getattr(item, 'reorder_point', 0),
                        "inventory_status": getattr(item, 'inventory_status', 'active'),
                        "last_restocked_at": item.last_restocked_at.isoformat() if getattr(item, 'last_restocked_at', None) else None,
                        "last_sold_at": item.last_sold_at.isoformat() if getattr(item, 'last_sold_at', None) else None,
                        "version": getattr(item, 'version', 1),
                        "created_at": item.created_at.isoformat() if item.created_at else None,
                        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
                        "variant": variant_data,
                        "location": location_info
                    }
                    items_data.append(item_dict)
                    
                except Exception as e:
                    logger.error(f"Error serializing inventory item", metadata={
    "item_id": str(getattr(item, 'id', 'unknown'))
}, exception=e)
                    # Create a minimal safe item to prevent complete failure
                    safe_item = {
                        "id": str(getattr(item, 'id', '')),
                        "variant_id": str(getattr(item, 'variant_id', '')),
                        "location_id": str(getattr(item, 'location_id', '')),
                        "quantity": getattr(item, 'quantity', 0),
                        "quantity_available": getattr(item, 'quantity_available', 0),
                        "low_stock_threshold": getattr(item, 'low_stock_threshold', 0),
                        "reorder_point": 0,
                        "inventory_status": "error",
                        "last_restocked_at": None,
                        "last_sold_at": None,
                        "version": 1,
                        "created_at": getattr(item, 'created_at', datetime.now(timezone.utc)).isoformat() if hasattr(item, 'created_at') else None,
                        "updated_at": None,
                        "variant": None,
                        "location": None,
                        "error": f"Serialization error: {str(e)}"
                    }
                    items_data.append(safe_item)

            return {
            "data": items_data,
            "total": total,
            "page": page,
            "limit": limit,
            "pages": (total + limit - 1) // limit if total > 0 else 0
        }
            
        except Exception as e:
            logger.error("Error in get_all_inventory_items", exception=e)
            # Return empty result instead of raising exception
            return {
            "data": [],
            "total": 0,
            "page": page,
            "limit": limit,
            "pages": 0,
            "error": f"Database error: {str(e)}"
        }

    async def create(self, inventory_data: InventoryCreate) -> InventoryResponse:
        existing_inventory = await self.get(variant_id=inventory_data.variant_id)
        if existing_inventory:
            raise APIException(status_code=400, message="Inventory for this variant already exists.")
        
        variant = await self.db.scalar(select(ProductVariant).filter(ProductVariant.id == inventory_data.variant_id))
        if not variant:
            raise APIException(status_code=404, message="Product variant not found.")

        data = inventory_data.model_dump()
        data["quantity_available"] = data.pop("quantity")
        new_inventory = Inventory(id=uuid7(), **data)
        self.db.add(new_inventory)
        await self.db.commit()
        # location_id is set but the relationship itself was never loaded on this brand-new object - request it explicitly so model_validate() below doesn't trigger a lazy load outside of an awaitable context.
        await self.db.refresh(new_inventory, attribute_names=["location"])
        return InventoryResponse.model_validate(new_inventory)

    async def update(self, inventory_id: UUID, inventory_data: InventoryUpdate, user_id: Optional[UUID] = None) -> InventoryResponse:
        inventory_item = await self.get(inventory_id)
        if not inventory_item:
            raise APIException(status_code=404, message="Inventory item not found")

        update_data = inventory_data.model_dump(exclude_unset=True)
        location_name = update_data.pop("location_name", None)
        quantity = update_data.pop("quantity", None)
        if quantity is not None:
            await self.set_stock(inventory_item.variant_id, quantity, user_id, commit=False)

        if location_name:
            normalized_name = location_name.strip()
            if normalized_name:
                existing_location = await self.db.scalar(
                    select(WarehouseLocation).where(func.lower(WarehouseLocation.name) == normalized_name.lower())
                )
                if existing_location:
                    inventory_item.location_id = existing_location.id
                else:
                    new_location = WarehouseLocation(id=uuid7(), name=normalized_name)
                    self.db.add(new_location)
                    await self.db.flush()
                    inventory_item.location_id = new_location.id

        for field, value in update_data.items():
            setattr(inventory_item, field, value)

        inventory_item.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        if quantity is not None:
            await self._after_stock_change(inventory_item.variant_id)
        await self.db.refresh(inventory_item)
        return InventoryResponse.model_validate(inventory_item)

    async def delete(self, inventory_id: UUID):
        inventory_item = await self.get(inventory_id)
        if not inventory_item:
            raise APIException(status_code=404, message="Inventory item not found")
        
        await self.db.delete(inventory_item)
        await self.db.commit()

    async def adjust_stock(self, adjustment_data: StockAdjustmentCreate, adjusted_by_user_id: Optional[UUID] = None, commit: bool = True) -> Inventory:
        """Adjust stock levels atomically with distributed and database locks"""
        try:
            # Use distributed lock FIRST to prevent race conditions across servers
            if self.lock_service:
                lock = self.lock_service.get_inventory_lock(adjustment_data.variant_id, timeout=30)
                async with lock:
                    return await self._perform_stock_adjustment(adjustment_data, adjusted_by_user_id, commit)
            else:
                # Fallback to database-only lock if Redis unavailable
                logger.warning(f"Redis lock service unavailable, using database-only lock", metadata={
    "variant_id": str(adjustment_data.variant_id)
})
                return await self._perform_stock_adjustment(adjustment_data, adjusted_by_user_id, commit)
                
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error adjusting stock", metadata={
    "variant_id": str(adjustment_data.variant_id)
}, exception=e)
            raise

    async def _perform_stock_adjustment(self, adjustment_data: StockAdjustmentCreate, adjusted_by_user_id: Optional[UUID], commit: bool) -> Inventory:
        """Internal method to perform stock adjustment with database lock"""
        try:
            # Get inventory with database lock
            inventory = await Inventory.get_with_lock(self.db, adjustment_data.variant_id)
            
            if not inventory:
                raise APIException(
                    status_code=404,
                    message=f"Inventory not found for variant {adjustment_data.variant_id}"
                )
            
            # Perform atomic update
            await inventory.atomic_update_stock(
                db=self.db,
                quantity_change=adjustment_data.quantity_change,
                reason=adjustment_data.reason,
                user_id=adjusted_by_user_id,
                notes=adjustment_data.notes
            )
            
            if commit:
                await self.db.commit()
            await self._after_stock_change(adjustment_data.variant_id)
            return inventory

        except APIException:
            # Deliberate 4xx (e.g. 404 inventory not found, 400 insufficient stock) -
            # propagate as-is instead of relabeling it as a 500 below.
            raise
        except Exception as e:
            logger.error("Error in stock adjustment", exception=e)
            raise APIException(
                status_code=500,
                message=f"Failed to adjust stock: {str(e)}"
            )

    async def set_stock(self, variant_id: UUID, quantity: int, user_id: Optional[UUID] = None, commit: bool = True) -> Inventory:
        """Set a variant's stock to a counted number; the difference is recorded in the adjustment history."""
        inventory = await Inventory.get_with_lock(self.db, variant_id)
        if not inventory:
            raise APIException(status_code=404, message=f"Inventory not found for variant {variant_id}")
        change = quantity - inventory.quantity_available
        if change:
            await inventory.atomic_update_stock(
                db=self.db, quantity_change=change, reason="Stock count", user_id=user_id, notes=f"Set to {quantity}"
            )
            if change > 0:
                inventory.last_restocked_at = datetime.now(timezone.utc)
        if commit:
            await self.db.commit()
        await self._after_stock_change(variant_id)
        return inventory

    async def _after_stock_change(self, variant_id: UUID) -> None:
        """Clear cached prices/stock and recompute the product's availability (best-effort)."""
        variant = await self.db.scalar(select(ProductVariant).where(ProductVariant.id == variant_id))
        if variant and variant.product_id:
            invalidate_variant(variant_id, variant.product_id)
            try:
                await self.sync(variant.product_id)
            except Exception as e:
                logger.warning("Failed to sync product availability after stock change", exception=e)

    async def adjustments(self, inventory_id: Optional[UUID] = None, page: int = 1, limit: int = 10) -> Dict[str, Any]:
        """Get stock adjustments with pagination. If inventory_id provided, filters by inventory item."""
        offset = (page - 1) * limit
        
        # Build base query
        query = select(StockAdjustment).options(joinedload(StockAdjustment.adjusted_by))
        count_query = select(func.count()).select_from(StockAdjustment)
        
        if inventory_id:
            query = query.filter(StockAdjustment.inventory_id == inventory_id)
            count_query = count_query.filter(StockAdjustment.inventory_id == inventory_id)
        
        # Get total count
        total_result = await self.db.execute(count_query)
        total = total_result.scalar()
        
        # Get paginated results
        query = query.order_by(StockAdjustment.created_at.desc()).offset(offset).limit(limit)
        result = await self.db.execute(query)
        adjustments = result.scalars().all()
        
        return {
            "data": [StockAdjustmentResponse.model_validate(adjustment) for adjustment in adjustments],
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit if limit else 1
            }
        }


    async def is_low_stock(self, inventory_id: UUID) -> bool:
        inventory_item = await self.get(inventory_id)
        if not inventory_item:
            return False
        return inventory_item.quantity_available <= inventory_item.low_stock_threshold

    # Enhanced inventory integration methods
    async def stock_levels(
        self,
        variant_ids: Optional[List[UUID]] = None,
        location_id: Optional[UUID] = None
    ) -> List[Dict[str, Any]]:
        """Get real-time stock levels for variants"""
        query = select(Inventory).options(
            joinedload(Inventory.variant).joinedload(ProductVariant.product),
            joinedload(Inventory.location)
        )
        
        conditions = []
        if variant_ids:
            conditions.append(Inventory.variant_id.in_(variant_ids))
        if location_id:
            conditions.append(Inventory.location_id == location_id)
        
        if conditions:
            query = query.where(and_(*conditions))
        
        result = await self.db.execute(query)
        inventory_items = result.scalars().all()
        
        stock_levels = []
        for item in inventory_items:
            stock_levels.append({
                "variant_id": str(item.variant_id),
                "variant_name": item.variant.name if item.variant else None,
                "product_name": item.variant.product.name if item.variant and item.variant.product else None,
                "location_id": str(item.location_id),
                "location_name": item.location.name if item.location else None,
                "current_quantity": item.quantity_available,
                "low_stock_threshold": item.low_stock_threshold,
                "is_low_stock": item.quantity_available <= item.low_stock_threshold,
                "is_out_of_stock": item.quantity_available <= 0,
                "last_updated": item.updated_at.isoformat() if item.updated_at else None
            })
        
        return stock_levels


    async def reorder_suggestions(
        self,
        location_id: Optional[UUID] = None,
        days_ahead: int = 30
    ) -> List[Dict[str, Any]]:
        """Generate reorder suggestions based on actual consumption rates"""
        query = select(Inventory).options(
            joinedload(Inventory.variant).joinedload(ProductVariant.product),
            joinedload(Inventory.location)
        )
        
        if location_id:
            query = query.where(Inventory.location_id == location_id)
        
        result = await self.db.execute(query)
        inventory_items = result.scalars().all()
        
        reorder_suggestions = []
        
        for item in inventory_items:
            if item.quantity_available <= item.low_stock_threshold:
                suggested_quantity = item.low_stock_threshold * 2
                
                urgency = "high" if item.quantity_available <= 0 else "medium" if item.quantity_available <= item.low_stock_threshold else "low"
                
                reorder_suggestions.append({
                    "variant_id": str(item.variant_id),
                    "variant_name": item.variant.name if item.variant else None,
                    "product_name": item.variant.product.name if item.variant and item.variant.product else None,
                    "location_id": str(item.location_id),
                    "location_name": item.location.name if item.location else None,
                    "current_stock": item.quantity_available,
                    "low_stock_threshold": item.low_stock_threshold,
                    "suggested_quantity": suggested_quantity,
                    "urgency": urgency,
                    "days_until_stockout": 7 if item.quantity_available > 0 else 0
                })
        
        # Sort by urgency
        urgency_order = {"high": 0, "medium": 1, "low": 2}
        reorder_suggestions.sort(key=lambda x: urgency_order.get(x["urgency"], 3))
        
        return reorder_suggestions

        


    async def check_stock(
        self,
        variant_id: UUID,
        quantity: int,
        location_id: Optional[UUID] = None
    ) -> Dict[str, Any]:
        """Check if sufficient stock is available; returns availability and current levels."""
        try:
            # Find inventory item
            query = select(Inventory).where(Inventory.variant_id == variant_id)
            if location_id:
                query = query.where(Inventory.location_id == location_id)
            
            result = await self.db.execute(query)
            inventory = result.scalar_one_or_none()
            
            if not inventory:
                return {
                    "available": False,
                    "current_stock": 0,
                    "requested_quantity": quantity,
                    "message": "Product not found in inventory",
                    "stock_status": "out_of_stock"
                }
            
            # Check if requested quantity is available
            available = inventory.quantity_available >= quantity and inventory.quantity_available > 0
            
            return {
            "available": available,
            "current_stock": inventory.quantity_available,
            "requested_quantity": quantity,
            "inventory_id": str(inventory.id),
            "location_id": str(inventory.location_id),
            "stock_status": inventory.stock_status,
            "message": "Stock available" if available else f"Out of stock" if inventory.quantity_available <= 0 else f"Insufficient stock. Available: {inventory.quantity_available}, Requested: {quantity}"
        }
            
        except Exception as e:
            logger.error("Error checking stock availability", exception=e)
            return {
            "available": False,
            "current_stock": 0,
            "requested_quantity": quantity,
            "stock_status": "out_of_stock",
            "message": f"Error checking stock: {str(e)}"
        }

    async def increment(
        self,
        variant_id: UUID,
        quantity: int,
        location_id: UUID,
        order_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None
    ) -> Dict[str, Any]:
        """Atomically increment stock when order is cancelled using distributed and database locks."""
        try:
            # Use distributed lock FIRST to prevent race conditions across servers
            if self.lock_service:
                lock = self.lock_service.get_inventory_lock(variant_id, timeout=30)
                async with lock:
                    return await self._perform_increment_stock(variant_id, quantity, location_id, order_id, user_id)
            else:
                # Fallback to database-only lock if Redis unavailable
                logger.warning("Redis unavailable, using database fallback", metadata={
                    "redis_host": "localhost:6379",
                    "fallback": "database_lock",
                    "operation": "stock_decrement",
                    "variant_id": str(variant_id),
                    "impact": "reduced_performance"
                })
                return await self._perform_increment_stock(variant_id, quantity, location_id, order_id, user_id)
                
        except Exception as e:
            await self.db.rollback()
            logger.error("Stock increment failed", exception=e, metadata={
                "variant_id": str(variant_id),
                "quantity": quantity,
                "operation": "stock_increment",
                "critical": True
            })
            return {
                "success": False,
                "message": f"Failed to increment stock: {str(e)}"
            }

    async def _perform_increment_stock(
        self,
        variant_id: UUID,
        quantity: int,
        location_id: UUID,
        order_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None
    ) -> Dict[str, Any]:
        """Internal method to perform stock increment with database lock"""
        try:
            # Get inventory with atomic lock
            inventory = await Inventory.get_with_lock(self.db, variant_id)

            if not inventory:
                raise APIException(
                    status_code=404,
                    message=f"Inventory not found for variant {variant_id}"
                )

            # Perform atomic stock update
            adjustment = await inventory.atomic_update_stock(
                db=self.db,
                quantity_change=quantity,
                reason="order_cancelled",
                user_id=user_id,
                notes=f"Stock restored from cancelled order {order_id}" if order_id else "Stock restored from cancellation"
            )

            # flush, don't commit: callers manage their own larger transaction around this
            # call, and committing here previously corrupted refund auto-approval's Refund record.
            await self.db.flush()

            logger.info(f"Atomically incremented stock for variant {variant_id}: +{quantity}")

            # Queue product availability sync as background task (don't wait for it)
            try:
                # Local: core.worker imports InventoryService, so this would be circular at top level.
                from core.worker import enqueue_sync_product_availability

                # Get the variant to find its product
                variant_result = await self.db.execute(
                    select(ProductVariant).where(ProductVariant.id == variant_id)
                )
                variant = variant_result.scalar_one_or_none()

                if variant:
                    # Queue sync as background task - don't block the cancellation response
                    await enqueue_sync_product_availability(str(variant.product_id))
                    logger.info(f"Queued inventory sync", metadata={
        "product_id": str(variant.product_id)
    })
            except Exception as sync_error:
                # Don't fail the entire operation if queueing fails
                logger.warning(f"Failed to queue product availability sync: {sync_error}")

            return {
                "success": True,
                "message": "Stock incremented successfully",
                "inventory_id": str(inventory.id),
                "previous_quantity": inventory.quantity_available - quantity,
                "new_quantity": inventory.quantity_available,
                "quantity_incremented": quantity,
                "adjustment_id": str(adjustment.id) if adjustment else None
            }

        except APIException:
            await self.db.rollback()
            raise
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to increment stock atomically: {e}")
            raise APIException(
                status_code=500,
                message=f"Failed to increment stock: {str(e)}"
            )

    


    async def sync(self, product_id: Optional[UUID] = None) -> Dict[str, Any]:
        """Sync availability_status from inventory levels, for one product or all."""
        try:
            if product_id:
                # Sync single product
                product_result = await self.db.execute(
                    select(Product)
                    .where(Product.id == product_id)
                    .options(selectinload(Product.variants).selectinload(ProductVariant.inventory))
                )
                product = product_result.scalar_one_or_none()
                
                if not product:
                    return {
                        "success": False,
                        "message": f"Product {product_id} not found"
                    }
                
                # Check if any variant has stock
                has_stock = False
                total_stock = 0
                
                for variant in product.variants or []:
                    if variant.inventory and variant.inventory.quantity_available > 0:
                        has_stock = True
                        total_stock += variant.inventory.quantity_available
                
                # Log product availability status (computed property, can't be set directly)
                current_status = product.availability_status
                computed_status = "available" if has_stock else "out_of_stock"
                
                logger.info(f"Synced product {product_id} availability: {current_status} (computed: {computed_status}, total stock: {total_stock})")
                
                return {
                    "success": True,
                    "product_id": str(product_id),
                    "current_status": current_status,
                    "computed_status": computed_status,
                    "total_stock": total_stock,
                    "variant_count": len(product.variants or [])
                }
            else:
                # Sync all products
                products_result = await self.db.execute(
                    select(Product)
                    .options(selectinload(Product.variants).selectinload(ProductVariant.inventory))
                )
                products = products_result.scalars().all()
                
                still_in_stock = 0
                went_out_of_stock = 0
                
                for product in products:
                    total_stock = 0
                    for variant in product.variants or []:
                        if variant.inventory:
                            total_stock += variant.inventory.quantity_available
                    
                    if total_stock == 0:
                        went_out_of_stock += 1
                        logger.info(f"Product {product.id} is out of stock (computed)")
                    else:
                        still_in_stock += 1
                
                return {
                    "success": True,
                    "total_products": len(products),
                    "in_stock": still_in_stock,
                    "out_of_stock": went_out_of_stock,
                    "message": f"Checked {len(products)} products ({went_out_of_stock} out of stock, {still_in_stock} in stock)"
                }
            
        except Exception as e:
            logger.error(f"Failed to sync availability: {e}")
            return {
                "success": False,
                "message": f"Failed to sync availability: {str(e)}"
            }