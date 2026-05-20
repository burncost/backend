from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
    BackgroundTasks
)
from typing import List, Optional

from app.core.database import get_mongodb
from app.repositories.boq_repository import BOQRepository
from app.services.boq_generator import BOQGenerator
from app.api.deps import get_current_user
from app.schemas.boq import (
    BOQCreate,
    BOQUpdate,
    BOQResponse,
    BOQListResponse,
    BOQGenerateRequest
)

### Bill of Quantities (BOQ) Endpoints

router = APIRouter()

### Generate BOQ from documents using AI
@router.post("/generate", response_model=BOQResponse, status_code=status.HTTP_201_CREATED)
async def generate_boq(
    request: BOQGenerateRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    boq_generator = BOQGenerator(db)
    
    # Create initial BOQ
    boq = await boq_generator.create_boq(
        project_id=request.project_id,
        source_document_ids=request.source_document_ids,
        template_id=request.template_id,
        title=request.title,
        created_by=current_user["id"]
    )
    
    # Generate BOQ items in background
    background_tasks.add_task(
        boq_generator.generate_boq_items,
        boq_id=str(boq["_id"]),
        document_ids=request.source_document_ids
    )
    
    return boq

### Get BOQ by ID
@router.get("/{boq_id}", response_model=BOQResponse)
async def get_boq(
    boq_id: str,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    boq_repo = BOQRepository(db)
    boq = await boq_repo.get_by_id(boq_id)
    
    if not boq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="BOQ not found"
        )
    
    return boq

### List all BOQs for a project
@router.get("/project/{project_id}", response_model=BOQListResponse)
async def list_project_boqs(
    project_id: str,
    page: int = 1,
    page_size: int = 20,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    boq_repo = BOQRepository(db)
    boqs = await boq_repo.list_by_project(
        project_id=project_id,
        skip=(page - 1) * page_size,
        limit=page_size
    )
    
    total = await boq_repo.count_by_project(project_id)
    
    return {
        "boqs": boqs,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size
    }

### Update BOQ
@router.put("/{boq_id}", response_model=BOQResponse)
async def update_boq(
    boq_id: str,
    boq_update: BOQUpdate,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    boq_repo = BOQRepository(db)
    boq = await boq_repo.get_by_id(boq_id)
    
    if not boq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="BOQ not found"
        )
    
    updated_boq = await boq_repo.update(
        boq_id=boq_id,
        update_data=boq_update.dict(exclude_unset=True)
    )
    
    return updated_boq

### Approve BOQ
@router.post("/{boq_id}/approve")
async def approve_boq(
    boq_id: str,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    boq_generator = BOQGenerator(db)
    approved_boq = await boq_generator.approve_boq(
        boq_id=boq_id,
        approved_by=current_user["id"]
    )
    
    if not approved_boq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="BOQ not found"
        )
    
    return approved_boq

### Export BOQ to PDF, Excel, or CSV
@router.post("/{boq_id}/export/{format}")
async def export_boq(
    boq_id: str,
    format: str,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    if format not in ['pdf', 'excel', 'csv']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format must be one of: pdf, excel, csv"
        )
    
    boq_generator = BOQGenerator(db)
    file_url = await boq_generator.export_boq(
        boq_id=boq_id,
        format=format
    )
    
    return {"file_url": file_url}
