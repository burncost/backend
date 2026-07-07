from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from uuid import UUID
from decimal import Decimal


class CategoryBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    slug: str = Field(..., min_length=2, max_length=255)
    description: Optional[str] = None
    parent_id: Optional[UUID] = None
    is_active: bool = True
    division: Optional[str] = None
    material_type: Optional[str] = "material"
    default_unit: Optional[str] = None
    waste_factor: Optional[Decimal] = Decimal("0.00")


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    parent_id: Optional[UUID] = None
    is_active: Optional[bool] = None
    division: Optional[str] = None
    material_type: Optional[str] = None
    default_unit: Optional[str] = None
    waste_factor: Optional[Decimal] = None


class CategoryResponse(CategoryBase):
    id: UUID
    image_url: Optional[str] = None
    display_order: int
    created_at: datetime
    children: Optional[List["CategoryResponse"]] = None

    class Config:
        from_attributes = True


class CategoryTreeResponse(BaseModel):
    """Hierarchical category tree for BOQ taxonomy."""
    id: UUID
    name: str
    slug: str
    division: Optional[str] = None
    material_type: Optional[str] = None
    default_unit: Optional[str] = None
    waste_factor: Optional[Decimal] = None
    children: List["CategoryTreeResponse"] = []

    class Config:
        from_attributes = True
