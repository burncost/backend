from app.core.celery_app import celery_app
from app.core.database import mongodb, connect_to_mongo
from app.services.document_service import DocumentService
import logging

logger = logging.getLogger(__name__)

### Background task to process uploaded document
@celery_app.task(name="process_document")
def process_document_task(document_id: str):
    try:
        logger.info(f"Starting document processing: {document_id}")
        
        # Ensure MongoDB connection
        if not mongodb.db:
            import asyncio
            asyncio.run(connect_to_mongo())
        
        # Process document
        document_service = DocumentService(mongodb.db)
        
        import asyncio
        asyncio.run(document_service.process_document(document_id))
        
        logger.info(f"Document processed successfully: {document_id}")
        
        return {"status": "success", "document_id": document_id}
        
    except Exception as e:
        logger.error(f"Error processing document {document_id}: {str(e)}")
        raise

### Process multiple documents in batch
@celery_app.task(name="batch_process_documents")
def batch_process_documents_task(document_ids: list):
    results = []
    
    for doc_id in document_ids:
        try:
            result = process_document_task.delay(doc_id)
            results.append({"document_id": doc_id, "task_id": result.id})
        except Exception as e:
            logger.error(f"Error queuing document {doc_id}: {str(e)}")
            results.append({"document_id": doc_id, "error": str(e)})
    
    return results