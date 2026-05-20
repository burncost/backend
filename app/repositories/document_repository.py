from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import List, Dict, Any, Optional
from bson import ObjectId

from app.repositories.base_repository import BaseRepository

class DocumentRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, "documents")
    
    ### List documents for a project
    async def list_by_project(
        self,
        project_id: str,
        skip: int = 0,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        query = {"projectId": ObjectId(project_id)}
        sort = [("uploadedAt", -1)]
        
        return await self.find(query, skip=skip, limit=limit, sort=sort)
    
    ### Count documents for a project
    async def count_by_project(self, project_id: str) -> int:
        query = {"projectId": ObjectId(project_id)}
        return await self.count(query)
    
    ### Count documents by file type
    async def count_by_type(self, file_type: str) -> int:
        query = {"fileType": file_type}
        return await self.count(query)
    
    ### Find documents by status
    async def find_by_status(self, status: str) -> List[Dict[str, Any]]:
        query = {"status": status}
        return await self.find(query)
    
    ### Search documents by filename or content
    async def search(
        self,
        search_term: str,
        project_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        query = {
            "$or": [
                {"fileName": {"$regex": search_term, "$options": "i"}},
                {"originalFileName": {"$regex": search_term, "$options": "i"}},
            ]
        }
        
        if project_id:
            query["projectId"] = ObjectId(project_id)
        
        return await self.find(query)