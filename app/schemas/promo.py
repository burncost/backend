from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID
from datetime import datetime


class PromoCodeCreate(BaseModel):
    code: str = Field(..., min_length=3, max_length=50)
    description: Optional[str] = None
    discount_percent: float = Field(..., gt=0, le=100)
    max_uses: int = Field(default=0, ge=0)
    min_order_amount: float = Field(default=0, ge=0)
    expires_at: Optional[datetime] = None


class PromoCodeResponse(BaseModel):
    id: str
    code: str
    description: Optional[str] = None
    discount_percent: float
    max_uses: int
    current_uses: int
    min_order_amount: float
    is_active: bool
    expires_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class PromoValidateRequest(BaseModel):
    code: str


class PromoValidateResponse(BaseModel):
    valid: bool
    discount_percent: float = 0
    message: str = ""
