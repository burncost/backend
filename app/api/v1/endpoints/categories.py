from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from uuid import UUID

from app.core.database import get_db
from app.models.category import Category
from app.schemas.category import CategoryCreate, CategoryUpdate, CategoryResponse, CategoryTreeResponse
from app.api.deps import get_current_admin

import logging

logger = logging.getLogger(__name__)

router = APIRouter()


### List all active categories
@router.get("/", response_model=List[CategoryResponse])
async def list_categories(
    include_inactive: bool = Query(False),
    parent_id: Optional[UUID] = Query(None),
    division: Optional[str] = Query(None),
    material_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    query = select(Category)
    if not include_inactive:
        query = query.where(Category.is_active == True)
    if parent_id is not None:
        query = query.where(Category.parent_id == parent_id)
    if division:
        query = query.where(Category.division == division)
    if material_type:
        query = query.where(Category.material_type == material_type)
    query = query.order_by(Category.display_order, Category.name)

    result = await db.execute(query)
    categories = result.scalars().all()
    return categories


### Get category tree (hierarchical)
@router.get("/tree", response_model=List[CategoryTreeResponse])
async def get_category_tree(
    division: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """Returns the full category hierarchy as a tree."""
    query = select(Category).where(Category.is_active == True)
    if division:
        query = query.where(Category.division == division)
    query = query.order_by(Category.display_order, Category.name)

    result = await db.execute(query)
    all_categories = result.scalars().all()

    # Build tree
    category_map = {}
    roots = []

    for cat in all_categories:
        cat_id_str = str(cat.id)
        category_map[cat_id_str] = CategoryTreeResponse(
            id=cat.id,
            name=cat.name,
            slug=cat.slug,
            division=cat.division,
            material_type=cat.material_type,
            default_unit=cat.default_unit,
            waste_factor=cat.waste_factor,
            children=[],
        )

    for cat in all_categories:
        cat_id_str = str(cat.id)
        node = category_map[cat_id_str]
        if cat.parent_id and str(cat.parent_id) in category_map:
            category_map[str(cat.parent_id)].children.append(node)
        else:
            roots.append(node)

    return roots


### List all divisions
@router.get("/divisions", response_model=List[str])
async def list_divisions(
    db: AsyncSession = Depends(get_db)
):
    """Returns all unique division names."""
    result = await db.execute(
        select(Category.division)
        .where(Category.division.isnot(None))
        .distinct()
        .order_by(Category.division)
    )
    divisions = result.scalars().all()
    return divisions


### Get category by ID
@router.get("/{category_id}", response_model=CategoryResponse)
async def get_category(
    category_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Category).where(Category.id == category_id))
    category = result.scalar_one_or_none()

    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found"
        )

    return category


### Create a new category (admin only)
@router.post("/", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
async def create_category(
    category_in: CategoryCreate,
    current_admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    # Check for duplicate slug
    result = await db.execute(select(Category).where(Category.slug == category_in.slug))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Category with slug '{category_in.slug}' already exists"
        )

    category = Category(**category_in.dict())
    db.add(category)
    await db.commit()
    await db.refresh(category)

    logger.info(f"Category created: {category.name} ({category.id})")
    return category


### Update a category (admin only)
@router.put("/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: UUID,
    category_in: CategoryUpdate,
    current_admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Category).where(Category.id == category_id))
    category = result.scalar_one_or_none()

    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found"
        )

    update_data = category_in.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(category, field, value)

    await db.commit()
    await db.refresh(category)
    return category


### Delete a category (admin only)
@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: UUID,
    current_admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Category).where(Category.id == category_id))
    category = result.scalar_one_or_none()

    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found"
        )

    await db.delete(category)
    await db.commit()
    return None
