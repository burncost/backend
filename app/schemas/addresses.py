from pydantic import BaseModel
from typing import Optional


class AddressUpdate(BaseModel):
    address_type: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    lga: Optional[str] = None
    postal_code: Optional[str] = None
    landmark: Optional[str] = None
    