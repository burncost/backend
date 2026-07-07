from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID


class CartItemCreate(BaseModel):
    product_id: UUID
    variant_id: Optional[UUID] = None
    quantity: int = Field(1, ge=1)


class CartItemUpdate(BaseModel):
    quantity: int = Field(..., ge=0)


class CartItemResponse(BaseModel):
    id: str
    product_id: str
    product_name: str
    supplier_name: str
    unit_price: float
    base_price: float = 0
    quantity: int
    minimum_order_quantity: int
    stock: int
    unit_of_measure: str
    image_url: str
    is_verified: bool


class CartResponse(BaseModel):
    items: List[CartItemResponse]
