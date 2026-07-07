"""Custom exceptions for the Burncost API."""
from typing import Any, Dict, Optional


class AppException(Exception):
    """Base application exception."""

    def __init__(self, message: str, status_code: int = 500, details: Optional[Dict[str, Any]] = None):
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)


class NotFoundException(AppException):
    """Resource not found exception."""

    def __init__(self, resource: str, resource_id: Optional[str] = None):
        message = f"{resource} not found"
        if resource_id:
            message += f": {resource_id}"
        super().__init__(message, status_code=404)


class UnauthorizedException(AppException):
    """Unauthorized access exception."""

    def __init__(self, message: str = "Unauthorized"):
        super().__init__(message, status_code=401)


class ForbiddenException(AppException):
    """Forbidden access exception."""

    def __init__(self, message: str = "Forbidden"):
        super().__init__(message, status_code=403)


class ValidationException(AppException):
    """Validation error exception."""

    def __init__(self, message: str, errors: Optional[Dict[str, Any]] = None):
        super().__init__(message, status_code=422, details=errors or {})


class ConflictException(AppException):
    """Resource conflict exception (e.g., duplicate)."""

    def __init__(self, message: str):
        super().__init__(message, status_code=409)


class PaymentException(AppException):
    """Payment processing exception."""

    def __init__(self, message: str, payment_ref: Optional[str] = None):
        details = {"paymentReference": payment_ref} if payment_ref else {}
        super().__init__(message, status_code=402, details=details)


class RateLimitException(AppException):
    """Rate limit exceeded exception."""

    def __init__(self, message: str = "Rate limit exceeded. Try again later."):
        super().__init__(message, status_code=429)


class ExternalServiceException(AppException):
    """External service failure exception."""

    def __init__(self, service: str, message: str):
        super().__init__(
            f"External service '{service}' error: {message}",
            status_code=502,
            details={"service": service}
        )


class DatabaseException(AppException):
    """Database operation exception."""

    def __init__(self, message: str, operation: Optional[str] = None):
        details = {"operation": operation} if operation else {}
        super().__init__(message, status_code=500, details=details)
