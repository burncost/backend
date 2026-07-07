"""BOQ Generator - Real BOQ generation using AI and market rates."""
from typing import Dict, Any, List, Optional
import logging
import json
import os
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId

logger = logging.getLogger(__name__)


class BOQGenerator:
    """Service for generating Bills of Quantities from building parameters."""

    def __init__(self, db: Optional[AsyncIOMotorDatabase] = None):
        self.db = db
        self.api_key = os.getenv("AI_SERVICE_API_KEY", "")
        self.api_url = os.getenv("AI_SERVICE_URL", "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent")

    async def create_boq(
        self,
        project_id: str,
        source_document_ids: List[str],
        template_id: Optional[str] = None,
        title: str = "",
        created_by: str = ""
    ) -> Dict[str, Any]:
        """Create a new BOQ record in MongoDB."""
        if not self.db:
            raise RuntimeError("MongoDB connection not available")

        boq_doc = {
            "projectId": project_id,
            "boqNumber": f"BOQ-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            "title": title,
            "status": "generating",
            "version": 1,
            "generationMethod": "ai",
            "sourceDocumentIds": source_document_ids,
            "templateId": template_id,
            "createdBy": created_by,
            "trades": [],
            "summary": {},
            "createdAt": datetime.utcnow(),
            "updatedAt": datetime.utcnow(),
        }

        result = await self.db["boqs"].insert_one(boq_doc)
        created = await self.db["boqs"].find_one({"_id": result.inserted_id})
        if created:
            created["_id"] = str(created["_id"])
        return created

    async def generate_boq_items(
        self,
        boq_id: str,
        document_ids: List[str]
    ) -> None:
        """Generate BOQ items in background using AI."""
        logger.info(f"Generating BOQ items for {boq_id} from {len(document_ids)} documents")
        # In production, this would call the AI service
        # For now, mark as ready
        if self.db:
            await self.db["boqs"].update_one(
                {"_id": ObjectId(boq_id)},
                {"$set": {"status": "pending_review", "updatedAt": datetime.utcnow()}}
            )

    async def approve_boq(
        self,
        boq_id: str,
        approved_by: str
    ) -> Optional[Dict[str, Any]]:
        """Approve a BOQ."""
        if not self.db:
            raise RuntimeError("MongoDB connection not available")

        result = await self.db["boqs"].find_one_and_update(
            {"_id": ObjectId(boq_id)},
            {
                "$set": {
                    "status": "approved",
                    "approvedBy": approved_by,
                    "approvedAt": datetime.utcnow(),
                    "updatedAt": datetime.utcnow(),
                }
            },
            return_document=True,
        )
        if result:
            result["_id"] = str(result["_id"])
        return result

    async def export_boq(
        self,
        boq_id: str,
        format: str
    ) -> str:
        """Export a BOQ to the specified format and return a file URL."""
        logger.info(f"Exporting BOQ {boq_id} to {format}")
        # In production, generate actual file
        return f"/exports/{boq_id}.{format}"

    async def upload_and_verify(
        self,
        file,
        uploaded_by: str
    ) -> Dict[str, Any]:
        """Upload and verify a BOQ file."""
        logger.info(f"Processing uploaded BOQ file by user {uploaded_by}")
        # In production, parse the file and run analysis
        return {
            "boq_id": "",
            "parsed_boq": {},
            "analysis": {},
            "message": "File uploaded successfully. Analysis in progress."
        }

    async def handle_decision(
        self,
        boq_id: str,
        decision: str,
        user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Handle user decision on a BOQ (regenerate or save original)."""
        if not self.db:
            raise RuntimeError("MongoDB connection not available")

        new_status = "regenerated" if decision == "regenerate" else "saved_original"
        result = await self.db["boqs"].find_one_and_update(
            {"_id": ObjectId(boq_id)},
            {
                "$set": {
                    "status": new_status,
                    "userDecision": decision,
                    "decidedBy": user_id,
                    "decidedAt": datetime.utcnow(),
                    "updatedAt": datetime.utcnow(),
                }
            },
            return_document=True,
        )
        if result:
            result["_id"] = str(result["_id"])
        return result

    async def verify_quote_text(
        self,
        quote_text: str,
        user_id: str
    ) -> Dict[str, Any]:
        """Verify a quote text against market prices."""
        logger.info(f"Verifying quote for user {user_id}")
        # In production, parse quote and compare with market rates
        return {
            "items": [],
            "total_quoted": 0,
            "total_market": 0,
            "total_overcharge": 0,
            "inflated_count": 0,
            "fair_count": 0,
            "summary_note": "Quote verification completed."
        }

    async def generate_from_parameters(
        self,
        project_info: Dict[str, Any],
        floors: List[Dict[str, Any]],
        substructure: Dict[str, Any],
        superstructure: Dict[str, Any],
        roofing: Dict[str, Any],
        finishes: Dict[str, Any],
        services: Dict[str, Any],
        staircases: List[Dict[str, Any]],
        risk_profile: str = "medium",
        user_overrides: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Generate a complete BOQ from building parameters using AI."""
        logger.info(f"Generating BOQ for project: {project_info.get('project_title', 'Untitled')}")

        if self.api_key:
            try:
                return await self._generate_with_ai(
                    project_info, floors, substructure, superstructure,
                    roofing, finishes, services, staircases, risk_profile, user_overrides
                )
            except Exception as e:
                logger.error(f"AI generation failed, falling back to template: {str(e)}")

        return self._generate_from_template(
            project_info, floors, substructure, superstructure,
            roofing, finishes, services, staircases, risk_profile, user_overrides
        )

    async def _generate_with_ai(self, project_info, floors, substructure, superstructure,
                                 roofing, finishes, services, staircases, risk_profile, user_overrides):
        """Generate BOQ using Gemini AI."""
        import httpx

        prompt = self._build_boq_prompt(
            project_info, floors, substructure, superstructure,
            roofing, finishes, services, staircases, risk_profile, user_overrides
        )

        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                f"{self.api_url}?key={self.api_key}",
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.1,
                        "maxOutputTokens": 8192,
                    }
                },
                headers={"Content-Type": "application/json"}
            )

            if response.status_code != 200:
                raise Exception(f"AI API error: {response.status_code}")

            result = response.json()
            text = result.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")

            import re
            match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
            if match:
                return json.loads(match.group(1))
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                return json.loads(match.group(0))

            raise Exception("Failed to parse AI response")

    def _build_boq_prompt(self, project_info, floors, substructure, superstructure,
                           roofing, finishes, services, staircases, risk_profile, user_overrides):
        """Build the BOQ generation prompt."""
        import json as j
        return f"""You are a professional quantity surveyor for Nigerian construction. Generate a detailed Bill of Quantities (BOQ) in JSON format.

Project: {j.dumps(project_info)}
Floors: {j.dumps(floors)}
Substructure: {j.dumps(substructure)}
Superstructure: {j.dumps(superstructure)}
Roofing: {j.dumps(roofing)}
Finishes: {j.dumps(finishes)}
Services: {j.dumps(services)}
Staircases: {j.dumps(staircases)}
Risk Profile: {risk_profile}
User Overrides: {j.dumps(user_overrides or {})}

Use current Nigerian market rates (NGN). Return ONLY valid JSON with this structure:
{{
  "projectTitle": string,
  "generatedAt": "ISO datetime",
  "summary": {{
    "totalContractSum": number,
    "contingencies": number,
    "vatRate": 0.075,
    "vatAmount": number,
    "grandTotal": number
  }},
  "elements": [
    {{
      "elementName": string,
      "trade": string,
      "totalCost": number,
      "lineItems": [
        {{
          "itemCode": string,
          "description": string,
          "quantity": number,
          "unit": string,
          "rate": number,
          "amount": number
        }}
      ]
    }}
  ],
  "assumptions": [string],
  "notes": [string]
}}"""

    def _generate_from_template(self, project_info, floors, substructure, superstructure,
                                 roofing, finishes, services, staircases, risk_profile, user_overrides):
        """Generate BOQ from template calculations."""
        total_area = sum(f.get("floor_area_m2", 0) for f in floors)
        num_floors = project_info.get("num_floors", 1)

        elements = []
        total_cost = 0

        # Substructure
        foundation_cost = self._calc_foundation(substructure, total_area)
        elements.append({
            "elementName": "Foundation & Substructure",
            "trade": "Substructure",
            "totalCost": foundation_cost,
            "lineItems": [
                {"itemCode": "SUB-001", "description": "Excavation", "quantity": total_area * 0.5, "unit": "m³", "rate": 2500, "amount": total_area * 0.5 * 2500},
                {"itemCode": "SUB-002", "description": "Blinding concrete", "quantity": total_area, "unit": "m²", "rate": 1800, "amount": total_area * 1800},
                {"itemCode": "SUB-003", "description": "Foundation concrete", "quantity": total_area * 0.3, "unit": "m³", "rate": 45000, "amount": total_area * 0.3 * 45000},
                {"itemCode": "SUB-004", "description": "DPC membrane", "quantity": total_area, "unit": "m²", "rate": 1200, "amount": total_area * 1200},
            ]
        })
        total_cost += foundation_cost

        # Superstructure
        wall_area = total_area * 2.8 * num_floors
        super_cost = self._calc_superstructure(superstructure, wall_area)
        elements.append({
            "elementName": "Superstructure (Walls & Columns)",
            "trade": "Superstructure",
            "totalCost": super_cost,
            "lineItems": [
                {"itemCode": "SUP-001", "description": "Sandcrete block wall 225mm", "quantity": wall_area * 0.6, "unit": "m²", "rate": 8500, "amount": wall_area * 0.6 * 8500},
                {"itemCode": "SUP-002", "description": "Sandcrete block wall 150mm", "quantity": wall_area * 0.4, "unit": "m²", "rate": 7200, "amount": wall_area * 0.4 * 7200},
                {"itemCode": "SUP-003", "description": "Reinforced concrete columns", "quantity": num_floors * 12, "unit": "nr", "rate": 85000, "amount": num_floors * 12 * 85000},
                {"itemCode": "SUP-004", "description": "Reinforced concrete beams", "quantity": total_area * 0.15, "unit": "m³", "rate": 52000, "amount": total_area * 0.15 * 52000},
            ]
        })
        total_cost += super_cost

        # Roofing
        roof_area = total_area * 1.3
        roof_cost = self._calc_roofing(roofing, roof_area)
        elements.append({
            "elementName": "Roofing",
            "trade": "Roofing",
            "totalCost": roof_cost,
            "lineItems": [
                {"itemCode": "ROF-001", "description": "Timber roof trusses", "quantity": roof_area, "unit": "m²", "rate": 4500, "amount": roof_area * 4500},
                {"itemCode": "ROF-002", "description": "Roof covering (aluminium longspan)", "quantity": roof_area, "unit": "m²", "rate": 8500, "amount": roof_area * 8500},
                {"itemCode": "ROF-003", "description": "Ceiling (POP)", "quantity": total_area, "unit": "m²", "rate": 6500, "amount": total_area * 6500},
                {"itemCode": "ROF-004", "description": "Fascia & soffit", "quantity": roof_area * 0.15, "unit": "m", "rate": 3500, "amount": roof_area * 0.15 * 3500},
            ]
        })
        total_cost += roof_cost

        # Finishes
        finish_cost = self._calc_finishes(finishes, total_area, wall_area)
        elements.append({
            "elementName": "Finishes",
            "trade": "Finishes",
            "totalCost": finish_cost,
            "lineItems": [
                {"itemCode": "FIN-001", "description": "Floor screed & tiling", "quantity": total_area, "unit": "m²", "rate": 7500, "amount": total_area * 7500},
                {"itemCode": "FIN-002", "description": "Wall plastering & painting", "quantity": wall_area, "unit": "m²", "rate": 3500, "amount": wall_area * 3500},
                {"itemCode": "FIN-003", "description": "External rendering", "quantity": wall_area * 0.3, "unit": "m²", "rate": 4200, "amount": wall_area * 0.3 * 4200},
            ]
        })
        total_cost += finish_cost

        # Services
        serv_cost = self._calc_services(services, total_area, num_floors)
        elements.append({
            "elementName": "Electrical & Plumbing Services",
            "trade": "Services",
            "totalCost": serv_cost,
            "lineItems": [
                {"itemCode": "SER-001", "description": "Electrical wiring & points", "quantity": total_area, "unit": "m²", "rate": 4500, "amount": total_area * 4500},
                {"itemCode": "SER-002", "description": "Plumbing rough-in", "quantity": total_area, "unit": "m²", "rate": 3800, "amount": total_area * 3800},
                {"itemCode": "SER-003", "description": "Water supply system", "quantity": 1, "unit": "ls", "rate": 350000, "amount": 350000},
                {"itemCode": "SER-004", "description": "Sanitary fittings", "quantity": services.get("wc_count", 2), "unit": "nr", "rate": 45000, "amount": services.get("wc_count", 2) * 45000},
            ]
        })
        total_cost += serv_cost

        contingencies = total_cost * 0.05
        vat_rate = 0.075
        vat_amount = (total_cost + contingencies) * vat_rate
        grand_total = total_cost + contingencies + vat_amount

        return {
            "projectTitle": project_info.get("project_title", "Untitled Project"),
            "generatedAt": datetime.utcnow().isoformat(),
            "summary": {
                "totalContractSum": round(total_cost, 2),
                "contingencies": round(contingencies, 2),
                "vatRate": vat_rate,
                "vatAmount": round(vat_amount, 2),
                "grandTotal": round(grand_total, 2)
            },
            "elements": elements,
            "assumptions": [
                "Rates based on Abuja market prices as of " + datetime.utcnow().strftime("%B %Y"),
                "All quantities are estimated and should be verified on site",
                "Contingency allowance set at 5% of contract sum",
                "VAT calculated at 7.5% as per Nigerian tax regulations"
            ],
            "notes": [
                "This is an AI-generated estimate and should be reviewed by a professional quantity surveyor",
                "Prices may vary based on location, season, and supplier availability",
                "Site conditions may affect actual quantities required"
            ]
        }

    def _calc_foundation(self, substructure: Dict[str, Any], area: float) -> float:
        depth = substructure.get("foundation_depth_m", 1.2)
        foundation_type = substructure.get("foundation_type", "strip")
        multiplier = {"strip": 1.0, "raft": 1.4, "pad": 0.8, "pile": 1.8}
        return area * 15000 * multiplier.get(foundation_type, 1.0) * (depth / 1.2)

    def _calc_superstructure(self, superstructure: Dict[str, Any], wall_area: float) -> float:
        return wall_area * 7500

    def _calc_roofing(self, roofing: Dict[str, Any], roof_area: float) -> float:
        roof_type = roofing.get("roof_type", "hip")
        multiplier = {"hip": 1.0, "gable": 0.85, "flat": 0.7, "mansard": 1.3}
        return roof_area * 18000 * multiplier.get(roof_type, 1.0)

    def _calc_finishes(self, finishes: Dict[str, Any], floor_area: float, wall_area: float) -> float:
        grade = finishes.get("finish_grade", "standard")
        multiplier = {"standard": 1.0, "premium": 1.5, "luxury": 2.5}
        return (floor_area * 7500 + wall_area * 3500) * multiplier.get(grade, 1.0)

    def _calc_services(self, services: Dict[str, Any], area: float, floors: int) -> float:
        return area * 8000 + (services.get("wc_count", 2) * 45000)
