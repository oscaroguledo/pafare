from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, desc, update, delete, column, String, cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import selectinload
from typing import Optional, List, Dict, Any
from uuid import UUID
from core.utils.uuid_utils import uuid7
from services.catalog.inventory import InventoryService
from models.catalog.product import Product, ProductVariant, ProductStatus, ProductImage
from models.catalog.category import Category
from services.catalog.category import category_ids_matching
from models.catalog.inventories import Inventory, WarehouseLocation
from models.catalog.review import Review
from models.commerce.cart import CartItem
from models.commerce.orders import OrderItem
from models.commerce.subscriptions import SubscriptionProductAssociation
from schemas.catalog.product import Create as ProductCreate, Update as ProductUpdate, Response as ProductResponse, VariantCreate as ProductVariantCreate, VariantResponse as ProductVariantResponse, PriceRange
from schemas.catalog.category import CategoryBrief
from core.logging import get_structured_logger
from core.utils.cache import product_read_cache, home_page_cache, invalidate_variant, invalidate_product
from core import redis_cache
from fastapi import BackgroundTasks, HTTPException
from datetime import datetime, date
from schemas.catalog.product import Create as ProductCreate, Update as ProductUpdate, Response as ProductResponse, VariantCreate as ProductVariantCreate, VariantUpdate as ProductVariantUpdate, VariantResponse as ProductVariantResponse, PriceRange
from core.exceptions import APIException
from datetime import datetime, timezone, date

logger = get_structured_logger(__name__)

HOME_CACHE_KEY = "products:home"
# The worker rewrites this daily at 20:00; the TTL only matters if it stops running.
HOME_CACHE_TTL = 25 * 3600



def hashtag_matches(term: str):
    """Condition: one of the product's hashtags contains term (ignoring case). The LIKE over the whole list
    lets Postgres use idx_products_hashtags_trgm; the per-hashtag check keeps "tag"/"language" keys from matching."""
    tag = func.jsonb_array_elements(Product.hashtags).table_valued(column("value", JSONB)).alias("h")
    return and_(
        func.lower(cast(Product.hashtags, String)).like(func.lower(term)),
        select(1).select_from(tag).where(tag.c.value["tag"].astext.ilike(term)).exists(),
    )


def matched_hashtag(product: Product, query: Optional[str]) -> Optional[str]:
    """The hashtag a search for query found this product by, when the customer searched in another language
    than English. None when the name or brand matched, or when an English hashtag matched (same language)."""
    needle = (query or "").strip().casefold()
    if not needle or needle in (product.name or "").casefold() or needle in (product.brand or "").casefold():
        return None
    matches = [h for h in (product.hashtags or []) if needle in h.get("tag", "").casefold()]
    if not matches or any(h.get("language", "").split("-")[0] == "en" for h in matches):
        return None
    exact = next((h for h in matches if h["tag"].casefold() == needle), None)
    return (exact or matches[0])["tag"]


def sale_price_or_none(base_price, sale_price):
    """A sale only counts when it's below the base price; zero or a "sale" at/above base means no sale."""
    if sale_price is None or sale_price <= 0 or sale_price >= base_price:
        return None
    return sale_price

class ProductService:
    def __init__(self, db: AsyncSession):
        self.db = db


    def _convert_variant_to_response(self, variant: ProductVariant) -> ProductVariantResponse:
        """Convert ProductVariant model to response format."""
        try:
            # Use the model's built-in to_dict method
            variant_dict = variant.to_dict(include_images=True)
            return ProductVariantResponse.model_validate(variant_dict)
        except Exception as e:
            logger.error(f"Error converting variant {variant.id}: {e}")
            # Return minimal variant data
            return ProductVariantResponse(
                id=variant.id,
                product_id=variant.product_id,
                sku=getattr(variant, 'sku', ''),
                name=getattr(variant, 'name', ''),
                base_price=getattr(variant, 'base_price', 0.0),
                sale_price=getattr(variant, 'sale_price', None),
                current_price=getattr(variant, 'sale_price', None) or getattr(
                    variant, 'base_price', 0.0),
                discount_percentage=0,
                stock=getattr(variant.inventory, 'quantity_available', 0) if hasattr(variant, 'inventory') and variant.inventory else 0,
                attributes=getattr(variant, 'attributes', {}),
                is_active=getattr(variant, 'is_active', True),
                images=[],
                primary_image=None,
                created_at=variant.created_at.isoformat() if isinstance(variant.created_at, (datetime, date)) else (variant.created_at or ""),
                updated_at=variant.updated_at.isoformat() if isinstance(variant.updated_at, (datetime, date)) else variant.updated_at
            )

    def _convert_product_to_response(self, product: Product) -> ProductResponse:
        """Convert Product model to response format."""
        try:
            # Convert variants using model's to_dict method with proper validation
            variants = []
            for variant in (product.variants or []):
                try:
                    variant_dict = variant.to_dict(include_images=True)
                    
                    # Fix datetime fields (only call isoformat on datetime objects)
                    if 'created_at' in variant_dict and variant_dict['created_at']:
                        if isinstance(variant_dict['created_at'], (datetime, date)):
                            variant_dict['created_at'] = variant_dict['created_at'].isoformat()
                    if 'updated_at' in variant_dict and variant_dict['updated_at']:
                        if isinstance(variant_dict['updated_at'], (datetime, date)):
                            variant_dict['updated_at'] = variant_dict['updated_at'].isoformat()
                    
                    # Fix dietary_tags - convert string to list
                    if 'dietary_tags' in variant_dict and isinstance(variant_dict['dietary_tags'], str):
                        variant_dict['dietary_tags'] = [tag.strip() for tag in variant_dict['dietary_tags'].split(',') if tag.strip()]
                    
                    # Fix tags - convert string to list  
                    if 'tags' in variant_dict and isinstance(variant_dict['tags'], str):
                        variant_dict['tags'] = [tag.strip() for tag in variant_dict['tags'].split(',') if tag.strip()]
                    
                    # Fix images datetime fields
                    if 'images' in variant_dict:
                        for img in variant_dict['images']:
                            if 'created_at' in img and img['created_at']:
                                if isinstance(img['created_at'], (datetime, date)):
                                    img['created_at'] = img['created_at'].isoformat()
                            if 'updated_at' in img and img['updated_at']:
                                if isinstance(img['updated_at'], (datetime, date)):
                                    img['updated_at'] = img['updated_at'].isoformat()
                    
                    # Add stock from inventory
                    if hasattr(variant, 'inventory') and variant.inventory:
                        variant_dict['stock'] = variant.inventory.quantity_available
                    else:
                        variant_dict['stock'] = 0
                    
                    variants.append(ProductVariantResponse.model_validate(variant_dict))
                except Exception as e:
                    logger.error(f"Error converting variant {variant.id}: {e}")
                    continue
            
            # Get primary variant
            primary_variant = None
            if product.variants:
                primary_variant = min(product.variants, key=lambda v: v.current_price)
                try:
                    primary_variant_dict = primary_variant.to_dict(include_images=True)
                    
                    # Fix datetime fields (only call isoformat on datetime objects)
                    if 'created_at' in primary_variant_dict and primary_variant_dict['created_at']:
                        if isinstance(primary_variant_dict['created_at'], (datetime, date)):
                            primary_variant_dict['created_at'] = primary_variant_dict['created_at'].isoformat()
                    if 'updated_at' in primary_variant_dict and primary_variant_dict['updated_at']:
                        if isinstance(primary_variant_dict['updated_at'], (datetime, date)):
                            primary_variant_dict['updated_at'] = primary_variant_dict['updated_at'].isoformat()
                    
                    # Fix dietary_tags and tags
                    if 'dietary_tags' in primary_variant_dict and isinstance(primary_variant_dict['dietary_tags'], str):
                        primary_variant_dict['dietary_tags'] = [tag.strip() for tag in primary_variant_dict['dietary_tags'].split(',') if tag.strip()]
                    if 'tags' in primary_variant_dict and isinstance(primary_variant_dict['tags'], str):
                        primary_variant_dict['tags'] = [tag.strip() for tag in primary_variant_dict['tags'].split(',') if tag.strip()]
                    
                    # Fix images datetime fields
                    if 'images' in primary_variant_dict:
                        for img in primary_variant_dict['images']:
                            if 'created_at' in img and img['created_at']:
                                if isinstance(img['created_at'], (datetime, date)):
                                    img['created_at'] = img['created_at'].isoformat()
                            if 'updated_at' in img and img['updated_at']:
                                if isinstance(img['updated_at'], (datetime, date)):
                                    img['updated_at'] = img['updated_at'].isoformat()
                    
                    # Add stock from inventory
                    if hasattr(primary_variant, 'inventory') and primary_variant.inventory:
                        primary_variant_dict['stock'] = primary_variant.inventory.quantity_available
                    else:
                        primary_variant_dict['stock'] = 0
                    
                    primary_variant = ProductVariantResponse.model_validate(primary_variant_dict)
                except Exception as e:
                    logger.error(f"Error converting primary variant: {e}")
                    primary_variant = None
            
            return ProductResponse(
                id=product.id,
                name=product.name,
                brand=product.brand,
                hashtags=product.hashtags or [],
                description=product.description,
                short_description=product.short_description,
                is_featured=product.is_featured,
                is_bestseller=product.is_bestseller,
                rating=product.rating_average,
                review_count=product.review_count,
                origin=getattr(product, 'origin', ''),
                is_active=product.is_active,
                product_status=product.product_status,
                availability_status=product.availability_status,
                price_range=product.price_range,
                in_stock=product.in_stock,
                created_at=product.created_at.isoformat() if isinstance(product.created_at, (datetime, date)) else (product.created_at or ""),
                updated_at=product.updated_at.isoformat() if isinstance(product.updated_at, (datetime, date)) else product.updated_at,
                category_id=product.category_id,
                category=CategoryBrief.model_validate(product.category) if product.category else None,
                variants=variants,
                primary_variant=primary_variant
            )
        except Exception as e:
            logger.error(f"Error converting product {product.id}: {e}")
            # Return minimal product data
            return ProductResponse(
                id=product.id,
                name=getattr(product, 'name', ''),
                brand=getattr(product, 'brand', None),
                hashtags=getattr(product, 'hashtags', None) or [],
                description=getattr(product, 'description', ''),
                short_description=getattr(product, 'short_description', None),
                is_featured=getattr(product, 'is_featured', False),
                rating=getattr(product, 'rating_average', 0.0),
                review_count=getattr(product, 'review_count', 0),
                origin=getattr(product, 'origin', ''),
                is_active=getattr(product, 'is_active', True),
                product_status=getattr(product, 'product_status', ProductStatus.ACTIVE),
                availability_status="out_of_stock",
                price_range=PriceRange(min=0, max=0),
                in_stock=False,
                created_at=product.created_at.isoformat() if isinstance(product.created_at, (datetime, date)) else (product.created_at or ""),
                updated_at=product.updated_at.isoformat() if isinstance(product.updated_at, (datetime, date)) else product.updated_at,
                category_id=getattr(product, 'category_id', None),
                variants=[],
                primary_variant=None
            )

    async def list(
        self,
        page: int = 1,
        limit: int = 1000,
        filters: Optional[Dict[str, Any]] = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        status: Optional[ProductStatus] = ProductStatus.ACTIVE
    ) -> Dict[str, Any]:
        """Get products with filtering and pagination; status=None lists every status (admin)."""
        offset = (page - 1) * limit

        # Build filter conditions
        base_conditions = [Product.product_status == status] if status else []
        
        if filters:
            if filters.get("q"):
                search_term = f"%{filters['q']}%"
                base_conditions.append(
                    or_(
                        Product.name.ilike(search_term),
                        Product.brand.ilike(search_term),
                        Product.description.ilike(search_term),
                        hashtag_matches(search_term),
                    )
                )
            
            if filters.get("min_rating") is not None:
                base_conditions.append(Product.rating_average >= filters["min_rating"])
            
            if filters.get("max_rating") is not None:
                base_conditions.append(Product.rating_average <= filters["max_rating"])

            if filters.get("is_featured") is not None:
                base_conditions.append(Product.is_featured.is_(filters["is_featured"]))

            if filters.get("is_bestseller") is not None:
                base_conditions.append(Product.is_bestseller.is_(filters["is_bestseller"]))
        
            # Filter by category, given as its slug (website) or id (app)
            if filters.get("category"):
                base_conditions.append(Product.category_id.in_(category_ids_matching(filters["category"])))
        
            # Build subquery for filtering by variant properties
            price_filters = []
            if filters.get("min_price") is not None:
                price_filters.append(ProductVariant.base_price >= filters["min_price"])
            
            if filters.get("max_price") is not None:
                price_filters.append(ProductVariant.base_price <= filters["max_price"])
            
            if filters.get("availability") is not None:
                if filters["availability"]:
                    # Join with inventory and check quantity_available > 0
                    price_filters.append(
                        ProductVariant.inventory.has(
                            Inventory.quantity_available > 0
                        )
                    )
                else:
                    # Join with inventory and check quantity_available == 0 or no inventory
                    price_filters.append(
                        or_(
                            ~ProductVariant.inventory.has(),
                            ProductVariant.inventory.has(
                                Inventory.quantity_available == 0
                            )
                        )
                    )
            
            if filters.get("sale"):
                # Product is on sale if any variant has a discount (discount_percentage > 0)
                price_filters.append(
                    and_(
                        ProductVariant.sale_price.isnot(None),
                        ProductVariant.sale_price < ProductVariant.base_price
                    )
                )
            
            if price_filters:
                # Use EXISTS with correlated subquery for better performance
                variant_subquery = (
                    select(1)
                    .where(
                        and_(
                            ProductVariant.product_id == Product.id,
                            *price_filters
                        )
                    )
                    .exists()
                )
                base_conditions.append(variant_subquery)
        
        # Build the main query with simpler eager loading
        query = (
            select(Product)
            .where(*base_conditions)
            .options(
                selectinload(Product.variants)
            )
        )

        # Apply sorting; "price" orders by each product's cheapest current variant price, "popular" by units sold
        if sort_by == "popular":
            units_sold = (
                select(func.coalesce(func.sum(ProductVariant.purchase_count), 0))
                .where(ProductVariant.product_id == Product.id)
                .correlate(Product)
                .scalar_subquery()
            )
            query = query.order_by(units_sold.desc(), Product.rating_average.desc(), Product.created_at.desc())
        elif sort_by == "price":
            min_price = (
                select(func.min(func.coalesce(ProductVariant.sale_price, ProductVariant.base_price)))
                .where(ProductVariant.product_id == Product.id)
                .correlate(Product)
                .scalar_subquery()
            )
            query = query.order_by(min_price.desc() if sort_order.lower() == "desc" else min_price.asc())
        elif hasattr(Product, sort_by):
            if sort_order.lower() == "desc":
                query = query.order_by(getattr(Product, sort_by).desc())
            else:
                query = query.order_by(getattr(Product, sort_by).asc())

        # Get total count for pagination - must match the main query filters
        count_query = select(func.count(Product.id))
        for condition in base_conditions:
            count_query = count_query.where(condition)

        count_result = await self.db.execute(count_query)
        total = count_result.scalar()

        # Apply pagination
        query = query.offset(offset).limit(limit)

        result = await self.db.execute(query)
        products = result.scalars().all()

        # Convert to response format
        products_data = []
        for product in products:
            try:
                product_response = self._convert_product_to_response(product)
                product_response.matched_hashtag = matched_hashtag(product, (filters or {}).get("q"))
                products_data.append(product_response)
            except Exception as e:
                logger.error(f"Error converting product {product.id}: {e}")
                continue

        return {
            "data": products_data,
            "total": total,
            "page": page,
            "per_page": limit,
            "total_pages": (total + limit - 1) // limit
        }


    async def home(self) -> Dict[str, List[dict]]:
        """Home page sections as JSON-ready dicts; a section is empty when nothing qualifies (no filler products)."""
        sections = {
            "featured": await self.list(limit=4, filters={"is_featured": True}),
            "popular": await self.list(limit=8, sort_by="popular"),
            "deals": await self.list(limit=4, filters={"sale": True}),
        }
        return {name: [p.model_dump(mode="json") for p in result["data"]] for name, result in sections.items()}

    async def refresh_home_cache(self) -> Dict[str, List[dict]]:
        """Rebuild the home page sections from the database and store them in Redis (and this process's copy)."""
        data = await self.home()
        await redis_cache.set_json(HOME_CACHE_KEY, data, ttl_seconds=HOME_CACHE_TTL)
        home_page_cache[HOME_CACHE_KEY] = data
        return data

    async def home_cached(self, background_tasks: BackgroundTasks) -> Dict[str, List[dict]]:
        """Home page sections from this process's 5-minute copy, else Redis (refreshed by the worker),
        else built from the database. On a Redis miss, Redis is refilled after the response is sent."""
        data = home_page_cache.get(HOME_CACHE_KEY)
        if data is not None:
            return data
        data = await redis_cache.get_json(HOME_CACHE_KEY)
        if data is None:
            data = await self.home()
            background_tasks.add_task(redis_cache.set_json, HOME_CACHE_KEY, data, HOME_CACHE_TTL)
        home_page_cache[HOME_CACHE_KEY] = data
        return data

    async def featured(self, limit: int = 4) -> List[ProductResponse]:
        """Active featured products, newest first."""
        return (await self.list(limit=limit, filters={"is_featured": True}))["data"]

    async def recommended(self, product_id: UUID, limit: int = 4) -> List[ProductResponse]:
        """Get smart recommendations (complementary, similar, behavioral); see RecommendationService."""
        # Local: recommendations.py imports ProductService, so this would be circular at top level.
        from services.catalog.recommendations import RecommendationService
        
        recommendation_service = RecommendationService(self.db)
        return await recommendation_service.get_smart_recommendations(product_id, limit)


    async def get(self, product_id: UUID) -> Optional[ProductResponse]:
        """Get product by ID. Cached for 5s (display only - cart/checkout never read this)."""
        cache_key = ("product", product_id)
        if cache_key in product_read_cache:
            return product_read_cache[cache_key]

        query = select(Product).options(
            selectinload(Product.variants).selectinload(ProductVariant.images),
            selectinload(Product.variants).selectinload(ProductVariant.inventory)
        ).where(Product.id == product_id)

        result = await self.db.execute(query)
        product = result.scalar_one_or_none()

        response = self._convert_product_to_response(product) if product else None
        if response is not None:
            product_read_cache[cache_key] = response
        return response


    async def get_variant(self, variant_id: UUID) -> Optional[ProductVariantResponse]:
        """Get a variant by ID. Cached for 5s (display only - cart/checkout never read this)."""
        cache_key = ("variant", variant_id)
        if cache_key in product_read_cache:
            return product_read_cache[cache_key]

        query = select(ProductVariant).options(
            selectinload(ProductVariant.images),
            selectinload(ProductVariant.inventory)
        ).where(ProductVariant.id == variant_id).execution_options(populate_existing=True)  # never a stale in-session copy
        result = await self.db.execute(query)
        variant = result.scalar_one_or_none()

        response = self._convert_variant_to_response(variant) if variant else None
        if response is not None:
            product_read_cache[cache_key] = response
        return response

    async def list_variants(self, product_id: UUID) -> List[ProductVariantResponse]:
        """List all variants for a product. Cached for 5s (display only - cart/checkout never read this)."""
        cache_key = ("variants", product_id)
        if cache_key in product_read_cache:
            return product_read_cache[cache_key]

        query = select(ProductVariant).options(
            selectinload(ProductVariant.images),
            selectinload(ProductVariant.inventory)
        ).where(ProductVariant.product_id == product_id)
        result = await self.db.execute(query)
        variants = result.scalars().all()

        response = [self._convert_variant_to_response(v) for v in variants]
        product_read_cache[cache_key] = response
        return response

    async def create_variant(self, product_id: UUID, variant_data: ProductVariantCreate) -> ProductVariantResponse:
        """Create a new variant for a product"""
        # Check if product exists
        product_result = await self.db.execute(select(Product).where(Product.id == product_id))
        product = product_result.scalar_one_or_none()
        if not product:
            raise APIException(status_code=404, message="Product not found")
        
        # Generate SKU if not provided
        sku = variant_data.sku or f"SKU-{product_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # Create variant - stock isn't a column on ProductVariant, it's derived from the
        # related Inventory row's quantity_available, so it's set up separately below.
        variant = ProductVariant(
            id=uuid7(),
            product_id=product_id,
            sku=sku,
            name=variant_data.name,
            base_price=variant_data.base_price,
            sale_price=sale_price_or_none(variant_data.base_price, variant_data.sale_price),
            attributes=variant_data.attributes or {},
            specifications=variant_data.specifications,
            dietary_tags=variant_data.dietary_tags or [],
            tags=variant_data.tags,
            availability_status=variant_data.availability_status
        )

        self.db.add(variant)
        await self.db.flush()

        self.db.add(Inventory(id=uuid7(), variant_id=variant.id, quantity_available=variant_data.stock))
        await self.db.commit()
        await self.db.refresh(variant)

        # Add images if provided
        if variant_data.image_urls:
            for idx, url in enumerate(variant_data.image_urls):
                image = ProductImage(
                    id=uuid7(),
                    variant_id=variant.id,
                    url=url,
                    is_primary=(idx == 0),
                    sort_order=idx
                )
                self.db.add(image)
            await self.db.commit()
            await self.db.refresh(variant)

        invalidate_variant(variant.id, product_id)
        return await self.get_variant(variant.id)

    async def update_variant(self, variant_id: UUID, update_data: ProductVariantUpdate) -> ProductVariantResponse:
        """Update a variant's fields and stock; images have their own endpoints."""
        result = await self.db.execute(select(ProductVariant).where(ProductVariant.id == variant_id))
        variant = result.scalar_one_or_none()
        if not variant:
            raise APIException(status_code=404, message="Variant not found")

        # Stock lives on the related Inventory row; sale_price may be cleared (None ends a sale).
        data = update_data.model_dump(exclude_unset=True, exclude={"id", "stock"})
        if "sale_price" in data:
            data["sale_price"] = sale_price_or_none(data.get("base_price", variant.base_price), data["sale_price"])
        for field, value in data.items():
            if hasattr(variant, field) and (value is not None or field == "sale_price"):
                setattr(variant, field, value)

        if update_data.stock is not None:
            # Stock changes go through the adjustment history, like any other count.
            has_inventory = await self.db.scalar(select(Inventory.id).where(Inventory.variant_id == variant.id))
            if has_inventory:
                await InventoryService(self.db).set_stock(variant.id, update_data.stock, commit=False)
            else:
                self.db.add(Inventory(id=uuid7(), variant_id=variant.id, quantity_available=update_data.stock))

        product_id = variant.product_id
        await self.db.commit()
        invalidate_variant(variant_id, product_id)
        return await self.get_variant(variant_id)

    async def delete_variant(self, variant_id: UUID) -> Optional[str]:
        """Remove a variant: "deleted", or "archived" (made inactive) when orders or subscriptions reference it.
        Returns None when it doesn't exist; a product's last variant can't be removed."""
        variant = (await self.db.execute(select(ProductVariant).where(ProductVariant.id == variant_id))).scalar_one_or_none()
        if not variant:
            return None
        siblings = (await self.db.execute(
            select(func.count()).select_from(ProductVariant).where(ProductVariant.product_id == variant.product_id)
        )).scalar()
        if siblings <= 1:
            raise APIException(status_code=400, message="A product needs at least one variant. Delete the product instead.")

        await self.db.execute(delete(CartItem).where(CartItem.variant_id == variant_id))
        in_orders = (await self.db.execute(select(OrderItem.id).where(OrderItem.variant_id == variant_id).limit(1))).first()
        in_subscriptions = (await self.db.execute(
            select(SubscriptionProductAssociation.product_variant_id)
            .where(SubscriptionProductAssociation.product_variant_id == variant_id).limit(1)
        )).first()
        if in_orders or in_subscriptions:
            variant.is_active = False  # keep the history intact; it just can't be bought any more
            outcome = "archived"
        else:
            await self.db.delete(variant)
            outcome = "deleted"
        await self.db.commit()
        invalidate_variant(variant_id, variant.product_id)
        return outcome


    async def create(self, product_data: ProductCreate, created_by: UUID) -> ProductResponse:
        """Create a new product."""
        # Build metadata including origin info
        product_metadata = {}
        origin_value = getattr(product_data, 'origin', None) or getattr(product_data, 'origin_country', None)
        if origin_value:
            product_metadata['origin'] = origin_value
            product_metadata['origin_country'] = origin_value

        # Create product
        db_product = Product(
            id=uuid7(),
            name=product_data.name,
            hashtags=[h.model_dump() for h in product_data.hashtags],
            description=product_data.description,
            short_description=product_data.short_description,
            brand=product_data.brand,
            category_id=product_data.category_id,
            product_metadata=product_metadata if product_metadata else None,
            is_featured=product_data.is_featured,
            is_bestseller=product_data.is_bestseller
        )

        self.db.add(db_product)
        await self.db.flush()  # Get the product ID

        # Build variants list - support flat product data (auto-create default variant)
        variants_to_create = product_data.variants or []
        if not variants_to_create and product_data.base_price is not None:
            variants_to_create = [ProductVariantCreate(
                name=product_data.name,
                base_price=product_data.base_price,
                sale_price=product_data.sale_price,
                stock=product_data.quantity or 0,
                sku=product_data.sku,
            )]

        # Create variants
        for v_idx, variant_data in enumerate(variants_to_create):
            # uuid7's leading bits are a shared timestamp, not random, so use the
            # trailing hex, which actually distinguishes IDs created close together.
            product_prefix = db_product.name[:3].upper().replace(' ', '')
            auto_sku = f"{product_prefix}-{str(db_product.id).replace('-', '')[-8:]}-{v_idx}"
            final_sku = variant_data.sku if variant_data.sku else auto_sku
            
            # Create variant first to get the ID
            db_variant = ProductVariant(
                id=uuid7(),
                product_id=db_product.id,
                sku=final_sku,
                name=variant_data.name,
                base_price=variant_data.base_price,
                sale_price=sale_price_or_none(variant_data.base_price, variant_data.sale_price),
                attributes=variant_data.attributes or {},
                specifications=variant_data.specifications,
                dietary_tags=variant_data.dietary_tags or [],
                tags=variant_data.tags,
                availability_status=variant_data.availability_status or "available",
                view_count=0,
                purchase_count=0
            )
            self.db.add(db_variant)
            await self.db.flush()  # Get variant ID
            
            # ALWAYS create inventory record for the variant (even if stock is 0)
            # Get warehouse location from variant data if provided, otherwise use default
            warehouse_location_id = None
            if hasattr(variant_data, 'warehouse_location_id') and variant_data.warehouse_location_id:
                # Use the warehouse location provided in variant data
                warehouse_location_id = variant_data.warehouse_location_id
            else:
                # Try to find an existing warehouse location
                default_location_result = await self.db.execute(
                    select(WarehouseLocation).where(WarehouseLocation.name == "Main Warehouse")
                )
                default_location = default_location_result.scalar_one_or_none()
                
                if not default_location:
                    # Try "Default" as fallback
                    default_location_result = await self.db.execute(
                        select(WarehouseLocation).where(WarehouseLocation.name == "Default")
                    )
                    default_location = default_location_result.scalar_one_or_none()
                
                if default_location:
                    warehouse_location_id = default_location.id
            
            # Get stock quantity from variant_data, default to 0 if not provided
            stock_quantity = getattr(variant_data, 'stock', 0) if hasattr(variant_data, 'stock') else 0
            
            # Create inventory record - ALWAYS, even if stock is 0
            inventory = Inventory(
                id=uuid7(),
                variant_id=db_variant.id,
                location_id=warehouse_location_id,
                quantity_available=stock_quantity,
                low_stock_threshold=10,
                reorder_point=5,
                inventory_status="active"
            )
            self.db.add(inventory)
            
            # Create variant images from CDN URLs
            if variant_data.image_urls:
                for img_idx, image_url in enumerate(variant_data.image_urls):
                    db_image = ProductImage(
                        id=uuid7(),
                        variant_id=db_variant.id,
                        url=image_url,
                        is_primary=(img_idx == 0),  # First image is primary
                        sort_order=img_idx
                    )
                    self.db.add(db_image)

        await self.db.commit()

        # Return the created product
        return await self.get(db_product.id)

    async def update(
        self,
        product_id: UUID,
        product_data: ProductUpdate,
        user_id: UUID,
        is_admin: bool = False
    ) -> ProductResponse:
        """Update a product's own fields; variants and images have their own endpoints."""
        logger.info(f"Updating product {product_id} with data: {product_data.model_dump(exclude_unset=True)}")
        
        query = select(Product).options(
            selectinload(Product.variants).selectinload(ProductVariant.images),
            selectinload(Product.variants).selectinload(ProductVariant.inventory)
        ).where(Product.id == product_id)
        result = await self.db.execute(query)
        product = result.scalar_one_or_none()

        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        # Check if user is admin (only admins can update products)
        if not is_admin:
            raise HTTPException(
                status_code=403, detail="Not authorized to update this product")

        # Update product fields - exclude_unset so omitted fields keep their current value
        # instead of being overwritten with the schema's None defaults.
        update_dict = product_data.model_dump(exclude_unset=True)
        if update_dict.get("hashtags") is None:
            update_dict.pop("hashtags", None)  # null means "leave them"; [] clears them
        for field, value in update_dict.items():
            setattr(product, field, value)

        logger.info(f"Updated product fields: {update_dict}")


        await self.db.commit()
        logger.info(f"Product {product_id} updated successfully")

        invalidate_product(product_id)

        # product.variants was eager-loaded by the initial SELECT and won't reflect a
        # row added afterwards via raw db.add() - expire so self.get() below re-fetches.
        self.db.expire_all()
        return await self.get(product_id)


    async def moderate(self, product_id: UUID, action: str, notes: Optional[str] = None) -> ProductResponse:
        """Approve or reject a product, publishing/unpublishing it accordingly."""
        result = await self.db.execute(select(Product).where(Product.id == product_id))
        product = result.scalar_one_or_none()
        if not product:
            raise APIException(status_code=404, message="Product not found")

        if action == "approved":
            product.product_status = ProductStatus.ACTIVE
            if not product.published_at:
                product.published_at = datetime.now(timezone.utc)
        elif action == "rejected":
            product.product_status = ProductStatus.INACTIVE
        else:
            raise APIException(status_code=400, message=f"Unknown moderation action: {action}")

        if notes:
            product.product_metadata = {**(product.product_metadata or {}), "moderation_notes": notes}

        await self.db.commit()
        invalidate_product(product_id)
        return await self.get(product_id)

    async def set_featured(self, product_id: UUID, featured: bool) -> ProductResponse:
        """Toggle a product's featured flag."""
        result = await self.db.execute(select(Product).where(Product.id == product_id))
        product = result.scalar_one_or_none()
        if not product:
            raise APIException(status_code=404, message="Product not found")

        product.is_featured = featured
        await self.db.commit()
        invalidate_product(product_id)
        return await self.get(product_id)

    async def delete(self, product_id: UUID, user_id: UUID, is_admin: bool = False):
        """Delete a product and all its associated data (variants, inventory, reviews, cart item)."""
        query = select(Product).where(Product.id == product_id)
        result = await self.db.execute(query)
        product = result.scalar_one_or_none()

        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        # Check if user is admin (only admins can delete products)
        if not is_admin:
            raise HTTPException(
                status_code=403, detail="Not authorized to delete this product")

        # Check if product has any order items (prevent deletion of ordered products)
        variant_ids_result = await self.db.execute(
            select(ProductVariant.id).where(ProductVariant.product_id == product_id)
        )
        variant_ids = [row[0] for row in variant_ids_result.fetchall()]
        if variant_ids:
            order_items = (await self.db.execute(
                select(OrderItem).where(OrderItem.variant_id.in_(variant_ids))
            )).scalars().first()
            
            if order_items:
                raise HTTPException(
                    status_code=400, 
                    detail="Cannot delete product that has been ordered. Product has order history."
                )

        # Delete all reviews for this product
        reviews = (await self.db.execute(
            select(Review).where(Review.product_id == product_id)
        )).scalars().all()
        for review in reviews:
            await self.db.delete(review)

        # Delete all cart items for this product
        cart_items = (await self.db.execute(
            select(CartItem).where(CartItem.product_id == product_id)
        )).scalars().all()
        for cart_item in cart_items:
            await self.db.delete(cart_item)

        # Delete the product (this will cascade delete variants and inventory due to cascade="all, delete-orphan")
        await self.db.delete(product)
        await self.db.commit()
        invalidate_product(product_id)
        for variant_id in variant_ids:
            invalidate_variant(variant_id)
    async def create_image(self, variant_id: UUID, url: str, alt_text: Optional[str] = None,
                          is_primary: bool = False, sort_order: int = 0) -> dict:
        """Create a new image for a variant"""
        # Check if variant exists
        variant_result = await self.db.execute(
            select(ProductVariant).where(ProductVariant.id == variant_id)
        )
        if not variant_result.scalar_one_or_none():
            raise APIException(status_code=404, message="Variant not found")
        
        # If this is primary, unset other primary images
        if is_primary:
            await self.db.execute(
                update(ProductImage)
                .where(ProductImage.variant_id == variant_id)
                .values(is_primary=False)
            )
        
        image = ProductImage(
            id=uuid7(),
            variant_id=variant_id,
            url=url,
            alt_text=alt_text,
            is_primary=is_primary,
            sort_order=sort_order
        )
        
        self.db.add(image)
        await self.db.commit()
        await self.db.refresh(image)
        return image.to_dict()
    async def get_image(self, image_id: UUID) -> Optional[dict]:
        """Get an image by ID"""
        result = await self.db.execute(
            select(ProductImage).where(ProductImage.id == image_id)
        )
        image = result.scalar_one_or_none()
        return image.to_dict() if image else None
    async def list_images(self, variant_id: UUID) -> List[dict]:
        """List all images for a variant"""
        result = await self.db.execute(
            select(ProductImage)
            .where(ProductImage.variant_id == variant_id)
            .order_by(ProductImage.sort_order)
        )
        images = result.scalars().all()
        return [img.to_dict() for img in images]
    async def update_image(self, image_id: UUID, url: Optional[str] = None,
                          alt_text: Optional[str] = None, is_primary: Optional[bool] = None,
                          sort_order: Optional[int] = None) -> Optional[dict]:
        """Update an image"""
        result = await self.db.execute(
            select(ProductImage).where(ProductImage.id == image_id)
        )
        image = result.scalar_one_or_none()
        if not image:
            return None
        
        # If setting as primary, unset other primary images for this variant
        if is_primary and not image.is_primary:
            await self.db.execute(
                update(ProductImage)
                .where(ProductImage.variant_id == image.variant_id)
                .values(is_primary=False)
            )
        
        if url is not None:
            image.url = url
        if alt_text is not None:
            image.alt_text = alt_text
        if is_primary is not None:
            image.is_primary = is_primary
        if sort_order is not None:
            image.sort_order = sort_order
        
        await self.db.commit()
        await self.db.refresh(image)
        return image.to_dict()
    async def delete_image(self, image_id: UUID) -> bool:
        """Delete an image"""
        result = await self.db.execute(
            select(ProductImage).where(ProductImage.id == image_id)
        )
        image = result.scalar_one_or_none()
        if not image:
            return False

        await self.db.delete(image)
        if image.is_primary:
            # Keep a main image: promote the next one in order.
            successor = (await self.db.execute(
                select(ProductImage).where(ProductImage.variant_id == image.variant_id, ProductImage.id != image.id)
                .order_by(ProductImage.sort_order, ProductImage.created_at).limit(1)
            )).scalar_one_or_none()
            if successor:
                successor.is_primary = True
        await self.db.commit()
        return True


    # --- Variant image CRUD ---


