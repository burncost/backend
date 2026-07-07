from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from uuid import UUID

from app.core.database import get_db
from app.models.brand import Brand
from app.api.deps import get_current_admin

import logging

logger = logging.getLogger(__name__)

router = APIRouter()


### List all brands
@router.get("/")
async def list_brands(
    include_inactive: bool = Query(False),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    query = select(Brand)
    if not include_inactive:
        query = query.where(Brand.is_active == True)
    if search:
        query = query.where(Brand.name.ilike(f"%{search}%"))
    query = query.order_by(Brand.name)

    result = await db.execute(query)
    brands = result.scalars().all()

    return [
        {
            "id": str(b.id),
            "name": b.name,
            "slug": b.slug,
            "logo_url": b.logo_url,
            "description": b.description,
            "is_active": b.is_active,
        }
        for b in brands
    ]


### Get brand by ID
@router.get("/{brand_id}")
async def get_brand(
    brand_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Brand).where(Brand.id == brand_id))
    brand = result.scalar_one_or_none()

    if not brand:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Brand not found"
        )

    return {
        "id": str(brand.id),
        "name": brand.name,
        "slug": brand.slug,
        "logo_url": brand.logo_url,
        "description": brand.description,
        "is_active": brand.is_active,
    }


### Create a brand (admin only)
@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_brand(
    name: str,
    slug: str,
    description: Optional[str] = None,
    logo_url: Optional[str] = None,
    current_admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    # Check for duplicate slug
    result = await db.execute(select(Brand).where(Brand.slug == slug))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Brand with slug '{slug}' already exists"
        )

    brand = Brand(
        name=name,
        slug=slug,
        description=description,
        logo_url=logo_url,
    )
    db.add(brand)
    await db.commit()
    await db.refresh(brand)

    return {
        "id": str(brand.id),
        "name": brand.name,
        "slug": brand.slug,
        "logo_url": brand.logo_url,
        "description": brand.description,
        "is_active": brand.is_active,
    }


### Update a brand (admin only)
@router.put("/{brand_id}")
async def update_brand(
    brand_id: UUID,
    name: Optional[str] = None,
    slug: Optional[str] = None,
    description: Optional[str] = None,
    logo_url: Optional[str] = None,
    is_active: Optional[bool] = None,
    current_admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Brand).where(Brand.id == brand_id))
    brand = result.scalar_one_or_none()

    if not brand:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Brand not found"
        )

    if slug is not None and slug != brand.slug:
        # Check for duplicate slug
        slug_result = await db.execute(select(Brand).where(Brand.slug == slug))
        existing = slug_result.scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Brand with slug '{slug}' already exists"
            )
        brand.slug = slug

    if name is not None:
        brand.name = name
    if description is not None:
        brand.description = description
    if logo_url is not None:
        brand.logo_url = logo_url
    if is_active is not None:
        brand.is_active = is_active

    await db.commit()
    await db.refresh(brand)

    logger.info(f"Brand {brand_id} updated by admin {current_admin['id']}")

    return {
        "id": str(brand.id),
        "name": brand.name,
        "slug": brand.slug,
        "logo_url": brand.logo_url,
        "description": brand.description,
        "is_active": brand.is_active,
    }


### Delete a brand (admin only)
@router.delete("/{brand_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_brand(
    brand_id: UUID,
    current_admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Brand).where(Brand.id == brand_id))
    brand = result.scalar_one_or_none()

    if not brand:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Brand not found"
        )

    await db.delete(brand)
    await db.commit()

    logger.info(f"Brand {brand_id} deleted by admin {current_admin['id']}")
    return None
