from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

from app.models.promo import PromoCode
from app.schemas.promo import PromoCodeCreate


async def create(db: AsyncSession, data: PromoCodeCreate, vendor_id: UUID | None = None) -> PromoCode:
    promo = PromoCode(
        code=data.code.upper(),
        description=data.description,
        discount_percent=data.discount_percent,
        max_uses=data.max_uses,
        min_order_amount=data.min_order_amount,
        expires_at=data.expires_at,
        vendor_id=vendor_id,
    )
    db.add(promo)
    await db.commit()
    await db.refresh(promo)
    return promo


async def get_by_code(db: AsyncSession, code: str) -> PromoCode | None:
    result = await db.execute(
        select(PromoCode).where(PromoCode.code == code.upper())
    )
    return result.scalar_one_or_none()


async def list_by_vendor(db: AsyncSession, vendor_id: UUID) -> list[PromoCode]:
    result = await db.execute(
        select(PromoCode)
        .where(PromoCode.vendor_id == vendor_id)
        .order_by(PromoCode.created_at.desc())
    )
    return list(result.scalars().all())


async def delete(db: AsyncSession, promo_id: UUID) -> bool:
    result = await db.execute(
        select(PromoCode).where(PromoCode.id == promo_id)
    )
    promo = result.scalar_one_or_none()
    if not promo:
        return False
    await db.delete(promo)
    await db.commit()
    return True


async def increment_uses(db: AsyncSession, promo: PromoCode) -> None:
    promo.current_uses += 1
    await db.commit()
