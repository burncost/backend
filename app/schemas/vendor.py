from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from uuid import UUID
from decimal import Decimal


class VendorBase(BaseModel):
    business_name: str = Field(..., min_length=2, max_length=255)
    cac_business_registration_number: Optional[str] = None
    tax_identification_number: Optional[str] = None


class VendorCreate(VendorBase):
    business_name: str = Field(..., max_length=100)
    business_type: str = Field(..., max_length=100)
    business_address: str = Field(..., max_length=255)
    city: str = Field(..., max_length=100)
    state: str = Field(..., max_length=100)
    cac_business_registration_number: Optional[str] = Field(None, max_length=100)
    tax_identification_number: Optional[str] = Field(None, max_length=50)
    verification_status: Optional[str] = Field(None, max_length=50)
    verification_date: Optional[datetime] = None
    verified_by: Optional[UUID] = None
    bank_name: str = Field(..., max_length=100)
    bank_account_number: str = Field(..., max_length=100)
    bank_account_name: str = Field(..., max_length=100)
    bank_code: Optional[str] = Field(None, max_length=10)
    commission_rate: Optional[Decimal] = None
    rating: Optional[Decimal] = None
    total_reviews: Optional[int] = None
    total_sales: Optional[Decimal] = None
    is_featured: Optional[bool] = False


class VendorUpdate(BaseModel):
    business_name: Optional[str] = Field(None, max_length=100)
    business_type: Optional[str] = Field(None, max_length=100)
    business_address: Optional[str] = Field(None, max_length=255)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    cac_business_registration_number: Optional[str] = Field(None, max_length=100)
    tax_identification_number: Optional[str] = Field(None, max_length=50)
    bank_name: Optional[str] = Field(None, max_length=100)
    bank_account_number: Optional[str] = Field(None, max_length=100)
    bank_account_name: Optional[str] = Field(None, max_length=100)
    verification_status: Optional[str] = Field(None, max_length=50)
    verification_date: Optional[datetime] = None
    verified_by: Optional[UUID] = None
    commission_rate: Optional[Decimal] = None
    rating: Optional[Decimal] = None
    total_reviews: Optional[int] = None
    total_sales: Optional[Decimal] = None
    is_featured: Optional[bool] = False
    business_image: Optional[str] = None
    delivery_time: Optional[str] = Field(None, max_length=100)
    response_time: Optional[str] = Field(None, max_length=100)
    specializations: Optional[List[str]] = None


class VendorResponse(VendorBase):
    id: UUID
    user_id: UUID
    business_type: Optional[str] = None
    business_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_account_name: Optional[str] = None
    verification_status: str
    commission_rate: Decimal
    rating: Decimal
    total_reviews: int
    total_sales: Decimal
    is_featured: bool
    business_image: Optional[str] = None
    delivery_time: Optional[str] = "1-3 Days"
    response_time: Optional[str] = "< 1 hour"
    specializations: Optional[List[str]] = []
    created_at: datetime

    class Config:
        from_attributes = True
        