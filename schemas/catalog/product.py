from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal, Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID

from models.catalog.product import ProductStatus, AvailabilityStatus
from schemas.catalog.category import CategoryBrief

def normalize_dietary_tags(value):
    """Normalize dietary_tags to always be a dict, handling both list and dict inputs."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        # Convert list to dict with True values
        return {tag: True for tag in value}
    return {}


class ImageResponse(BaseModel):
    id: UUID
    variant_id: UUID
    url: str
    alt_text: Optional[str]
    is_primary: bool
    sort_order: int
    format: Optional[str]
    created_at: str

    model_config = ConfigDict(from_attributes=True, json_encoders={
        datetime: lambda v: v.isoformat() if v else None
    })


class VariantCreate(BaseModel):
    sku: Optional[str] = None  # Optional - will be auto-generated if not provided
    name: str
    base_price: float
    sale_price: Optional[float] = None
    stock: int = 0
    attributes: Optional[Dict[str, Any]] = {}
    specifications: Optional[Dict[str, Any]] = None
    dietary_tags: Optional[Dict[str, Any]] = {}
    tags: Optional[str] = None
    availability_status: str = "available"
    image_urls: Optional[List[str]] = []  # jsDelivr CDN URLs

    @field_validator('dietary_tags', mode='before')
    @classmethod
    def validate_dietary_tags(cls, v):
        return normalize_dietary_tags(v)


HASHTAG_LANGUAGE = r'^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$'  # ISO 639 code, optionally with a script or region: ha, fil, zh-Hant


class Hashtag(BaseModel):
    """A name the product is known by, and the language it's in: {"tag": "yaji", "language": "ha"}."""
    tag: str = Field(..., min_length=1, max_length=100)
    language: str = Field(..., pattern=HASHTAG_LANGUAGE)

    @field_validator('tag', mode='before')
    @classmethod
    def clean_tag(cls, v):
        # "#Yaji " -> "Yaji": the # is how people write it, not part of the name.
        return ' '.join(v.strip().lstrip('#').split()) if isinstance(v, str) else v


def unique_hashtags(tags: Optional[List[Hashtag]]) -> Optional[List[Hashtag]]:
    """Drop repeats of the same name (ignoring case), keeping the first."""
    if tags is None:
        return None
    seen = set()
    return [t for t in tags if not (t.tag.casefold() in seen or seen.add(t.tag.casefold()))]


class Create(BaseModel):
    name: str
    hashtags: List[Hashtag] = Field(default_factory=list, max_length=50)
    description: Optional[str] = None
    short_description: Optional[str] = None
    brand: Optional[str] = Field(None, max_length=100)
    category_id: UUID
    variants: Optional[List[VariantCreate]] = None
    origin: Optional[str] = None
    is_featured: bool = False
    is_bestseller: bool = False
    # Flat fields for simple product creation (auto-creates a default variant)
    base_price: Optional[float] = None
    sale_price: Optional[float] = None
    cost_price: Optional[float] = None
    quantity: Optional[int] = 0
    sku: Optional[str] = None
    weight_kg: Optional[float] = None
    tags: Optional[List[str]] = []
    origin_country: Optional[str] = None
    is_active: Optional[bool] = True

    @field_validator('brand', mode='before')
    @classmethod
    def blank_brand_is_none(cls, v):
        return (v.strip() or None) if isinstance(v, str) else v

    @field_validator('hashtags')
    @classmethod
    def drop_repeated_hashtags(cls, v):
        return unique_hashtags(v)


class VariantUpdate(BaseModel):
    sku: Optional[str] = None
    name: Optional[str] = None
    base_price: Optional[float] = None
    sale_price: Optional[float] = None
    stock: Optional[int] = None
    attributes: Optional[Dict[str, Any]] = None
    specifications: Optional[Dict[str, Any]] = None
    dietary_tags: Optional[Dict[str, Any]] = None
    tags: Optional[str] = None
    is_active: Optional[bool] = None
    availability_status: Optional[AvailabilityStatus] = None

    @field_validator('dietary_tags', mode='before')
    @classmethod
    def validate_dietary_tags(cls, v):
        return normalize_dietary_tags(v)


class Update(BaseModel):
    name: Optional[str] = None
    hashtags: Optional[List[Hashtag]] = Field(None, max_length=50)
    description: Optional[str] = None
    short_description: Optional[str] = None
    brand: Optional[str] = Field(None, max_length=100)
    category_id: Optional[UUID] = None
    origin: Optional[str] = None
    product_status: Optional[ProductStatus] = None
    is_featured: Optional[bool] = None
    is_bestseller: Optional[bool] = None

    @field_validator('brand', mode='before')
    @classmethod
    def blank_brand_is_none(cls, v):
        return (v.strip() or None) if isinstance(v, str) else v

    @field_validator('hashtags')
    @classmethod
    def drop_repeated_hashtags(cls, v):
        return unique_hashtags(v)


class InventoryResponse(BaseModel):
    id: Optional[UUID] = None
    quantity_available: int = 0
    low_stock_threshold: int = 10
    inventory_status: str = "active"

    model_config = ConfigDict(from_attributes=True)


class VariantResponse(BaseModel):
    id: UUID
    product_id: UUID
    sku: str
    name: str
    base_price: float
    sale_price: Optional[float]
    current_price: float
    discount_percentage: float
    stock: int
    attributes: Optional[Dict[str, Any]]
    specifications: Optional[Dict[str, Any]] = None
    dietary_tags: Optional[Dict[str, Any]] = {}
    tags: List[str] = []
    availability_status: AvailabilityStatus = AvailabilityStatus.AVAILABLE
    view_count: int = 0
    purchase_count: int = 0
    is_active: bool
    images: List[ImageResponse] = []
    primary_image: Optional[ImageResponse] = None
    inventory: Optional[InventoryResponse] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    product_name: Optional[str] = None
    product_description: Optional[str] = None

    @field_validator('dietary_tags', mode='before')
    @classmethod
    def validate_dietary_tags(cls, v):
        return normalize_dietary_tags(v)

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={
            datetime: lambda v: v.isoformat() if v else None
        }
    )


class PriceRange(BaseModel):
    min: float
    max: float


class Response(BaseModel):
    id: UUID
    name: str
    brand: Optional[str] = None
    hashtags: List[Hashtag] = []
    # The hashtag a search found this product by, when it's not English: shown as "Suya spice (yaji)".
    matched_hashtag: Optional[str] = None
    description: Optional[str]
    short_description: Optional[str] = None
    category_id: Optional[UUID] = None
    category: Optional[CategoryBrief] = None
    is_featured: bool = False
    is_bestseller: bool = False
    rating: float
    review_count: int
    origin: Optional[str]
    is_active: bool
    product_status: ProductStatus = ProductStatus.ACTIVE
    price_range: PriceRange
    in_stock: bool
    availability_status: AvailabilityStatus = AvailabilityStatus.AVAILABLE
    created_at: datetime
    updated_at: Optional[datetime] = None
    # Relationships
    variants: List[VariantResponse] = []
    primary_variant: Optional[VariantResponse] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={
            datetime: lambda v: v.isoformat() if v else None
        }
    )


class ListResponse(BaseModel):
    products: List[Response]
    total: int
    page: int
    per_page: int
    pages: int


# Product image schemas


# Admin product management schemas


class ImageCreate(BaseModel):
    url: str
    alt_text: Optional[str] = None
    is_primary: bool = False
    sort_order: int = 0


class ImageUpdate(BaseModel):
    url: Optional[str] = None
    alt_text: Optional[str] = None
    is_primary: Optional[bool] = None
    sort_order: Optional[int] = None


class ProductModeration(BaseModel):
    """Approve (publish) or reject (take down) a product, with an optional note kept on the product."""
    status: Literal["approved", "rejected"]
    notes: Optional[str] = Field(None, max_length=1000)
