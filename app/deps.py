"""Dependencies for FastAPI route handlers."""
from typing import Optional, Dict, Any
import logging

from fastapi import Depends, HTTPException, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.services.auth_service import AuthService
from app.services.product_service import ProductService
from app.services.order_service import OrderService
from app.services.payment_service import PaymentService
from app.services.notification_service import NotificationService
from app.services.document_service import DocumentService
from app.services.boq_generator import BOQGenerator
from app.services.ai_service import AIService
from app.utils.storage import StorageService

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)


# Service singletons
_auth_service: Optional[AuthService] = None
_product_service: Optional[ProductService] = None
_order_service: Optional[OrderService] = None
_payment_service: Optional[PaymentService] = None
_notification_service: Optional[NotificationService] = None
_document_service: Optional[DocumentService] = None
_boq_generator: Optional[BOQGenerator] = None
_ai_service: Optional[AIService] = None
_storage_service: Optional[StorageService] = None


def get_auth_service() -> AuthService:
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service


def get_product_service() -> ProductService:
    global _product_service
    if _product_service is None:
        _product_service = ProductService()
    return _product_service


def get_order_service() -> OrderService:
    global _order_service
    if _order_service is None:
        _order_service = OrderService()
    return _order_service


def get_payment_service() -> PaymentService:
    global _payment_service
    if _payment_service is None:
        _payment_service = PaymentService()
    return _payment_service


def get_notification_service() -> NotificationService:
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService()
    return _notification_service


def get_document_service() -> DocumentService:
    global _document_service
    if _document_service is None:
        _document_service = DocumentService()
    return _document_service


def get_boq_generator() -> BOQGenerator:
    global _boq_generator
    if _boq_generator is None:
        _boq_generator = BOQGenerator()
    return _boq_generator


def get_ai_service() -> AIService:
    global _ai_service
    if _ai_service is None:
        _ai_service = AIService()
    return _ai_service


def get_storage_service() -> StorageService:
    global _storage_service
    if _storage_service is None:
        _storage_service = StorageService()
    return _storage_service


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_api_key: Optional[str] = Header(None),
    request: Optional[Request] = None,
) -> Dict[str, Any]:
    """Get the current authenticated user from JWT or API key."""
    auth_service = get_auth_service()

    # Try JWT token from Authorization header first
    if credentials:
        token = credentials.credentials
        user = await auth_service.verify_token(token)
        if user:
            return user

    # Try JWT token from httpOnly cookie as fallback
    if request and not credentials:
        token = request.cookies.get("access_token")
        if token:
            user = await auth_service.verify_token(token)
            if user:
                return user

    # Try API key
    if x_api_key:
        # In production, validate API key against database
        return {
            "userId": "api-user",
            "email": "api@burncost.com",
            "role": "api",
            "type": "api_key",
        }

    # Return anonymous user for public endpoints
    return {
        "userId": "anonymous",
        "email": "",
        "role": "anonymous",
        "type": "anonymous",
    }


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[Dict[str, Any]]:
    """Get current user if authenticated, None otherwise."""
    try:
        return await get_current_user(credentials)
    except Exception:
        return None


async def require_admin(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Require admin role for the endpoint."""
    if current_user.get("role") not in ["admin", "superadmin"]:
        raise HTTPException(
            status_code=403,
            detail="Admin access required",
        )
    return current_user


async def require_vendor(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Require vendor role for the endpoint."""
    if current_user.get("role") not in ["vendor", "admin", "superadmin"]:
        raise HTTPException(
            status_code=403,
            detail="Vendor access required",
        )
    return current_user
