from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from decimal import Decimal


class BOQItemBase(BaseModel):
    itemNumber: str
    description: str
    unit: str
    quantity: float
    rate: Optional[float] = 0.0


class BOQSectionBase(BaseModel):
    sectionCode: str
    sectionName: str
    items: List[BOQItemBase] = []


class BOQTradeBase(BaseModel):
    tradeCode: str
    tradeName: str
    sections: List[BOQSectionBase] = []


class BOQCreate(BaseModel):
    projectId: str
    title: str
    sourceDocumentIds: List[str] = []
    templateId: Optional[str] = None


class BOQUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    trades: Optional[List[Dict[str, Any]]] = None


class BOQResponse(BaseModel):
    id: str = Field(alias="_id")
    boqNumber: str
    projectId: str
    title: str
    status: str
    version: int
    generationMethod: str
    trades: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {}
    createdAt: datetime
    updatedAt: Optional[datetime] = None

    class Config:
        populate_by_name = True


class BOQListResponse(BaseModel):
    boqs: List[BOQResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class BOQGenerateRequest(BaseModel):
    project_id: str
    source_document_ids: List[str]
    template_id: Optional[str] = None
    title: str