"""Projects endpoints — Phase 5: project memory / procurement intelligence."""
from typing import Dict, Any, Optional
import logging

from fastapi import APIRouter, Depends, HTTPException

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_mongodb, get_db
from app.api.deps import get_current_user
from app.services.project_memory_service import ProjectMemoryService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{project_id}/materials")
async def get_project_materials(
    project_id: str,
    current_user: dict = Depends(get_current_user),
    mongo_db = Depends(get_mongodb),
    pg_db: AsyncSession = Depends(get_db),
):
    """Project memory: what's been bought vs remaining per material.

    Remaining = BOQ required qty − ordered qty (PostgreSQL order_items).
    Answers "what do I still need for this project?".
    """
    service = ProjectMemoryService(mongo_db=mongo_db, pg_db=pg_db)
    result = await service.get_project_materials(project_id)

    if not result.get("exists"):
        raise HTTPException(status_code=404, detail=result.get("error", "Project not found"))

    return result