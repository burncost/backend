from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List, Optional

from app.core.database import get_db
from app.models.category import Category
from app.schemas.category import CategoryResponse, CategoryTreeResponse
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/")
async def list_categories(
    parent_only: bool = Query(False, description="Return only parent categories"),
    active_only: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    """List all categories. If parent_only=True, returns only top-level categories."""
    query = select(Category)

    if active_only:
        query = query.where(Category.is_active == True)

    if parent_only:
        query = query.where(Category.parent_id.is_(None))
    else:
        # Eager load children
        query = query.options(selectinload(Category.children))

    query = query.order_by(Category.display_order, Category.name)
    result = await db.execute(query)
    categories = result.scalars().unique().all()

    cats_out = []
    for cat in categories:
        d = {
            "id": str(cat.id),
            "name": cat.name,
            "slug": cat.slug,
            "parent_id": str(cat.parent_id) if cat.parent_id else None,
            "division": cat.division,
            "default_unit": cat.default_unit,
            "platform_margin": float(cat.platform_margin) if cat.platform_margin else 0,
            "fee_model": cat.fee_model or "percentage",
            "fee_fixed": float(cat.fee_fixed) if cat.fee_fixed else None,
            "description": cat.description,
            "display_order": cat.display_order or 0,
            "is_active": cat.is_active,
            "children": [],
        }
        if not parent_only and hasattr(cat, "children"):
            for child in cat.children:
                d["children"].append({
                    "id": str(child.id),
                    "name": child.name,
                    "slug": child.slug,
                    "parent_id": str(child.parent_id) if child.parent_id else None,
                    "division": child.division,
                    "default_unit": child.default_unit,
                    "platform_margin": float(child.platform_margin) if child.platform_margin else 0,
                    "description": child.description,
                    "display_order": child.display_order or 0,
                    "is_active": child.is_active,
                })
        cats_out.append(d)

    return cats_out


@router.get("/{category_id}")
async def get_category(
    category_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a single category by ID."""
    from uuid import UUID
    try:
        uid = UUID(category_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid category ID")

    result = await db.execute(
        select(Category)
        .options(selectinload(Category.children))
        .where(Category.id == uid)
    )
    cat = result.scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")

    return {
        "id": str(cat.id),
        "name": cat.name,
        "slug": cat.slug,
        "parent_id": str(cat.parent_id) if cat.parent_id else None,
        "division": cat.division,
        "default_unit": cat.default_unit,
        "platform_margin": float(cat.platform_margin) if cat.platform_margin else 0,
        "description": cat.description,
        "display_order": cat.display_order or 0,
        "is_active": cat.is_active,
        "children": [
            {
                "id": str(c.id),
                "name": c.name,
                "slug": c.slug,
                "parent_id": str(c.parent_id) if c.parent_id else None,
                "division": c.division,
                "default_unit": c.default_unit,
                "platform_margin": float(c.platform_margin) if c.platform_margin else 0,
            }
            for c in cat.children
        ],
    }