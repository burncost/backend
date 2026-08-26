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
    admin_phase1,
    admin_negotiation,
    admin_fraud,
    admin_price_boq,
    admin_dispute,
    admin_settings,
    negotiations,
    disputes,
    demand_alerts,
    vendor_reviews,
    admin_shipping,
    reports,
    ai,
    projects,
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
api_router.include_router(admin_phase1.router, prefix="/admin", tags=["Admin"])
api_router.include_router(admin_negotiation.router, prefix="/admin", tags=["Admin Negotiation"])
api_router.include_router(admin_fraud.router, prefix="/admin", tags=["Admin Fraud"])
api_router.include_router(admin_price_boq.router, prefix="/admin", tags=["Admin Price/BOQ"])
api_router.include_router(admin_dispute.router, prefix="/admin", tags=["Admin Dispute"])
api_router.include_router(admin_settings.router, prefix="/admin", tags=["Admin Settings"])
api_router.include_router(admin_shipping.router, prefix="/admin", tags=["Admin Shipping"])
api_router.include_router(negotiations.router, prefix="/negotiations", tags=["Negotiations"])
api_router.include_router(disputes.router, prefix="/disputes", tags=["Disputes"])
api_router.include_router(demand_alerts.router, prefix="/demand-alerts", tags=["Demand Alerts"])
api_router.include_router(vendor_reviews.router, prefix="/vendors", tags=["Vendor Reviews"])
api_router.include_router(reports.router, prefix="/reports", tags=["Reports"])
api_router.include_router(ai.router, prefix="/ai", tags=["AI Intelligence"])
api_router.include_router(projects.router, prefix="/projects", tags=["Projects"])
