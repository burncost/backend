"""Order Service - Real order management with payment integration."""
from typing import Dict, Any, Optional, List
import logging
import uuid
from datetime import datetime

from app.services.payment_service import PaymentService
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


class OrderService:
    """Service for managing orders, payments, and escrow."""

    def __init__(self):
        self.payment_service = PaymentService()
        self.notification_service = NotificationService()

    async def create_order(
        self,
        user_id: str,
        items: List[Dict[str, Any]],
        shipping_address_id: str,
        payment_method: str,
        customer_notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new order with escrow payment."""
        logger.info(f"Creating order for user {user_id} with {len(items)} items")

        order_id = str(uuid.uuid4())
        order_number = f"ORD-{datetime.utcnow().strftime('%y%m')}-{str(uuid.uuid4())[:4].upper()}"

        subtotal = sum(
            item.get("price", 0) * item.get("quantity", 0)
            for item in items
        )
        shipping_fee = self._calculate_shipping(items)
        tax_amount = subtotal * 0.075  # 7.5% VAT
        total_amount = subtotal + shipping_fee + tax_amount

        order = {
            "id": order_id,
            "orderNumber": order_number,
            "userId": user_id,
            "status": "pending_payment",
            "subtotal": subtotal,
            "shippingFee": shipping_fee,
            "taxAmount": tax_amount,
            "totalAmount": total_amount,
            "paymentStatus": "pending",
            "paymentMethod": payment_method,
            "shippingAddressId": shipping_address_id,
            "customerNotes": customer_notes,
            "items": items,
            "createdAt": datetime.utcnow().isoformat(),
            "updatedAt": datetime.utcnow().isoformat(),
        }

        return order

    async def process_payment(
        self,
        order: Dict[str, Any],
        user_email: str
    ) -> Dict[str, Any]:
        """Process payment for an order."""
        logger.info(f"Processing payment for order {order.get('orderNumber')}")

        payment_ref = f"PAY-{order['id'][:8]}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

        result = await self.payment_service.initialize_payment(
            amount=order["totalAmount"],
            email=user_email,
            reference=payment_ref,
            metadata={
                "order_id": order["id"],
                "order_number": order["orderNumber"],
                "user_id": order["userId"],
            }
        )

        if result.get("success"):
            order["paymentReference"] = payment_ref
            order["paymentProvider"] = result.get("provider", "mock")
            order["authorizationUrl"] = result.get("authorization_url")

        return result

    async def confirm_payment(self, reference: str, order: Dict[str, Any], user_email: Optional[str] = None) -> Dict[str, Any]:
        """Confirm payment and update order status."""
        logger.info(f"Confirming payment for reference: {reference}")

        verification = await self.payment_service.verify_payment(reference)

        if verification.get("success"):
            order["status"] = "confirmed"
            order["paymentStatus"] = "paid"
            order["updatedAt"] = datetime.utcnow().isoformat()

            # Send confirmation - use provided email or fall back to order metadata
            customer_email = user_email or order.get("userEmail", "customer@example.com")
            await self.notification_service.send_order_confirmation(
                email=customer_email,
                order_number=order["orderNumber"],
                items=order.get("items", []),
                total=order["totalAmount"],
            )

        return verification

    async def update_delivery_status(
        self,
        order: Dict[str, Any],
        status: str,
        location: str,
        user_email: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update delivery status and notify customer."""
        logger.info(f"Updating delivery for {order.get('orderNumber')}: {status}")

        order["status"] = status
        order["updatedAt"] = datetime.utcnow().isoformat()

        if status in ["in_transit", "out_for_delivery", "delivered"]:
            customer_email = user_email or order.get("userEmail", "customer@example.com")
            await self.notification_service.send_delivery_update(
                email=customer_email,
                order_number=order["orderNumber"],
                status=status,
                location=location,
            )

        return order

    async def release_escrow(
        self,
        order: Dict[str, Any],
        supplier_recipient_code: str
    ) -> Dict[str, Any]:
        """Release escrow funds to supplier after delivery confirmation."""
        logger.info(f"Releasing escrow for order {order.get('orderNumber')}")

        transfer_ref = f"TRF-{order['id'][:8]}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

        result = await self.payment_service.create_transfer(
            amount=order["totalAmount"],
            recipient_code=supplier_recipient_code,
            reference=transfer_ref,
            reason=f"Escrow release - Order {order['orderNumber']}"
        )

        if result.get("success"):
            order["status"] = "completed"
            order["paymentStatus"] = "released"
            order["escrowReleasedAt"] = datetime.utcnow().isoformat()
            order["updatedAt"] = datetime.utcnow().isoformat()

        return result

    def _calculate_shipping(self, items: List[Dict[str, Any]]) -> float:
        """Calculate shipping fee based on items."""
        total_qty = sum(item.get("quantity") or 0 for item in items)
        if total_qty <= 10:
            return 5000
        elif total_qty <= 50:
            return 10000
        elif total_qty <= 100:
            return 15000
        return 25000
