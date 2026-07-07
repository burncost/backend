from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from uuid import UUID
from decimal import Decimal


class MaterialRateBase(BaseModel):
    category_id: UUID
    material_name: str = Field(..., min_length=2, max_length=255)
    specification: Optional[str] = None
    unit: str = Field(..., max_length=50)
    current_price: Decimal = Field(..., gt=0)
    previous_price: Optional[Decimal] = None
    currency: str = "NGN"
    state: str = Field(..., max_length=100)
    lga: Optional[str] = None
    supplier_id: Optional[UUID] = None
    trend: str = "stable"
    source: str = "manual"


class MaterialRateCreate(MaterialRateBase):
    pass


class MaterialRateUpdate(BaseModel):
    current_price: Optional[Decimal] = None
    previous_price: Optional[Decimal] = None
    trend: Optional[str] = None
    source: Optional[str] = None
    verified_at: Optional[datetime] = None


class MaterialRateResponse(MaterialRateBase):
    id: UUID
    verified_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MaterialRateListResponse(BaseModel):
    rates: List[MaterialRateResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class MaterialRateFilter(BaseModel):
    category_id: Optional[UUID] = None
    state: Optional[str] = None
    material_name: Optional[str] = None
    supplier_id: Optional[UUID] = None
    sort_by: str = "material_name"
    sort_order: str = "asc"
