from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from uuid import UUID
from decimal import Decimal


class OrderItemBase(BaseModel):
    product_id: UUID
    variant_id: Optional[UUID] = None
    quantity: int = Field(..., gt=0)


class OrderItemCreate(OrderItemBase):
    pass


class OrderItemResponse(OrderItemBase):
    id: UUID
    order_id: UUID
    vendor_id: UUID
    product_name: str
    sku: str
    unit_price: Decimal
    total_price: Decimal
    vendor_status: str

    class Config:
        from_attributes = True


class OrderCreate(BaseModel):
    shipping_address_id: UUID
    payment_method: str
    customer_notes: Optional[str] = None


class OrderUpdate(BaseModel):
    status: Optional[str] = None
    admin_notes: Optional[str] = None


class OrderResponse(BaseModel):
    id: UUID
    order_number: str
    user_id: UUID
    status: str
    subtotal: Decimal
    shipping_fee: Decimal
    tax_amount: Decimal
    discount_amount: Decimal
    total_amount: Decimal
    payment_status: str
    payment_method: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    items: List[OrderItemResponse] = []

    class Config:
        from_attributes = True


class OrderListResponse(BaseModel):
    orders: List[OrderResponse]
    total: int
    page: int
    page_size: int
    total_pages: int