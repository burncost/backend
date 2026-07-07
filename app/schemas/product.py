from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from uuid import UUID
from decimal import Decimal


class ProductBase(BaseModel):
    name: str = Field(..., min_length=3, max_length=500)
    description: Optional[str] = None
    short_description: Optional[str] = Field(None, max_length=255)
    category_id: Optional[UUID] = None
    brand_id: Optional[UUID] = None
    brand_name: Optional[str] = None
    sku: str = Field(..., min_length=3, max_length=100)
    base_price: Decimal = Field(..., gt=0)
    discount_price: Optional[Decimal] = Field(None, gt=0)
    quantity: int = Field(default=0, ge=0)
    unit_of_measure: str = Field(default="piece")
    minimum_order_quantity: int = Field(default=1, ge=1)
    shipping_fee: Optional[Decimal] = Field(default=0.00)
    estimated_delivery_days: Optional[int] = Field(default=5)
    free_shipping_threshold: Optional[int] = Field(default=0)
    status: Optional[str] = None
    is_featured: Optional[bool] = False 
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
    category: Optional[str]
    category_division: Optional[str] = None
    category_material_type: Optional[str] = None
    brand_name: Optional[str] = None
    vendor_name: Optional[str] = None
    vendor_email: Optional[str] = None
    vendor_location: Optional[str] = None
    rating: Decimal
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


class CategoryRequest(BaseModel):
    category_name: str


class ProductFilter(BaseModel):
    category_id: Optional[UUID] = None
    category: Optional[str] = None
    vendor_id: Optional[UUID] = None
    brand_id: Optional[UUID] = None
    search: Optional[str] = None
    min_price: Optional[Decimal] = None
    max_price: Optional[Decimal] = None
    is_featured: Optional[bool] = None
    division: Optional[str] = None
    material_type: Optional[str] = None
    sort_by: str = "created_at"
    sort_order: str = "desc"
