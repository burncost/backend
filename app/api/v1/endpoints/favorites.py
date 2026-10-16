"""User-facing product favourites (saved products).

Guests keep favourites in localStorage and replay them here on sign-in
(mirrors the guest-cart merge flow); these endpoints only ever serve
authenticated users.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from uuid import UUID

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.vendor import Vendor
from app.models.product import Product, ProductImage
from app.models.product_favorite import ProductFavorite

router = APIRouter()


def _fav_product_dict(product: Product, vendor: Vendor, image_url) -> dict:
    """Compact product summary the favourites UI needs."""
    return {
        "id": str(product.id),
        "name": product.name,
        "price": float(product.discount_price if product.discount_price is not None else product.base_price or 0),
        "base_price": float(product.base_price or 0),
        "unit": product.unit_of_measure or "piece",
        "image_url": image_url,
        "vendor_id": str(product.vendor_id) if product.vendor_id else None,
        "vendor_name": vendor.business_name if vendor else None,
        "status": product.status.value if hasattr(product.status, "value") else product.status,
    }


@router.get("/")
async def list_favorites(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's saved products (newest first)."""
    rows = (await db.execute(
        select(ProductFavorite, Product, Vendor)
        .join(Product, ProductFavorite.product_id == Product.id)
        .outerjoin(Vendor, Product.vendor_id == Vendor.id)
        .where(ProductFavorite.user_id == current_user.id)
        .order_by(ProductFavorite.created_at.desc())
    )).all()

    favorites = []
    for fav, product, vendor in rows:
        image_url = (await db.execute(
            select(ProductImage.image_url)
            .where(ProductImage.product_id == product.id)
            .order_by(ProductImage.is_primary.desc(), ProductImage.display_order.asc())
            .limit(1)
        )).scalar_one_or_none()
        item = _fav_product_dict(product, vendor, image_url)
        item["favorite_id"] = str(fav.id)
        item["favorited_at"] = fav.created_at.isoformat() if fav.created_at else None
        favorites.append(item)
    return {"favorites": favorites}


@router.post("/{product_id}", status_code=201)
async def add_favorite(
    product_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Idempotently save a product (used by the guest->account merge too)."""
    product = (await db.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
    if not product:
        raise HTTPException(404, "Product not found")
    existing = (await db.execute(
        select(ProductFavorite).where(
            ProductFavorite.user_id == current_user.id,
            ProductFavorite.product_id == product_id,
        )
    )).scalar_one_or_none()
    if existing:
        return {"success": True, "favorited": True, "already": True}
    db.add(ProductFavorite(user_id=current_user.id, product_id=product_id))
    await db.commit()
    return {"success": True, "favorited": True, "already": False}


@router.delete("/{product_id}")
async def remove_favorite(
    product_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Idempotently remove a saved product."""
    await db.execute(
        delete(ProductFavorite).where(
            ProductFavorite.user_id == current_user.id,
            ProductFavorite.product_id == product_id,
        )
    )
    await db.commit()
    return {"success": True, "favorited": False}
