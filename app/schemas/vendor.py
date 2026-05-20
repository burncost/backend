import re

from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime
from uuid import UUID
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String


class VendorBase(BaseModel):
    business_name: str = Field(..., min_length=2, max_length=255)
    business_registration_number: Optional[str] = None
    tax_identification_number: Optional[str] = None


class VendorCreate(VendorBase):
    business_name: str = Field(...,max_length=100)
    business_type: str = Field(..., max_length=100)
    business_address: str = Field(..., max_length=255)
    city: str = Field(..., max_length=50)
    state: str = Field(..., max_length=50)

    cac_business_registration_number: str = Field(..., max_length=15)
    tax_identification_number: Optional[str] = Field(..., max_length=20)
    verification_status: Optional [str] = Field(..., max_length=50)

    bank_account_name: Optional [str] = Field(..., max_length=100)
    bank_account_number: Optional [str] = Field(..., max_length=10)
    bank_name: Optional [str] = Field(...,max_length=100)
    
    verification_date: Optional [datetime] = None
    verified_by: Optional [UUID] = None
    commission_rate: Optional [Decimal] = Field(None, max_digits=5, decimal_places=2)
    rating: Optional [Decimal] = Field(None, max_digits=3, decimal_places=2)
    total_reviews : Optional [int] = None
    total_sales: Optional [Decimal] = Field(None, max_digits=15, decimal_places=2)
    is_featured: Optional [bool] = False


class VendorUpdate(BaseModel):
    business_name: Optional[str] = Field(None,max_length=100)
    business_type: Optional[str] = Field(None, max_length=100)
    business_address: Optional[str] = Field(None, max_length=255)
    city: Optional[str] = Field(None, max_length=50)
    state: Optional[str] = Field(None, max_length=50)

    cac_business_registration_number: Optional[str] = Field(None, max_length=15)
    tax_identification_number: Optional[str] = Field(None, max_length=20)
    verification_status: Optional [str] = Field(None, max_length=50)

    bank_account_name: Optional [str] = Field(None, max_length=100)
    bank_account_number: Optional [str] = Field(None, max_length=10)
    bank_name: Optional [str] = Field(None,max_length=100)

    cac_certificate: Optional[str] = Field(None,max_length=255)
    tax_clearance: Optional[str]= Field(None,max_length=255)
    business_license: Optional[str]= Field(None,max_length=255)
    utility_bill: Optional[str]= Field(None,max_length=255)
    
    verification_date: Optional [datetime] = None
    verified_by: Optional [UUID] = None
    commission_rate: Optional [Decimal] = Field(None, max_digits=5, decimal_places=2)
    rating: Optional [Decimal] = Field(None, max_digits=3, decimal_places=2)
    total_reviews : Optional [int] = None
    total_sales: Optional [Decimal] = Field(None, max_digits=15, decimal_places=2)
    is_featured: Optional [bool] = False


class VendorResponse(VendorBase):
    id: UUID
    user_id: UUID
    verification_status: str
    commission_rate: Decimal
    rating: Decimal
    total_reviews: int
    total_sales: Decimal
    is_featured: bool
    created_at: datetime

    class Config:
        from_attributes = True
        