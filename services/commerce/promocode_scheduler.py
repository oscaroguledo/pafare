"""Promocode scheduler: automatic activation/deactivation based on validity dates."""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from datetime import datetime, timezone
from typing import Dict, Any, List
from core.logging import get_structured_logger

from models.commerce.promocode import Promocode
from core.db import get_db

logger = get_structured_logger(__name__)


class PromoCodeScheduler:
    """Service for managing promocode lifecycle based on validity dates"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def update_promocode_statuses(self) -> Dict[str, Any]:
        """Activate/deactivate promocodes based on valid_from/valid_until/usage_limit."""
        current_time = datetime.now(timezone.utc)
        
        activated_count = 0
        deactivated_count = 0
        results = []
        
        try:
            # --- Step 1: activate inactive promocodes now within their validity window ---
            result = await self.db.execute(
                select(Promocode).where(
                    and_(
                        Promocode.is_active == False,
                        or_(
                            Promocode.valid_from == None,
                            Promocode.valid_from <= current_time
                        ),
                        or_(
                            Promocode.valid_until == None,
                            Promocode.valid_until > current_time
                        ),
                        or_(
                            Promocode.usage_limit == None,
                            Promocode.used_count < Promocode.usage_limit
                        )
                    )
                )
            )
            
            promocodes_to_activate = result.scalars().all()
            
            for promocode in promocodes_to_activate:
                promocode.is_active = True
                activated_count += 1
                results.append({
                    "code": promocode.code,
                    "action": "activated",
                    "reason": "validity period started",
                    "valid_from": promocode.valid_from.isoformat() if promocode.valid_from else None,
                    "valid_until": promocode.valid_until.isoformat() if promocode.valid_until else None
                })
                logger.info(f"✅ Activated promocode: {promocode.code}")
            
            # --- Step 2: deactivate active promocodes whose valid_until has passed ---
            result = await self.db.execute(
                select(Promocode).where(
                    and_(
                        Promocode.is_active == True,
                        Promocode.valid_until != None,
                        Promocode.valid_until <= current_time
                    )
                )
            )
            
            expired_promocodes = result.scalars().all()
            
            for promocode in expired_promocodes:
                promocode.is_active = False
                deactivated_count += 1
                results.append({
                    "code": promocode.code,
                    "action": "deactivated",
                    "reason": "expired",
                    "valid_until": promocode.valid_until.isoformat() if promocode.valid_until else None
                })
                logger.info(f"❌ Deactivated expired promocode: {promocode.code}")
            
            # --- Step 3: deactivate promocodes that reached their usage limit ---
            result = await self.db.execute(
                select(Promocode).where(
                    and_(
                        Promocode.is_active == True,
                        Promocode.usage_limit != None,
                        Promocode.used_count >= Promocode.usage_limit
                    )
                )
            )
            
            limit_reached_promocodes = result.scalars().all()
            
            for promocode in limit_reached_promocodes:
                promocode.is_active = False
                deactivated_count += 1
                results.append({
                    "code": promocode.code,
                    "action": "deactivated",
                    "reason": "usage_limit_reached",
                    "used_count": promocode.used_count,
                    "usage_limit": promocode.usage_limit
                })
                logger.info(f"❌ Deactivated promocode (limit reached): {promocode.code} ({promocode.used_count}/{promocode.usage_limit})")
            
            # --- Step 4: deactivate promocodes not yet within their validity window ---
            result = await self.db.execute(
                select(Promocode).where(
                    and_(
                        Promocode.is_active == True,
                        Promocode.valid_from != None,
                        Promocode.valid_from > current_time
                    )
                )
            )
            
            not_yet_valid_promocodes = result.scalars().all()
            
            for promocode in not_yet_valid_promocodes:
                promocode.is_active = False
                deactivated_count += 1
                results.append({
                    "code": promocode.code,
                    "action": "deactivated",
                    "reason": "not_yet_valid",
                    "valid_from": promocode.valid_from.isoformat() if promocode.valid_from else None
                })
                logger.info(f"❌ Deactivated promocode (not yet valid): {promocode.code}")
            
            # Commit all changes
            await self.db.commit()
            
            logger.info(f"✅ Promocode status update completed: {activated_count} activated, {deactivated_count} deactivated")
            
            return {
                "success": True,
                "activated_count": activated_count,
                "deactivated_count": deactivated_count,
                "total_updated": activated_count + deactivated_count,
                "timestamp": current_time.isoformat(),
                "results": results
            }
            
        except Exception as e:
            await self.db.rollback()
            logger.error(f"❌ Failed to update promocode statuses: {e}")
            return {
                "success": False,
                "error": str(e),
                "activated_count": activated_count,
                "deactivated_count": deactivated_count
            }
    
    async def list(self, status: str = "active") -> List[Promocode]:
        """Get promocodes by status: 'active', 'expired', or 'all'"""
        current_time = datetime.now(timezone.utc)
        
        if status == "expired":
            # Get expired promocodes
            result = await self.db.execute(
                select(Promocode).where(
                    and_(
                        Promocode.valid_until != None,
                        Promocode.valid_until <= current_time
                    )
                )
            )
        elif status == "all":
            # Get all promocodes
            result = await self.db.execute(select(Promocode))
        else:
            # Get active promocodes (default)
            result = await self.db.execute(
                select(Promocode).where(
                    and_(
                        Promocode.is_active == True,
                        or_(
                            Promocode.valid_from == None,
                            Promocode.valid_from <= current_time
                        ),
                        or_(
                            Promocode.valid_until == None,
                            Promocode.valid_until > current_time
                        ),
                        or_(
                            Promocode.usage_limit == None,
                            Promocode.used_count < Promocode.usage_limit
                        )
                    )
                )
            )
        
        return result.scalars().all()


# Standalone function for background task
async def update_promocode_statuses_task():
    """Background task function to update promocode statuses"""
    async for db in get_db():
        try:
            scheduler = PromoCodeScheduler(db)
            result = await scheduler.update_promocode_statuses()
            logger.info(f"Promocode status update completed: {result}")
            return result
        except Exception as e:
            logger.error(f"Error updating promocode statuses: {e}")
            raise
        finally:
            await db.close()
