from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List, Optional
from uuid import UUID
from decimal import Decimal

from app.core.database import get_db, get_mongodb
from app.models.material_rate import MaterialRate, MaterialRateHistory, PriceTrend
from app.schemas.material_rate import (
    MaterialRateCreate,
    MaterialRateUpdate,
    MaterialRateResponse,
    MaterialRateListResponse,
    MaterialRateFilter,
)
from app.api.deps import get_current_admin, get_current_user

import logging

logger = logging.getLogger(__name__)

router = APIRouter()


def _to_mongo_doc(rate: MaterialRate) -> dict:
    """Map a Postgres MaterialRate to the Mongo material_rates document shape
    expected by PriceEngine/PriceService."""
    return {
        "product_code": f"MAT-{str(rate.id)[:8].upper()}",
        "name": rate.material_name,
        "category": str(rate.category_id),
        "unit": rate.unit,
        "unit_price": float(rate.current_price),
        "city": rate.state,
        "supplier": str(rate.supplier_id) if rate.supplier_id else None,
        "last_updated": rate.updated_at.isoformat() if rate.updated_at else None,
        "materialName": rate.material_name,
        "rate": float(rate.current_price),
        "state": rate.state,
        "isActive": True,
    }


async def _sync_to_mongo(rate: MaterialRate, mongodb) -> None:
    """Upsert a Postgres MaterialRate into the Mongo material_rates collection."""
    if mongodb is None:
        return
    try:
        doc = _to_mongo_doc(rate)
        await mongodb["material_rates"].replace_one(
            {"product_code": doc["product_code"]},
            doc,
            upsert=True,
        )
    except Exception as e:
        logger.warning(f"Failed to sync material rate {rate.id} to MongoDB: {e}")


### List material rates with filtering and pagination
@router.get("/", response_model=MaterialRateListResponse)
async def list_material_rates(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category_id: Optional[UUID] = Query(None),
    state: Optional[str] = Query(None),
    material_name: Optional[str] = Query(None),
    supplier_id: Optional[UUID] = Query(None),
    sort_by: str = Query("material_name", regex="^(material_name|current_price|state|created_at)$"),
    sort_order: str = Query("asc", regex="^(asc|desc)$"),
    db: AsyncSession = Depends(get_db)
):
    query = select(MaterialRate)

    if category_id:
        query = query.where(MaterialRate.category_id == category_id)
    if state:
        query = query.where(MaterialRate.state.ilike(f"%{state}%"))
    if material_name:
        query = query.where(MaterialRate.material_name.ilike(f"%{material_name}%"))
    if supplier_id:
        query = query.where(MaterialRate.supplier_id == supplier_id)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Sort
    sort_column = getattr(MaterialRate, sort_by, MaterialRate.material_name)
    if sort_order == "desc":
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())

    # Paginate
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    rates = result.scalars().all()

    return {
        "rates": rates,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total > 0 else 0,
    }


### Get material rate by ID
@router.get("/{rate_id}", response_model=MaterialRateResponse)
async def get_material_rate(
    rate_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(MaterialRate).where(MaterialRate.id == rate_id))
    rate = result.scalar_one_or_none()
    if not rate:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Material rate not found"
        )
    return rate


### Create a new material rate (admin only)
@router.post("/", response_model=MaterialRateResponse, status_code=status.HTTP_201_CREATED)
async def create_material_rate(
    rate_in: MaterialRateCreate,
    current_admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    mongodb=Depends(get_mongodb),
):
    rate = MaterialRate(**rate_in.dict())
    db.add(rate)
    await db.commit()
    await db.refresh(rate)

    # Log initial price to history
    db.add(MaterialRateHistory(rate_id=rate.id, price=rate.current_price, source=rate.source))
    await db.commit()

    # Sync to MongoDB so BOQ enrichment can find it
    await _sync_to_mongo(rate, mongodb)

    logger.info(f"Material rate created: {rate.material_name} - {rate.state} ({rate.id})")
    return rate


### Update a material rate (admin only)
@router.put("/{rate_id}", response_model=MaterialRateResponse)
async def update_material_rate(
    rate_id: UUID,
    rate_in: MaterialRateUpdate,
    current_admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    mongodb=Depends(get_mongodb),
):
    result = await db.execute(select(MaterialRate).where(MaterialRate.id == rate_id))
    rate = result.scalar_one_or_none()
    if not rate:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Material rate not found"
        )

    old_price = rate.current_price
    update_data = rate_in.dict(exclude_unset=True)

    # Preserve previous price and record history when the price actually changes
    new_price = update_data.get("current_price", old_price)
    if float(new_price) != float(old_price):
        rate.previous_price = old_price
        # Auto-derive trend from price movement
        if new_price > old_price:
            rate.trend = PriceTrend.UP.value
        elif new_price < old_price:
            rate.trend = PriceTrend.DOWN.value
        else:
            rate.trend = PriceTrend.STABLE.value
        db.add(MaterialRateHistory(rate_id=rate.id, price=new_price, source=update_data.get("source") or rate.source))

    for field, value in update_data.items():
        setattr(rate, field, value)

    await db.commit()
    await db.refresh(rate)

    # Keep MongoDB in sync
    await _sync_to_mongo(rate, mongodb)

    return rate


### Delete a material rate (admin only)
@router.delete("/{rate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_material_rate(
    rate_id: UUID,
    current_admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    mongodb=Depends(get_mongodb),
):
    result = await db.execute(select(MaterialRate).where(MaterialRate.id == rate_id))
    rate = result.scalar_one_or_none()
    if not rate:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Material rate not found"
        )

    # Remove from MongoDB as well
    if mongodb is not None:
        try:
            await mongodb["material_rates"].delete_many({"product_code": f"MAT-{str(rate.id)[:8].upper()}"})
        except Exception as e:
            logger.warning(f"Failed to remove material rate {rate.id} from MongoDB: {e}")

    await db.delete(rate)
    await db.commit()
    return None


### Get price history for a material rate
@router.get("/{rate_id}/history")
async def get_rate_history(
    rate_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(MaterialRateHistory)
        .where(MaterialRateHistory.rate_id == rate_id)
        .order_by(MaterialRateHistory.recorded_at.desc())
        .limit(50)
    )
    history = result.scalars().all()
    return {"rate_id": rate_id, "history": history}
