from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from uuid import UUID

from app.core.database import get_db
from app.schemas.product import (
    ProductCreate,
    ProductUpdate,
    ProductResponse,
    ProductListResponse,
    ProductFilter
)
from app.crud import product as product_crud
from app.services.product_service import ProductService
from app.api.deps import get_current_vendor, get_current_user
from app.config import settings

router = APIRouter()

### List products with filtering and pagination
@router.get("/", response_model=ProductListResponse)
async def list_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=settings.MAX_PAGE_SIZE),
    category_id: Optional[UUID] = None,
    vendor_id: Optional[UUID] = None,
    brand_id: Optional[UUID] = None,
    search: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    is_featured: Optional[bool] = None,
    sort_by: str = Query("created_at", regex="^(created_at|price|rating|sales_count)$"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
    db: AsyncSession = Depends(get_db)
):
    filters = ProductFilter(
        category_id=category_id,
        vendor_id=vendor_id,
        brand_id=brand_id,
        search=search,
        min_price=min_price,
        max_price=max_price,
        is_featured=is_featured,
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
    product = await product_crud.get(db, id=product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found"
        )
    
    # Increment view count
    product_service = ProductService(db)
    await product_service.increment_view_count(product_id)
    
    return product

### Create a new product (vendor only)
@router.post("/", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    product_in: ProductCreate,
    current_vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db)
):
    product_service = ProductService(db)
    
    # Check if SKU already exists
    existing = await product_crud.get_by_sku(db, sku=product_in.sku)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product with SKU '{product_in.sku}' already exists"
        )
    
    product = await product_service.create_product(
        product_in=product_in,
        vendor_id=UUID(current_vendor["id"])
    )
    
    return product

### Update product (vendor only - own products)
@router.put("/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: UUID,
    product_in: ProductUpdate,
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
            detail="Not authorized to update this product"
        )
    
    product_service = ProductService(db)
    updated_product = await product_service.update_product(
        product_id=product_id,
        product_in=product_in
    )
    
    return updated_product

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