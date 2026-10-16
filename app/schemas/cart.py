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
    # Vendor behind the line. `vendor_id` is required to start a negotiation, and
    # `vendor_open_to_negotiate` gates the cart's per-item "Negotiate" button.
    vendor_id: Optional[str] = None
    vendor_open_to_negotiate: bool = False
    unit_price: float
    base_price: float = 0
    # Agreed private (negotiated) unit price for this buyer; None when no deal.
    negotiation_price: Optional[float] = None
    quantity: int
    minimum_order_quantity: int
    stock: int
    unit_of_measure: str
    image_url: str
    # Category name for the storefront's fallback image mapping (additive).
    category_name: Optional[str] = None
    # Category id, used by the cart to scope-check a promo code (additive).
    category_id: Optional[str] = None
    is_verified: bool


class CartResponse(BaseModel):
    items: List[CartItemResponse]
