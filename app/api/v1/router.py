from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    users,
    products,
    vendors,
    boqs,
    documents,
)

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(vendors.router, prefix="/vendors", tags=["Vendors"]) 
api_router.include_router(products.router, prefix="/products", tags=["Products"])
api_router.include_router(boqs.router, prefix="/boqs", tags=["BOQs"])
api_router.include_router(documents.router, prefix="/documents", tags=["Documents"])