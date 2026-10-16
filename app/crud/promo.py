from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from uuid import UUID

from app.models.promo import PromoCode
from app.models.order import Order, OrderStatus
from app.schemas.promo import PromoCodeCreate


async def create(
    db: AsyncSession,
    data: PromoCodeCreate,
    vendor_id: UUID | None = None,
    scope_type: str | None = None,
    scope_id: UUID | None = None,
) -> PromoCode:
    # Owner-implied default: an admin promo (no vendor) is platform-wide, a
    # vendor promo covers the vendor's own catalogue unless a narrower scope is
    # supplied by the endpoint.
    resolved_scope = scope_type or ("vendor" if vendor_id else "platform")
    promo = PromoCode(
        code=data.code.upper(),
        description=data.description,
        discount_amount=data.discount_amount,
        max_uses=data.max_uses,
        max_uses_per_user=data.max_uses_per_user,
        min_order_amount=data.min_order_amount,
        expires_at=data.expires_at,
        vendor_id=vendor_id,
        scope_type=resolved_scope,
        scope_id=scope_id,
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


async def get_by_id(db: AsyncSession, promo_id: UUID) -> PromoCode | None:
    result = await db.execute(
        select(PromoCode).where(PromoCode.id == promo_id)
    )
    return result.scalar_one_or_none()


async def list_by_vendor(db: AsyncSession, vendor_id: UUID) -> list[PromoCode]:
    result = await db.execute(
        select(PromoCode)
        .where(PromoCode.vendor_id == vendor_id)
        .order_by(PromoCode.created_at.desc())
    )
    return list(result.scalars().all())


async def list_all(db: AsyncSession) -> list[PromoCode]:
    """Every promo (admin view) - platform, vendor and scoped codes alike."""
    result = await db.execute(
        select(PromoCode).order_by(PromoCode.created_at.desc())
    )
    return list(result.scalars().all())


async def set_active(db: AsyncSession, promo_id: UUID, is_active: bool) -> PromoCode | None:
    promo = await get_by_id(db, promo_id)
    if not promo:
        return None
    promo.is_active = is_active
    await db.commit()
    await db.refresh(promo)
    return promo


async def update(db: AsyncSession, promo_id: UUID, fields: dict) -> PromoCode | None:
    """Apply `fields` (already validated/normalised by the endpoint) to a promo.

    Only the keys present in `fields` are written, so a partial PATCH leaves every
    other column - including `current_uses` - untouched.
    """
    promo = await get_by_id(db, promo_id)
    if not promo:
        return None
    if fields.get("code"):
        promo.code = str(fields.pop("code")).upper()
    for key, value in fields.items():
        setattr(promo, key, value)
    await db.commit()
    await db.refresh(promo)
    return promo


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


async def count_user_uses(db: AsyncSession, promo_code: str, user_id: UUID) -> int:
    """How many times `user_id` has redeemed `promo_code`.

    Derived from their orders carrying the code so it stays consistent with the
    global `current_uses` counter: a cancelled/deleted draft no longer counts
    (the global counter is likewise given back on cancel).
    """
    result = await db.execute(
        select(func.count(Order.id)).where(
            Order.user_id == user_id,
            Order.promo_code == promo_code.upper(),
            Order.status != OrderStatus.CANCELLED,
        )
    )
    return int(result.scalar() or 0)
