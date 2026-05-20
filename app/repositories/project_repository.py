from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import List, Dict, Any
from bson import ObjectId

from app.repositories.base_repository import BaseRepository


class ProjectRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, "projects")
    
    ### Find projects by client
    async def find_by_client(
        self,
        client_id: str,
        skip: int = 0,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        query = {"clientId": ObjectId(client_id)}
        sort = [("createdAt", -1)]
        
        return await self.find(query, skip=skip, limit=limit, sort=sort)
    
    ### Find projects by status
    async def find_by_status(self, status: str) -> List[Dict[str, Any]]:
        query = {"status": status}
        return await self.find(query)
    
    ### Count all projects
    async def count_all(self) -> int:
        return await self.count({})
    