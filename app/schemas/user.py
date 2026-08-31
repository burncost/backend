from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional
from datetime import datetime, date
from uuid import UUID
import re


class UserBase(BaseModel):
    email: EmailStr
    phone_number: Optional[str] = Field(None, max_length=20)
    
    ### Validate Nigerian phone number format
    @field_validator('phone_number')
    @classmethod
    def validate_nigerian_phone(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        # Remove spaces and dashes
        v = v.strip().replace(' ', '').replace('-', '')
        
        ### Accept formats: +2348032245295, 2348032245295, 08032245295        
        # If starts with 0, convert to +234
        if v.startswith('0'):
            v = '+234' + v[1:]
        # If starts with 234, add +
        elif v.startswith('234'):
            v = '+' + v
        # If doesn't start with +234, assume it needs it
        elif not v.startswith('+234'):
            v = '+234' + v
        
        # Validate format: +234 followed by 10 digits (7-9 at start)
        pattern = r'^\+234[7-9][0-9]{9}$'
        if not re.match(pattern, v):
            raise ValueError(
                'Invalid Nigerian phone number. Must be in format: '
                '+2348012345678 or 08012345678'
            )
        
        return v


class UserCreate(UserBase):
    password: str = Field(..., min_length=8)
    first_name: Optional[str] = Field(None, max_length=100)
    other_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    business_name: Optional[str] = Field(None, max_length=100)

    location: Optional[str] = Field(None, max_length=100)
    role:str = Field(..., max_length=50)
    
    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate password strength"""
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters long')
        if not re.search(r'[A-Z]', v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not re.search(r'[a-z]', v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not re.search(r'\d', v):
            raise ValueError('Password must contain at least one digit')
        return v

### One-time role/business-name completion for fresh OAuth accounts (Phase 13)
class OAuthCompleteRequest(BaseModel):
    role: str = Field(..., max_length=50)
    business_name: Optional[str] = Field(None, max_length=100)


### Schema for updating user profile
class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = None
    first_name: Optional[str] = Field(None, min_length=2, max_length=100)
    other_name: Optional[str] = Field(None, min_length=0, max_length=100)
    last_name: Optional[str] = Field(None, min_length=2, max_length=100)
    business_name: Optional[str] = Field(None, max_length=255)
    avatar_url: Optional[str] = None
    date_of_birth: Optional[date] = None
    location: Optional[str] = Field(None, min_length=2, max_length=100)
    role: Optional[str] = Field(None, max_length=50)
    

    @field_validator('phone_number')
    @classmethod
    def validate_phone_update(cls, v: Optional[str]) -> Optional[str]:
        """Validate phone number on update"""
        if v is None:
            return v
        
        v = v.strip().replace(' ', '').replace('-', '')
        
        if v.startswith('0'):
            v = '+234' + v[1:]
        elif v.startswith('234'):
            v = '+' + v
        elif not v.startswith('+234'):
            v = '+234' + v
        
        pattern = r'^\+234[7-9][0-9]{9}$'
        if not re.match(pattern, v):
            raise ValueError(
                'Invalid Nigerian phone number. Must be in format: '
                '+2348012345678 or 08012345678'
            )
        
        return v

### User profile information
class UserProfileResponse(BaseModel):
    first_name: str
    other_name: Optional[str] = None
    last_name: str
    business_name: Optional[str] = None
    location: Optional[str] = None
    avatar_url: Optional[str] = None
    date_of_birth: Optional[date] = None
    
    class Config:
        from_attributes = True
        
### User response with profile
class UserResponse(UserBase):
    id: UUID
    role: str
    status: str
    email_verified: bool
    phone_verified: bool
    created_at: datetime
    last_login: Optional[datetime] = None
    profile: Optional[UserProfileResponse] = None
    
    class Config:
        from_attributes = True


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = ""
    expires_in: int
    role: str
    vendor_verification: Optional[str]
    name: Optional[str]
    business_name: Optional[str]



class TokenRefresh(BaseModel):
    refresh_token: str


class PasswordUpdateRequest(BaseModel):
    currentPassword: str
    newPassword: str