from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List
from uuid import UUID

from app.core.database import get_db
from app.models.cart import CartItem
from app.models.product import Product, ProductImage
from app.api.deps import get_current_user
from app.schemas.cart import CartItemCreate, CartItemUpdate, CartResponse, CartItemResponse

import logging

logger = logging.getLogger(__name__)

router = APIRouter()


### Get current user's cart
@router.get("/", response_model=CartResponse)
async def get_cart(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user_id = current_user.id
    result = await db.execute(
        select(CartItem)
        .where(CartItem.user_id == user_id)
        .order_by(CartItem.created_at.desc())
    )
    items = result.scalars().all()

    cart_items = []
    for item in items:
        # Fetch product details
        product_result = await db.execute(
            select(Product).where(Product.id == item.product_id)
        )
        product = product_result.scalar_one_or_none()

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
            "supplier_name": "Verified Supplier",
            "unit_price": float(item.price_at_addition),
            "base_price": float(product.base_price) if product else 0,
            "quantity": item.quantity,
            "minimum_order_quantity": product.minimum_order_quantity if product else 1,
            "stock": product.quantity if product else 0,
            "unit_of_measure": product.unit_of_measure if product else "unit",
            "image_url": image_url or "/placeholder.png",
            "is_verified": product.is_verified if product else False,
        })

    return {"items": cart_items}


### Add item to cart
@router.post("/add", status_code=status.HTTP_201_CREATED)
async def add_to_cart(
    cart_in: CartItemCreate,
    current_user: dict = Depends(get_current_user),
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
    current_user: dict = Depends(get_current_user),
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
    current_user: dict = Depends(get_current_user),
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
