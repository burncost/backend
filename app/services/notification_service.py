from uuid import UUID
import logging

logger = logging.getLogger(__name__)


class NotificationService:
    async def notify_order_confirmed(self, user_id: UUID, order_id: UUID, order_number: str):
        logger.info(f"Notification: Order {order_number} confirmed for user {user_id}")
    
    async def notify_low_stock(self, vendor_id: UUID, product_id: UUID, current_quantity: int):
        logger.info(f"Notification: Low stock for product {product_id}, quantity: {current_quantity}")
    
    async def notify_product_published(self, vendor_id: UUID, product_id: UUID, product_name: str):
        logger.info(f"Notification: Product {product_name} published")
    
    async def notify_order_status_changed(self, user_id: UUID, order_id: UUID, old_status: str, new_status: str):
        logger.info(f"Notification: Order {order_id} status changed from {old_status} to {new_status}")
    
    async def notify_new_order_to_vendor(self, vendor_id: UUID, order_id: UUID, items: list):
        logger.info(f"Notification: New order {order_id} for vendor {vendor_id} with {len(items)} items")