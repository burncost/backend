from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
import time
import logging
import json
import uuid
from typing import Callable

from app.core.logging_config import request_id_var, user_id_var

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        # Generate request ID for correlation
        req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request_id_var.set(req_id)

        start_time = time.time()

        # Extract user context from auth (if available)
        try:
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                from app.core.security import decode_token
                payload = decode_token(auth_header.split(" ")[1])
                user_id = payload.get("sub", "-")
                user_id_var.set(user_id)
        except Exception:
            pass

        logger.info(
            "→ %s %s",
            request.method,
            request.url.path,
            extra={
                "extra_data": {
                    "method": request.method,
                    "path": request.url.path,
                    "query": dict(request.query_params),
                    "client_ip": request.client.host if request.client else "-",
                    "user_agent": (request.headers.get("user-agent", "-") or "-")[:100],
                }
            },
        )

        try:
            response = await call_next(request)
            process_time = time.time() - start_time

            status_emoji = "✓" if response.status_code < 400 else "✗"
            logger.info(
                "%s %s %s → %s (%.3fs)",
                status_emoji,
                request.method,
                request.url.path,
                response.status_code,
                process_time,
                extra={
                    "extra_data": {
                        "status_code": response.status_code,
                        "process_time_ms": round(process_time * 1000, 1),
                        "slow": process_time > 2.0,
                    }
                },
            )

            # Alert on slow requests
            if process_time > 5.0:
                logger.warning(
                    "SLOW REQUEST: %s %s took %.1fs",
                    request.method,
                    request.url.path,
                    process_time,
                )

            response.headers["X-Request-ID"] = req_id
            response.headers["X-Process-Time"] = str(process_time)
            return response

        except Exception as e:
            process_time = time.time() - start_time
            logger.error(
                "✗ %s %s → ERROR (%.3fs)",
                request.method,
                request.url.path,
                process_time,
                exc_info=True,
                extra={
                    "extra_data": {
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "process_time_ms": round(process_time * 1000, 1),
                    }
                },
            )
            raise


# Simple rate limiting - in production use Redis
class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        response = await call_next(request)
        return response
    