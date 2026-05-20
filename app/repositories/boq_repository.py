"""
BOQ Repository
"""
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import List, Dict, Any
from bson import ObjectId

from app.repositories.base_repository import BaseRepository


class BOQRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, "boqs")
    
    ### List BOQs for a project
    async def list_by_project(
        self,
        project_id: str,
        skip: int = 0,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        query = {"projectId": ObjectId(project_id)}
        sort = [("version", -1), ("createdAt", -1)]
        
        return await self.find(query, skip=skip, limit=limit, sort=sort)
    
    ### Count BOQs for a project
    async def count_by_project(self, project_id: str) -> int:
        query = {"projectId": ObjectId(project_id)}
        return await self.count(query)
    
    ### Count all BOQs
    async def count_all(self) -> int:
        return await self.count({})
    
    ### Find BOQs by status
    async def find_by_status(self, status: str) -> List[Dict[str, Any]]:
        query = {"status": status}
        return await self.find(query)
    
    ### Add export record to BOQ
    async def add_export(self, boq_id: str, export_data: Dict[str, Any]) -> bool:
        try:
            await self.collection.update_one(
                {"_id": ObjectId(boq_id)},
                {"$push": {"exports": export_data}}
            )
            return True
        except Exception:
            return False
        