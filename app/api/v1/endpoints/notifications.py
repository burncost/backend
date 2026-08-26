from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List, Optional
from uuid import UUID

from app.core.database import get_db
from app.models.notification import Notification
from app.api.deps import get_current_user
from app.models.user import User

import logging

logger = logging.getLogger(__name__)

router = APIRouter()


### Create a notification (internal/system use)
@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_notification(
    user_id: UUID,
    title: str,
    message: Optional[str] = None,
    type: Optional[str] = "system",
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    notification = Notification(
        user_id=user_id,
        title=title,
        message=message,
        type=type,
    )
    db.add(notification)
    await db.commit()
    await db.refresh(notification)

    logger.info(f"Notification created for user {user_id}: {title}")
    return {
        "id": str(notification.id),
        "type": notification.type,
        "title": notification.title,
        "message": notification.message,
        "created_at": notification.created_at.isoformat() if notification.created_at else None,
        "read": notification.read,
    }


### List notifications for the current user
@router.get("/")
async def list_notifications(
    unread_only: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user_id = current_user.id
    query = select(Notification).where(Notification.user_id == user_id)

    if unread_only:
        query = query.where(Notification.read == False)

    # Count total
    count_query = select(func.count(Notification.id)).where(Notification.user_id == user_id)
    if unread_only:
        count_query = count_query.where(Notification.read == False)
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    # Fetch paginated
    query = query.order_by(Notification.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    notifications = result.scalars().all()

    return {
        "notifications": [
            {
                "id": str(n.id),
                "type": n.type or "system",
                "title": n.title,
                "message": n.message,
                "created_at": n.created_at.isoformat() if n.created_at else None,
                "read": n.read,
            }
            for n in notifications
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


### Mark a notification as read
@router.put("/{notification_id}/read")
async def mark_notification_read(
    notification_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user_id = current_user.id
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id
        )
    )
    notification = result.scalar_one_or_none()

    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found"
        )

    notification.read = True
    await db.commit()
    return {"message": "Notification marked as read"}


### Mark all notifications as read
@router.put("/read-all")
async def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user_id = current_user.id
    await db.execute(
        select(Notification)
        .where(Notification.user_id == user_id, Notification.read == False)
    )
    await db.execute(
        Notification.__table__.update()
        .where(Notification.user_id == user_id)
        .values(read=True)
    )
    await db.commit()
    return {"message": "All notifications marked as read"}


### Clear all notifications
@router.delete("/clear", status_code=status.HTTP_204_NO_CONTENT)
async def clear_notifications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user_id = current_user.id
    await db.execute(
        Notification.__table__.delete().where(Notification.user_id == user_id)
    )
    await db.commit()
    return None


### Delete a single notification
### (declared AFTER /clear so FastAPI route order resolves "clear" to the static path)
@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification(
    notification_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user_id = current_user.id
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id
        )
    )
    notification = result.scalar_one_or_none()
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found"
        )
    await db.delete(notification)
    await db.commit()
    return None
