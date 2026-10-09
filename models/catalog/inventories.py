"""Inventory models with atomic stock ops: WarehouseLocation, Inventory, StockAdjustment."""
from sqlalchemy import String, Integer, ForeignKey, Text, DateTime, func, Index, select
from sqlalchemy.orm import relationship, Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncSession
from core.db import Base, CHAR_LENGTH, GUID, UTCDateTime
from core.utils.uuid_utils import uuid7
from core.exceptions import APIException
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from uuid import UUID as UUIDType
from core.logging import get_structured_logger
import uuid
from enum import Enum

logger = get_structured_logger(__name__)


class InventoryStatus(str, Enum):
    """Inventory status types"""
    ACTIVE = "active"
    INACTIVE = "inactive"
    DISCONTINUED = "discontinued"


class WarehouseLocation(Base):
    """Warehouse locations for inventory management"""
    __tablename__ = "warehouse_locations"
    __table_args__ = (
        # Indexes for search and performance
        Index('idx_warehouse_locations_name', 'name'),
        {'schema': 'catalog'}
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    name: Mapped[str] = mapped_column(String(CHAR_LENGTH))
    address: Mapped[Optional[str]] = mapped_column(String(CHAR_LENGTH), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    inventories = relationship("Inventory", back_populates="location")


class Inventory(Base):
    """Product variant inventory tracking with atomic operations"""
    __tablename__ = "inventory"
    __table_args__ = (
        # Optimized indexes for atomic operations
        Index('idx_inventory_variant_id', 'variant_id'),
        Index('idx_inventory_location_id', 'location_id'),
        Index('idx_inventory_quantity_available', 'quantity_available'),
        Index('idx_inventory_low_stock', 'low_stock_threshold'),
        Index('idx_inventory_status', 'inventory_status'),
        # Composite indexes for common atomic queries
        Index('idx_inventory_variant_status', 'variant_id', 'inventory_status'),
        Index('idx_inventory_location_quantity', 'location_id', 'quantity_available'),
        {'schema': 'catalog'}
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    variant_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("catalog.product_variants.id"), unique=True)
    location_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), ForeignKey("catalog.warehouse_locations.id"), nullable=True)

    # Atomic stock tracking fields
    quantity_available: Mapped[int] = mapped_column(Integer, default=0)  # Available for sale

    # Thresholds
    low_stock_threshold: Mapped[int] = mapped_column(Integer, default=10)
    reorder_point: Mapped[int] = mapped_column(Integer, default=5)

    # Status tracking
    inventory_status: Mapped[InventoryStatus] = mapped_column(String(50), default=InventoryStatus.ACTIVE)

    # Timestamps for tracking
    last_restocked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    last_sold_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)

    # Optimistic locking version counter
    version: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    variant = relationship("ProductVariant", back_populates="inventory")
    location = relationship("WarehouseLocation", back_populates="inventories")
    adjustments = relationship("StockAdjustment", back_populates="inventory", cascade="all, delete-orphan")
    
    @classmethod
    async def get_with_lock(cls, db: AsyncSession, variant_id: UUIDType) -> Optional['Inventory']:
        """Get inventory with SELECT ... FOR UPDATE. populate_existing=True is required: without
        it, a row already read earlier in the session comes back stale, silently defeating the lock."""
        try:
            query = select(cls).where(cls.variant_id == variant_id).with_for_update().execution_options(populate_existing=True)
            result = await db.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error getting inventory with lock for variant {variant_id}: {e}")
            raise

    @classmethod
    async def get_multiple_with_lock(cls, db: AsyncSession, variant_ids: List[UUIDType]) -> List['Inventory']:
        """Get multiple inventory records with locks, ordered by variant_id to prevent deadlocks."""
        try:
            query = select(cls).where(
                cls.variant_id.in_(variant_ids)
            ).order_by(cls.variant_id).with_for_update().execution_options(populate_existing=True)

            result = await db.execute(query)
            return result.scalars().all()
        except Exception as e:
            logger.error(f"Error getting multiple inventories with lock: {e}")
            raise

    async def atomic_update_stock(
        self, 
        db: AsyncSession,
        quantity_change: int, 
        reason: str,
        user_id: Optional[UUIDType] = None,
        notes: Optional[str] = None
    ) -> 'StockAdjustment':
        """Atomically update stock with an audit trail. Caller must already hold the row lock."""
        # Calculate new quantities
        new_available = self.quantity_available + quantity_change
        
        # Validate stock levels
        if new_available < 0:
            raise APIException(
                status_code=400,
                message=f"Insufficient stock. Available: {self.quantity_available}, Requested: {abs(quantity_change)}"
            )
        
        # Update inventory atomically
        old_quantity = self.quantity_available
        self.quantity_available = new_available
        self.version += 1  # Optimistic locking
        
        # Update timestamps
        if quantity_change < 0:
            self.last_sold_at = datetime.now(timezone.utc)
        elif quantity_change > 0:
            self.last_restocked_at = datetime.now(timezone.utc)
        
        # Create audit record
        adjustment = StockAdjustment(
            inventory_id=self.id,
            quantity_change=quantity_change,
            reason=reason,
            adjusted_by_user_id=user_id,
            notes=notes or f"Stock changed from {old_quantity} to {new_available}"
        )
        
        db.add(adjustment)
        
        logger.info(f"Stock updated atomically: variant={self.variant_id}, change={quantity_change}, new_stock={new_available}")
        
        return adjustment



    @property
    def stock_status(self) -> str:
        """Determine stock status based on available quantity"""
        if self.quantity_available <= 0:
            return "out_of_stock"
        elif self.quantity_available <= self.low_stock_threshold:
            return "low_stock"
        else:
            return "in_stock"

    def to_dict(self) -> Dict[str, Any]:
        """Convert inventory to dictionary for API responses"""
        return {
            "id": str(self.id),
            "variant_id": str(self.variant_id),
            "location_id": str(self.location_id),
            "quantity_available": self.quantity_available,
            "low_stock_threshold": self.low_stock_threshold,
            "reorder_point": self.reorder_point,
            "inventory_status": self.inventory_status,
            "stock_status": self.stock_status,
            "last_restocked_at": self.last_restocked_at.isoformat() if self.last_restocked_at else None,
            "last_sold_at": self.last_sold_at.isoformat() if self.last_sold_at else None,
            "version": self.version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class StockAdjustment(Base):
    """Stock adjustment records for audit trail"""
    __tablename__ = "stock_adjustments"
    __table_args__ = (
        # Indexes for search and performance
        Index('idx_stock_adjustments_inventory_id', 'inventory_id'),
        Index('idx_stock_adjustments_reason', 'reason'),
        Index('idx_stock_adjustments_user_id', 'adjusted_by_user_id'),
        Index('idx_stock_adjustments_created_at', 'created_at'),
        # Composite indexes for common queries
        Index('idx_stock_adjustments_inventory_created', 'inventory_id', 'created_at'),
        {'schema': 'catalog'}
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    inventory_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("catalog.inventory.id"))
    quantity_change: Mapped[int] = mapped_column(Integer)  # Positive for add, negative for remove
    reason: Mapped[str] = mapped_column(String(CHAR_LENGTH))  # e.g., "initial_stock", "received", "sold", "returned", "damaged"
    adjusted_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), ForeignKey("accounts.users.id"), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    inventory = relationship("Inventory", back_populates="adjustments")
    adjusted_by = relationship("User", back_populates="stock_adjustments")


async def atomic_bulk_stock_update(
    db: AsyncSession,
    stock_changes: List[Dict],
    reason: str,
    user_id: Optional[UUIDType] = None
) -> List[Dict]:
    """Atomically update multiple stock levels in a single transaction."""
    try:
        # Extract variant IDs and get locks in consistent order
        variant_ids = [change['variant_id'] for change in stock_changes]
        inventories = await Inventory.get_multiple_with_lock(db, variant_ids)
        
        # Create lookup for faster access
        inventory_map = {inv.variant_id: inv for inv in inventories}
        
        results = []
        
        # Process each stock change atomically
        for change in stock_changes:
            variant_id = change['variant_id']
            quantity_change = change['quantity_change']
            notes = change.get('notes')
            
            inventory = inventory_map.get(variant_id)
            if not inventory:
                raise APIException(
                    status_code=404,
                    message=f"Inventory not found for variant {variant_id}"
                )
            
            # Perform atomic update
            adjustment = await inventory.atomic_update_stock(
                db=db,
                quantity_change=quantity_change,
                reason=reason,
                user_id=user_id,
                notes=notes
            )
            
            results.append({
                "variant_id": str(variant_id),
                "quantity_change": quantity_change,
                "new_quantity": inventory.quantity_available,
                "adjustment_id": str(adjustment.id)
            })
        
        # Commit all changes atomically
        await db.commit()
        
        logger.info(f"Bulk stock update completed: {len(stock_changes)} variants updated")
        
        return results
        
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in bulk stock update: {e}")
        raise