from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from app.api.deps import get_current_vendor, get_current_user, get_current_verified_vendor
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload, joinedload
from uuid import UUID
from typing import Optional
from datetime import datetime
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
from app.core.database import get_db
from app.services.notification_service import NotificationService
from app.services.shipping_service import ShippingService
from app.models.category import Category
from app.crud import promo as promo_crud

class VendorOrderUpdate(BaseModel):
    status: str
    driverName: str = ""
    driverPhone: str = ""

import logging

router = APIRouter()
logger = logging.getLogger(__name__)


def _generate_order_number() -> str:
    """Generate a unique order number like 'BC-20260703-XXXX'."""
    date_part = datetime.utcnow().strftime("%Y%m%d")
    random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"BC-{date_part}-{random_part}"


### Create order from cart
@router.post("/create", status_code=status.HTTP_201_CREATED)
async def create_order(
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    promo_code: Optional[str] = Query(None, max_length=50),
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
        item_savings = round((base_price - unit_price) * cart_item.quantity, 2)
        total_price = unit_price * cart_item.quantity
        subtotal += total_price

        order_items.append({
            "product_id": product.id,
            "vendor_id": product.vendor_id,
            "product_name": product.name,
            "sku": product.sku,
            "quantity": cart_item.quantity,
            "unit_price": unit_price,
            "total_price": total_price,
            "base_price": base_price,
        })

    # Calculate platform margin per category
    platform_fee = 0.0
    platform_fee_breakdown = []
    for item_data in order_items:
        # Get category margin
        product = None
        product_result = await db.execute(
            select(Product).where(Product.id == item_data["product_id"])
        )
        product = product_result.scalar_one_or_none()
        if product:
            cat_result = await db.execute(
                select(Category.platform_margin).where(Category.id == product.category_id)
            )
            cat_margin = cat_result.scalar() or 5.00
        else:
            cat_margin = 5.00
        item_margin = round(item_data["total_price"] * float(cat_margin) / 100, 2)
        platform_fee += item_margin
        platform_fee_breakdown.append({
            "product_name": item_data["product_name"],
            "margin_pct": float(cat_margin),
            "amount": item_margin,
        })

    # Calculate shipping (dev: flat rate, prod: zone+weight)
    shipping_service = ShippingService()
    shipping_result = await shipping_service.calculate_shipping(
        db=db,
        vendor_id=order_items[0]["vendor_id"],
        items=[{"base_price": i["base_price"], "discount_price": i["unit_price"], "quantity": i["quantity"]} for i in order_items],
    )
    shipping_fee = shipping_result["shipping_fee"]

    # VAT on (subtotal + platform_fee)
    vat_amount = round((subtotal + platform_fee) * 0.075, 2)

    # Product discount (base_price - unit_price)
    discount_amount = round(sum(item["base_price"] * item["quantity"] - item["total_price"] for item in order_items), 2)

    # Server-side promo code validation
    promo_discount = 0.0
    promo_applied = None
    if promo_code:
        promo = await promo_crud.get_by_code(db, promo_code)
        if promo and promo.is_active:
            now = datetime.utcnow()
            if (promo.max_uses == 0 or promo.current_uses < promo.max_uses) and \
               (promo.expires_at is None or promo.expires_at > now) and \
               (promo.min_order_amount == 0 or subtotal >= float(promo.min_order_amount)):
                promo_discount = round(subtotal * float(promo.discount_percent) / 100, 2)
                promo.current_uses += 1
                promo_applied = {
                    "code": promo.code,
                    "discount_percent": float(promo.discount_percent),
                    "amount": promo_discount,
                }
                logger.info(f"Promo code '{promo_code}' applied: {promo.discount_percent}% off, saved ₦{promo_discount:,.2f}")
            else:
                logger.warning(f"Promo code '{promo_code}' validation failed: inactive/expired/limit reached")
        else:
            logger.warning(f"Promo code '{promo_code}' not found or inactive")

    # Total
    total_amount = round(subtotal + platform_fee + shipping_fee + vat_amount - discount_amount - promo_discount, 2)

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

    # --- Soft tier transaction-cap enforcement ---
    from app.models.vendor_verification_tier import VendorVerificationTier
    for vid in {i["vendor_id"] for i in order_items}:
        vr = (await db.execute(select(Vendor).where(Vendor.id == vid))).scalar_one_or_none()
        tr = (await db.execute(select(VendorVerificationTier).where(VendorVerificationTier.tier_code == vr.verification_tier))).scalar_one_or_none() if vr else None
        if not (vr and tr):
            continue
        order_share = sum(i["total_price"] for i in order_items if str(i["vendor_id"]) == str(vid))
        if float(vr.transaction_volume or 0) + order_share > float(tr.transaction_cap):
            order.status = "on_hold"
            db.add(Notification(user_id=vr.user_id, type="verification",
                title="Transaction limit reached",
                message=f"You're at your {tr.display_name} cap of ₦{float(tr.transaction_cap):,.0f}. Upgrade to keep selling.", read=False))

    # Clear the cart
    for cart_item in cart_items:
        await db.delete(cart_item)

    # Create notification
    notification = Notification(
        user_id=user_id,
        type="order",
        title="Order Placed Successfully",
        message=f"Your order {order.order_number} has been placed. Total: ₦{total_amount:,.2f}",
    )
    db.add(notification)

    await db.commit()
    await db.refresh(order)

    logger.info(f"Order {order.order_number} created for user {current_user.id}")

    return {
        "success": True,
        "message": "Order created successfully",
        "order_id": str(order.id),
        "order_number": order.order_number,
        "total_amount": total_amount,
    }


### List orders for the current user (customer-facing)
@router.get("/", response_model=OrderListResponse)
async def list_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    order_status: Optional[str] = Query(None, alias="status"),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user_id = current_user.id
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
    current_user: dict = Depends(get_current_user),
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


### Get order detail with vendor info (for customer tracking)
@router.get("/{order_id}/detail")
async def get_order_detail(
    order_id: str,
    current_user: dict = Depends(get_current_user),
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
    current_user: dict = Depends(get_current_user),
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

    if order.status in ["confirmed", "processing", "shipped", "in_transit", "delivered"]:
        timeline.append({
            "status": "Confirmed", "location": "Vendor", "timestamp": order.updated_at.isoformat(), "completed": True, "current": order.status == "confirmed"
        })

    if order.status in ["processing", "shipped", "in_transit", "delivered"]:
        timeline.append({
            "status": "Processing", "location": "Warehouse", "timestamp": order.updated_at.isoformat(), "completed": True, "current": order.status == "processing"
        })

    if order.status in ["shipped", "in_transit", "delivered"]:
        timeline.append({
            "status": "Shipped", "location": "In Transit", "timestamp": order.updated_at.isoformat(), "completed": order.status in ["in_transit", "delivered"], "current": order.status == "shipped"
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
    current_user: dict = Depends(get_current_user),
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

    if order.status not in ["shipped", "in_transit"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot confirm delivery for order in '{order.status}' status"
        )

    order.status = "delivered"
    order.delivered_at = datetime.utcnow()
    order.payment_status = "completed"
    await db.commit()

    # Trigger escrow release to vendor(s) in background
    for item in order.items:
        if item.vendor_id:
            background_tasks.add_task(
                _release_escrow_to_vendor,
                vendor_id=str(item.vendor_id),
                order_number=order.order_number,
                amount=float(item.total_price),
                db_session=db,
            )

    logger.info(f"Order {order_id} delivery confirmed by user {current_user.id}")
    return {"message": "Delivery confirmed", "order_id": order_id}


async def _release_escrow_to_vendor(
    vendor_id: str,
    order_number: str,
    amount: float,
    db_session: AsyncSession,
):
    """Background task: release escrow funds to vendor."""
    try:
        # Get vendor bank details
        from app.models.vendor import Vendor
        vendor_result = await db_session.execute(
            select(Vendor).where(Vendor.id == vendor_id)
        )
        vendor = vendor_result.scalar_one_or_none()
        if not vendor:
            logger.warning(f"Vendor {vendor_id} not found for escrow release")
            return

        # Create mock transfer (real transfer in production via PaymentService)
        transfer_ref = f"TRF-{order_number}-{vendor_id[:8]}"
        payment_service = PaymentService()
        await payment_service.create_transfer(
            amount=amount,
            recipient_code=f"VENDOR-{vendor.bank_account_number[-4:]}",
            reference=transfer_ref,
            reason=f"Escrow release - Order {order_number}",
        )

        logger.info(f"Escrow released to vendor {vendor_id}: ₦{amount:,.2f} for order {order_number}")
    except Exception as e:
        logger.error(f"Failed to release escrow to vendor {vendor_id}: {e}")


### Vendor: Update order status
@router.put("/{order_id}/status")
async def update_order_status(
    order_id: str,
    background_tasks: BackgroundTasks,
    new_status: str = Query(..., alias="status", pattern="^(pending|confirmed|processing|shipped|in_transit|delivered|cancelled)$"),
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
        "processing": ["shipped", "cancelled"],
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

    # Create shipment record on "shipped" status
    if new_status == "shipped":
        background_tasks.add_task(
            _create_shipment_record,
            order_id=str(order.id),
            order_number=order.order_number,
            driver_name=driver_name or "",
            driver_phone=driver_phone or "",
            db_session=db,
        )

    logger.info(f"Order {order_id} status updated to '{new_status}' by vendor {current_vendor['id']}")

    # Send delivery update email for shipped/delivered status changes
    if new_status in ("shipped", "delivered"):
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

    # Validate status transitions
    valid_transitions = {
        "pending": ["confirmed", "cancelled"],
        "pending_payment": ["confirmed", "cancelled"],
        "confirmed": ["processing", "cancelled"],
        "processing": ["shipped", "cancelled"],
        "shipped": ["in_transit", "delivered", "cancelled"],
        "in_transit": ["delivered", "cancelled"],
        "delivered": [],
        "cancelled": [],
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

    logger.info(f"Order {order_id} updated to '{new_status}' by vendor {current_vendor['id']}")

    # Send delivery update email for shipped/delivered status changes
    if new_status in ("shipped", "delivered"):
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
    current_user: dict = Depends(get_current_user),
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

    # Restore stock for each item in the order
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

    order.status = "cancelled"
    order.admin_notes = reason
    await db.commit()

    logger.info(f"Order {order_id} cancelled by user {current_user.id}")
    return {"message": "Order cancelled", "order_id": order_id}

