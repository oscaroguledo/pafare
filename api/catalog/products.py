from fastapi import APIRouter, BackgroundTasks, Depends, Query, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from uuid import UUID

from core.db import get_db
from core.dependencies import require_admin
from core.utils.response import Response
from core.exceptions import APIException
from core.logging import get_structured_logger as get_logger
from services.catalog.products import ProductService
from models.accounts.user import User
from models.catalog.product import ProductStatus
from schemas.catalog.product import ProductModeration, Create, Update, ImageCreate, ImageUpdate, VariantCreate as ProductVariantCreate, VariantUpdate as ProductVariantUpdate

logger = get_logger(__name__)

router = APIRouter(prefix="/products", tags=["Products"])
# /products?sort_by=created_at&sort_order=desc&page=1&limit=12


@router.get("/home/")
async def get_home_data(background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """Home page sections in one request: from Redis, else from Postgres (Redis is refilled in the background)."""
    return Response.success(data=await ProductService(db).home_cached(background_tasks))


@router.get("/")
async def list(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=1000),
    category: Optional[str] = Query(None, description="category of the products"),
    q: Optional[str] = Query(None, description="Search query for filtering"),
    min_price: Optional[float] = Query(None, ge=0, description="Minimum price filter"),
    max_price: Optional[float] = Query(None, ge=0, description="Maximum price filter"),
    min_rating: Optional[int] = Query(None, ge=1, le=5),
    max_rating: Optional[int] = Query(None, ge=1, le=5),
    sort_by: Optional[str] = Query("created_at", description="created_at, name, price, rating_average or popular"),
    sort_order: Optional[str] = Query("desc"),
    availability: Optional[bool] = None,
    is_featured: Optional[bool] = None,
    is_bestseller: Optional[bool] = None,
    sale: Optional[bool] = None,
    db: AsyncSession = Depends(get_db)
):
    """Get products with optional filtering and pagination."""
    try:
        # Use basic product service for regular queries
        product_service = ProductService(db)

        filters = {
            "q": q,
            "category":category,
            "min_price": min_price,
            "max_price": max_price,
            "min_rating": min_rating,
            "max_rating": max_rating,
            "availability": availability,
            "is_featured": is_featured,
            "is_bestseller": is_bestseller,
            "sale": sale
        }

        result = await product_service.list(
            page=page,
            limit=limit,
            filters=filters,
            sort_by=sort_by,
            sort_order=sort_order
        )
        
        # Format response with pagination at the correct level
        pagination = {
            "page": result["page"],
            "limit": result["per_page"],
            "total": result["total"],
            "pages": result["total_pages"]
        }
        return Response.success(
            data=result["data"],
            pagination=pagination
        )
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to fetch products {str(e)}"
        )


@router.get("/admin/")
async def admin_list(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=1000),
    q: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    product_status: Optional[ProductStatus] = Query(None, alias="status"),
    sort_by: Optional[str] = Query("created_at"),
    sort_order: Optional[str] = Query("desc"),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Admin product list across every status (the public list only shows active products)."""
    result = await ProductService(db).list(
        page=page, limit=limit, filters={"q": q, "category": category},
        sort_by=sort_by, sort_order=sort_order, status=product_status
    )
    pagination = {"page": result["page"], "limit": result["per_page"], "total": result["total"], "pages": result["total_pages"]}
    return Response.success(data=result["data"], pagination=pagination)


@router.get("/featured/")
async def get_featured(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db)
):
    """Get featured products."""
    try:
        product_service = ProductService(db)
        products = await product_service.featured(limit=limit)
        return Response.success(data=products)
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to fetch featured products: {str(e)}"
        )


@router.get("/deals/")
async def get_deals(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """Get products on sale/deals."""
    try:
        product_service = ProductService(db)
        result = await product_service.list(
            page=page,
            limit=limit,
            filters={"sale": True},
            sort_by="created_at",
            sort_order="desc"
        )
        pagination = {
            "page": result.get("page", page),
            "limit": result.get("per_page", result.get("limit", limit)),
            "total": result.get("total", 0),
            "pages": result.get("total_pages", 1)
        }
        return Response.success(data=result.get("data", result), pagination=pagination)
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to fetch deals: {str(e)}"
        )


@router.get("/{product_id}/recommendations/")
async def recommended(
    product_id: UUID,
    limit: int = Query(4, ge=1, le=20),
    db: AsyncSession = Depends(get_db)
):
    """Get recommended products based on a product."""
    try:
        product_service = ProductService(db)
        products = await product_service.recommended(product_id, limit)
        return Response.success(data=products)
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to fetch recommended products - {str(e)}"
        )


@router.get("/{product_id}/")
async def get_product(
    product_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """Get a specific product by ID."""
    try:
        product = await ProductService(db).get(product_id)
        if not product:
            raise APIException(
                status_code=status.HTTP_404_NOT_FOUND,
                message="Product not found"
            )
        return Response.success(data=product)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error fetching product {product_id}")
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to fetch product: {str(e)}"
        )


@router.post("/")
async def create(
    product_data: Create,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Create a new product (admin only)."""
    try:
        product_service = ProductService(db)
        product = await product_service.create(product_data, current_user.id)
        return Response.success(data=product, message="Product created successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error creating product")
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to create product: {str(e)}"
        )


@router.patch("/{product_id}/")
async def update(
    product_id: UUID,
    product_data: Update,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Update a product (admin only)."""
    try:
        product_service = ProductService(db)
        product = await product_service.update(product_id, product_data, current_user.id, is_admin=True)
        return Response.success(data=product, message="Product updated successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to update product"
        )


@router.delete("/{product_id}/")
async def delete(
    product_id: UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Delete a product (admin only)."""
    try:
        product_service = ProductService(db)
        await product_service.delete(product_id, current_user.id, is_admin=True)
        return Response.success(message="Product deleted successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to delete product"
        )


# --- VARIANTS - 5 Standard APIs ---


# --- VARIANT IMAGES - 5 Standard APIs ---


@router.post("/{product_id}/variants/")
async def create_variant(
    product_id: UUID,
    variant_data: ProductVariantCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Create a new variant for a product (admin only)."""
    try:
        product_service = ProductService(db)
        variant = await product_service.create_variant(product_id, variant_data)
        return Response.success(data=variant, message="Variant created successfully", status_code=status.HTTP_201_CREATED)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to create variant: {str(e)}")


@router.get("/variants/{variant_id}/")
async def get_variant(
    variant_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """Get a specific variant by ID."""
    try:
        product_service = ProductService(db)
        variant = await product_service.get_variant(variant_id)
        if not variant:
            raise APIException(status_code=404, message="Variant not found")
        return Response.success(data=variant)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to fetch variant: {str(e)}")


@router.get("/{product_id}/variants/")
async def list_variants(
    product_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """List all variants for a product."""
    try:
        product_service = ProductService(db)
        variants = await product_service.list_variants(product_id)
        return Response.success(data=variants)
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to fetch variants: {str(e)}")


@router.patch("/variants/{variant_id}/")
async def patch_variant(
    variant_id: UUID,
    variant_data: ProductVariantUpdate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Update a variant (admin only)."""
    try:
        product_service = ProductService(db)
        variant = await product_service.update_variant(variant_id, variant_data)
        return Response.success(data=variant, message="Variant updated successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to update variant: {str(e)}")


@router.delete("/variants/{variant_id}/")
async def delete_variant(
    variant_id: UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Delete a variant (admin only)."""
    try:
        product_service = ProductService(db)
        outcome = await product_service.delete_variant(variant_id)
        if not outcome:
            raise APIException(status_code=404, message="Variant not found")
        message = "Variant deleted" if outcome == "deleted" else "Variant archived: it has orders or subscriptions, so it was made inactive"
        return Response.success(data={"outcome": outcome}, message=message)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to delete variant: {str(e)}")


@router.post("/image-upload-url/")
async def get_image_upload_url(
    filename: str = Query(..., description="filename with extension, e.g., 'product-image.jpg'"),
    current_user: User = Depends(require_admin),
):
    """Get a signed R2 URL for uploading a product image (admin only).

    Frontend: GET this URL, then PUT the image file to it, then send the public URL
    back to create_image. R2 is optional: returns 501 if not configured.
    """
    from core.r2_storage import get_signed_upload_url, get_public_url
    from uuid import uuid4

    # Generate a unique S3 key
    key = f"products/{uuid4()}/{filename}"
    upload_url = get_signed_upload_url(key)
    if not upload_url:
        raise APIException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            message="R2 storage is not configured"
        )

    public_url = get_public_url(key)
    return Response.success(
        data={"upload_url": upload_url, "public_url": public_url},
        message="Signed upload URL generated"
    )


@router.post("/variants/{variant_id}/images/")
async def create_image(
    variant_id: UUID,
    image_data: ImageCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Create a new image for a variant (admin only)."""
    try:
        product_service = ProductService(db)
        image = await product_service.create_image(
            variant_id=variant_id,
            url=image_data.url,
            alt_text=image_data.alt_text,
            is_primary=image_data.is_primary,
            sort_order=image_data.sort_order
        )
        return Response.success(data=image, message="Image created successfully", status_code=status.HTTP_201_CREATED)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to create image: {str(e)}")


@router.get("/images/{image_id}/")
async def get_image(
    image_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """Get a specific image by ID."""
    try:
        product_service = ProductService(db)
        image = await product_service.get_image(image_id)
        if not image:
            raise APIException(status_code=404, message="Image not found")
        return Response.success(data=image)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to fetch image: {str(e)}")


@router.get("/variants/{variant_id}/images/")
async def list_images(
    variant_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """List all images for a variant."""
    try:
        product_service = ProductService(db)
        images = await product_service.list_images(variant_id)
        return Response.success(data=images)
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to fetch images: {str(e)}")


@router.patch("/images/{image_id}/")
async def patch_image(
    image_id: UUID,
    image_data: ImageUpdate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Update an image (admin only)."""
    try:
        product_service = ProductService(db)
        image = await product_service.update_image(
            image_id=image_id,
            url=image_data.url,
            alt_text=image_data.alt_text,
            is_primary=image_data.is_primary,
            sort_order=image_data.sort_order
        )
        if not image:
            raise APIException(status_code=404, message="Image not found")
        return Response.success(data=image, message="Image updated successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to update image: {str(e)}")


@router.delete("/images/{image_id}/")
async def delete_image(
    image_id: UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Delete an image (admin only)."""
    try:
        product_service = ProductService(db)
        deleted = await product_service.delete_image(image_id)
        if not deleted:
            raise APIException(status_code=404, message="Image not found")
        return Response.success(message="Image deleted successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to delete image: {str(e)}")


@router.patch("/{product_id}/moderate/")
async def moderate(
    product_id: UUID,
    request: ProductModeration,
    current_user = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Moderate product (admin only)."""
    try:
        product_service = ProductService(db)
        result = await product_service.moderate(product_id, request.status, request.notes)
        return Response.success(data=result, message="Product moderated successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to moderate product: {str(e)}")


@router.patch("/{product_id}/feature/")
async def feature(
    product_id: UUID,
    featured: bool = True,
    current_user = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Feature/unfeature product (admin only)."""
    try:
        product_service = ProductService(db)
        result = await product_service.set_featured(product_id, featured)
        return Response.success(data=result, message="Product featured status updated")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=500, message=f"Failed to update featured status: {str(e)}")