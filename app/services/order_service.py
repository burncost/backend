from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Dict, Any
from uuid import UUID
from datetime import datetime
import logging

from app.models.order import Order, OrderItem
from app.models.product import Product
from app.models.cart import CartItem
from app.schemas.order import OrderCreate
from app.crud import order as order_crud
from app.services.payment_service import PaymentService
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


class OrderService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.payment_service = PaymentService()
        self.notification_service = NotificationService()
    
    ### Create order from user's cart
    async def create_order_from_cart(
        self,
        user_id: UUID,
        shipping_address_id: UUID,
        payment_method: str
    ) -> Order:
        # Get cart items
        cart_query = select(CartItem).where(CartItem.user_id == user_id)
        result = await self.db.execute(cart_query)
        cart_items = result.scalars().all()
        
        if not cart_items:
            raise ValueError("Cart is empty")
        
        # Calculate totals and validate stock
        subtotal = 0
        order_items_data = []
        
        for cart_item in cart_items:
            product = await self.db.get(Product, cart_item.product_id)
            
            if not product:
                raise ValueError(f"Product {cart_item.product_id} not found")
            
            if product.quantity < cart_item.quantity:
                raise ValueError(
                    f"Insufficient stock for {product.name}. "
                    f"Available: {product.quantity}, Requested: {cart_item.quantity}"
                )
            
            item_total = product.base_price * cart_item.quantity
            subtotal += item_total
            
            order_items_data.append({
                "product_id": product.id,
                "vendor_id": product.vendor_id,
                "product_name": product.name,
                "sku": product.sku,
                "quantity": cart_item.quantity,
                "unit_price": product.base_price,
                "total_price": item_total
            })
        
        # Calculate shipping (simplified - would be more complex in production)
        shipping_fee = self._calculate_shipping_fee(subtotal)
        
        # Calculate tax (7.5% VAT in Nigeria)
        tax_amount = subtotal * 0.075
        
        total_amount = subtotal + shipping_fee + tax_amount
        
        # Generate order number
        order_number = await self._generate_order_number()
        
        # Create order
        order_data = {
            "order_number": order_number,
            "user_id": user_id,
            "shipping_address_id": shipping_address_id,
            "billing_address_id": shipping_address_id,  # Same as shipping for now
            "subtotal": subtotal,
            "shipping_fee": shipping_fee,
            "tax_amount": tax_amount,
            "total_amount": total_amount,
            "payment_method": payment_method,
            "status": "pending_payment"
        }
        
        order = Order(**order_data)
        self.db.add(order)
        await self.db.flush()
        
        # Create order items
        for item_data in order_items_data:
            item_data["order_id"] = order.id
            order_item = OrderItem(**item_data)
            self.db.add(order_item)
        
        await self.db.commit()
        await self.db.refresh(order)
        
        # Clear cart
        for cart_item in cart_items:
            await self.db.delete(cart_item)
        await self.db.commit()
        
        logger.info(f"Order created: {order.id} for user {user_id}")
        
        return order
    
    ### Process payment for order
    async def process_payment(
        self,
        order_id: UUID,
        payment_details: Dict[str, Any]
    ) -> Dict[str, Any]:
        order = await self.db.get(Order, order_id)
        if not order:
            raise ValueError("Order not found")
        
        if order.status != "pending_payment":
            raise ValueError(f"Order is not pending payment. Current status: {order.status}")
        
        # Process payment through payment gateway
        payment_result = await self.payment_service.process_payment(
            order_id=order_id,
            amount=float(order.total_amount),
            payment_method=order.payment_method,
            payment_details=payment_details
        )
        
        if payment_result["status"] == "completed":
            # Update order status
            order.status = "confirmed"
            order.payment_status = "completed"
            
            # Reduce product stock
            await self._reduce_product_stock(order_id)
            
            # Send notifications
            await self.notification_service.notify_order_confirmed(
                user_id=order.user_id,
                order_id=order.id,
                order_number=order.order_number
            )
            
            # Notify vendors
            await self._notify_vendors(order_id)
            
            await self.db.commit()
            
            logger.info(f"Payment successful for order {order_id}")
        else:
            order.status = "payment_failed"
            order.payment_status = "failed"
            await self.db.commit()
            
            logger.warning(f"Payment failed for order {order_id}")
        
        return payment_result
    
    ### Update order status
    async def update_order_status(
        self,
        order_id: UUID,
        new_status: str,
        updated_by: UUID
    ) -> Order:
        order = await self.db.get(Order, order_id)
        if not order:
            raise ValueError("Order not found")
        
        old_status = order.status
        order.status = new_status
        order.updated_at = datetime.utcnow()
        
        await self.db.commit()
        await self.db.refresh(order)
        
        # Send status update notification
        await self.notification_service.notify_order_status_changed(
            user_id=order.user_id,
            order_id=order.id,
            old_status=old_status,
            new_status=new_status
        )
        
        logger.info(f"Order {order_id} status updated: {old_status} -> {new_status}")
        
        return order
    
    ### Generate unique order number
    async def _generate_order_number(self) -> str:
        from datetime import datetime
        timestamp = datetime.utcnow().strftime("%Y%m%d")
        
        # Get count of orders today
        query = select(func.count()).select_from(Order).where(
            func.date(Order.created_at) == datetime.utcnow().date()
        )
        result = await self.db.execute(query)
        count = result.scalar() or 0
        
        return f"ORD-{timestamp}-{count + 1:06d}"
    
    ### Calculate shipping fee based on subtotal
    ### Simplified version - production would consider location, weight, etc.
    def _calculate_shipping_fee(self, subtotal: float) -> float:
        if subtotal >= 50000:  # Free shipping over ₦50,000
            return 0
        elif subtotal >= 20000:
            return 2000
        else:
            return 3500
    
    ### Reduce product stock after successful payment
    async def _reduce_product_stock(self, order_id: UUID):
        query = select(OrderItem).where(OrderItem.order_id == order_id)
        result = await self.db.execute(query)
        order_items = result.scalars().all()
        
        for item in order_items:
            product = await self.db.get(Product, item.product_id)
            if product:
                product.quantity -= item.quantity
                product.sales_count += item.quantity
                
                # Check for low stock
                if product.quantity <= product.low_stock_threshold:
                    await self.notification_service.notify_low_stock(
                        vendor_id=product.vendor_id,
                        product_id=product.id,
                        current_quantity=product.quantity
                    )
    ### Notify vendors about new orders
    async def _notify_vendors(self, order_id: UUID):
        query = select(OrderItem).where(OrderItem.order_id == order_id)
        result = await self.db.execute(query)
        order_items = result.scalars().all()
        
        # Group items by vendor
        vendor_items = {}
        for item in order_items:
            vendor_id = str(item.vendor_id)
            if vendor_id not in vendor_items:
                vendor_items[vendor_id] = []
            vendor_items[vendor_id].append(item)
        
        # Notify each vendor
        for vendor_id, items in vendor_items.items():
            await self.notification_service.notify_new_order_to_vendor(
                vendor_id=UUID(vendor_id),
                order_id=order_id,
                items=items
            )