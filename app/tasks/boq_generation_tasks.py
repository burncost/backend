from app.core.celery_app import celery_app
from app.core.database import mongodb, connect_to_mongo
from app.services.boq_generator import BOQGenerator
import logging

logger = logging.getLogger(__name__)

### Generate BOQ items from documents
@celery_app.task(name="generate_boq_items")
def generate_boq_items_task(boq_id: str, document_ids: list):
    try:
        logger.info(f"Starting BOQ generation: {boq_id}")
        
        # Ensure MongoDB connection
        if not mongodb.db:
            import asyncio
            asyncio.run(connect_to_mongo())
        
        # Generate BOQ
        boq_generator = BOQGenerator(mongodb.db)
        
        import asyncio
        asyncio.run(boq_generator.generate_boq_items(boq_id, document_ids))
        
        logger.info(f"BOQ generated successfully: {boq_id}")
        
        return {"status": "success", "boq_id": boq_id}
        
    except Exception as e:
        logger.error(f"Error generating BOQ {boq_id}: {str(e)}")
        raise

### Recalculate BOQ totals and rates
@celery_app.task(name="recalculate_boq")
def recalculate_boq_task(boq_id: str):
    try:
        logger.info(f"Recalculating BOQ: {boq_id}")
        
        # Implementation would recalculate all rates and totals
        # based on updated material rates
        
        return {"status": "success", "boq_id": boq_id}
        
    except Exception as e:
        logger.error(f"Error recalculating BOQ {boq_id}: {str(e)}")
        raise
