from pydantic import BaseModel, Field
from typing import Literal, Optional, List
from uuid import UUID
from datetime import datetime

# Scope values shared by the create schema, CRUD layer and both the vendor and
# admin endpoints. `platform` = every product site-wide (admin only), `vendor` =
# all of the owner's products, `product`/`category` = narrow to one target.
PromoScope = Literal["platform", "vendor", "product", "category"]


class PromoCodeCreate(BaseModel):
    code: str = Field(..., min_length=3, max_length=50)
    description: Optional[str] = None
    # Flat amount off, in Naira (whole number, e.g. 1000).
    discount_amount: float = Field(..., gt=0)
    max_uses: int = Field(default=0, ge=0)
    # 0 = unlimited redemptions per buyer.
    max_uses_per_user: int = Field(default=0, ge=0)
    min_order_amount: float = Field(default=0, ge=0)
    expires_at: Optional[datetime] = None
    # None keeps older clients (which send no scope) working unchanged: the
    # vendor endpoint coerces an omitted scope to "vendor", the admin endpoint to
    # "platform".
    scope_type: Optional[PromoScope] = None
    scope_id: Optional[UUID] = None


class PromoCodeUpdate(BaseModel):
    """Partial update for an existing promo code (`PATCH /promos/{id}`).

    Every field is optional: the CRUD layer only writes the keys the caller
    actually sent (`exclude_unset`), so a cancel/reactivate stays a one-column
    write while the same endpoint also serves full edits. An explicit `null`
    clears a nullable column (e.g. dropping a product/category target or the
    expiry date) — omitting the key leaves the stored value alone.
    """

    code: Optional[str] = Field(default=None, min_length=3, max_length=50)
    description: Optional[str] = None
    discount_amount: Optional[float] = Field(default=None, gt=0)
    max_uses: Optional[int] = Field(default=None, ge=0)
    max_uses_per_user: Optional[int] = Field(default=None, ge=0)
    min_order_amount: Optional[float] = Field(default=None, ge=0)
    expires_at: Optional[datetime] = None
    is_active: Optional[bool] = None
    scope_type: Optional[PromoScope] = None
    scope_id: Optional[UUID] = None


class PromoCodeResponse(BaseModel):
    # UUID-typed like the other ORM-backed response schemas (category/product/
    # order/vendor/...). Pydantic serialises a UUID to a JSON string, so the wire
    # format is unchanged; typing them `str` made FastAPI 500 on ORM rows.
    id: UUID
    code: str
    description: Optional[str] = None
    discount_amount: float
    max_uses: int
    current_uses: int
    max_uses_per_user: int = 0
    min_order_amount: float
    is_active: bool
    vendor_id: Optional[UUID] = None
    scope_type: str = "platform"
    scope_id: Optional[UUID] = None
    expires_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class PromoValidateItem(BaseModel):
    """One cart line sent with a code so it can be checked scope-aware.

    Ids are optional: a guest cart only knows product ids, and a line missing an
    id simply cannot match that scope (see `promo_service.eligible_base`).
    """

    product_id: Optional[UUID] = None
    vendor_id: Optional[UUID] = None
    category_id: Optional[UUID] = None
    unit_price: float = 0
    quantity: int = Field(default=1, ge=1)


class PromoValidateRequest(BaseModel):
    code: str
    # Optional cart context. When present, the code is checked against the same
    # scope/min-order/cap rules `orders.create` enforces, so a code that would be
    # dropped at checkout is flagged in the cart instead. Without it the response
    # keeps its historical "is this a valid code" shape.
    subtotal: Optional[float] = Field(default=None, ge=0)
    items: Optional[List[PromoValidateItem]] = None


class PromoValidateResponse(BaseModel):
    valid: bool
    discount_amount: float = 0
    message: str = ""
    # Whole-Naira minimum the cart must reach for the code to apply (0 = none):
    # lets the cart state the requirement instead of only failing it.
    min_order_amount: float = 0
