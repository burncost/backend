from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


class DocumentBase(BaseModel):
    fileName: str
    fileType: str
    documentCategory: str


class DocumentCreate(DocumentBase):
    projectId: str
    uploadedBy: str


class DocumentResponse(BaseModel):
    id: str = Field(alias="_id")
    projectId: str
    documentNumber: str
    fileName: str
    fileType: str
    documentCategory: str
    version: int
    fileSize: int
    status: str
    uploadedAt: datetime
    processedAt: Optional[datetime] = None
    extractedMetadata: Optional[Dict[str, Any]] = None
    aiAnalysis: Optional[Dict[str, Any]] = None
    thumbnailUrl: Optional[str] = None

    class Config:
        populate_by_name = True


class DocumentListResponse(BaseModel):
    documents: List[DocumentResponse]
    total: int
    page: int
    page_size: int
    total_pages: int