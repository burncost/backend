"""
BOQ Repository
"""
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import List, Dict, Any, Optional
from bson import ObjectId

from app.repositories.base_repository import BaseRepository


def _created_key(doc: Dict[str, Any]) -> str:
    """Sort key that normalises datetime / ISO-string / missing timestamps."""
    val = doc.get("createdAt")
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val or "")


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
    
    ### List BOQs for a user (generated bills + bills uploaded for verification)
    async def list_by_user(
        self,
        user_id: str,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {"createdBy": user_id}
        if status:
            query["status"] = status
        sort = [("createdAt", -1)]
        # Pull enough from each source that the merged, globally-sorted page is
        # correct: the first (skip + limit) rows of a merged desc sort can only
        # come from the first (skip + limit) rows of each source.
        fetch = skip + limit
        results = await self.find(query, skip=0, limit=fetch, sort=sort)

        # Tag how each bill was created so the UI can show a source badge.
        for doc in results:
            doc.setdefault("source", doc.get("generationMethod") or "generated")

        # Bills uploaded for verification live in their own collection; surface
        # them in the same list (they are bills the user owns) tagged as such.
        if status in (None, "verification"):
            cursor = (
                self.db["boq_verifications"]
                .find({"uploadedBy": user_id})
                .sort("uploadedAt", -1)
                .skip(0)
                .limit(fetch)
            )
            for v in await cursor.to_list(length=fetch):
                results.append({
                    "_id": str(v.get("_id")),
                    "projectId": v.get("filename") or "",
                    "boqNumber": "VER-" + str(v.get("_id"))[-6:].upper(),
                    "title": v.get("filename") or "Uploaded BOQ",
                    "status": "verification",
                    "version": 1,
                    "source": "verification",
                    "generationMethod": "verification",
                    "createdAt": v.get("uploadedAt"),
                    "boqData": {"summary": {"total_expected": v.get("total_quoted")}},
                })

        # Merge both sources newest-first, then return just the requested page.
        results.sort(key=_created_key, reverse=True)
        return results[skip: skip + limit]

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
        