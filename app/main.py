import uvicorn   
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError, HTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException
import time
import logging

from app.config import settings
from app.core.exceptions import CustomException
from app.core.middleware import LoggingMiddleware, RateLimitMiddleware
from app.api.v1.router import api_router

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# control pyongo logging verbosity
logging.getLogger('pymongo').setLevel(logging.WARNING)
logging.getLogger('pymongo.topology').setLevel(logging.WARNING)
logging.getLogger('pymongo.serverSelection').setLevel(logging.WARNING)
logging.getLogger('pymongo.connection').setLevel(logging.WARNING)

# Create FastAPI app
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Building Materials, E-commerce and BOQ Management Platform",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc",
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(LoggingMiddleware)
# app.add_middleware(RateLimitMiddleware)


# Exception Handlers
@app.exception_handler(CustomException)
async def custom_exception_handler(request: Request, exc: CustomException):
    logger.error(f"CustomException: {exc.message} - {exc.details}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "code": exc.error_code,
                "message": exc.message,
                "details": exc.details
            }
        },
    )


@app.exception_handler(HTTPException)
async def fastapi_http_exception_handler(request: Request, exc: HTTPException):
    ### handle exceptions from endpoints
    logger.warning(f"HTTPException: {exc.status_code} - {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "code": f"HTTP_{exc.status_code}",
                "message": exc.detail
            }
        },
    )


@app.exception_handler(StarletteHTTPException)
async def starlette_http_exception_handler(request: Request, exc: StarletteHTTPException):
    ### handle exceptions from middleware/starlette
    logger.error(f"HTTPException: {exc.status_code} - {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "code": f"HTTP_{exc.status_code}",
                "message": exc.detail,
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    ### handle pydantic validation errors here
    # Format errors to be JSON serializable
    formatted_errors = []
    for error in exc.errors():
        # Extract only JSON-serializable fields
        formatted_error = {
            "loc": list(error.get("loc", [])),
            "msg": error.get("msg", ""),
            "type": error.get("type", ""),
        }
        
        # Handle the input field carefully
        input_value = error.get("input")
        if input_value is not None:
            # Convert to string if it's not already JSON serializable
            try:
                import json
                json.dumps(input_value)
                formatted_error["input"] = input_value
            except (TypeError, ValueError):
                formatted_error["input"] = str(input_value)
        
        # Extract the actual error message from ctx if it exists
        if "ctx" in error and "error" in error["ctx"]:
            error_obj = error["ctx"]["error"]
            if isinstance(error_obj, ValueError):
                formatted_error["msg"] = str(error_obj)
        
        formatted_errors.append(formatted_error)
    
    logger.error(f"ValidationError: {formatted_errors}")
    
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "success": False,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": formatted_errors
            }
        },
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    ### handle any other error here
    logger.exception(f"Unhandled exception: {str(exc)}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred",
                "details": str(exc) if settings.DEBUG else None
            }
        },
    )


# Startup and Shutdown Events
### Initialize services on startup"""
@app.on_event("startup")
async def startup_event():
    logger.info("Starting up application...")
    
    # Test database connections
    from app.core.database import test_db_connection, test_mongo_connection
    
    if not await test_db_connection():
        logger.error("PostgreSQL connection failed!")
    else:
        logger.info("PostgreSQL connected successfully")
    
    if not await test_mongo_connection():
        logger.error("MongoDB connection failed!")
    else:
        logger.info("MongoDB connected successfully")
    
    logger.info("Application startup complete")

    logger.info("=" * 50)
    logger.info("Starting up application...")
    logger.info(f"Environment: {'Development' if settings.DEBUG else 'Production'}")
    logger.info(f"Version: {settings.VERSION}")
    logger.info("=" * 50)


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down application...")
    
    from app.core.database import close_db_connections
    await close_db_connections()
    
    logger.info("Application shutdown complete")


# Health Check Endpoints
@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "version": settings.VERSION
    }

### Detailed health check including database connections
@app.get("/health/detailed", tags=["Health"])
async def detailed_health_check():
    from app.core.database import test_db_connection, test_mongo_connection
    
    postgres_healthy = await test_db_connection()
    mongo_healthy = await test_mongo_connection()
    
    return {
        "Project": settings.PROJECT_NAME,
        "status": "healthy" if (postgres_healthy and mongo_healthy) else "degraded",
        "timestamp": time.time(),
        "version": settings.VERSION,
        "services": {
            "postgresql": "healthy" if postgres_healthy else "unhealthy",
            "mongodb": "healthy" if mongo_healthy else "unhealthy",
        }
    }


# Include API Router
app.include_router(api_router, prefix=settings.API_V1_STR)


# Root endpoint
@app.get("/", tags=["Root"])
async def root():
    return {
        "message": "Burncost API Platform",
        "version": settings.VERSION,
        "docs": f"{settings.API_V1_STR}/docs"
    }


if __name__ == "__main__":     
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.UVIPORT,
        reload=settings.DEBUG,
        workers=4 if not settings.DEBUG else 1,
        log_level=settings.LOG_LEVEL.lower()
    )
    