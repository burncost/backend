from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import List, Dict, Any, Optional

from app.repositories.base_repository import BaseRepository


class MaterialRatesRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, "material_rates")
    
    ### Search for material by name
    async def search_by_name(self, material_name: str) -> Optional[Dict[str, Any]]:
        query = {
            "materialName": {"$regex": material_name, "$options": "i"},
            "isActive": True
        }
        
        results = await self.find(query, limit=1)
        return results[0] if results else None
    
    ### Find materials by category
    async def find_by_category(self, category: str) -> List[Dict[str, Any]]:
        query = {"category": category, "isActive": True}
        return await self.find(query)
    
    ### Get regional rate for material
    async def get_regional_rate(
        self,
        material_id: str,
        state: str
    ) -> Optional[float]:
        material = await self.get_by_id(material_id)
        
        if not material:
            return None
        
        # Check regional rates
        regional_rates = material.get("regionalRates", [])
        for rate_entry in regional_rates:
            if rate_entry.get("state") == state:
                return rate_entry.get("rate")
        
        # Return base rate if no regional rate found
        return material.get("rate")