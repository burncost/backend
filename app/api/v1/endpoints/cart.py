from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List
from uuid import UUID

from app.core.database import get_db
from app.models.cart import CartItem
from app.models.product import Product, ProductImage
from app.models.vendor import Vendor
from app.api.deps import get_current_user
from app.schemas.cart import CartItemCreate, CartItemUpdate, CartResponse, CartItemResponse
# Single source of truth for builder-scoped agreed prices, so the cart quotes
# exactly what `orders.create` will charge. orders.py does not import this module,
# so there is no import cycle.
from app.api.v1.endpoints.orders import _agreed_prices_for_builder

import logging
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()


### Get current user's cart
@router.get("/", response_model=CartResponse)
async def get_cart(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user_id = current_user.id
    result = await db.execute(
        select(CartItem)
        .where(CartItem.user_id == user_id)
        .order_by(CartItem.created_at.desc())
    )
    items = result.scalars().all()

    # Builder-scoped agreed negotiation prices: the cart must quote the figure
    # checkout will actually charge, never the bare catalogue price.
    agreed_prices = await _agreed_prices_for_builder(
        db, user_id, [item.product_id for item in items]
    )

    # Vendor of each line, in ONE query (never per-item): the cart shows the real
    # supplier name and only offers "Negotiate" for vendors that opted in.
    vendor_by_product = {}
    product_ids = [item.product_id for item in items]
    if product_ids:
        vendor_result = await db.execute(
            select(
                Product.id,
                Vendor.id,
                Vendor.business_name,
                Vendor.open_to_negotiate,
            )
            .join(Vendor, Vendor.id == Product.vendor_id)
            .where(Product.id.in_(product_ids))
        )
        for product_id, vendor_id, business_name, open_to_negotiate in vendor_result.all():
            vendor_by_product[product_id] = (vendor_id, business_name, bool(open_to_negotiate))

    cart_items = []
    for item in items:
        # Fetch product details (eager-load category so the response can carry a
        # category name for the storefront's image fallback mapping).
        product_result = await db.execute(
            select(Product)
            .options(selectinload(Product.category))
            .where(Product.id == item.product_id)
        )
        product = product_result.scalar_one_or_none()

        # Vendor (name + negotiation opt-in) resolved from the batch above.
        vendor_id, vendor_name, open_to_negotiate = vendor_by_product.get(
            item.product_id, (None, None, False)
        )

        # Fetch primary image
        image_url = None
        if product:
            img_result = await db.execute(
                select(ProductImage)
                .where(ProductImage.product_id == product.id, ProductImage.is_primary == True)
                .limit(1)
            )
            primary_img = img_result.scalar_one_or_none()
            if primary_img:
                image_url = primary_img.image_url
            else:
                # Fallback to first image
                img_result = await db.execute(
                    select(ProductImage)
                    .where(ProductImage.product_id == product.id)
                    .limit(1)
                )
                first_img = img_result.scalar_one_or_none()
                if first_img:
                    image_url = first_img.image_url

        cart_items.append({
            "id": str(item.id),
            "product_id": str(item.product_id),
            "product_name": product.name if product else "Unknown Product",
            # Real vendor business name when known, else the historical placeholder.
            "supplier_name": vendor_name or "Verified Supplier",
            # Vendor identity + negotiation opt-in, so the line can offer
            # "Negotiate" only where the supplier accepts it.
            "vendor_id": str(vendor_id) if vendor_id else None,
            "vendor_open_to_negotiate": open_to_negotiate,
            "unit_price": float(item.price_at_addition),
            "base_price": float(product.base_price) if product else 0,
            # Agreed private price for this buyer (None when there is no deal):
            # `orders.create` applies the same override, so the cart can show it.
            "negotiation_price": agreed_prices.get(item.product_id),
            "quantity": item.quantity,
            "minimum_order_quantity": product.minimum_order_quantity if product else 1,
            "stock": product.quantity if product else 0,
            "unit_of_measure": product.unit_of_measure if product else "unit",
            # Empty string (not a fake path) so the storefront can fall back to
            # the category image mapping instead of a broken /placeholder.png.
            "image_url": image_url or "",
            "category_name": (
                product.category.name
                if product and product.category
                else None
            ),
            # Category id as well as the name: lets the cart verify a
            # category-scoped promo code before checkout.
            "category_id": (
                str(product.category_id)
                if product and product.category_id
                else None
            ),
            "is_verified": product.is_verified if product else False,
        })

    return {"items": cart_items}


### Add item to cart
@router.post("/add", status_code=status.HTTP_201_CREATED)
async def add_to_cart(
    cart_in: CartItemCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user_id = current_user.id

    # Verify product exists
    product_result = await db.execute(
        select(Product).where(Product.id == cart_in.product_id)
    )
    product = product_result.scalar_one_or_none()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found"
        )

    # Check if product is active
    if product.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Product is not available for purchase"
        )

    # Check if item already in cart
    existing_result = await db.execute(
        select(CartItem).where(
            CartItem.user_id == user_id,
            CartItem.product_id == cart_in.product_id,
            CartItem.variant_id == cart_in.variant_id
        )
    )
    existing = existing_result.scalar_one_or_none()

    if existing:
        # Update quantity
        existing.quantity += cart_in.quantity
        await db.commit()
        await db.refresh(existing)
        logger.info(f"Updated cart item {existing.id} quantity to {existing.quantity}")
        return {"message": "Cart item quantity updated", "item_id": str(existing.id)}
    else:
        # Create new cart item
        cart_item = CartItem(
            user_id=user_id,
            product_id=cart_in.product_id,
            variant_id=cart_in.variant_id,
            quantity=cart_in.quantity,
            price_at_addition=float(product.discount_price or product.base_price),
        )
        db.add(cart_item)
        await db.commit()
        await db.refresh(cart_item)
        logger.info(f"Added product {cart_in.product_id} to cart for user {user_id}")
        return {"message": "Product added to cart", "item_id": str(cart_item.id)}


### Update cart item quantity
@router.put("/{item_id}")
async def update_cart_item(
    item_id: UUID,
    cart_in: CartItemUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user_id = current_user.id
    result = await db.execute(
        select(CartItem).where(CartItem.id == item_id, CartItem.user_id == user_id)
    )
    cart_item = result.scalar_one_or_none()

    if not cart_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart item not found"
        )

    if cart_in.quantity < 1:
        # Remove item if quantity is 0
        await db.delete(cart_item)
        await db.commit()
        return {"message": "Cart item removed"}
    else:
        cart_item.quantity = cart_in.quantity
        await db.commit()
        await db.refresh(cart_item)
        return {"message": "Cart item updated", "item_id": str(cart_item.id)}


### Remove item from cart
@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_cart_item(
    item_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user_id = current_user.id
    result = await db.execute(
        select(CartItem).where(CartItem.id == item_id, CartItem.user_id == user_id)
    )
    cart_item = result.scalar_one_or_none()

    if not cart_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart item not found"
        )

    await db.delete(cart_item)
    await db.commit()
    return None
