from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
    UploadFile,
    File,
    BackgroundTasks,
    Form
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from uuid import UUID
from bson import ObjectId

from app.config import settings
import cloudinary
import cloudinary.uploader

from app.core.database import get_db
from app.core.database import get_mongodb

from app.models.vendor import Vendor
from app.models.user import User
from app.repositories.document_repository import DocumentRepository
from app.services.document_service import DocumentService
from app.api.deps import get_current_user
from app.schemas.vendor import VendorUpdate
from app.schemas.document import DocumentResponse, DocumentListResponse

### Document Upload and Processing Endpoints (CAD/PDF)

cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET
)

router = APIRouter()

DOCUMENT_FIELD_MAP = {
    "cac_certificate": "cac_certificate",
    "tax_clearance": "tax_clearance",
    "business_license": "business_license",
    "utility_bill": "utility_bill"
}

### upload to cloudinary
@router.post("/upload-document/", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    document_type: str = Form(...),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)):

    allowed_types = ["image/jpeg", "image/png", "application/pdf"]
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file type"
        )
    
    # Check if user already has a vendor profile
    result = await db.execute(
        select(Vendor).where(Vendor.user_id == current_user.id)
    )
    
    vendor = result.scalar_one_or_none()

    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor profile not found"
        )

    try:
        upload_result = cloudinary.uploader.upload(
            file.file,
            folder="supplier_documents",
            resource_type="auto"
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="File upload failed"
        )

    img_url = upload_result.get("secure_url")

    if not img_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve uploaded file URL"
        )

    field_name = DOCUMENT_FIELD_MAP[document_type]
    setattr(vendor, field_name, img_url)

    vendor.verification_status = "pending"

    await db.commit()
    await db.refresh(vendor)

    return {
        "message": "Document uploaded successfully",
        "document_type": document_type,
        "file_url": img_url
    }

### Upload a CAD or PDF document
@router.post("/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_cad_document(
    file: UploadFile = File(...),
    project_id: str = None,
    document_category: str = "other",
    background_tasks: BackgroundTasks = None,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    # Validate file type
    allowed_extensions = ['.dwg', '.dxf', '.rvt', '.ifc', '.pdf']
    file_ext = '.' + file.filename.split('.')[-1].lower()
    
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type not supported. Allowed: {', '.join(allowed_extensions)}"
        )
    
    # Validate file size
    content = await file.read()
    if len(content) > 100 * 1024 * 1024:  # 100MB
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File size exceeds 100MB limit"
        )
    
    # Reset file pointer
    await file.seek(0)
    
    # Upload document
    document_service = DocumentService(db)
    document = await document_service.upload_document(
        file=file,
        project_id=project_id,
        document_category=document_category,
        uploaded_by=current_user.id
    )
    
    # Process document in background
    background_tasks.add_task(
        document_service.process_document,
        document_id=str(document["_id"])
    )
    
    return document

### Get document by ID
@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: str,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    document_repo = DocumentRepository(db)
    document = await document_repo.get_by_id(document_id)
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    return document

### List all documents for a project
@router.get("/project/{project_id}", response_model=DocumentListResponse)
async def list_project_documents(
    project_id: str,
    page: int = 1,
    page_size: int = 20,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    document_repo = DocumentRepository(db)
    documents = await document_repo.list_by_project(
        project_id=project_id,
        skip=(page - 1) * page_size,
        limit=page_size
    )
    
    total = await document_repo.count_by_project(project_id)
    
    return {
        "documents": documents,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size
    }

### Trigger document processing (extraction and AI analysis)
@router.post("/{document_id}/process")
async def process_document(
    document_id: str,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    document_repo = DocumentRepository(db)
    document = await document_repo.get_by_id(document_id)
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    # Add processing task to background
    document_service = DocumentService(db)
    background_tasks.add_task(
        document_service.process_document,
        document_id=document_id
    )
    
    return {"message": "Document processing started"}

### Delete a document
@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    document_service = DocumentService(db)
    deleted = await document_service.delete_document(document_id)
    
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )