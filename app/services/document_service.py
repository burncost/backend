from motor.motor_asyncio import AsyncIOMotorDatabase
from fastapi import UploadFile
from typing import Dict, Any, Optional
from bson import ObjectId
from datetime import datetime
import logging
import io

from app.repositories.document_repository import DocumentRepository
from app.services.cad_parser import CADParser
from app.services.pdf_extractor import PDFExtractor
from app.services.ai_service import AIService
from app.utils.storage import StorageService

### Document Service - CAD/PDF processing and storage

logger = logging.getLogger(__name__)


class DocumentService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.document_repo = DocumentRepository(db)
        self.storage_service = StorageService()
        self.cad_parser = CADParser()
        self.pdf_extractor = PDFExtractor()
        self.ai_service = AIService()
    
    ###  Upload document to cloud storage and create database record
    async def upload_document(
        self,
        file: UploadFile,
        project_id: str,
        document_category: str,
        uploaded_by: str
    ) -> Dict[str, Any]:
        # Determine file type
        file_ext = file.filename.split('.')[-1].lower()
        file_type_map = {
            'dwg': 'dwg',
            'dxf': 'dxf',
            'rvt': 'rvt',
            'ifc': 'ifc',
            'pdf': 'pdf'
        }
        file_type = file_type_map.get(file_ext, 'other')
        
        # Upload to cloud storage
        content = await file.read()
        file_size = len(content)
        
        storage_path = f"documents/{project_id}/{file.filename}"
        file_url = await self.storage_service.upload_bytes(
            content=content,
            path=storage_path,
            content_type=file.content_type
        )
        
        # Generate document number
        doc_number = await self._generate_document_number(file_type)
        
        # Create document record
        document_data = {
            "projectId": ObjectId(project_id),
            "documentNumber": doc_number,
            "fileName": file.filename,
            "originalFileName": file.filename,
            "fileType": file_type,
            "documentCategory": document_category,
            "version": 1,
            "fileSize": file_size,
            "mimeType": file.content_type,
            "storageLocation": {
                "url": file_url,
                "path": storage_path
            },
            "status": "uploaded",
            "uploadedBy": ObjectId(uploaded_by),
            "uploadedAt": datetime.utcnow()
        }
        
        document = await self.document_repo.create(document_data)
        
        logger.info(f"Document uploaded: {document['_id']} - {file.filename}")
        
        return document
    
    ### Process document: extract metadata and run AI analysis
    ### This is a background task
    async def process_document(self, document_id: str):
        try:
            # Get document
            document = await self.document_repo.get_by_id(document_id)
            if not document:
                logger.error(f"Document not found: {document_id}")
                return
            
            # Update status to processing
            await self.document_repo.update(
                document_id,
                {"status": "processing"}
            )
            
            # Download file from storage
            file_content = await self.storage_service.download_file(
                document["storageLocation"]["url"]
            )
            
            # Extract metadata based on file type
            extracted_metadata = {}
            
            if document["fileType"] in ['dwg', 'dxf', 'rvt', 'ifc']:
                # Parse CAD file
                extracted_metadata = await self.cad_parser.parse(
                    file_content,
                    document["fileType"]
                )
            elif document["fileType"] == 'pdf':
                # Extract from PDF
                extracted_metadata = await self.pdf_extractor.extract(
                    file_content
                )
            
            # Run AI analysis
            ai_analysis = await self.ai_service.analyze_document(
                file_content=file_content,
                file_type=document["fileType"],
                extracted_metadata=extracted_metadata
            )
            
            # Generate thumbnail
            thumbnail_url = await self._generate_thumbnail(
                file_content,
                document["fileType"],
                document_id
            )
            
            # Update document with results
            update_data = {
                "extractedMetadata": extracted_metadata,
                "aiAnalysis": ai_analysis,
                "thumbnailUrl": thumbnail_url,
                "status": "processed",
                "processedAt": datetime.utcnow()
            }
            
            await self.document_repo.update(document_id, update_data)
            
            logger.info(f"Document processed successfully: {document_id}")
            
        except Exception as e:
            logger.error(f"Error processing document {document_id}: {str(e)}")
            
            # Update status to failed
            await self.document_repo.update(
                document_id,
                {
                    "status": "failed",
                    "aiAnalysis": {
                        "processed": False,
                        "processingErrors": [str(e)]
                    }
                }
            )
    
    ### Delete document from storage and database
    async def delete_document(self, document_id: str) -> bool:
        document = await self.document_repo.get_by_id(document_id)
        if not document:
            return False
        
        # Delete from cloud storage
        if "storageLocation" in document and "url" in document["storageLocation"]:
            await self.storage_service.delete_file(
                document["storageLocation"]["url"]
            )
        
        # Delete from database
        deleted = await self.document_repo.delete(document_id)
        
        logger.info(f"Document deleted: {document_id}")
        
        return deleted
    
    ### Generate unique document number
    async def _generate_document_number(self, file_type: str) -> str:
        prefix_map = {
            'dwg': 'DWG',
            'dxf': 'DXF',
            'rvt': 'RVT',
            'ifc': 'IFC',
            'pdf': 'PDF',
            'other': 'DOC'
        }
        prefix = prefix_map.get(file_type, 'DOC')
        
        # Get count of documents of this type
        count = await self.document_repo.count_by_type(file_type)
        
        return f"{prefix}-{count + 1:05d}"
    
    ###  Generate thumbnail for document
    async def _generate_thumbnail(
        self,
        file_content: bytes,
        file_type: str,
        document_id: str
    ) -> Optional[str]:
        try:
            if file_type == 'pdf':
                # Convert first page to image
                thumbnail_bytes = await self.pdf_extractor.generate_thumbnail(
                    file_content
                )
                
                if thumbnail_bytes:
                    # Upload thumbnail
                    thumbnail_path = f"thumbnails/{document_id}.jpg"
                    thumbnail_url = await self.storage_service.upload_bytes(
                        content=thumbnail_bytes,
                        path=thumbnail_path,
                        content_type="image/jpeg"
                    )
                    return thumbnail_url
            
            # CAD thumbnails would require specialized libraries
            # For now, return None for CAD files
            return None
            
        except Exception as e:
            logger.error(f"Error generating thumbnail: {str(e)}")
            return None