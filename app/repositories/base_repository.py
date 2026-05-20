from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import Dict, Any, List, Optional
from bson import ObjectId
from datetime import datetime
import logging

### Base Repository for MongoDB operations

logger = logging.getLogger(__name__)


class BaseRepository:
    def __init__(self, db: AsyncIOMotorDatabase, collection_name: str):
        self.db = db
        self.collection = db[collection_name]
    
    ### Create a new document
    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        data['createdAt'] = datetime.utcnow()
        data['updatedAt'] = datetime.utcnow()
        
        result = await self.collection.insert_one(data)
        created_doc = await self.collection.find_one({"_id": result.inserted_id})
        
        return self._serialize_doc(created_doc)
    
    ### Get document by ID
    async def get_by_id(self, doc_id: str) -> Optional[Dict[str, Any]]:
        try:
            doc = await self.collection.find_one({"_id": ObjectId(doc_id)})
            return self._serialize_doc(doc) if doc else None
        except Exception as e:
            logger.error(f"Error getting document {doc_id}: {str(e)}")
            return None
    
    ### Update document
    async def update(
        self,
        doc_id: str,
        update_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        try:
            update_data['updatedAt'] = datetime.utcnow()
            
            result = await self.collection.find_one_and_update(
                {"_id": ObjectId(doc_id)},
                {"$set": update_data},
                return_document=True
            )
            
            return self._serialize_doc(result) if result else None
        except Exception as e:
            logger.error(f"Error updating document {doc_id}: {str(e)}")
            return None
    
    ### Delete document
    async def delete(self, doc_id: str) -> bool:
        try:
            result = await self.collection.delete_one({"_id": ObjectId(doc_id)})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error deleting document {doc_id}: {str(e)}")
            return False
    
    async def find(
        self,
        query: Dict[str, Any],
        skip: int = 0,
        limit: int = 20,
        sort: Optional[List[tuple]] = None
    ) -> List[Dict[str, Any]]:
        """Find documents matching query"""
        cursor = self.collection.find(query)
        
        if sort:
            cursor = cursor.sort(sort)
        
        cursor = cursor.skip(skip).limit(limit)
        
        docs = await cursor.to_list(length=limit)
        return [self._serialize_doc(doc) for doc in docs]
    
    async def count(self, query: Dict[str, Any] = {}) -> int:
        """Count documents matching query"""
        return await self.collection.count_documents(query)
    
    ### Convert MongoDB document to JSON-serializable format
    def _serialize_doc(self, doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:        
        if not doc:
            return None
        
        # Convert ObjectId to string
        if '_id' in doc:
            doc['_id'] = str(doc['_id'])
        
        # Convert other ObjectIds
        for key, value in doc.items():
            if isinstance(value, ObjectId):
                doc[key] = str(value)
            elif isinstance(value, list):
                doc[key] = [
                    str(item) if isinstance(item, ObjectId) else item
                    for item in value
                ]
        
        return doc
    