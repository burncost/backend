from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
import time
import logging
import json
from typing import Callable

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        start_time = time.time()
        
        # Log request
        logger.info(f"→ {request.method} {request.url.path}")
        logger.debug(f"  Query params: {dict(request.query_params)}")
        
        # Log request body for non-GET requests (be careful with sensitive data)
        if request.method != "GET":
            try:
                body = await request.body()
                if body:
                    logger.debug(f"  Request body size: {len(body)} bytes")
            except:
                pass
        
        # Process request
        try:
            response = await call_next(request)
            process_time = time.time() - start_time
            
            # Log response
            status_emoji = "✓" if response.status_code < 400 else "✗"
            logger.info(
                f"{status_emoji} {request.method} {request.url.path} "
                f"→ {response.status_code} ({process_time:.3f}s)"
            )
            
            response.headers["X-Process-Time"] = str(process_time)
            return response
            
        except Exception as e:
            process_time = time.time() - start_time
            logger.error(
                f"✗ {request.method} {request.url.path} "
                f"→ ERROR ({process_time:.3f}s): {str(e)}",
                exc_info=True
            )
            raise


# Simple rate limiting - in production use Redis
class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        response = await call_next(request)
        return response
    