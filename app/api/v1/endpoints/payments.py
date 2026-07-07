from fastapi import APIRouter, Depends, HTTPException, status, Query, Request, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from typing import Optional, List
from uuid import UUID
from datetime import datetime
import os

from app.core.database import get_db
from app.models.order import Order, OrderItem, PaymentStatus
from app.models.product import Product
from app.models.notification import Notification
from app.api.deps import get_current_user, get_current_admin
from app.services.payment_service import PaymentService
from app.services.notification_service import NotificationService

import logging

logger = logging.getLogger(__name__)

router = APIRouter()
payment_service = PaymentService()


### List payments for the current user
@router.get("/")
async def list_payments(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user_id = current_user.id
    query = select(Order).where(
        Order.user_id == user_id,
        Order.payment_status.isnot(None)
    ).order_by(Order.created_at.desc())

    # Count total
    count_query = select(func.count(Order.id)).where(
        Order.user_id == user_id,
        Order.payment_status.isnot(None)
    )
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    orders = result.scalars().all()

    return {
        "payments": [
            {
                "id": str(o.id),
                "order_number": o.order_number,
                "amount": float(o.total_amount),
                "payment_status": o.payment_status.value if o.payment_status else "pending",
                "payment_method": o.payment_method.value if o.payment_method else None,
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }
            for o in orders
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }



### Process a payment for an order
@router.post("/process")
async def process_payment(
    payment_data: dict,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Process payment for an order.
    Expected payment_data:
    {
        "order_id": "uuid",
        "payment_method": "card" | "bank_transfer" | "ussd",
        "card_number": "xxxx" (optional, for card payments),
        "card_expiry": "MM/YY" (optional),
        "card_cvv": "xxx" (optional),
        "bank_code": "xxx" (optional, for bank transfer),
        "amount": 1000.00
    }
    """
    order_id = payment_data.get("order_id")
    payment_method = payment_data.get("payment_method", "card")
    amount = payment_data.get("amount")

    if not order_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="order_id is required"
        )

    # Verify order exists and belongs to user
    try:
        uid = UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order ID")

    result = await db.execute(
        select(Order).options(selectinload(Order.items)).where(Order.id == uid)
    )
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found"
        )

    if str(order.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Order does not belong to current user"
        )

    if order.payment_status == PaymentStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order has already been paid"
        )

    # Initialize payment via Flutterwave (or Paystack fallback, or mock)
    reference = f"BURNCOST-{order.order_number}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    user_email = current_user.email

    payment_result = await payment_service.initialize_payment(
        amount=float(order.total_amount),
        email=user_email,
        reference=reference,
        payment_type=payment_method,
        metadata={
            "order_id": str(order.id),
            "order_number": order.order_number,
            "user_id": str(current_user.id),
            "payment_method": payment_method,
        }
    )

    if not payment_result.get("success"):
        order.payment_status = PaymentStatus.FAILED
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Payment initialization failed. Please try again."
        )

    provider = payment_result.get("provider", "unknown")

    # If mock mode or Flutterwave returns immediate success, mark as completed
    if provider == "mock" or payment_result.get("status") == "success":
        order.payment_status = PaymentStatus.COMPLETED
        order.payment_method = payment_method
        order.status = "confirmed"

        # Deduct stock for each item in the order
        for item in order.items:
            product_result = await db.execute(
                select(Product).where(Product.id == item.product_id)
            )
            product = product_result.scalar_one_or_none()
            if product:
                product.quantity -= item.quantity
                product.sales_count = (product.sales_count or 0) + item.quantity
                if product.quantity <= 0:
                    product.status = "out_of_stock"

        # Create payment notification
        notification = Notification(
            user_id=current_user.id,
            type="payment",
            title="Payment Received",
            message=f"Payment of ₦{float(order.total_amount):,.2f} for order {order.order_number} was successful.",
        )
        db.add(notification)
        await db.commit()

        logger.info(f"Payment processed for order {order_id}: method={payment_method}, amount={amount}")

        # Send order confirmation + payment receipt emails in background
        email_items = [
            {"name": item.product_name, "quantity": item.quantity, "price": float(item.total_price)}
            for item in order.items
        ]
        background_tasks.add_task(
            NotificationService().send_order_confirmation,
            email=current_user.email,
            order_number=order.order_number,
            items=email_items,
            total=float(order.total_amount),
        )
        background_tasks.add_task(
            NotificationService().send_payment_receipt,
            email=current_user.email,
            amount=float(order.total_amount),
            reference=reference,
        )

        return {
            "success": True,
            "message": "Payment processed successfully",
            "transaction_id": str(order.id),
            "reference": reference,
            "provider": provider,
            "status": "completed",
        }

    # For redirect-based payments (Flutterwave standard), return authorization URL
    await db.commit()
    return {
        "success": True,
        "message": "Redirect to payment gateway",
        "authorization_url": payment_result.get("authorization_url"),
        "reference": reference,
        "provider": provider,
        "status": "pending",
    }


### Get payment status for an order
@router.get("/status/{order_id}")
async def get_payment_status(
    order_id: UUID,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found"
        )

    if str(order.user_id) != str(current_user.id) and current_user.role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this payment"
        )

    return {
        "order_id": str(order.id),
        "order_number": order.order_number,
        "payment_status": order.payment_status.value if order.payment_status else "pending",
        "payment_method": order.payment_method.value if order.payment_method else None,
        "total_amount": float(order.total_amount),
        "status": order.status.value if order.status else "pending",
    }


### Verify payment by reference (called after redirect from payment gateway)
@router.get("/verify/{reference}")
async def verify_payment(
    reference: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Verify a payment transaction by reference.
    Called after the user is redirected back from Flutterwave/Paystack.
    """
    result = await payment_service.verify_payment(reference)

    if result.get("success"):
        # Update order status if found
        if reference.startswith("BURNCOST-"):
            parts = reference.split("-")
            if len(parts) >= 2:
                order_number = parts[1]
                order_result = await db.execute(
                    select(Order).where(Order.order_number == order_number)
                )
                order = order_result.scalar_one_or_none()
                if order and order.payment_status != PaymentStatus.COMPLETED:
                    order.payment_status = PaymentStatus.COMPLETED
                    order.status = "confirmed"
                    notification = Notification(
                        user_id=order.user_id,
                        type="payment",
                        title="Payment Received",
                        message=f"Payment of ₦{float(order.total_amount):,.2f} for order {order.order_number} was successful.",
                    )
                    db.add(notification)
                    await db.commit()

    return result


### Flutterwave webhook (for payment callbacks)
@router.post("/webhook/flutterwave")
async def flutterwave_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Handle Flutterwave v3 webhook events.
    Flutterwave sends POST requests to this endpoint for payment status updates.
    """
    import hashlib
    import json

    # Get the signature from headers
    signature = request.headers.get("verif-hash", "")
    secret_hash = os.getenv("FLUTTERWAVE_SECRET_HASH", "")

    # Verify webhook signature
    if secret_hash and signature != secret_hash:
        logger.warning("Invalid Flutterwave webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    event = payload.get("event", "")
    data = payload.get("data", {})

    logger.info(f"Flutterwave webhook received: event={event}")

    if event == "charge.completed" and data.get("status") == "successful":
        tx_ref = data.get("tx_ref", "")
        # Extract order reference from tx_ref (format: BURNCOST-{order_number}-{timestamp})
        if tx_ref.startswith("BURNCOST-"):
            parts = tx_ref.split("-")
            if len(parts) >= 2:
                order_number = parts[1]
                # Find order by order_number
                result = await db.execute(
                    select(Order).where(Order.order_number == order_number)
                )
                order = result.scalar_one_or_none()
                if order and order.payment_status != PaymentStatus.COMPLETED:
                    order.payment_status = PaymentStatus.COMPLETED
                    order.status = "confirmed"
                    # Create notification
                    notification = Notification(
                        user_id=order.user_id,
                        type="payment",
                        title="Payment Received",
                        message=f"Payment of ₦{float(order.total_amount):,.2f} for order {order.order_number} was successful.",
                    )
                    db.add(notification)
                    await db.commit()
                    logger.info(f"Webhook: Payment confirmed for order {order_number}")

    return {"status": "ok"}


### Paystack webhook (for payment callbacks)
@router.post("/webhook/paystack")
async def paystack_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Handle Paystack webhook events.
    """
    import hashlib
    import json

    # Verify Paystack signature
    payload_bytes = await request.body()
    signature = request.headers.get("x-paystack-signature", "")
    secret = os.getenv("PAYSTACK_SECRET_KEY", "")
    expected_signature = hashlib.sha512(payload_bytes + secret.encode()).hexdigest()

    if signature and signature != expected_signature:
        logger.warning("Invalid Paystack webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(payload_bytes)
    event = payload.get("event", "")

    logger.info(f"Paystack webhook received: event={event}")

    if event == "charge.success":
        data = payload.get("data", {})
        reference = data.get("reference", "")
        if reference.startswith("BURNCOST-"):
            parts = reference.split("-")
            if len(parts) >= 2:
                order_number = parts[1]
                result = await db.execute(
                    select(Order).where(Order.order_number == order_number)
                )
                order = result.scalar_one_or_none()
                if order and order.payment_status != PaymentStatus.COMPLETED:
                    order.payment_status = PaymentStatus.COMPLETED
                    order.status = "confirmed"
                    notification = Notification(
                        user_id=order.user_id,
                        type="payment",
                        title="Payment Received",
                        message=f"Payment of ₦{float(order.total_amount):,.2f} for order {order.order_number} was successful.",
                    )
                    db.add(notification)
                    await db.commit()
                    logger.info(f"Webhook: Payment confirmed for order {order_number}")

    return {"status": "ok"}


### Initiate a refund (admin only)
@router.post("/refund/{order_id}")
async def initiate_refund(
    order_id: UUID,
    reason: Optional[str] = Query(None),
    current_admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found"
        )

    if order.payment_status != PaymentStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order has not been paid yet"
        )

    order.payment_status = PaymentStatus.REFUNDED
    order.status = "refunded"
    order.admin_notes = f"Refund initiated. Reason: {reason or 'No reason provided'}"
    await db.commit()

    logger.info(f"Refund initiated for order {order_id} by admin {current_admin.id}")

    return {
        "success": True,
        "message": "Refund initiated successfully",
        "order_id": str(order.id),
        "payment_status": "refunded",
    }
