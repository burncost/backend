from fastapi import APIRouter

from app.api.v1.endpoints import (
    tiers,
    auth,
    users,
    products,
    vendors,
    boqs,
    documents,
    orders,
    categories,
    cart,
    reviews,
    analytics,
    notifications,
    payments,
    suppliers,
    brands,
    prices,
    material_rates,
    promos,
    chat,
    tokens,
    admin,
    admin_actions,
    reports,
)

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(vendors.router, prefix="/vendors", tags=["Vendors"])
api_router.include_router(tiers.router, prefix="/vendors", tags=["Vendor Tiers"])
api_router.include_router(products.router, prefix="/products", tags=["Products"])
api_router.include_router(boqs.router, prefix="/boqs", tags=["BOQs"])
api_router.include_router(documents.router, prefix="/documents", tags=["Documents"])
api_router.include_router(orders.router, prefix="/orders", tags=["Orders"])
api_router.include_router(categories.router, prefix="/categories", tags=["Categories"])
api_router.include_router(cart.router, prefix="/cart", tags=["Cart"])
api_router.include_router(reviews.router, prefix="/reviews", tags=["Reviews"])
api_router.include_router(analytics.router, prefix="/dashboard", tags=["Analytics"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["Notifications"])
api_router.include_router(payments.router, prefix="/payments", tags=["Payments"])
api_router.include_router(suppliers.router, prefix="/suppliers", tags=["Suppliers"])
api_router.include_router(brands.router, prefix="/brands", tags=["Brands"])
api_router.include_router(prices.router, prefix="/prices", tags=["Prices"])
api_router.include_router(material_rates.router, prefix="/materials/rates", tags=["Material Rates"])
api_router.include_router(promos.router, prefix="/promos", tags=["Promotions"])
api_router.include_router(chat.router, prefix="", tags=["Chat"])
api_router.include_router(tokens.router, prefix="/tokens", tags=["Tokens"])
api_router.include_router(admin.router, prefix="/admin", tags=["Admin"])
api_router.include_router(admin_actions.router, prefix="/admin", tags=["Admin Actions"])
api_router.include_router(reports.router, prefix="/reports", tags=["Reports"])
