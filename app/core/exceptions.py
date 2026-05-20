from typing import Any, Dict, Optional


### Base custom exception class
class CustomException(Exception):
    def __init__(
        self,
        message: str,
        status_code: int = 400,
        error_code: str = "CUSTOM_ERROR",
        details: Optional[Dict[str, Any]] = None
    ):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.details = details or {}
        super().__init__(self.message)

### Resource not found exception
class NotFoundException(CustomException):
    def __init__(self, message: str = "Resource not found", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=404,
            error_code="NOT_FOUND",
            details=details
        )

### Unauthorized access exception
class UnauthorizedException(CustomException):
    def __init__(self, message: str = "Unauthorized", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=401,
            error_code="UNAUTHORIZED",
            details=details
        )

### Forbidden access exception
class ForbiddenException(CustomException):
    def __init__(self, message: str = "Forbidden", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=403,
            error_code="FORBIDDEN",
            details=details
        )


class BadRequestException(CustomException):
    """Bad request exception"""
    
    def __init__(self, message: str = "Bad request", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=400,
            error_code="BAD_REQUEST",
            details=details
        )

### Conflict exception (e.g., duplicate resource)
class ConflictException(CustomException):
    def __init__(self, message: str = "Resource conflict", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=409,
            error_code="CONFLICT",
            details=details
        )

### Validation error exception
class ValidationException(CustomException):    
    def __init__(self, message: str = "Validation failed", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=422,
            error_code="VALIDATION_ERROR",
            details=details
        )

### Internal server error exception
class InternalServerException(CustomException):   
    def __init__(self, message: str = "Internal server error", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=500,
            error_code="INTERNAL_SERVER_ERROR",
            details=details
        )

### Service unavailable exception
class ServiceUnavailableException(CustomException):    
    def __init__(self, message: str = "Service unavailable", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=503,
            error_code="SERVICE_UNAVAILABLE",
            details=details
        )