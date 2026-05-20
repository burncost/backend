from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import List, Dict, Any, Optional
from bson import ObjectId
from datetime import datetime
import logging

from app.repositories.boq_repository import BOQRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.material_rates_repository import MaterialRatesRepository
from app.services.ai_service import AIService

### BOQ Generator Service - Generate Bill of Quantities from documents

logger = logging.getLogger(__name__)

class BOQGenerator:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.boq_repo = BOQRepository(db)
        self.document_repo = DocumentRepository(db)
        self.material_rates_repo = MaterialRatesRepository(db)
        self.ai_service = AIService()
    
    ### Create initial BOQ structure
    async def create_boq(
        self,
        project_id: str,
        source_document_ids: List[str],
        template_id: Optional[str],
        title: str,
        created_by: str
    ) -> Dict[str, Any]:
        # Generate BOQ number
        boq_number = await self._generate_boq_number()
        
        boq_data = {
            "boqNumber": boq_number,
            "projectId": ObjectId(project_id),
            "title": title,
            "sourceDocuments": [ObjectId(doc_id) for doc_id in source_document_ids],
            "templateId": ObjectId(template_id) if template_id else None,
            "generationMethod": "ai_assisted",
            "version": 1,
            "status": "draft",
            "trades": [],
            "summary": {
                "subtotal": 0,
                "grandTotal": 0
            },
            "createdBy": ObjectId(created_by),
            "createdAt": datetime.utcnow()
        }
        
        boq = await self.boq_repo.create(boq_data)
        
        logger.info(f"BOQ created: {boq['_id']} for project {project_id}")
        
        return boq
    
    ### Generate BOQ items from documents using AI
    ### This is a background task
    async def generate_boq_items(
        self,
        boq_id: str,
        document_ids: List[str]
    ):
        try:
            # Get documents
            documents = []
            for doc_id in document_ids:
                doc = await self.document_repo.get_by_id(doc_id)
                if doc and doc.get("status") == "processed":
                    documents.append(doc)
            
            if not documents:
                logger.warning(f"No processed documents found for BOQ {boq_id}")
                return
            
            # Extract building elements from AI analysis
            all_elements = []
            for doc in documents:
                ai_analysis = doc.get("aiAnalysis", {})
                detected_elements = ai_analysis.get("detectedElements", [])
                all_elements.extend(detected_elements)
            
            # Generate BOQ structure with trades
            trades = await self._generate_trades_from_elements(all_elements)
            
            # Calculate totals
            summary = await self._calculate_summary(trades)
            
            # Update BOQ
            update_data = {
                "trades": trades,
                "summary": summary,
                "status": "in_review",
                "updatedAt": datetime.utcnow()
            }
            
            await self.boq_repo.update(boq_id, update_data)
            
            logger.info(f"BOQ items generated for {boq_id}")
            
        except Exception as e:
            logger.error(f"Error generating BOQ items: {str(e)}")
            await self.boq_repo.update(
                boq_id,
                {"status": "draft", "notes": f"Generation failed: {str(e)}"}
            )
    
    ### Generate trade structure from detected elements
    async def _generate_trades_from_elements(
        self,
        elements: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        # Group elements by trade
        trade_groups = {
            "substructure": [],
            "superstructure": [],
            "finishes": [],
            "services": [],
            "external_works": []
        }
        
        # Element to trade mapping
        element_trade_map = {
            "foundation": "substructure",
            "column": "superstructure",
            "beam": "superstructure",
            "slab": "superstructure",
            "wall": "superstructure",
            "floor_finish": "finishes",
            "wall_finish": "finishes",
            "ceiling": "finishes",
            "door": "finishes",
            "window": "finishes",
            "plumbing": "services",
            "electrical": "services",
            "drainage": "external_works"
        }
        
        for element in elements:
            element_type = element.get("elementType", "").lower()
            
            # Find matching trade
            trade_key = None
            for key in element_trade_map:
                if key in element_type:
                    trade_key = element_trade_map[key]
                    break
            
            if trade_key:
                trade_groups[trade_key].append(element)
        
        # Build trade structure
        trades = []
        trade_codes = {
            "substructure": ("1", "Substructure"),
            "superstructure": ("2", "Superstructure"),
            "finishes": ("3", "Internal Finishes"),
            "services": ("4", "Services"),
            "external_works": ("5", "External Works")
        }
        
        for trade_key, (code, name) in trade_codes.items():
            if trade_groups[trade_key]:
                sections = await self._generate_sections(
                    trade_groups[trade_key],
                    code
                )
                
                trade_subtotal = sum(
                    section.get("sectionSubtotal", 0)
                    for section in sections
                )
                
                trades.append({
                    "tradeCode": code,
                    "tradeName": name,
                    "sortOrder": int(code),
                    "sections": sections,
                    "tradeSubtotal": trade_subtotal
                })
        
        return trades
    
    ### Generate sections within a trade
    async def _generate_sections(
        self,
        elements: List[Dict[str, Any]],
        trade_code: str
    ) -> List[Dict[str, Any]]:
        # Group by element type
        element_groups = {}
        for element in elements:
            elem_type = element.get("elementType")
            if elem_type not in element_groups:
                element_groups[elem_type] = []
            element_groups[elem_type].append(element)
        
        sections = []
        for idx, (elem_type, elem_list) in enumerate(element_groups.items(), 1):
            section_code = f"{trade_code}.{idx}"
            items = await self._generate_items(elem_list, section_code)
            
            section_subtotal = sum(item.get("amount", 0) for item in items)
            
            sections.append({
                "sectionCode": section_code,
                "sectionName": elem_type.replace("_", " ").title(),
                "sortOrder": idx,
                "items": items,
                "sectionSubtotal": section_subtotal
            })
        
        return sections
    
    ### Generate BOQ items from elements
    async def _generate_items(
        self,
        elements: List[Dict[str, Any]],
        section_code: str
    ) -> List[Dict[str, Any]]:
        items = []
        
        for idx, element in enumerate(elements, 1):
            item_number = f"{section_code}.{idx}"
            
            # Get material rate
            material_name = element.get("elementType", "").replace("_", " ")
            rate = await self._get_material_rate(material_name)
            
            quantity = element.get("totalQuantity", 0)
            unit = element.get("unit", "nr")
            amount = quantity * rate if rate else 0
            
            # Build item
            item = {
                "itemNumber": item_number,
                "description": f"{material_name} as per drawing",
                "unit": unit,
                "quantity": quantity,
                "rate": rate,
                "amount": amount,
                "quantityCalculation": {
                    "method": "ai_extracted",
                    "dimensions": element.get("attributes", {}),
                    "notes": f"Extracted from CAD/PDF with {element.get('confidence', 0):.2%} confidence"
                },
                "aiMetadata": {
                    "confidence": element.get("confidence", 0),
                    "needsVerification": element.get("confidence", 0) < 0.85
                }
            }
            
            items.append(item)
        
        return items
    
    ### Get material rate from database
    async def _get_material_rate(self, material_name: str) -> float:
        try:
            material = await self.material_rates_repo.search_by_name(material_name)
            if material:
                return float(material.get("rate", 0))
            
            # Default rate if not found
            return 0.0
            
        except Exception as e:
            logger.error(f"Error getting material rate: {str(e)}")
            return 0.0
    
    ### Calculate BOQ summary totals
    async def _calculate_summary(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        subtotal = sum(trade.get("tradeSubtotal", 0) for trade in trades)
        
        # Preliminaries (10% of subtotal)
        preliminaries = subtotal * 0.10
        
        # Contingency (5%)
        contingency_percentage = 5.0
        contingency_amount = subtotal * 0.05
        
        # VAT (7.5% in Nigeria)
        vat_percentage = 7.5
        vat_amount = (subtotal + preliminaries + contingency_amount) * 0.075
        
        grand_total = subtotal + preliminaries + contingency_amount + vat_amount
        
        return {
            "subtotal": subtotal,
            "preliminaries": preliminaries,
            "contingency": {
                "percentage": contingency_percentage,
                "amount": contingency_amount
            },
            "vat": {
                "percentage": vat_percentage,
                "amount": vat_amount
            },
            "grandTotal": grand_total
        }
    
    ### Approve BOQ
    async def approve_boq(self, boq_id: str, approved_by: str) -> Dict[str, Any]:
        update_data = {
            "status": "approved",
            "approvedAt": datetime.utcnow(),
            "approvals": [{
                "approverRole": "quantity_surveyor",
                "approverId": ObjectId(approved_by),
                "status": "approved",
                "approvedAt": datetime.utcnow()
            }]
        }
        
        boq = await self.boq_repo.update(boq_id, update_data)
        
        logger.info(f"BOQ approved: {boq_id}")
        
        return boq
    
    ### Export BOQ to specified format (openpyxl for excel and reportlab for pdf)
    async def export_boq(self, boq_id: str, format: str) -> str:
        boq = await self.boq_repo.get_by_id(boq_id)
        if not boq:
            raise ValueError("BOQ not found")
        
        file_url = f"https://storage.example.com/boqs/{boq_id}.{format}"
        
        # Log export
        export_record = {
            "exportedBy": boq.get("createdBy"),
            "exportedAt": datetime.utcnow(),
            "format": format,
            "fileUrl": file_url
        }
        
        await self.boq_repo.add_export(boq_id, export_record)
        
        return file_url
    
    ### Generate unique BOQ number
    async def _generate_boq_number(self) -> str:
        count = await self.boq_repo.count_all()
        timestamp = datetime.utcnow().strftime("%Y%m")
        
        return f"BOQ-{timestamp}-{count + 1:04d}"
    