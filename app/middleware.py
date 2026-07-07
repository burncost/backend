"""Middleware for the Burncost API."""
from typing import Callable, Optional
import time
import logging
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all incoming requests and their durations."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = str(uuid.uuid4())[:8]
        start_time = time.time()

        # Add request ID to request state
        request.state.request_id = request_id

        logger.info(f"[{request_id}] {request.method} {request.url.path}")

        response = await call_next(request)

        duration = time.time() - start_time
        logger.info(
            f"[{request_id}] {request.method} {request.url.path} "
            f"-> {response.status_code} ({duration:.3f}s)"
        )

        response.headers["X-Request-ID"] = request_id
        return response


class CORSMiddleware(BaseHTTPMiddleware):
    """CORS middleware for cross-origin requests."""

    def __init__(self, app: ASGIApp, allowed_origins: Optional[list] = None):
        super().__init__(app)
        self.allowed_origins = allowed_origins or [
            "http://localhost:5173",
            "http://localhost:3000",
            "https://burncost.vercel.app",
            "https://burncost.com",
        ]

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        origin = request.headers.get("origin", "")

        if request.method == "OPTIONS":
            response = Response()
        else:
            response = await call_next(request)

        if origin in self.allowed_origins or "*" in self.allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = (
                "Content-Type, Authorization, X-Request-ID, X-API-Key"
            )
            response.headers["Access-Control-Max-Age"] = "3600"

        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiting middleware."""

    def __init__(self, app: ASGIApp, max_requests: int = 100, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests = {}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        client_ip = request.client.host if request.client else "unknown"
        current_time = time.time()

        # Clean old entries
        self._requests = {
            ip: times for ip, times in self._requests.items()
            if times[-1] > current_time - self.window_seconds
        }

        # Check rate limit
        if client_ip in self._requests:
            client_requests = self._requests[client_ip]
            if len(client_requests) >= self.max_requests:
                response = Response(
                    content='{"detail":"Rate limit exceeded. Try again later."}',
                    status_code=429,
                    media_type="application/json",
                )
                response.headers["Retry-After"] = str(self.window_seconds)
                return response
            client_requests.append(current_time)
        else:
            self._requests[client_ip] = [current_time]

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to add security headers to all responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

        return response
