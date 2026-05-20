from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime
from uuid import UUID
from decimal import Decimal


class ProductBase(BaseModel):
    name: str = Field(..., min_length=3, max_length=500)
    description: Optional[str] = None
    short_description: Optional[str] = Field(None, max_length=255)
    category_id: UUID
    brand_id: Optional[UUID] = None
    sku: str = Field(..., min_length=3, max_length=100)
    base_price: Decimal = Field(..., gt=0)
    discount_price: Optional[Decimal] = Field(None, gt=0)
    quantity: int = Field(default=0, ge=0)
    unit_of_measure: str = Field(default="piece")
    minimum_order_quantity: int = Field(default=1, ge=1)
    weight: Optional[Decimal] = None
    length: Optional[Decimal] = None
    width: Optional[Decimal] = None
    height: Optional[Decimal] = None


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=3, max_length=500)
    description: Optional[str] = None
    short_description: Optional[str] = None
    category_id: Optional[UUID] = None
    brand_id: Optional[UUID] = None
    base_price: Optional[Decimal] = None
    discount_price: Optional[Decimal] = None
    quantity: Optional[int] = None
    status: Optional[str] = None
    is_featured: Optional[bool] = None


class ProductResponse(ProductBase):
    id: UUID
    vendor_id: UUID
    slug: str
    status: str
    rating: Decimal
    review_count: int
    view_count: int
    sales_count: int
    is_featured: bool
    is_verified: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProductListResponse(BaseModel):
    products: List[ProductResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class ProductFilter(BaseModel):
    category_id: Optional[UUID] = None
    vendor_id: Optional[UUID] = None
    brand_id: Optional[UUID] = None
    search: Optional[str] = None
    min_price: Optional[Decimal] = None
    max_price: Optional[Decimal] = None
    is_featured: Optional[bool] = None
    sort_by: str = "created_at"
    sort_order: str = "desc"