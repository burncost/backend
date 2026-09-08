"""Buyer↔vendor order messaging endpoints (Phase 4B).

Participants are the buyer who owns the order and the vendor(s) fulfilling it.
Guards mirror delivery routing: buyers act on their own orders, vendors act on
orders in their catalog.
"""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.notification import Notification
from app.models.order import Order, OrderItem
from app.models.order_message import OrderMessage
from app.models.user import User
from app.models.vendor import Vendor

router = APIRouter()


class MessageCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)


async def _load_order(db: AsyncSession, order_id: UUID) -> Order:
    order = (await db.execute(
        select(Order).options(selectinload(Order.items)).where(Order.id == order_id)
    )).scalar_one_or_none()
    if not order:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order


async def _participant(db: AsyncSession, order: Order, user: User):
    """Return (role, vendor) if the user may message on this order, else (None, None)."""
    if order.user_id is not None and str(order.user_id) == str(user.id):
        return "buyer", None
    vendor_ids = [it.vendor_id for it in (order.items or []) if getattr(it, "vendor_id", None)]
    if vendor_ids:
        vendor = (await db.execute(
            select(Vendor).where(Vendor.id.in_(vendor_ids), Vendor.user_id == user.id)
        )).scalars().first()
        if vendor:
            return "vendor", vendor
    return None, None


def _notify(db: AsyncSession, user_id, title: str, message: str) -> None:
    db.add(Notification(user_id=user_id, type="order", title=title, message=message, read=False))


async def _mark_read(db: AsyncSession, order_id: UUID, reader_user_id: UUID) -> int:
    res = await db.execute(
        update(OrderMessage)
        .where(
            OrderMessage.order_id == order_id,
            OrderMessage.sender_user_id.isnot(None),
            OrderMessage.sender_user_id != reader_user_id,
        )
        .values(read=True)
    )
    return res.rowcount or 0


@router.post("/order/{order_id}")
async def send_order_message(
    order_id: UUID,
    payload: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await _load_order(db, order_id)
    role, _vendor = await _participant(db, order, current_user)
    if not role:
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="You are not a participant on this order")

    msg = OrderMessage(
        order_id=order.id,
        sender_user_id=current_user.id,
        sender_role=role,
        body=payload.body.strip(),
        created_at=datetime.utcnow(),
    )
    db.add(msg)

    # Notify the other side.
    counterpart_user_ids: set = set()
    if role == "vendor":
        if order.user_id:
            counterpart_user_ids.add(order.user_id)
    else:
        vendor_ids = [it.vendor_id for it in (order.items or []) if getattr(it, "vendor_id", None)]
        if vendor_ids:
            vs = (await db.execute(select(Vendor.user_id).where(Vendor.id.in_(vendor_ids)))).scalars().all()
            counterpart_user_ids.update(v for v in vs if v is not None)
    for uid in counterpart_user_ids:
        if str(uid) == str(current_user.id):
            continue
        _notify(db, uid, "New message on your order",
                f"New message on order {order.order_number}: {payload.body.strip()[:120]}")

    await db.commit()
    await db.refresh(msg)
    return {"id": str(msg.id), "order_id": str(msg.order_id), "sender_role": msg.sender_role, "created_at": msg.created_at}

@router.get("/order/{order_id}")
async def get_order_thread(
    order_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await _load_order(db, order_id)
    role, _vendor = await _participant(db, order, current_user)
    if not role:
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="You are not a participant on this order")

    messages = (await db.execute(
        select(OrderMessage).where(OrderMessage.order_id == order.id).order_by(OrderMessage.created_at.asc())
    )).scalars().all()

    await _mark_read(db, order.id, current_user.id)
    await db.commit()

    return {
        "order_id": str(order.id),
        "order_number": order.order_number,
        "role": role,
        "messages": [
            {
                "id": str(m.id),
                "sender_role": m.sender_role,
                "is_mine": m.sender_user_id is not None and str(m.sender_user_id) == str(current_user.id),
                "body": m.body,
                "created_at": m.created_at,
            }
            for m in messages
        ],
    }


@router.post("/order/{order_id}/read")
async def mark_order_thread_read(
    order_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await _load_order(db, order_id)
    role, _vendor = await _participant(db, order, current_user)
    if not role:
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="You are not a participant on this order")
    marked = await _mark_read(db, order.id, current_user.id)
    await db.commit()
    return {"ok": True, "marked": marked}


@router.get("/threads")
async def list_my_threads(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Threads (orders) the current user participates in, with unread indicators."""
    buyer_ids = set((await db.execute(select(Order.id).where(Order.user_id == current_user.id))).scalars().all())

    my_vendor_ids = set((await db.execute(select(Vendor.id).where(Vendor.user_id == current_user.id))).scalars().all())
    vendor_order_ids = set()
    if my_vendor_ids:
        vendor_order_ids = set((await db.execute(
            select(OrderItem.order_id).where(OrderItem.vendor_id.in_(list(my_vendor_ids)))
        )).scalars().all())

    order_ids = buyer_ids | vendor_order_ids
    if not order_ids:
        return {"threads": []}

    orders = (await db.execute(
        select(Order).where(Order.id.in_(list(order_ids))).order_by(Order.updated_at.desc()).limit(100)
    )).scalars().all()

    threads = []
    for order in orders:
        has_unread = (await db.execute(
            select(OrderMessage.id).where(
                OrderMessage.order_id == order.id,
                OrderMessage.read.is_(False),
                OrderMessage.sender_user_id.isnot(None),
                OrderMessage.sender_user_id != current_user.id,
            ).limit(1)
        )).first()
        last = (await db.execute(
            select(OrderMessage).where(OrderMessage.order_id == order.id).order_by(OrderMessage.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        role = "buyer" if order.id in buyer_ids else "vendor"
        threads.append({
            "order_id": str(order.id),
            "order_number": order.order_number,
            "role": role,
            "unread": bool(has_unread),
            "last_message": last.body[:160] if last else None,
            "last_sender_role": last.sender_role if last else None,
            "last_at": last.created_at if last else None,
        })
    return {"threads": threads}
