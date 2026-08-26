"""Phase 6 admin settings endpoints — system settings key-value store.

Powered by the SystemSetting model.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
from typing import Optional

from app.core.database import get_db
from app.api.deps import require_roles
from app.models.system_setting import SystemSetting

router = APIRouter()
admin_guard = require_roles("manager", "support", "marketing")


@router.get("/settings")
async def admin_list_settings(section: Optional[str] = None, current_user=Depends(admin_guard), db=Depends(get_db)):
    """All system settings (optionally filtered by section)."""
    query = select(SystemSetting)
    if section:
        query = query.where(SystemSetting.section == section)
    rows = (await db.execute(query)).scalars().all()
    return {"settings": [{"key": s.key, "value": s.value, "section": s.section, "description": s.description} for s in rows]}


@router.put("/settings")
async def admin_update_settings(payload: dict, current_user=Depends(admin_guard), db=Depends(get_db)):
    """Bulk upsert system settings from a {key: value} map."""
    values = payload.get("settings", payload)
    count = 0
    for key, value in values.items():
        if not isinstance(key, str):
            continue
        existing = (await db.execute(select(SystemSetting).where(SystemSetting.key == key))).scalar_one_or_none()
        if existing:
            existing.value = str(value)
            existing.updated_at = datetime.utcnow()
            existing.updated_by = str(current_user.get("id", "admin"))
        else:
            db.add(SystemSetting(key=key, value=str(value), section="general", updated_by=str(current_user.get("id", "admin"))))
        count += 1
    await db.commit()
    return {"updated": count}