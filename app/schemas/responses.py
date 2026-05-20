from pydantic import BaseModel
from typing import Optional, Any, Dict


class SuccessResponse(BaseModel):
    success: bool = True
    message: str
    data: Optional[Any] = None


class ErrorResponse(BaseModel):
    success: bool = False
    error: Dict[str, Any]


class MessageResponse(BaseModel):
    message: str