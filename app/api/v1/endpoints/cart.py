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
from app.models.negotiation import Negotiation
# Single source of truth for per-transaction agreed prices, so the cart quotes
# exactly what `orders.create` will charge. orders.py does not import this module,
# so there is no import cycle.
from app.api.v1.endpoints.orders import negotiated_cart_lines

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

    # Per-line agreed prices: the cart must quote the figure checkout will
    # actually charge, never the bare catalogue price.
    negotiated = await negotiated_cart_lines(db, user_id, items)

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
            # Agreed price for THIS line (None when there is no deal):
            # `orders.create` applies the same per-line override.
            "negotiation_price": (
                float(negotiated[str(item.negotiation_id)].agreed_price)
                if getattr(item, "negotiation_id", None) and str(item.negotiation_id) in negotiated
                else None
            ),
            "negotiation_id": str(item.negotiation_id) if getattr(item, "negotiation_id", None) else None,
            "negotiated_quantity": (
                int(float(negotiated[str(item.negotiation_id)].quantity or 0)) or None
                if getattr(item, "negotiation_id", None) and str(item.negotiation_id) in negotiated
                else None
            ),
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

    # Optional accepted-negotiation pricing for this line.
    negotiation = None
    if cart_in.negotiation_id is not None:
        negotiation = (await db.execute(
            select(Negotiation).where(
                Negotiation.id == cart_in.negotiation_id,
                Negotiation.builder_id == user_id,
                Negotiation.status == "price_agreed",
                Negotiation.agreed_price.isnot(None),
                Negotiation.agreed_order_id.is_(None),
            )
        )).scalars().first()
        if negotiation is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That negotiated price is not available (already used or not yours)",
            )
        # The agreed line must be the product the deal is for - otherwise a buyer
        # could price any catalogue item at another item's negotiated rate.
        if negotiation.product_id is not None and negotiation.product_id != cart_in.product_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That negotiated price belongs to a different product",
            )

    # Check if item already in cart. A negotiated line is deduped on the
    # agreement, and a plain line is matched NULL-safely on variant_id (SQL `=`
    # never matches NULL, which used to create a duplicate row per add).
    if negotiation is not None:
        existing_result = await db.execute(
            select(CartItem).where(
                CartItem.user_id == user_id,
                CartItem.negotiation_id == negotiation.id,
            )
        )
    else:
        variant_clause = (
            CartItem.variant_id.is_(None)
            if cart_in.variant_id is None
            else CartItem.variant_id == cart_in.variant_id
        )
        existing_result = await db.execute(
            select(CartItem).where(
                CartItem.user_id == user_id,
                CartItem.product_id == cart_in.product_id,
                variant_clause,
            )
        )
    existing = existing_result.scalars().first()

    if existing:
        # Update quantity (never above the negotiated quantity)
        quantity = existing.quantity + cart_in.quantity
        if negotiation is not None and negotiation.quantity:
            quantity = min(quantity, int(float(negotiation.quantity)))
        existing.quantity = quantity
        await db.commit()
        await db.refresh(existing)
        logger.info(f"Updated cart item {existing.id} quantity to {existing.quantity}")
        return {"message": "Cart item quantity updated", "item_id": str(existing.id)}
    else:
        # Create new cart item
        quantity = cart_in.quantity
        if negotiation is not None and negotiation.quantity:
            quantity = min(quantity, int(float(negotiation.quantity)))
        cart_item = CartItem(
            user_id=user_id,
            product_id=cart_in.product_id,
            variant_id=cart_in.variant_id,
            quantity=quantity,
            price_at_addition=float(product.discount_price or product.base_price),
            negotiation_id=negotiation.id if negotiation is not None else None,
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


### Seed the cart from an accepted negotiation (per-transaction price)
@router.post("/from-negotiation/{negotiation_id}", status_code=status.HTTP_201_CREATED)
async def add_negotiated_line(
    negotiation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add the agreed product to the buyer's cart at the agreed price.

    One accepted agreement produces one priced cart line. The product comes from
    the negotiation itself (or the newest snapshot on its thread), so the buyer
    never has to find the right catalogue item, and the quantity is capped at the
    negotiated quantity. The line is refused once the agreement has been checked
    out (`agreed_order_id`), which is what makes the price single-use.
    """
    from app.models.negotiation import NegotiationCounterOffer

    negotiation = (await db.execute(
        select(Negotiation).where(
            Negotiation.id == negotiation_id,
            Negotiation.builder_id == current_user.id,
            Negotiation.status == "price_agreed",
            Negotiation.agreed_price.isnot(None),
            Negotiation.agreed_order_id.is_(None),
        )
    )).scalars().first()
    if negotiation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No usable agreement for this negotiation (not yours, unused or already checked out)",
        )

    product_id = negotiation.product_id
    if product_id is None:
        snapshot_offer = (await db.execute(
            select(NegotiationCounterOffer)
            .where(
                NegotiationCounterOffer.negotiation_id == negotiation.id,
                NegotiationCounterOffer.product_id.isnot(None),
            )
            .order_by(NegotiationCounterOffer.created_at.desc())
            .limit(1)
        )).scalars().first()
        product_id = snapshot_offer.product_id if snapshot_offer else None
    if product_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This agreement has no catalogue product attached to buy",
        )

    product = (await db.execute(
        select(Product).where(Product.id == product_id)
    )).scalars().first()
    if product is None or product.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The agreed product is no longer available",
        )

    quantity = max(1, int(float(negotiation.quantity or 1)))
    existing = (await db.execute(
        select(CartItem).where(
            CartItem.user_id == current_user.id,
            CartItem.negotiation_id == negotiation.id,
        )
    )).scalars().first()
    if existing is not None:
        existing.quantity = quantity
        existing.price_at_addition = float(product.discount_price or product.base_price)
        await db.commit()
        await db.refresh(existing)
        return {
            "message": "Negotiated line updated",
            "item_id": str(existing.id),
            "quantity": existing.quantity,
            "negotiation_price": float(negotiation.agreed_price),
        }

    cart_item = CartItem(
        user_id=current_user.id,
        product_id=product.id,
        variant_id=None,
        quantity=quantity,
        price_at_addition=float(product.discount_price or product.base_price),
        negotiation_id=negotiation.id,
    )
    db.add(cart_item)
    await db.commit()
    await db.refresh(cart_item)
    return {
        "message": "Negotiated line added to cart",
        "item_id": str(cart_item.id),
        "quantity": cart_item.quantity,
        "negotiation_price": float(negotiation.agreed_price),
    }
