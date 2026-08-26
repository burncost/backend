"""Project Memory Service — derives what's been bought vs remaining for a project.

Remaining = BOQ required qty − ordered qty.
Reuses existing data: MongoDB `boqs` (required quantities) and PostgreSQL
`order_items` (purchased quantities). No new table is created.
"""
import logging
from typing import Dict, List, Any, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

logger = logging.getLogger(__name__)


class ProjectMemoryService:
    """Project procurement memory: required, purchased, remaining per material."""

    def __init__(
        self,
        mongo_db: Optional[AsyncIOMotorDatabase] = None,
        pg_db: Optional[AsyncSession] = None,
    ):
        self.mongo_db = mongo_db
        self.pg_db = pg_db

    async def get_project_materials(self, project_id: str) -> Dict[str, Any]:
        """
        Return material memory for a project.
        Verifies project exists (Mongo), aggregates BOQ items (required qty),
        then subtracts ordered quantities (PostgreSQL order_items matched by
        product/description).
        """
        if self.mongo_db is None:
            return {"project_id": project_id, "exists": False, "materials": [], "error": "No MongoDB connection"}

        project = await self.mongo_db["projects"].find_one({"_id": self._oid(project_id)})
        if not project:
            return {"project_id": project_id, "exists": False, "materials": [], "error": "Project not found"}

        # Aggregate required quantities across all BOQs for this project.
        cursor = self.mongo_db["boqs"].find({"projectId": self._oid(project_id)})
        required: Dict[str, Dict[str, Any]] = {}
        for boq in await cursor.to_list(length=100):
            for element in (boq.get("boqData") or {}).get("elements", []) or boq.get("elements", []):
                for item in element.get("items", []):
                    desc = item.get("description") or item.get("itemCode") or "unknown"
                    qty = float(item.get("quantity") or 0)
                    unit = item.get("unit", "")
                    if desc not in required:
                        required[desc] = {"description": desc, "required_qty": 0.0, "unit": unit}
                    required[desc]["required_qty"] += qty

        if not required:
            return {"project_id": project_id, "exists": True, "materials": [], "message": "No BOQ items found for this project."}

        # Purchased quantities from PostgreSQL order_items (matched by product_name).
        purchased = await self._aggregate_purchased(project_id)

        materials = []
        for desc, info in required.items():
            qty_required = info["required_qty"]
            qty_purchased = purchased.get(desc.lower(), 0.0)
            qty_remaining = max(round(qty_required - qty_purchased, 2), 0.0)
            status = "complete" if qty_remaining <= 0 else ("partial" if qty_purchased > 0 else "pending")
            materials.append({
                **info,
                "purchased_qty": round(qty_purchased, 2),
                "remaining_qty": qty_remaining,
                "status": status,
            })

        # Sort by priority: pending > partial > complete.
        order = {"pending": 0, "partial": 1, "complete": 2}
        materials.sort(key=lambda m: order.get(m["status"], 3))

        return {
            "project_id": project_id,
            "exists": True,
            "title": project.get("title", project.get("name", "")),
            "materials": materials,
            "total_materials": len(materials),
            "remaining_materials_count": sum(1 for m in materials if m["status"] != "complete"),
        }

    async def _aggregate_purchased(self, project_id: str) -> Dict[str, float]:
        """Sum purchased qty per product from order_items (via orders → user/project)."""
        if self.pg_db is None:
            return {}
        try:
            # Match orders belonging to this project's owner via project.ownerId/creator.
            project = await self.mongo_db["projects"].find_one({"_id": self._oid(project_id)}) if self.mongo_db else None
            owner_id = str(project.get("clientId") or project.get("createdBy") or "") if project else ""

            result = await self.pg_db.execute(
                text("""
                    SELECT LOWER(oi.product_name) AS key, SUM(oi.quantity) AS total
                    FROM order_items oi
                    JOIN orders o ON o.id = oi.order_id
                    WHERE o.user_id = :owner_id
                    GROUP BY LOWER(oi.product_name)
                """),
                {"owner_id": owner_id},
            )
            return {row[0]: float(row[1] or 0) for row in result.fetchall()}
        except Exception as e:
            logger.warning(f"_aggregate_purchased failed: {e}")
            return {}

    @staticmethod
    def _oid(project_id: str):
        from bson import ObjectId
        try:
            return ObjectId(project_id)
        except Exception:
            return project_id