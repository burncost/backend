from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from uuid import UUID


class CategoryBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    slug: str = Field(..., min_length=2, max_length=255)
    description: Optional[str] = None
    parent_id: Optional[UUID] = None
    is_active: bool = True


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    parent_id: Optional[UUID] = None
    is_active: Optional[bool] = None


class CategoryResponse(CategoryBase):
    id: UUID
    image_url: Optional[str] = None
    display_order: int
    created_at: datetime

    class Config:
        from_attributes = True