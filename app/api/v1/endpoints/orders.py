from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks, Body
from app.api.deps import get_current_vendor, get_current_user, get_current_verified_vendor
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload, joinedload
from uuid import UUID
from typing import Optional
from datetime import datetime, timedelta
from decimal import Decimal
import random
import string
import re

from pydantic import BaseModel

from app.schemas.order import OrderListUI, OrderResponse, OrderListResponse
from app.models.order import Order, OrderItem, OrderStatus, PaymentStatus
from app.models.cart import CartItem
from app.models.product import Product
from app.models.notification import Notification
from app.models.vendor import Vendor
from app.models.user import User, UserProfile
from app.models.delivery import DeliveryJob
from app.models.delivery_event import DeliveryJobEvent
from app.models.delivery_proof import DeliveryProof
from app.models.address import CustomerAddress
from app.models.negotiation import Negotiation
# Reuse the single source of truth for the no-GPS buyer receipt-confirmation gate
# (same rule the delivery engine serialises to the buyer UI).
from app.api.v1.endpoints.deliveries import (
    _buyer_confirm_hint,
    _can_buyer_confirm,
    _credit_driver_earnings,
)
from app.core.database import get_db
from app.services.notification_service import NotificationService
from app.services.shipping_service import ShippingService
# Used by the escrow release below. Was referenced without being imported, so the
# transfer raised NameError and was swallowed by the function's broad `except`
# (the payout silently never happened).
from app.services.payment_service import PaymentService
from app.models.category import Category
from app.services import pricing_service as _pricing
from app.services import runtime_settings as _runtime_settings
from app.services import settlement_service as _settlement_service
from app.crud import promo as promo_crud
from app.services import promo_service

class VendorOrderUpdate(BaseModel):
    status: str
    driverName: str = ""
    driverPhone: str = ""


### Optional delivery details captured during checkout (Phase 13)
class OrderCreateRequest(BaseModel):
    shipping_address: Optional[str] = None
    shipping_city: Optional[str] = None
    shipping_state: Optional[str] = None
    shipping_phone: Optional[str] = None
    # When set, the order links to one of the user's saved delivery addresses.
    shipping_address_id: Optional[str] = None
    # Coordinates picked on the checkout map (used for live delivery tracking).
    shipping_latitude: Optional[float] = None
    shipping_longitude: Optional[float] = None


class OrderCompleteRequest(BaseModel):
    shipping_address: Optional[str] = None
    shipping_city: Optional[str] = None
    shipping_state: Optional[str] = None
    shipping_phone: Optional[str] = None
    shipping_address_id: Optional[str] = None
    shipping_latitude: Optional[float] = None
    shipping_longitude: Optional[float] = None

import logging

router = APIRouter()
logger = logging.getLogger(__name__)


def _generate_order_number() -> str:
    """Generate a unique order number like 'BC-20260703-XXXX'."""
    date_part = datetime.utcnow().strftime("%Y%m%d")
    random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"BC-{date_part}-{random_part}"


# Order rows (drafts) are created the moment a shopper clicks "Proceed to
# Checkout", before payment. Nothing used to expire them, so backing out and
# re-checking out stacked "phantom" order rows - and every extra
# PENDING_PAYMENT row ate one of the dashboard's "active order" slots. These
# helpers keep the draft-on-checkout flow (the payment screen depends on it)
# while making it safe.
_DRAFT_STATUSES = (OrderStatus.PENDING_PAYMENT, OrderStatus.PAYMENT_FAILED)
_DRAFT_TTL = timedelta(hours=24)          # auto-expire abandoned drafts
_SUPERSEDE_AFTER = timedelta(minutes=5)   # cancel older drafts on re-checkout


async def _release_promo(db: AsyncSession, order: Order) -> None:
    """Return a consumed promo use to its pool when a draft is abandoned.

    The promo counter is bumped at order creation (not payment), so cancelling
    or deleting an unpaid order must give the use back - otherwise a retry
    silently burns the code's remaining uses.
    """
    code = getattr(order, "promo_code", None)
    if not code:
        return
    promo = await promo_crud.get_by_code(db, code)
    if promo and (promo.current_uses or 0) > 0:
        promo.current_uses -= 1


async def _cancel_draft(db: AsyncSession, order: Order, reason: str) -> None:
    """Soft-cancel an unpaid draft order and release anything it reserved."""
    order.status = OrderStatus.CANCELLED
    order.admin_notes = reason
    await _release_promo(db, order)


async def _expire_stale_drafts(db: AsyncSession, user_id) -> int:
    """Soft-cancel this user's unpaid drafts older than the TTL."""
    cutoff = datetime.utcnow() - _DRAFT_TTL
    rows = await db.execute(
        select(Order).where(
            Order.user_id == user_id,
            Order.status.in_([s.value for s in _DRAFT_STATUSES]),
            Order.created_at < cutoff,
        )
    )
    expired = 0
    for order in rows.scalars().all():
        await _cancel_draft(db, order, "Expired: checkout was not completed in time")
        expired += 1
    return expired


async def _supersede_older_drafts(db: AsyncSession, user_id) -> int:
    """Cancel this user's other unpaid drafts so a re-checkout can't stack rows.

    Only drafts older than a short grace period are touched, so the draft the
    shopper is actively paying for (created seconds ago) is never removed.
    """
    cutoff = datetime.utcnow() - _SUPERSEDE_AFTER
    rows = await db.execute(
        select(Order).where(
            Order.user_id == user_id,
            Order.status.in_([s.value for s in _DRAFT_STATUSES]),
            Order.created_at < cutoff,
        )
    )
    superseded = 0
    for order in rows.scalars().all():
        await _cancel_draft(db, order, "Superseded by a newer checkout")
        superseded += 1
    return superseded


async def _resolve_shipping_address(
    db: AsyncSession, user: User, preferred_id: Optional[str] = None
) -> Optional[CustomerAddress]:
    """Return the delivery address to attach to an order.

    Uses the explicitly chosen saved address when supplied and owned by the
    user; otherwise falls back to their default address, then their most recent
    one. Returns None when the user has no saved addresses.
    """
    if preferred_id:
        try:
            aid = UUID(str(preferred_id))
        except (ValueError, TypeError):
            aid = None
        if aid:
            row = await db.execute(
                select(CustomerAddress).where(
                    CustomerAddress.id == aid, CustomerAddress.user_id == user.id
                )
            )
            addr = row.scalar_one_or_none()
            if addr:
                return addr
    row = await db.execute(
        select(CustomerAddress)
        .where(CustomerAddress.user_id == user.id)
        .order_by(CustomerAddress.is_default.desc(), CustomerAddress.created_at.desc())
    )
    return row.scalars().first()


async def _ensure_shipping_address(
    db: AsyncSession, user: User, details, resolved: Optional[CustomerAddress]
) -> Optional[CustomerAddress]:
    """Guarantee the order has a customer_addresses row carrying coordinates.

    Draft orders used to keep delivery details only on the user profile, so the
    dispatch engine had no `shipping_address` and therefore no destination
    lat/lng - the live map rendered the driver with nowhere to go. When checkout
    supplies a pinned location we persist a real address row (or backfill the
    coordinates onto the saved one it already had).

    Returns None when there is nothing to attach (no address and no pin), which
    keeps the previous behaviour for orders that still lack details.
    """
    lat = getattr(details, "shipping_latitude", None)
    lng = getattr(details, "shipping_longitude", None)

    if resolved is not None:
        # Backfill coordinates onto an older saved address that has none.
        if resolved.latitude is None and lat is not None and lng is not None:
            resolved.latitude = Decimal(str(lat))
            resolved.longitude = Decimal(str(lng))
        return resolved

    if not getattr(details, "shipping_address", None) or lat is None or lng is None:
        return None

    address = CustomerAddress(
        user_id=user.id,
        address_type="home",
        contact_phone=getattr(details, "shipping_phone", None) or user.phone_number,
        address_line1=details.shipping_address,
        city=getattr(details, "shipping_city", None),
        state=getattr(details, "shipping_state", None),
        latitude=Decimal(str(lat)),
        longitude=Decimal(str(lng)),
        # Never steal the default flag from a saved address.
        is_default=False,
    )
    db.add(address)
    await db.flush()
    return address


async def _agreed_prices_for_builder(db: AsyncSession, builder_id, product_ids: list) -> dict:
    """Map product_id -> agreed unit price for this builder's `price_agreed` deals.

    Strictly scoped to the builder who is a party to the negotiation, so an agreed
    price only ever auto-applies to that builder's own checkout. Returns the most
    recently agreed price per product; products without an agreement are absent.
    """
    if not product_ids:
        return {}
    rows = (await db.execute(
        select(Negotiation)
        .where(
            Negotiation.builder_id == builder_id,
            Negotiation.status == "price_agreed",
            Negotiation.agreed_price.isnot(None),
            Negotiation.product_id.in_(list(product_ids)),
        )
        .order_by(Negotiation.agreed_at.asc().nulls_first())
    )).scalars().all()
    agreed: dict = {}
    for n in rows:
        if n.product_id is not None and n.agreed_price is not None:
            agreed[n.product_id] = float(n.agreed_price)
    return agreed


### Create order from cart
@router.post("/create", status_code=status.HTTP_201_CREATED)
async def create_order(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    promo_code: Optional[str] = Query(None, max_length=50),
    details: Optional[OrderCreateRequest] = Body(None),
):
    user_id = current_user.id

    # Fetch cart items with product details
    cart_result = await db.execute(
        select(CartItem).where(CartItem.user_id == user_id)
    )
    cart_items = cart_result.scalars().all()

    if not cart_items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your cart is empty. Add items before placing an order."
        )

    # Build order items and calculate totals
    order_items = []
    subtotal = 0.0

    # Two-party negotiated final price: only auto-applies for this builder (the
    # party who agreed it) and overrides the catalogue price below.
    agreed_prices = await _agreed_prices_for_builder(
        db, user_id, [ci.product_id for ci in cart_items]
    )

    for cart_item in cart_items:
        product_result = await db.execute(
            select(Product).where(Product.id == cart_item.product_id)
        )
        product = product_result.scalar_one_or_none()

        if not product:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Product '{cart_item.product_id}' is no longer available."
            )

        if product.status != "active":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Product '{product.name}' is not available for purchase."
            )

        if product.quantity < cart_item.quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Insufficient stock for '{product.name}'. Available: {product.quantity}."
            )

        base_price = float(product.base_price)
        unit_price = float(product.discount_price or product.base_price)
        # Agreed negotiation price (builder-only) overrides the catalogue price.
        if product.id in agreed_prices:
            unit_price = agreed_prices[product.id]
        item_savings = round((base_price - unit_price) * cart_item.quantity, 2)
        total_price = unit_price * cart_item.quantity
        subtotal += total_price

        order_items.append({
            "product_id": product.id,
            "category_id": product.category_id,
            "vendor_id": product.vendor_id,
            "product_name": product.name,
            "sku": product.sku,
            "quantity": cart_item.quantity,
            "unit_price": unit_price,
            "total_price": total_price,
            "base_price": base_price,
        })

    # ── Pricing: platform margin (fee_model aware) + VAT via pricing_service ──
    # Admin overrides live in `system_settings`; refresh the cached snapshot so a
    # change saved in the admin panel is honoured on the very next checkout.
    await _runtime_settings.refresh(db)

    fees = await _pricing.load_category_fees(
        db, [item_data.get("category_id") for item_data in order_items]
    )

    # Calculate shipping (dev: flat rate, prod: zone+weight)
    shipping_service = ShippingService()
    shipping_result = await shipping_service.calculate_shipping(
        db=db,
        vendor_id=order_items[0]["vendor_id"],
        items=[{"base_price": i["base_price"], "discount_price": i["unit_price"], "quantity": i["quantity"]} for i in order_items],
    )
    shipping_fee = shipping_result["shipping_fee"]

    # Product discount (base_price - unit_price)
    discount_amount = round(sum(item["base_price"] * item["quantity"] - item["total_price"] for item in order_items), 2)

    # Server-side promo code validation (scope-aware)
    promo_discount = 0.0
    promo_applied = None
    if promo_code:
        promo = await promo_crud.get_by_code(db, promo_code)
        if promo and promo.is_active:
            # Per-buyer cap: 0 means unlimited. Counts this buyer's non-cancelled
            # orders carrying the code, so a released draft frees the slot again.
            per_user_limit = int(promo.max_uses_per_user or 0)
            per_user_ok = True
            if per_user_limit > 0:
                per_user_ok = (await promo_crud.count_user_uses(db, promo.code, user_id)) < per_user_limit

            # Scope coverage (platform/vendor/product/category), minimum order and
            # the flat-amount cap all live in the shared helper - the cart
            # pre-checks a code with these exact rules, so a code shown as
            # applicable there is never silently dropped here.
            promo_discount, reason = promo_service.evaluate(
                promo, order_items, per_user_ok=per_user_ok
            )
            if reason is None:
                promo.current_uses += 1
                promo_applied = {
                    "code": promo.code,
                    "discount_amount": float(promo.discount_amount),
                    "amount": promo_discount,
                }
                logger.info(f"Promo code '{promo_code}' applied: ₦{float(promo.discount_amount):,.2f} off, saved ₦{promo_discount:,.2f}")
            else:
                logger.warning(f"Promo code '{promo_code}' validation failed: {reason}")
        else:
            logger.warning(f"Promo code '{promo_code}' not found or inactive")

    # Total (platform margin, VAT base, discounts and grand total - pricing_service)
    pricing = _pricing.compute_order_pricing(
        order_items,
        fees,
        shipping_fee=shipping_fee,
        discount_amount=discount_amount,
        promo_discount=promo_discount,
    )
    subtotal = float(pricing.subtotal)
    platform_fee = float(pricing.platform_fee)       # charged to the buyer
    vendor_fee = float(pricing.vendor_fee)           # deducted from the vendor payout
    platform_fee_breakdown = pricing.breakdown
    vat_amount = float(pricing.vat_amount)
    total_amount = float(pricing.total_amount)

    # Phase 13: delivery completeness + soft email verification. The order is
    # still persisted as a draft; missing details / unverified email are returned
    # to the client instead of failing the request.
    phone = current_user.phone_number or None
    location = None
    if current_user.profile and current_user.profile.location:
        location = current_user.profile.location
    if details is not None:
        if details.shipping_phone:
            phone = details.shipping_phone
        addr = ", ".join(p for p in [details.shipping_city, details.shipping_state] if p) or (details.shipping_address or "")
        if addr:
            location = addr

    # Link the order to a saved delivery address (explicit choice, else default).
    resolved_address = await _resolve_shipping_address(
        db, current_user, details.shipping_address_id if details else None
    )
    if details is not None:
        # Persist the pinned checkout location so dispatch has a real drop-off.
        resolved_address = await _ensure_shipping_address(db, current_user, details, resolved_address)
    if resolved_address:
        if not phone:
            phone = resolved_address.contact_phone or phone
        if not location:
            location = ", ".join(
                p for p in [resolved_address.address_line1, resolved_address.city, resolved_address.state] if p
            ) or location

    missing_fields = []
    if not phone:
        missing_fields.append("phone")
    if not location:
        missing_fields.append("address")
    requires_verification = not bool(current_user.email_verified)

    # Keep the draft flow from piling up: expire stale drafts and cancel any
    # older unpaid draft a previous checkout left behind (the phantom order bug).
    await _expire_stale_drafts(db, user_id)
    await _supersede_older_drafts(db, user_id)

    # Create the order
    order = Order(
        order_number=_generate_order_number(),
        user_id=user_id,
        status=OrderStatus.PENDING_PAYMENT,
        subtotal=subtotal,
        shipping_fee=shipping_fee,
        tax_amount=vat_amount,
        discount_amount=discount_amount,
        total_amount=total_amount,
        payment_status=PaymentStatus.PENDING,
        promo_code=promo_applied["code"] if promo_applied else None,
        promo_discount=float(promo_discount),
        shipping_address_id=resolved_address.id if resolved_address else None,
    )
    db.add(order)
    await db.flush()  # Get order.id

    # Create order items
    for item_data in order_items:
        order_item = OrderItem(
            order_id=order.id,
            product_id=item_data["product_id"],
            vendor_id=item_data["vendor_id"],
            product_name=item_data["product_name"],
            sku=item_data["sku"],
            quantity=item_data["quantity"],
            unit_price=item_data["unit_price"],
            total_price=item_data["total_price"],
        )
        db.add(order_item)

    # --- Soft tier transaction-cap enforcement (Tier 0-3) ---
    from app.services.vendor_tier_service import tier_cap as v_tier_cap
    _DISALLOWED = {"suspended", "deactivated", "rejected"}
    for vid in {i["vendor_id"] for i in order_items}:
        vr = (await db.execute(select(Vendor).where(Vendor.id == vid))).scalar_one_or_none()
        if not vr:
            continue
        vstat = vr.verification_status.value if hasattr(vr.verification_status, "value") else str(vr.verification_status or "")
        if vstat in _DISALLOWED:
            order.status = "on_hold"
            db.add(Notification(user_id=vr.user_id, type="verification",
                title="Account not active",
                message="Your account is not active for selling. Please contact support.", read=False,
                link="/supplier-dashboard/settings"))
            continue
        tier = vr.verification_tier or "starter"
        cap = v_tier_cap(tier)
        order_share = sum(i["total_price"] for i in order_items if str(i["vendor_id"]) == str(vid))
        if cap is not None and (float(vr.transaction_volume or 0) + order_share) > cap:
            order.status = "on_hold"
            cap_label = f"N{cap:,.0f}"
            db.add(Notification(user_id=vr.user_id, type="verification",
                title="Transaction limit reached",
                message=f"You are at your {tier} cap of {cap_label}. Complete the next step to keep selling.", read=False,
                link="/supplier-dashboard/settings"))
    # Clear the cart
    for cart_item in cart_items:
        await db.delete(cart_item)

    # Create notification
    notification = Notification(
        user_id=user_id,
        type="order",
        title="Order Placed Successfully",
        message=f"Your order {order.order_number} has been placed. Total: ₦{total_amount:,.2f}",
        link="/dashboard/orders",
    )
    db.add(notification)

    # Persist any supplied delivery details to the user profile (progressive disclosure).
    if details is not None:
        if details.shipping_phone:
            current_user.phone_number = details.shipping_phone
        loc = ", ".join(p for p in [details.shipping_city, details.shipping_state] if p) or (details.shipping_address or "")
        if loc and current_user.profile:
            current_user.profile.location = loc

    await db.commit()
    await db.refresh(order)

    logger.info(f"Order {order.order_number} created for user {current_user.id}")

    requires_completion = bool(missing_fields) or requires_verification

    return {
        "success": True,
        "message": "Order created successfully",
        "order_id": str(order.id),
        "order_number": order.order_number,
        "total_amount": total_amount,
        "requires_completion": requires_completion,
        "missing_fields": missing_fields,
        "requires_verification": requires_verification,
    }


### Complete delivery details for a draft order (Phase 13)
@router.post("/{order_id}/complete")
async def complete_order_details(
    order_id: str,
    payload: OrderCompleteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        uid = UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order ID")

    result = await db.execute(select(Order).where(Order.id == uid))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if str(order.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized to update this order")

    # Persist delivery details to the user profile.
    if payload.shipping_phone:
        current_user.phone_number = payload.shipping_phone
    loc = ", ".join(p for p in [payload.shipping_city, payload.shipping_state] if p) or (payload.shipping_address or "")
    if loc and current_user.profile:
        current_user.profile.location = loc

    # Link the order to a saved delivery address (explicit choice, else default).
    resolved_address = await _resolve_shipping_address(db, current_user, payload.shipping_address_id)
    # Persist the pinned location (or backfill it onto the chosen saved address)
    # so the order always has a drop-off the dispatch engine can place on a map.
    resolved_address = await _ensure_shipping_address(db, current_user, payload, resolved_address)
    if resolved_address:
        order.shipping_address_id = resolved_address.id
        if not current_user.phone_number and resolved_address.contact_phone:
            current_user.phone_number = resolved_address.contact_phone
        if not loc and current_user.profile:
            saved_loc = ", ".join(
                p for p in [resolved_address.address_line1, resolved_address.city, resolved_address.state] if p
            )
            if saved_loc:
                current_user.profile.location = saved_loc

    await db.commit()

    phone = current_user.phone_number or None
    location = current_user.profile.location if current_user.profile else None
    missing_fields = []
    if not phone:
        missing_fields.append("phone")
    if not location:
        missing_fields.append("address")

    requires_verification = not bool(current_user.email_verified)
    requires_completion = bool(missing_fields) or requires_verification

    return {
        "order_id": str(order.id),
        "order_number": order.order_number,
        "total_amount": float(order.total_amount),
        "requires_completion": requires_completion,
        "missing_fields": missing_fields,
        "requires_verification": requires_verification,
    }


### List orders for the current user (customer-facing)
@router.get("/", response_model=OrderListResponse)
async def list_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    order_status: Optional[str] = Query(None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user_id = current_user.id

    # Housekeeping: abandoned checkouts shouldn't linger as active orders.
    await _expire_stale_drafts(db, user_id)
    await db.commit()

    query = select(Order).options(selectinload(Order.items)).where(Order.user_id == user_id)

    if order_status:
        query = query.where(Order.status == order_status)

    query = query.order_by(Order.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    orders = result.scalars().all()

    # Count total
    count_query = select(Order).where(Order.user_id == user_id)
    if order_status:
        count_query = count_query.where(Order.status == order_status)
    count_result = await db.execute(count_query)
    total = len(count_result.scalars().all())

    return {
        "orders": orders,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }

### Vendor UI: list orders
@router.get("/ui", response_model=list[OrderListUI])
async def get_orders_ui(
    current_vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db)
):
    try:
        vendor_id = UUID(current_vendor["id"])
        # Get distinct order IDs that contain this vendor's products
        order_id_subq = (
            select(OrderItem.order_id)
            .where(OrderItem.vendor_id == vendor_id)
            .distinct()
            .subquery()
        )
        # Fetch orders with eager-loaded relationships
        result = await db.execute(
            select(Order)
            .options(
                selectinload(Order.items),
                selectinload(Order.user).selectinload(User.profile),
                selectinload(Order.shipping_address),
            )
            .where(Order.id.in_(select(order_id_subq.c.order_id)))
            .order_by(Order.created_at.desc())
        )
        orders = result.scalars().all()

        response = []

        for order in orders:
            # Filter items to only include this vendor's products
            vendor_items = [item for item in order.items if str(item.vendor_id) == str(vendor_id)]
            items_str = ", ".join(
                [f"{item.product_name} × {item.quantity}" for item in vendor_items]
            )

            # Customer name from profile (lazy loaded)
            customer_name = "Unknown"
            if order.user and order.user.profile:
                profile = order.user.profile
                customer_name = profile.business_name or f"{profile.first_name} {profile.last_name}".strip() or "Unknown"

            # Location from shipping address, fallback to user profile location
            location = ""
            if order.shipping_address:
                parts = [p for p in [order.shipping_address.city, order.shipping_address.state] if p]
                location = ", ".join(parts)
            elif order.user and order.user.profile:
                profile = order.user.profile
                if profile.location:
                    location = profile.location

            # Driver info from dedicated columns
            driver_name = order.driver_name or ""
            driver_phone = order.driver_phone or ""

            response.append({
                "id": order.order_number,
                "customerName": customer_name,
                "location": location,
                "date": order.created_at.date().isoformat(),
                "total": float(order.total_amount),
                "status": order.status.value if order.status else "",
                "driverName": driver_name,
                "driverPhone": driver_phone,
                "items": items_str,
                # Lets the logistics tab only offer paid orders for dispatch.
                "payment_status": order.payment_status.value if order.payment_status else "",
                # This vendor's own slice of the order (an order can span vendors), so the
                # Orders tab doesn't show other suppliers' money as if it were theirs.
                "vendor_total": float(sum(item.total_price or 0 for item in vendor_items)),
                "item_count": len(vendor_items),
            })

        return response

    except Exception as e:
        logger.error(f"Failed to fetch vendor orders: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to load orders. Please try again later."
        )


### Get order by ID
@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        uid = UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order ID")

    result = await db.execute(
        select(Order).options(selectinload(Order.items)).where(Order.id == uid)
    )
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    return order


### Pricing breakdown for an order (Phase 8)
@router.get("/{order_id}/pricing")
async def get_order_pricing(
    order_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Platform fee, VAT and margin-split breakdown for one order.

    Returns both the amounts actually *charged* at checkout (persisted on the
    order) and a *current-config* restatement, so the UI can show the buyer's
    platform fee + VAT without re-deriving them from the total.
    """
    try:
        uid = UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order ID")

    result = await db.execute(
        select(Order).options(selectinload(Order.items)).where(Order.id == uid)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    role = str(getattr(current_user, "role", "") or "")
    is_admin = role in ("admin", "super_admin", "manager", "support", "marketing")
    if str(order.user_id) != str(current_user.id) and not is_admin:
        raise HTTPException(status_code=403, detail="Not authorized to view this order")

    await _runtime_settings.refresh(db)

    category_by_product = {}
    product_ids = [i.product_id for i in order.items if i.product_id]
    if product_ids:
        rows = (await db.execute(
            select(Product.id, Product.category_id).where(Product.id.in_(product_ids))
        )).all()
        category_by_product = {str(pid): cid for pid, cid in rows}

    items = [
        {
            "product_id": i.product_id,
            "category_id": category_by_product.get(str(i.product_id)),
            "product_name": i.product_name,
            "quantity": i.quantity,
            "unit_price": i.unit_price,
            "total_price": i.total_price,
        }
        for i in order.items
    ]

    fees = await _pricing.load_category_fees(db, [i["category_id"] for i in items])
    pricing = _pricing.compute_order_pricing(
        items,
        fees,
        shipping_fee=order.shipping_fee or 0,
        discount_amount=order.discount_amount or 0,
        promo_discount=order.promo_discount or 0,
    )
    client_share, vendor_share = _settlement_service.margin_split()

    charged_subtotal = float(order.subtotal or 0)
    charged_shipping = float(order.shipping_fee or 0)
    charged_vat = float(order.tax_amount or 0)
    charged_discount = float(order.discount_amount or 0)
    charged_promo = float(order.promo_discount or 0)
    charged_total = float(order.total_amount or 0)
    # The platform fee charged to the buyer is the residual per the checkout formula,
    # adding the promo back so the fee is not understated by the promo taken off.
    charged_platform_fee = max(
        0.0,
        charged_total - charged_subtotal - charged_shipping - charged_vat
        + charged_discount + charged_promo
    )

    return {
        "order_id": str(order.id),
        "order_number": order.order_number,
        "charged": {
            "subtotal": round(charged_subtotal, 2),
            "platform_fee": round(charged_platform_fee, 2),
            "shipping_fee": round(charged_shipping, 2),
            "vat_amount": round(charged_vat, 2),
            "discount_amount": round(charged_discount, 2),
            "promo_discount": round(charged_promo, 2),
            "total_amount": round(charged_total, 2),
        },
        "current": pricing.as_dict(),
        "vat_enabled": pricing.vat_enabled,
        "vat_rate": _pricing.f2(pricing.vat_rate),
        "vat_base": pricing.vat_base,
        "margin_split": {
            "client_share": _pricing.f2(client_share),
            "vendor_share": _pricing.f2(vendor_share),
        },
    }

@router.get("/{order_id}/detail")
async def get_order_detail(
    order_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        uid = UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order ID")

    result = await db.execute(
        select(Order)
        .options(
            selectinload(Order.items).selectinload(OrderItem.vendor).selectinload(Vendor.user),
            selectinload(Order.shipping_address),
        )
        .where(Order.id == uid)
    )
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Driver info from dedicated columns
    driver_name = order.driver_name or ""
    driver_phone = order.driver_phone or ""

    # Get vendor info from first item
    vendor_name = ""
    vendor_email = ""
    if order.items and len(order.items) > 0:
        item = order.items[0]
        if item.vendor:
            vendor_name = item.vendor.business_name
            if item.vendor.user:
                vendor_email = item.vendor.user.email

    # Get pickup/delivery locations
    pickup_location = ""
    delivery_location = ""
    if order.shipping_address:
        parts = [p for p in [order.shipping_address.city, order.shipping_address.state] if p]
        delivery_location = ", ".join(parts)

    return {
        "id": str(order.id),
        "order_number": order.order_number,
        "status": order.status.value if order.status else "",
        "driver_name": driver_name,
        "driver_phone": driver_phone,
        "vendor_name": vendor_name,
        "vendor_email": vendor_email,
        "pickup_location": pickup_location,
        "delivery_location": delivery_location,
        "estimated_delivery": order.estimated_delivery_date.isoformat() if order.estimated_delivery_date else None,
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }


### Get order tracking timeline
@router.get("/{order_id}/tracking")
async def get_order_tracking(
    order_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        uid = UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order ID")

    result = await db.execute(
        select(Order).where(Order.id == uid)
    )
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Build timeline from order status history
    timeline = [
        {"status": "Order Placed", "location": "Online", "timestamp": order.created_at.isoformat(), "completed": True, "current": False},
    ]

    if order.status in ["confirmed", "processing", "picked_up", "shipped", "in_transit", "delivered"]:
        timeline.append({
            "status": "Confirmed", "location": "Vendor", "timestamp": order.updated_at.isoformat(), "completed": True, "current": order.status == "confirmed"
        })

    if order.status in ["processing", "picked_up", "shipped", "in_transit", "delivered"]:
        timeline.append({
            "status": "Processing", "location": "Warehouse", "timestamp": order.updated_at.isoformat(), "completed": True, "current": order.status == "processing"
        })

    # Pickup step. `picked_up` is the canonical status; `shipped` is the legacy
    # value for rows written before it existed and renders as the same step.
    if order.status in ["picked_up", "shipped", "in_transit", "delivered"]:
        timeline.append({
            "status": "Picked Up", "location": "With driver", "timestamp": order.updated_at.isoformat(), "completed": order.status in ["in_transit", "delivered"], "current": order.status in ["picked_up", "shipped"]
        })

    if order.status in ["in_transit", "delivered"]:
        timeline.append({
            "status": "In Transit", "location": "En Route", "timestamp": order.updated_at.isoformat(), "completed": order.status == "delivered", "current": order.status == "in_transit"
        })

    if order.status == "delivered":
        timeline.append({
            "status": "Delivered", "location": "Destination", "timestamp": (order.delivered_at or order.updated_at).isoformat(), "completed": True, "current": True
        })

    return {"timeline": timeline}

### Confirm delivery of an order (triggers escrow release to vendor)
@router.post("/{order_id}/confirm-delivery")
async def confirm_delivery(
    order_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        uid = UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order ID")

    result = await db.execute(
        select(Order).options(selectinload(Order.items)).where(Order.id == uid)
    )
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if str(order.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to confirm delivery for this order"
        )

    if order.status in ("cancelled", "refunded"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot confirm delivery for order in '{order.status}' status"
        )

    # A POD photo with no location data is scored `awaiting_buyer` AND blocks the
    # driver from closing the job, so this confirmation is what actually completes
    # the delivery (job + driver earnings + escrow) - not just the buyer's side.
    proof_awaiting_receipt = await _order_awaiting_receipt_proof(db, uid)

    if proof_awaiting_receipt is not None:
        if proof_awaiting_receipt.buyer_confirmed_at:
            # Idempotent: escrow was already released on the first confirmation.
            return {"message": "Delivery already confirmed", "order_id": order_id}
        if not _can_buyer_confirm(proof_awaiting_receipt):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_buyer_confirm_hint(proof_awaiting_receipt) or "Receipt cannot be confirmed yet.",
            )
        # Records the confirmation on the proof row (so it is no longer
        # `awaiting_buyer`) and finalises the order + job + driver earnings.
        await _finalize_delivery_on_receipt(
            db,
            order,
            proof_awaiting_receipt,
            confirmed_by=current_user.id,
            actor_type="buyer",
            actor_name="Buyer",
            note="Buyer confirmed receipt (photo had no location data).",
        )
    elif order.status in ("picked_up", "shipped", "in_transit"):
        # Standard flow: the driver already captured a verified geotagged proof,
        # so confirming only closes the order and releases the escrow.
        order.status = "delivered"
        order.delivered_at = datetime.utcnow()
        order.payment_status = "completed"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot confirm delivery for order in '{order.status}' status"
        )

    await db.commit()

    # Trigger escrow release to vendor(s) in background
    await release_order_escrow(background_tasks, order, db)

    logger.info(f"Order {order_id} delivery confirmed by user {current_user.id}")
    return {"message": "Delivery confirmed", "order_id": order_id}


async def _order_awaiting_receipt_proof(db: AsyncSession, order_id) -> Optional[DeliveryProof]:
    """The proof that requires the buyer's receipt confirmation for an order.

    Set only for a no-GPS POD photo (scored `awaiting_buyer`); returns None when
    the order has no such proof, so the caller can reject the request.
    """
    row = await db.execute(
        select(DeliveryProof)
        .join(DeliveryJob, DeliveryJob.id == DeliveryProof.delivery_job_id)
        .where(DeliveryJob.order_id == order_id)
        .order_by(DeliveryProof.created_at.desc())
    )
    proof = row.scalars().first()
    if proof is not None and proof.buyer_confirmation_required:
        return proof
    return None


# Job statuses that can no longer be advanced (mirrors deliveries._TERMINAL).
_JOB_TERMINAL = ("delivered", "cancelled", "expired")


async def _finalize_delivery_on_receipt(
    db: AsyncSession,
    order: Order,
    proof: Optional[DeliveryProof],
    *,
    confirmed_by,
    actor_type: str,
    actor_name: str,
    note: str,
) -> Optional[DeliveryJob]:
    """Complete a delivery once its receipt is confirmed (single choke point).

    Used by the buyer's `confirm-delivery` and by the admin
    "accept on behalf" escalation. A no-GPS proof blocks the driver's own
    "mark delivered", so without closing the job here the delivery would strand
    in transit while the money moved.

    Sets the confirmation on the proof (clearing the `awaiting_buyer` verdict),
    closes the job (accruing the driver's earnings) and the order, and appends
    the buyer-visible feed event. Commits nothing - the caller owns the
    transaction and the escrow release.
    """
    now = datetime.utcnow()

    if proof is not None:
        proof.buyer_confirmed_at = now
        proof.buyer_confirmed_by = confirmed_by
        proof.verification_status = "buyer_confirmed"
        proof.needs_review = False
        proof.verification_note = note

    job = (
        await db.execute(
            select(DeliveryJob)
            .where(DeliveryJob.order_id == order.id)
            .order_by(DeliveryJob.created_at.desc())
        )
    ).scalars().first()
    if job is not None and job.status not in _JOB_TERMINAL:
        job.status = "delivered"
        job.delivered_at = now
        # The driver's own transition never ran for this job, so the ledger row
        # is accrued here (idempotent: one row per job).
        await _credit_driver_earnings(db, job)

    order.status = "delivered"
    order.delivered_at = now
    order.payment_status = "completed"

    if proof is not None:
        # Mirror the confirmation into the buyer-facing delivery feed so the
        # event appears next to the driver's updates.
        db.add(DeliveryJobEvent(
            delivery_job_id=job.id if job else None,
            order_id=order.id,
            actor_type=actor_type,
            actor_name=actor_name,
            event_type="status",
            status="delivered",
            note=note,
        ))
    return job


async def release_order_escrow(background_tasks: BackgroundTasks, order: Order, db: AsyncSession) -> None:
    """Queue the escrow release for every vendor line on a completed order.

    Each vendor line is paid its own line total MINUS that line's vendor-borne
    share of the platform margin (`pricing_service.vendor_margins_by_item`). The
    buyer was only ever charged the *client* share at checkout, so without this
    deduction the platform would be paying out its own margin. The buyer's
    shipping fee is not part of any vendor line and is settled to the driver.

    Shared so an order finalised outside the buyer's own confirmation (the admin
    accept-on-behalf escalation) settles the vendors identically.
    """
    try:
        deductions = await _pricing.vendor_margins_by_item(db, order.items)
    except Exception as exc:  # a pricing recompute must never block a release
        logger.error(f"Vendor margin recompute failed for order {order.order_number}: {exc}")
        deductions = {}

    for index, item in enumerate(order.items):
        if not item.vendor_id:
            continue
        gross = _pricing.f2(item.total_price)
        deduction = min(_pricing.f2(deductions.get(_pricing.item_key(item, index), 0)), gross)
        background_tasks.add_task(
            _release_escrow_to_vendor,
            vendor_id=str(item.vendor_id),
            order_number=order.order_number,
            # Only the net leaves escrow; the retained margin stays on the platform.
            amount=round(gross - deduction, 2),
            gross_amount=gross,
            platform_deduction=deduction,
            db_session=db,
        )


# Phase 11: escrow/transfer safety limits.
ESCROW_TRANSFER_CAP = 1_000_000.0   # NGN per release; above this requires admin approval


async def _release_escrow_to_vendor(
    vendor_id: str,
    order_number: str,
    amount: float,
    db_session: AsyncSession,
    gross_amount: Optional[float] = None,
    platform_deduction: float = 0.0,
):
    """Background task: release escrow funds to vendor.

    `amount` is the NET the vendor receives - the line total already reduced by the
    vendor-borne share of the platform margin (`platform_deduction`, with
    `gross_amount` kept only for the log/audit trail). The cap and the bank-details
    check therefore apply to what actually leaves escrow.

    Phase 11 safety:
      - Only release when the vendor bank details (account number) exist
        and the transfer amount is within cap.
      - Releases above the cap are NOT auto-executed - they are logged for
        admin approval (escrow/transfer safety).
      - Validates the recipient reference is well-formed before calling the gateway.
    """
    try:
        from app.models.vendor import Vendor
        vendor_result = await db_session.execute(
            select(Vendor).where(Vendor.id == vendor_id)
        )
        vendor = vendor_result.scalar_one_or_none()
        if not vendor:
            logger.warning(f"Vendor {vendor_id} not found for escrow release")
            return

        # Phase 11: validate recipient - must have a bank account on file.
        account_number = getattr(vendor, "bank_account_number", None)
        if not account_number:
            logger.warning(f"Escrow release blocked: vendor {vendor_id} has no bank account on file (order {order_number})")
            return

        # Phase 11: enforce transfer amount cap - large payouts staged for admin.
        if float(amount) > ESCROW_TRANSFER_CAP:
            logger.warning(
                f"Escrow release for order {order_number} to vendor {vendor_id} "
                f"(₦{amount:,.2f}) exceeds cap ₦{ESCROW_TRANSFER_CAP:,.2f} - requires admin approval."
            )
            return  # do not auto-execute; flagged for manual/admin review

        # Phase 11: validate the recipient reference is well-formed before calling.
        recipient_code = f"VENDOR-{account_number[-4:]}"
        if len(recipient_code) < 8:
            logger.warning(f"Escrow release skipped: invalid recipient reference for vendor {vendor_id}")
            return

        transfer_ref = f"TRF-{order_number}-{vendor_id[:8]}"
        reason = f"Escrow release - Order {order_number}"
        if platform_deduction:
            reason = (
                f"{reason} (net of ₦{platform_deduction:,.2f} platform fee "
                f"on ₦{float(gross_amount or amount):,.2f} gross)"
            )
        payment_service = PaymentService()
        await payment_service.create_transfer(
            amount=amount,
            recipient_code=recipient_code,
            reference=transfer_ref,
            reason=reason,
        )

        logger.info(
            f"Escrow released to vendor {vendor_id}: ₦{amount:,.2f} net "
            f"(₦{float(gross_amount or amount):,.2f} gross - ₦{platform_deduction:,.2f} "
            f"platform fee) for order {order_number}"
        )
    except Exception as e:
        logger.error(f"Failed to release escrow to vendor {vendor_id}: {e}")


async def _vendor_job_event(
    db: AsyncSession,
    order_id,
    actor_name: Optional[str],
    status_value: str,
    note: Optional[str] = None,
) -> None:
    """Mirror a vendor status change into the buyer-facing delivery feed.

    Reuses the existing vendor manual-update path (`PUT /orders/{id}/status`)
    so vendor events appear next to the driver's updates. No-op (never raises)
    when the order has no dispatch job yet, so legacy vendor workflows keep
    working exactly as before.

    The same change also lands in the buyer's notification bell, since the feed
    itself is only visible from the order page.
    """
    try:
        job = (
            await db.execute(
                select(DeliveryJob)
                .where(DeliveryJob.order_id == order_id)
                .order_by(DeliveryJob.created_at.desc())
            )
        ).scalars().first()
        db.add(DeliveryJobEvent(
            delivery_job_id=job.id if job else None,
            order_id=order_id,
            actor_type="vendor",
            actor_name=(actor_name or "").strip() or None,
            event_type="status",
            status=status_value,
            note=note,
        ))
        # The buyer-facing feed is only visible once the order page is opened, so
        # the same status change also raises an in-app notification for the buyer.
        buyer = (
            await db.execute(select(Order.user_id, Order.order_number).where(Order.id == order_id))
        ).first()
        if buyer and buyer.user_id:
            status_label = status_value.replace("_", " ")
            db.add(Notification(
                user_id=buyer.user_id,
                type="delivery",
                title="Delivery update",
                message=(
                    f"Order {buyer.order_number} is now {status_label}"
                    + (f" - {note}" if note else "")
                    + "."
                ),
                read=False,
                link="/dashboard/orders",
            ))
        await db.commit()
    except Exception as e:  # pragma: no cover - never block the vendor action
        logger.warning(f"Vendor delivery event not recorded for order {order_id}: {e}")


### Vendor: Update order status
@router.put("/{order_id}/status")
async def update_order_status(
    order_id: str,
    background_tasks: BackgroundTasks,
    new_status: str = Query(..., alias="status", pattern="^(pending|confirmed|processing|ready_for_pickup|picked_up|shipped|in_transit|delivered|cancelled)$"),
    driver_name: Optional[str] = Query(None),
    driver_phone: Optional[str] = Query(None),
    current_vendor: dict = Depends(get_current_verified_vendor),
    db: AsyncSession = Depends(get_db)
):
    try:
        uid = UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order ID")

    result = await db.execute(
        select(Order).options(selectinload(Order.items), selectinload(Order.user)).where(Order.id == uid)
    )
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Validate status transitions
    valid_transitions = {
        "pending": ["confirmed", "cancelled"],
        "confirmed": ["processing", "cancelled"],
        "processing": ["picked_up", "shipped", "cancelled"],
        "ready_for_pickup": ["picked_up", "shipped", "delivered", "cancelled"],
        "picked_up": ["in_transit", "delivered", "cancelled"],
        "shipped": ["in_transit", "delivered", "cancelled"],
        "in_transit": ["delivered", "cancelled"],
        "delivered": [],
        "cancelled": [],
    }

    current_status = order.status.value.lower() if order.status else "pending"
    allowed = valid_transitions.get(current_status, [])

    logger.warning("update_order_status: order=%s current_status=%s new_status=%s allowed=%s",
                   order_id, current_status, new_status, allowed)

    if new_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot transition order from '{current_status}' to '{new_status}'"
        )

    order.status = new_status
    if new_status == "delivered":
        order.delivered_at = datetime.utcnow()
        order.payment_status = "completed"
    if driver_name:
        order.driver_name = driver_name
        order.driver_phone = driver_phone or ""

    await db.commit()

    # Mirror the status change into the buyer-facing delivery feed.
    await _vendor_job_event(
        db, order.id, current_vendor.get("business_name"), new_status,
        note=(f"Driver: {driver_name}" if driver_name else None),
    )

    # Create shipment record on the pickup step (picked_up, or legacy shipped).
    if new_status in ("picked_up", "shipped"):
        background_tasks.add_task(
            _create_shipment_record,
            order_id=str(order.id),
            order_number=order.order_number,
            driver_name=driver_name or "",
            driver_phone=driver_phone or "",
            db_session=db,
        )

    logger.info(f"Order {order_id} status updated to '{new_status}' by vendor {current_vendor['id']}")

    # Send delivery update email for pickup/transit/delivered status changes
    if new_status in ("picked_up", "shipped", "delivered"):
        location = driver_name or "Vendor location"
        background_tasks.add_task(
            NotificationService().send_delivery_update,
            email=order.user.email if order.user else "",
            order_number=order.order_number,
            status=new_status,
            location=location,
        )

    return {"message": f"Order status updated to '{new_status}'", "order_id": order_id}


### Vendor: Update order with JSON body (for SupplierAdmin.tsx)
@router.put("/{order_id}/vendor-update")
async def vendor_update_order(
    order_id: str,
    update_data: VendorOrderUpdate,
    background_tasks: BackgroundTasks,
    current_vendor: dict = Depends(get_current_verified_vendor),
    db: AsyncSession = Depends(get_db)
):
    # Accept both UUID and order number
    try:
        uid = UUID(order_id)
        result = await db.execute(
            select(Order)
            .options(selectinload(Order.items), selectinload(Order.user))
            .where(Order.id == uid)
        )
    except ValueError:
        result = await db.execute(
            select(Order)
            .options(selectinload(Order.items), selectinload(Order.user))
            .where(Order.order_number == order_id)
        )
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Authorization: a vendor may only act on orders that contain their own items.
    # This check was missing entirely, so any vendor could transition - or mark
    # delivered/cancelled - another vendor's order.
    vendor_id = current_vendor["id"]
    if not any(str(item.vendor_id) == str(vendor_id) for item in order.items):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This order is not from your catalog.",
        )

    # Validate status transitions
    valid_transitions = {
        "pending": ["confirmed", "cancelled"],          # legacy status
        "pending_payment": ["confirmed", "cancelled"],
        "payment_failed": ["confirmed", "cancelled"],   # allow retry or cancel
        "confirmed": ["processing", "cancelled"],
        "processing": ["picked_up", "shipped", "cancelled"],
        "ready_for_pickup": ["picked_up", "shipped", "delivered", "cancelled"],
        "picked_up": ["in_transit", "delivered", "cancelled"],
        "shipped": ["in_transit", "delivered", "cancelled"],
        "in_transit": ["delivered", "cancelled"],
        "delivered": [],
        "cancelled": [],
        "refunded": [],
    }

    current_status = order.status.value.lower() if order.status else "pending"
    allowed = valid_transitions.get(current_status, [])

    new_status = update_data.status.lower()

    logger.warning("vendor_update_order: order=%s current_status=%s new_status=%s allowed=%s",
                   order_id, current_status, new_status, allowed)

    if new_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot transition order from '{current_status}' to '{new_status}'"
        )

    order.status = new_status
    if new_status == "delivered":
        order.delivered_at = datetime.utcnow()
        order.payment_status = "completed"

    # Store driver info
    if update_data.driverName:
        order.driver_name = update_data.driverName
        order.driver_phone = update_data.driverPhone or ""

    await db.commit()

    # Mirror the status change into the buyer-facing delivery feed.
    await _vendor_job_event(db, order.id, current_vendor.get("business_name"), new_status)

    logger.info(f"Order {order_id} updated to '{new_status}' by vendor {current_vendor['id']}")

    # Send delivery update email for pickup/transit/delivered status changes
    if new_status in ("picked_up", "shipped", "delivered"):
        location = update_data.driverName or "Vendor location"
        background_tasks.add_task(
            NotificationService().send_delivery_update,
            email=order.user.email if order.user else "",
            order_number=order.order_number,
            status=new_status,
            location=location,
        )

    return {"message": f"Order updated to '{new_status}'", "order_id": order_id}


async def _create_shipment_record(
    order_id: str,
    order_number: str,
    driver_name: str,
    driver_phone: str,
    db_session: AsyncSession,
):
    """Background task: create shipment tracking record when order is shipped."""
    try:
        from app.models.vendor import Vendor
        tracking_number = f"BNC-{order_number}-{datetime.utcnow().strftime('%H%M')}"
        
        # Store shipment info in order (already has driver_name/phone)
        logger.info(
            f"Shipment created for order {order_number}: "
            f"tracking={tracking_number}, driver={driver_name}, phone={driver_phone}"
        )
    except Exception as e:
        logger.error(f"Failed to create shipment for order {order_number}: {e}")


### Cancel an order
@router.post("/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    reason: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        uid = UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order ID")

    result = await db.execute(
        select(Order).options(selectinload(Order.items)).where(Order.id == uid)
    )
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if str(order.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to cancel this order"
        )

    if order.status in ["delivered", "cancelled", "refunded"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel order in '{order.status}' status"
        )

    # Stock is only decremented once payment succeeds (payments.py), so only
    # restore it for paid orders. Restoring an unpaid draft's stock would inflate
    # inventory that was never taken.
    if order.payment_status == PaymentStatus.COMPLETED:
        for item in order.items:
            product_result = await db.execute(
                select(Product).where(Product.id == item.product_id)
            )
            product = product_result.scalar_one_or_none()
            if product:
                product.quantity += item.quantity
                product.sales_count = max(0, (product.sales_count or 0) - item.quantity)
                # If product was out_of_stock and now has stock, set back to active
                if product.status == "out_of_stock" and product.quantity > 0:
                    product.status = "active"

    # Give back any promo use this order reserved at creation.
    await _release_promo(db, order)

    order.status = "cancelled"
    order.admin_notes = reason
    await db.commit()

    logger.info(f"Order {order_id} cancelled by user {current_user.id}")
    return {"message": "Order cancelled", "order_id": order_id}


### Delete an unpaid order (draft cleanup)
@router.delete("/{order_id}")
async def delete_order(
    order_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete an unpaid order for the current user.

    Only drafts (pending_payment / payment_failed) that were never paid can be
    hard-deleted - paid orders must go through /cancel so escrow, stock and the
    promo counter are handled correctly.
    """
    try:
        uid = UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order ID")

    result = await db.execute(
        select(Order).options(selectinload(Order.items)).where(Order.id == uid)
    )
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if str(order.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this order"
        )

    is_draft = order.status in (OrderStatus.PENDING_PAYMENT, OrderStatus.PAYMENT_FAILED)
    if not is_draft or order.payment_status == PaymentStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only unpaid orders can be deleted. Cancel paid orders instead."
        )

    # Give back any promo use reserved at creation, then remove the row (items
    # cascade). No stock restore - unpaid orders never decremented it.
    await _release_promo(db, order)
    await db.delete(order)
    await db.commit()

    logger.info(f"Unpaid order {order_id} deleted by user {current_user.id}")
    return {"message": "Order deleted", "order_id": order_id}

