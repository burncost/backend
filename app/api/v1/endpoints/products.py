from fastapi import Response, APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from uuid import UUID
from decimal import Decimal

from app.core.database import get_db
from app.schemas.product import (
    CategoryRequest,
    ProductCreate,
    ProductUpdate,
    ProductResponse,
    ProductListResponse,
    ProductFilter
)
from app.schemas.review import ProductReviewsResponse
from app.models.product import Product
from app.models.category import Category
from app.models.brand import Brand
from app.models.product import Review
from app.models.vendor import Vendor
from app.models.user import User, UserProfile
from app.crud import product as product_crud
from app.crud import review as review_crud
from app.services.product_service import ProductService
from app.api.deps import get_current_vendor, get_current_user
from app.config import settings
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


### List products with filtering and pagination
@router.get("/", response_model=ProductListResponse)
async def list_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=settings.MAX_PAGE_SIZE),
    category_id: Optional[UUID] = None,
    category: Optional[str] = Query(None),
    vendor_id: Optional[UUID] = None,
    brand_id: Optional[UUID] = None,
    search: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    is_featured: Optional[bool] = None,
    division: Optional[str] = Query(None),
    material_type: Optional[str] = Query(None),
    sort_by: str = Query("created_at", regex="^(created_at|price|rating|sales_count)$"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
    db: AsyncSession = Depends(get_db)
):
    filters = ProductFilter(
        category_id=category_id,
        category=category,
        vendor_id=vendor_id,
        brand_id=brand_id,
        search=search,
        min_price=min_price,
        max_price=max_price,
        is_featured=is_featured,
        division=division,
        material_type=material_type,
        sort_by=sort_by,
        sort_order=sort_order
    )
    
    product_service = ProductService(db)
    result = await product_service.list_products(
        filters=filters,
        page=page,
        page_size=page_size
    )
    
    return result


### Get product by ID
@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Product, Category.name, Category.division, Category.material_type, Brand.name)
        .join(Category, Product.category_id == Category.id)
        .outerjoin(Brand, Product.brand_id == Brand.id)
        .where(Product.id == product_id)
    )

    row = result.first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found"
        )

    product, category_name, category_division, category_material_type, brand_name = row
    
    # Fetch vendor details
    vendor_name = None
    vendor_email = None
    vendor_location = None
    vendor_result = await db.execute(
        select(Vendor, User, UserProfile)
        .join(User, Vendor.user_id == User.id)
        .outerjoin(UserProfile, User.id == UserProfile.user_id)
        .where(Vendor.id == product.vendor_id)
    )
    vendor_row = vendor_result.first()
    if vendor_row:
        vendor, user, profile = vendor_row
        vendor_name = vendor.business_name
        vendor_email = user.email
        vendor_location = profile.location if profile and profile.location else f"{vendor.city}, {vendor.state}"
    
    # Increment view count
    product_service = ProductService(db)
    await product_service.increment_view_count(product_id)
    
    return {
        **product.__dict__,
        "category": category_name,
        "category_division": category_division,
        "category_material_type": category_material_type,
        "brand_name": brand_name,
        "vendor_name": vendor_name,
        "vendor_email": vendor_email,
        "vendor_location": vendor_location,
    }


### Create a new product (vendor only)
@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_product(
    product_in: ProductCreate,
    current_vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db)
):
    try:
        product_service = ProductService(db)

        # Check if SKU already exists
        existing = await product_crud.get_by_sku(db, sku=product_in.sku)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"A product with SKU '{product_in.sku}' already exists. Please use a different SKU."
            )
        
        product = await product_service.create_product(
            product_in=product_in,
            vendor_id=UUID(current_vendor["id"])
        )

        # Return the created product data
        return {"id": str(product.id), "name": product.name, "slug": product.slug}

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Failed to create product: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to create product. Please try again later."
        )


### Update product (vendor only - own products)
@router.put("/{product_id}", status_code=status.HTTP_200_OK)
async def update_product(
    product_id: UUID,
    product_in: ProductUpdate,
    current_vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db)
):
    try:
        product = await product_crud.get(db, id=product_id)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found"
            )
        
        # Check ownership
        if str(product.vendor_id) != current_vendor["id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to update this product"
            )
        
        update_data = product_in.model_dump(exclude_unset=True)
        
        if not update_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields provided for update"
            )
        
        # Apply updates
        for field, value in update_data.items():
            setattr(product, field, value)
        
        await db.commit()
        await db.refresh(product)
        
        return Response(status_code=status.HTTP_200_OK)

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to update product {product_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to update product. Please try again later."
        )


### Delete product (vendor only - own products)
@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: UUID,
    current_vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db)
):
    product = await product_crud.get(db, id=product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found"
        )
    
    # Check ownership
    if str(product.vendor_id) != current_vendor["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to delete this product"
        )
    
    await product_crud.remove(db, id=product_id)
    return None


### list vendor only products
@router.get("/vendor/my-products", response_model=ProductListResponse)
async def list_my_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db)
):
    filters = ProductFilter(
        vendor_id=UUID(current_vendor["id"]),
        sort_by="created_at",
        sort_order="desc"
    )
    
    product_service = ProductService(db)
    result = await product_service.list_products(filters, page, page_size)
    
    return result


### Upload product images
@router.post("/{product_id}/images")
async def upload_product_images(
    product_id: UUID,
    files: List[UploadFile] = File(...),
    current_vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db)
):
    product = await product_crud.get(db, id=product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found"
        )
    
    # Check ownership
    if str(product.vendor_id) != current_vendor["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to upload images for this product"
        )
    
    product_service = ProductService(db)
    images = await product_service.upload_product_images(
        product_id=product_id,
        files=files
    )
    
    return {"images": images}


@router.post("/cat-id", status_code=status.HTTP_200_OK)
async def get_cat_id(
    payload: CategoryRequest,
    db: AsyncSession = Depends(get_db)
):
    logger.info(f"Received category name: {payload.category_name}")

    category_id = await product_crud.get_id_by_name(
        db,
        name=payload.category_name
    )

    if not category_id:
        raise HTTPException(
            status_code=400,
            detail=f"Category '{payload.category_name}' does not exist"
        )

    return {"category_id": str(category_id)}


@router.get("/{product_id}/reviews", response_model=ProductReviewsResponse)
async def get_product_reviews(
    product_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    # 1. fetch reviews (latest 3 for UI preview)
    reviews = await review_crud.get_reviews_by_product(
        db,
        product_id=product_id,
        limit=3
    )

    if not reviews:
        return {
            "product_id": product_id,
            "average_rating": 0,
            "total_reviews": 0,
            "reviews": []
        }

    # 2. compute stats
    total, avg = await review_crud.get_review_stats(db, product_id)

    return {
        "product_id": product_id,
        "average_rating": round(avg, 2),
        "total_reviews": total,
        "reviews": reviews,
    }


@router.get("/{product_id}/reviews/all", response_model=ProductReviewsResponse)
async def get_all_product_reviews(
    product_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Review)
        .where(Review.product_id == product_id)
        .order_by(Review.created_at.desc())
    )

    reviews = result.scalars().all()

    total, avg = await review_crud.get_review_stats(db, product_id)

    return {
        "product_id": product_id,
        "average_rating": round(avg, 2),
        "total_reviews": total,
        "reviews": reviews,
    }
