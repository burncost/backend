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
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_default: Optional[bool] = None
    delivery_instructions: Optional[str] = None


class AddressCreate(BaseModel):
    """Payload for saving a new delivery address at checkout / in settings."""
    address_type: Optional[str] = "home"
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    address_line1: str
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    lga: Optional[str] = None
    postal_code: Optional[str] = None
    landmark: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_default: Optional[bool] = False
    delivery_instructions: Optional[str] = None
