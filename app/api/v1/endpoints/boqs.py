from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
    BackgroundTasks,
    UploadFile,
    File,
    Query,
)
from typing import List, Optional, Dict, Any
import logging
from datetime import datetime

from app.core.database import get_mongodb, get_db
from app.repositories.boq_repository import BOQRepository
from app.services.boq_generator import BOQGenerator
from app.services.mitm_engine import MITMEngine
from app.services.price_service import PriceService
from app.services.token_service import TokenService
from app.services.ai_service import AIService
from app.api.deps import get_current_user, get_optional_user

from app.schemas.boq import (
    BOQUpdate,
    BOQResponse,
    BOQListResponse,
    BOQGenerationRequest,
    DrawingAnalysisResponse,
    DrawingQuality,
    BOQOrderRequest,
    BOQOrderResponse,
)

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

### Bill of Quantities (BOQ) Endpoints

router = APIRouter()

# Accepted MIME types for drawing upload
_ACCEPTED_MIMES = {
    "application/pdf",
    "image/jpeg", "image/jpg", "image/png",
    "image/webp", "image/tiff",
}

DRAWING_UPLOAD_GUIDANCE = (
    "For best BOQ accuracy, upload a complete PDF drawing set "
    "(architectural + structural). "
    "CAD files (.dwg/.dxf) are NOT accepted — export to PDF first. "
    "Images (JPG/PNG) are accepted but produce lower accuracy for structural items."
)


### Analyze uploaded drawing (no token cost, anonymous allowed)
@router.post("/analyze-drawing", response_model=DrawingAnalysisResponse)
async def analyze_drawing(
    file: UploadFile = File(...),
    current_user: Optional[dict] = Depends(get_optional_user),
):

    """
    Upload a drawing file (PDF or image) for AI analysis.
    Returns extracted geometry, drawing quality assessment, and confidence score.
    No token cost — free to use.
    """
    # Validate MIME type
    if file.content_type not in _ACCEPTED_MIMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported file type: {file.content_type}. "
                f"Accepted: PDF, JPEG, PNG, WebP, TIFF. "
                f"CAD files (.dwg/.dxf) are NOT accepted."
            )
        )

    # Read file content
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:  # 20MB
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File size exceeds 20MB limit for drawing analysis."
        )
    await file.seek(0)

    # Run Gemini Vision analysis
    ai_service = AIService()
    metadata = {
        "file_name": file.filename or "uploaded_drawing",
        "file_type": file.content_type or "image/png",
        "file_size_bytes": len(content),
    }
    analysis = await ai_service.analyze_document(
        file_content=content,
        file_type=file.filename or "drawing.png",
        extracted_metadata=metadata,
    )

    if not analysis.get("processed"):
        errors = analysis.get("processingErrors", ["AI analysis failed"])
        return DrawingAnalysisResponse(
            success=False,
            error="; ".join(errors),
            notes=["AI analysis could not extract building data from this drawing. Please enter dimensions manually."],
            upload_guidance=DRAWING_UPLOAD_GUIDANCE,
        )

    # Map AI response to DrawingAnalysisResponse
    rooms = analysis.get("rooms", [])
    elements = analysis.get("detectedElements", [])
    materials = analysis.get("detectedMaterials", [])

    # Determine drawing type from detected elements
    has_structural = any(
        e.get("elementType") in ("column", "beam", "foundation", "slab")
        for e in elements
    )
    has_sections = any(
        e.get("elementType") in ("external_wall", "internal_wall")
        for e in elements
    )
    if has_structural and has_sections:
        drawing_type = "complete_set"
    elif has_sections:
        drawing_type = "floor_and_sections"
    else:
        drawing_type = "floor_plan_only"

    # Compute confidence from AI response
    room_confidences = [r.get("confidence", 0.5) for r in rooms if "confidence" in r]
    element_confidences = [e.get("confidence", 0.5) for e in elements if "confidence" in e]
    all_confidences = room_confidences + element_confidences
    avg_confidence = sum(all_confidences) / len(all_confidences) if all_confidences else 0.5

    drawing_quality = DrawingQuality(
        drawing_format="pdf" if file.content_type == "application/pdf" else "image",
        drawing_type=drawing_type,
        image_quality_score=avg_confidence,
        has_dimensions=any("dimension" in str(e).lower() for e in elements),
        has_scale_bar=False,
        has_room_labels=len(rooms) > 0,
        has_structural_elements=has_structural,
        ocr_dimension_count=0,
        extracted_dimensions=[],
        notes=[
            f"AI extracted {len(rooms)} rooms, {len(elements)} elements, {len(materials)} materials.",
            f"Drawing classified as: {drawing_type}.",
        ],
        accuracy_caps={
            "geometry": min(0.95, avg_confidence + 0.1),
            "specification": min(0.85, avg_confidence),
            "pricing": min(0.75, avg_confidence - 0.1),
            "completeness": min(0.90, avg_confidence + 0.05),
        },
    )

    # Build extracted geometry
    extracted_rooms = [
        {
            "name": r.get("roomName", f"Room {i+1}"),
            "area_m2": r.get("area", 0),
            "perimeter_m": r.get("perimeter", 0),
            "is_wet_area": r.get("roomType", "").lower() in ("bathroom", "toilet", "kitchen", "shower", "laundry"),
        }
        for i, r in enumerate(rooms)
    ]

    extracted_geometry = {
        "source": "gemini_vision",
        "format": "pdf" if file.content_type == "application/pdf" else "image",
        "file_name": file.filename,
        "file_size_bytes": len(content),
        "rooms": extracted_rooms,
        "floor_area_m2": sum(r.get("area", 0) for r in rooms),
        "elements": elements,
        "materials": materials,
    }

    return DrawingAnalysisResponse(
        success=True,
        drawing_quality=drawing_quality,
        extracted_geometry=extracted_geometry,
        confidence=avg_confidence,
        notes=drawing_quality.notes,
        upload_guidance=DRAWING_UPLOAD_GUIDANCE,
        upgrade_prompt=(
            None if file.content_type == "application/pdf" else
            "For better accuracy, upload a PDF drawing set instead of an image. "
            "PDFs preserve vector dimensions and allow structural element detection."
        ),
    )


### Generate BOQ from building parameters

@router.post("/generate-from-params", status_code=status.HTTP_201_CREATED)
async def generate_boq_from_params(
    request: BOQGenerationRequest,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb),
    pg_db: AsyncSession = Depends(get_db),
):
    """
    Generate a complete BOQ from building parameters.
    Uses MITM enrichment + AI/template generation + price enrichment.
    Deducts tokens from user balance.
    """
    # Check token balance
    token_service = TokenService(pg_db)
    action_type = "boq_generate_drawing" if request.drawing_extracted_data else "boq_generate_manual"
    has_tokens = await token_service.deduct_tokens(
        user_id=current_user["id"],
        action_type=action_type,
        description=f"BOQ generation: {request.project_info.project_title}"
    )
    if not has_tokens:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Insufficient tokens. Purchase more tokens or use your free tier."
        )

    boq_generator = BOQGenerator(db)
    boq = await boq_generator.generate_from_parameters(
        request=request,
        user_id=current_user["id"]
    )
    
    # Save to MongoDB if available
    if db:
        now = datetime.utcnow()
        boq_doc = {
            "projectId": request.project_info.project_title,
            "boqNumber": f"BOQ-{now.strftime('%Y%m%d%H%M%S')}",
            "title": request.project_info.project_title,
            "status": "generated",
            "version": 1,
            "generationMethod": "parameters",
            "createdBy": current_user["id"],
            "boqData": boq,
            "createdAt": now,
            "updatedAt": now,
        }
        result = await db["boqs"].insert_one(boq_doc)
        boq["_id"] = str(result.inserted_id)
    
    return boq


### Public Preview (no auth required — truncated response)
@router.post("/public-preview")
async def public_preview(
    request: BOQGenerationRequest,
    db = Depends(get_mongodb),
):
    """
    Anonymous preview — generates a truncated BOQ with masked totals.
    No auth required, no token cost, no save to DB.
    Returns enough data to convince users to sign up.
    """
    boq_generator = BOQGenerator(db)
    full = await boq_generator.generate_from_parameters(
        request=request,
        user_id="anonymous"
    )

    # Truncate elements to first 3 items each
    if "elements" in full:
        for el in full["elements"]:
            if "items" in el and len(el["items"]) > 3:
                el["items"] = el["items"][:3]
                el["items"].append({
                    "item_code": "...",
                    "description": "Sign up to see all items",
                    "unit": "",
                    "quantity": 0,
                    "rate": 0,
                    "amount": 0,
                    "confidence": 0,
                    "estimated": True,
                })

    # Mask summary totals
    if "summary" in full:
        for key in ("sub_total", "total_contract_sum", "total_low", "total_expected", "total_high"):
            if key in full["summary"]:
                full["summary"][key] = _mask_amount(full["summary"][key])

    full["requires_signup"] = True
    full["_id"] = None
    return full


def _mask_amount(amount: float) -> float:
    """Return a masked version — e.g. 5,234,000 → 5,000,000"""
    if amount < 1000:
        return amount
    s = str(int(round(amount)))
    # Keep first digit, replace rest with zeros
    masked = s[0] + "0" * (len(s) - 1)
    return float(masked)


### MITM Preview (no token cost, anonymous allowed)
@router.post("/mitm-preview")

async def mitm_preview(
    request: BOQGenerationRequest,
    current_user: Optional[dict] = Depends(get_optional_user),
):
    """
    Preview MITM analysis without generating a full BOQ.
    Shows flags, assumptions, and estimated confidence.
    No token cost.
    """
    engine = MITMEngine()
    preview = engine.preview(request)
    return preview


### Upload a BOQ Excel/CSV file for verification
@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_boq(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    """Upload an existing BOQ file (Excel/CSV) for verification and analysis."""
    allowed_extensions = ['.xlsx', '.xls', '.csv']
    file_ext = '.' + file.filename.split('.')[-1].lower() if file.filename else ''
    
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type not supported. Allowed: {', '.join(allowed_extensions)}"
        )
    
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:  # 50MB
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File size exceeds 50MB limit"
        )
    await file.seek(0)
    
    boq_generator = BOQGenerator(db)
    result = await boq_generator.upload_and_verify(
        file=file,
        uploaded_by=current_user["id"]
    )
    
    return result


### Get BOQ by ID
@router.get("/{boq_id}", response_model=BOQResponse)
async def get_boq(
    boq_id: str,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    boq_repo = BOQRepository(db)
    boq = await boq_repo.get_by_id(boq_id)
    
    if not boq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="BOQ not found"
        )
    
    return boq


### List all BOQs for the current user (root list)
@router.get("/", response_model=List[Dict[str, Any]])
async def list_boqs(
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    """List all BOQs for the current user, with optional status filter."""
    boq_repo = BOQRepository(db)
    boqs = await boq_repo.list_by_user(
        user_id=current_user["id"],
        status=status,
        skip=(page - 1) * page_size,
        limit=page_size
    )
    return boqs


### List all BOQs for a project
@router.get("/project/{project_id}", response_model=BOQListResponse)
async def list_project_boqs(
    project_id: str,
    page: int = 1,
    page_size: int = 20,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    boq_repo = BOQRepository(db)
    boqs = await boq_repo.list_by_project(
        project_id=project_id,
        skip=(page - 1) * page_size,
        limit=page_size
    )
    
    total = await boq_repo.count_by_project(project_id)
    
    return {
        "boqs": boqs,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size
    }


### Update BOQ
@router.put("/{boq_id}", response_model=BOQResponse)
async def update_boq(
    boq_id: str,
    boq_update: BOQUpdate,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    boq_repo = BOQRepository(db)
    boq = await boq_repo.get_by_id(boq_id)
    
    if not boq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="BOQ not found"
        )
    
    updated_boq = await boq_repo.update(
        boq_id=boq_id,
        update_data=boq_update.dict(exclude_unset=True)
    )
    
    return updated_boq


### Submit MITM decision (regenerate or save original)
@router.post("/{boq_id}/decision")
async def submit_decision(
    boq_id: str,
    decision: dict,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    """Submit a user decision on a BOQ: 'regenerate' or 'save_original'."""
    decision_value = decision.get("decision")
    if decision_value not in ["regenerate", "save_original"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Decision must be 'regenerate' or 'save_original'"
        )
    
    boq_generator = BOQGenerator(db)
    result = await boq_generator.handle_decision(
        boq_id=boq_id,
        decision=decision_value,
        user_id=current_user["id"]
    )
    
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="BOQ not found"
        )
    
    return result


### Approve BOQ
@router.post("/{boq_id}/approve")
async def approve_boq(
    boq_id: str,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    boq_generator = BOQGenerator(db)
    approved_boq = await boq_generator.approve_boq(
        boq_id=boq_id,
        approved_by=current_user["id"]
    )
    
    if not approved_boq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="BOQ not found"
        )
    
    return approved_boq


### Verify quote text against market prices
@router.post("/verify-quote")
async def verify_quote(
    quote_data: dict,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    """Verify a quote text against market prices."""
    quote_text = quote_data.get("quote_text", "")
    if not quote_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="quote_text is required"
        )
    
    boq_generator = BOQGenerator(db)
    result = await boq_generator.verify_quote_text(
        quote_text=quote_text,
        user_id=current_user["id"]
    )
    
    return result


### Export BOQ to PDF, Excel, or CSV
@router.post("/{boq_id}/export/{format}")
async def export_boq(
    boq_id: str,
    format: str,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb),
    pg_db: AsyncSession = Depends(get_db),
):
    if format not in ['pdf', 'excel', 'csv']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format must be one of: pdf, excel, csv"
        )
    
    # Deduct token for export
    token_service = TokenService(pg_db)
    has_tokens = await token_service.deduct_tokens(
        user_id=current_user["id"],
        action_type=f"export_{format}",
        description=f"Export BOQ {boq_id} to {format}"
    )
    if not has_tokens:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Insufficient tokens for export."
        )
    
    boq_generator = BOQGenerator(db)
    file_url = await boq_generator.export_boq(
        boq_id=boq_id,
        format=format
    )
    
    return {"file_url": file_url}


### Place order from BOQ items
@router.post("/{boq_id}/place-order", response_model=BOQOrderResponse)
async def place_boq_order(
    boq_id: str,
    order_request: BOQOrderRequest,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb),
    pg_db: AsyncSession = Depends(get_db),
):
    """
    Place an order from selected BOQ items.
    Creates orders + order_items records in PostgreSQL.
    """
    # Verify BOQ exists
    boq_repo = BOQRepository(db)
    boq = await boq_repo.get_by_id(boq_id)
    if not boq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="BOQ not found"
        )

    if not order_request.items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one item is required to place an order"
        )

    try:
        # Calculate total
        total_amount = sum(item.quantity * item.rate for item in order_request.items)

        # Generate order number
        order_number = f"ORD-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{current_user['id'][:8]}"

        # Create order in PostgreSQL
        from sqlalchemy import text
        order_result = await pg_db.execute(
            text("""
                INSERT INTO orders (
                    order_number, user_id, status, subtotal, total_amount,
                    payment_status, shipping_address, notes
                ) VALUES (
                    :order_number, :user_id, 'pending', :subtotal, :total_amount,
                    'unpaid', :shipping_address, :notes
                )
                RETURNING id, order_number
            """),
            {
                "order_number": order_number,
                "user_id": current_user["id"],
                "subtotal": total_amount,
                "total_amount": total_amount,
                "shipping_address": order_request.shipping_address or "",
                "notes": order_request.notes or "",
            }
        )
        order_row = order_result.fetchone()
        order_id = str(order_row[0])
        order_number = str(order_row[1])

        # Insert order items
        for item in order_request.items:
            await pg_db.execute(
                text("""
                    INSERT INTO order_items (
                        order_id, product_name, quantity, unit_price, total_price
                    ) VALUES (
                        :order_id, :product_name, :quantity, :unit_price, :total_price
                    )
                """),
                {
                    "order_id": order_id,
                    "product_name": item.description,
                    "quantity": item.quantity,
                    "unit_price": item.rate,
                    "total_price": item.quantity * item.rate,
                }
            )

        await pg_db.commit()

        return BOQOrderResponse(
            success=True,
            order_id=order_id,
            order_number=order_number,
            message=f"Order {order_number} placed successfully",
            items_ordered=len(order_request.items),
            total_amount=total_amount,
        )

    except Exception as e:
        await pg_db.rollback()
        logger.error(f"Failed to place order for BOQ {boq_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to place order: {str(e)}"
        )


### Check order status for a BOQ
@router.get("/{boq_id}/order-status")
async def get_boq_order_status(
    boq_id: str,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb),
    pg_db: AsyncSession = Depends(get_db),
):
    """
    Check if items from a BOQ have been ordered.
    Returns order details if found.
    """
    # Verify BOQ exists
    boq_repo = BOQRepository(db)
    boq = await boq_repo.get_by_id(boq_id)
    if not boq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="BOQ not found"
        )

    try:
        from sqlalchemy import text
        # Look for orders associated with this BOQ via notes or user
        result = await pg_db.execute(
            text("""
                SELECT id, order_number, status, total_amount, payment_status,
                       created_at, shipping_address
                FROM orders
                WHERE user_id = :user_id
                  AND notes LIKE :boq_ref
                ORDER BY created_at DESC
                LIMIT 10
            """),
            {
                "user_id": current_user["id"],
                "boq_ref": f"%{boq_id}%",
            }
        )
        orders = result.fetchall()

        return {
            "has_orders": len(orders) > 0,
            "orders": [
                {
                    "order_id": str(o[0]),
                    "order_number": str(o[1]),
                    "status": str(o[2]),
                    "total_amount": float(o[3]),
                    "payment_status": str(o[4]),
                    "created_at": str(o[5]),
                    "shipping_address": str(o[6]),
                }
                for o in orders
            ],
        }

    except Exception as e:
        logger.error(f"Failed to check order status for BOQ {boq_id}: {e}")
        return {"has_orders": False, "orders": [], "error": str(e)}


### Delete BOQ
@router.delete("/{boq_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_boq(
    boq_id: str,
    current_user: dict = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    boq_repo = BOQRepository(db)
    boq = await boq_repo.get_by_id(boq_id)
    
    if not boq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="BOQ not found"
        )
    
    await boq_repo.delete(boq_id)
    return None
