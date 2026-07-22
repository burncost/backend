from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class VendorDraftSave(BaseModel):
    current_step: Optional[str] = None
    business_info: Optional[dict] = None
    banking_info: Optional[dict] = None


class VendorDraftResponse(BaseModel):
    current_step: str
    business_info: dict
    banking_info: dict
    updated_at: datetime

    class Config:
        from_attributes = True