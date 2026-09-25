from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
    BackgroundTasks,
    UploadFile,
    File,
    Query,
    Request,
)
from typing import List, Optional, Dict, Any
import logging
import re
from datetime import datetime

from app.core.database import get_mongodb, get_db
from app.core.ratelimit import rate_limit
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
from app.models.user import User

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

# Anonymous BOQ uploads cost Gemini Vision / analysis time, so throttle them per
# client IP. Signed-in callers are unaffected (their plans govern usage).
_GUEST_BOQ_RATE_LIMIT = 5
_GUEST_BOQ_RATE_WINDOW = 60


async def _enforce_guest_rate_limit(http_request: Request, bucket: str) -> None:
    """Throttle anonymous BOQ uploads by client IP.

    Fails open when Redis is unavailable (matches the shared rate-limit helper),
    so a Redis outage never blocks legitimate guest uploads.
    """
    client_ip = http_request.client.host if http_request.client else "unknown"
    if not await rate_limit(_GUEST_BOQ_RATE_LIMIT, _GUEST_BOQ_RATE_WINDOW, bucket, client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many BOQ requests. Please wait a minute and try again.",
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
            "area_m2": r.get("area") or 0,
            "perimeter_m": r.get("perimeter") or 0,
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
        "floor_area_m2": sum(r.get("area") or 0 for r in rooms),
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


### Generate BOQ from drawing (automatic pipeline: analyze → map → generate)
@router.post("/generate-from-drawing", status_code=status.HTTP_201_CREATED)
async def generate_boq_from_drawing(
    http_request: Request,
    file: UploadFile = File(...),
    current_user: Optional[User] = Depends(get_optional_user),
    db = Depends(get_mongodb),
    pg_db: AsyncSession = Depends(get_db),
):
    """
    Upload a drawing → Gemini Vision extraction → DrawingToBOQMapper →
    full BOQ generation in one call.
    When drawing confidence is low, returns a targeted-manual-input fallback
    with the extracted geometry pre-filled so the user only confirms dimensions.

    Signed-in users: full BOQ, token cost boq_generate_drawing (2), saved to DB.
    Anonymous users: free truncated preview (no persistence) that requires signup.
    """
    if current_user is None:
        await _enforce_guest_rate_limit(http_request, "boq-drawing")

    # Validate MIME type + size (mirror analyze_drawing)
    if file.content_type not in _ACCEPTED_MIMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported file type: {file.content_type}. "
                f"Accepted: PDF, JPEG, PNG, WebP, TIFF. "
                f"CAD files (.dwg/.dxf) are NOT accepted."
            )
        )
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File size exceeds 20MB limit for drawing analysis.",
        )
    await file.seek(0)

    # ── 1. Analyze drawing (free, matches /analyze-drawing behavior) ──
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
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "AI analysis could not extract building data from this drawing.",
                "fallback": "manual",
                "notes": ["Please enter dimensions manually."],
                "errors": errors,
            },
        )

    # Build extracted geometry (same shape as /analyze-drawing)
    rooms = analysis.get("rooms", [])
    elements = analysis.get("detectedElements", [])
    materials = analysis.get("detectedMaterials", [])
    extracted_geometry = {
        "source": "gemini_vision",
        "format": "pdf" if file.content_type == "application/pdf" else "image",
        "file_name": file.filename,
        "file_size_bytes": len(content),
        "rooms": [
            {
                "name": r.get("roomName", f"Room {i+1}"),
                "area_m2": r.get("area") or 0,
                "perimeter_m": r.get("perimeter") or 0,
            }
            for i, r in enumerate(rooms)
        ],
        "floor_area_m2": sum(r.get("area") or 0 for r in rooms),
        "elements": elements,
        "materials": materials,
    }
    has_structural = any(
        e.get("elementType") in ("column", "beam", "foundation", "slab")
        for e in elements
    )
    has_sections = any(
        e.get("elementType") in ("external_wall", "internal_wall")
        for e in elements
    )
    drawing_type = (
        "complete_set" if (has_structural and has_sections)
        else "floor_and_sections" if has_sections
        else "floor_plan_only"
    )

    room_confidences = [r.get("confidence", 0.5) for r in rooms if "confidence" in r]
    element_confidences = [e.get("confidence", 0.5) for e in elements if "confidence" in e]
    all_confidences = room_confidences + element_confidences
    avg_confidence = sum(all_confidences) / len(all_confidences) if all_confidences else 0.5

    from app.services.drawing_to_boq_mapper import DrawingToBOQMapper
    mapper = DrawingToBOQMapper()
    mapped = mapper.map(
        extracted_geometry=extracted_geometry,
        drawing_quality=None,
        project_meta={"city": "Abuja", "project_title": file.filename or "Drawing BOQ"},
    )

    # ── 2. Targeted manual fallback when confidence too low / no geometry ──
    if mapped["needs_manual_fallback"] or avg_confidence < 0.4:
        return {
            "success": True,
            "path": "targeted_manual",
            "drawing_type": drawing_type,
            "confidence": round(min(avg_confidence, mapped.get("confidence", avg_confidence)), 2),
            "extracted_geometry": extracted_geometry,
            "geometry_warnings": mapped.get("geometry_warnings") or [],
            "fallback_reason": mapped.get("fallback_reason") or (
                "Drawing confidence is low. Review extracted dimensions, "
                "then call /generate-from-params with pre-filled data."
            ),
        }

    # ── 3. Generate (registered: token cost + persistence; guest: free teaser) ──
    if current_user is not None:
        token_service = TokenService(pg_db)
        has_tokens = await token_service.deduct_tokens(
            user_id=str(current_user.id),
            action_type="boq_generate_drawing",
            description=f"BOQ generation from drawing: {file.filename}",
        )
        if not has_tokens:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Insufficient tokens. Purchase more tokens or use your free tier.",
            )

    boq_generator = BOQGenerator(db, pg_db=pg_db)
    boq = await boq_generator.generate_from_parameters(
        request=mapped["request"],
        user_id=str(current_user.id) if current_user else "anonymous",
    )
    boq["drawing_analysis"] = {
        "drawing_type": drawing_type,
        # The vision model's self-reported confidence is capped by the geometry
        # check: an implausible area can no longer be reported as "90% sure".
        "confidence": round(min(avg_confidence, mapped.get("confidence", avg_confidence)), 2),
        "extracted_geometry": extracted_geometry,
        "geometry_warnings": mapped.get("geometry_warnings") or [],
        "raw_area_m2": mapped.get("raw_area_m2"),
        "gross_area_m2": mapped.get("gross_area_m2"),
        "area_uplift_pct": mapped.get("area_uplift_pct"),
    }

    # Anonymous callers stop here — truncated preview, nothing persisted.
    if current_user is None:
        return _truncate_boq_for_guest(boq)

    # Save to MongoDB (mirror generate-from-params)
    if db:
        from datetime import datetime as _dt
        now = _dt.utcnow()
        boq_doc = {
            "projectId": request_title(boq, file.filename),
            "boqNumber": f"BOQ-{now.strftime('%Y%m%d%H%M%S')}",
            "title": request_title(boq, file.filename),
            "status": "generated",
            "version": 1,
            "generationMethod": "drawing",
            "createdBy": str(current_user.id),
            "boqData": boq,
            "createdAt": now,
            "updatedAt": now,
        }
        result = await db["boqs"].insert_one(boq_doc)
        boq["_id"] = str(result.inserted_id)

    return boq


def request_title(boq: Dict[str, Any], fallback: str) -> str:
    """Resolve the BOQ project title for persistence."""
    pi = boq.get("project_info") or {}
    return pi.get("project_title") or fallback or "Drawing BOQ"


### Generate BOQ from building parameters

@router.post("/generate-from-params", status_code=status.HTTP_201_CREATED)
async def generate_boq_from_params(
    request: BOQGenerationRequest,
    current_user: User = Depends(get_current_user),
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
        user_id=str(current_user.id),
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
        user_id=str(current_user.id)
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
            "createdBy": str(current_user.id),
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
    return _truncate_boq_for_guest(full)


def _mask_amount(amount: float) -> float:
    """Return a masked version — e.g. 5,234,000 → 5,000,000"""
    if amount < 1000:
        return amount
    s = str(int(round(amount)))
    # Keep first digit, replace rest with zeros
    masked = s[0] + "0" * (len(s) - 1)
    return float(masked)


# Money mentioned inside a message (e.g. a warning that two summaries disagree).
_AMOUNT_IN_TEXT_RE = re.compile(r"\d[\d,]{4,}(?:\.\d+)?")


def _redact_amounts(text: str) -> str:
    """Replace money figures inside prose so a warning cannot leak a total."""
    return _AMOUNT_IN_TEXT_RE.sub("•••", text or "")


def _mask_stated(stated: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Mask the document's stated money figures while keeping its structure."""
    masked = dict(stated or {})
    for key in ("sub_total", "vat", "total_contract_sum", "contingency"):
        if masked.get(key) is not None:
            masked[key] = _mask_amount(float(masked[key] or 0))
    if masked.get("element_totals"):
        masked["element_totals"] = {
            name: _mask_amount(float(value or 0))
            for name, value in masked["element_totals"].items()
        }
    if masked.get("bills"):
        masked["bills"] = {
            name: _mask_amount(float(value or 0)) for name, value in masked["bills"].items()
        }
    if masked.get("summaries"):
        # Each entry nests its own bill totals, so rebuild it rather than only
        # nulling the headline figures.
        masked["summaries"] = [
            {
                "sheet": summary.get("sheet"),
                "vat_rate": summary.get("vat_rate"),
                "sub_total": None,
                "vat": None,
                "contingency": None,
                "total_contract_sum": None,
                "bills": {
                    name: _mask_amount(float(value or 0))
                    for name, value in (summary.get("bills") or {}).items()
                },
            }
            for summary in masked["summaries"]
        ]
    return masked


# ── Guest (anonymous) preview limits ─────────────────────────────────────────
# Anonymous callers get a genuine teaser only: a few rows plus masked money
# totals. The full BOQ/analysis is never serialised to an unauthenticated
# request, so the sign-up wall cannot be bypassed by reading the response (or
# anything the client mirrors into localStorage).
_GUEST_ITEM_LIMIT = 3
_GUEST_DISCREPANCY_LIMIT = 2


def _locked_item_stub(hidden_count: int) -> Dict[str, Any]:
    """Placeholder row telling a guest how many items are hidden."""
    suffix = f" ({hidden_count} more)" if hidden_count else ""
    return {
        "item_code": "...",
        "description": f"Sign up to see all items{suffix}",
        "unit": "",
        "quantity": 0,
        "rate": 0,
        "amount": 0,
        "confidence": 0,
        "estimated": True,
    }


def _truncate_boq_for_guest(full: Dict[str, Any]) -> Dict[str, Any]:
    """Truncate a generated BOQ for anonymous callers (see _GUEST_ITEM_LIMIT)."""
    guest_elements = full.get("elements") or []
    elements_total = len(guest_elements)
    items_total = sum(len(el.get("items") or []) for el in guest_elements)
    items_shown = 0
    unpriced_items = 0
    teaser_item = None
    visible_priced = False

    if "elements" in full:
        for el in guest_elements:
            items = el.get("items") or []
            unpriced_items += sum(
                1
                for i in items
                if i.get("price_status") == "unavailable"
                or (not i.get("adjusted_rate") and not i.get("rate"))
            )
            # Note the first priced line (and whether any visible line is
            # priced) before truncation drops the rest of the element.
            for idx, candidate in enumerate(items):
                if not (candidate.get("adjusted_rate") or candidate.get("rate")):
                    continue
                if teaser_item is None:
                    teaser_item = candidate
                if idx < _GUEST_ITEM_LIMIT:
                    visible_priced = True
            if len(items) > _GUEST_ITEM_LIMIT:
                hidden = len(items) - _GUEST_ITEM_LIMIT
                el["items"] = items[:_GUEST_ITEM_LIMIT] + [_locked_item_stub(hidden)]
            items_shown += min(len(items), _GUEST_ITEM_LIMIT)
            # An element total is a real money figure too — mask it, otherwise
            # the exact bill is readable straight from the response body.
            if el.get("element_total") is not None:
                el["element_total"] = _mask_amount(float(el["element_total"] or 0))
            el.pop("cost_percentage_of_total", None)

    # Mask summary totals. Everything derived from the bill is masked, not just
    # the headline figures: contingency, VAT and cost/m² recover the same sum.
    if "summary" in full:
        summary = full["summary"]
        for key in (
            "sub_total",
            "contingency",
            "vat",
            "total_contract_sum",
            "total_low",
            "total_expected",
            "total_high",
            "cost_per_m2",
        ):
            if summary.get(key) is not None:
                summary[key] = _mask_amount(float(summary[key] or 0))
        scenarios = summary.get("cost_scenarios")
        if isinstance(scenarios, dict):
            summary["cost_scenarios"] = {
                key: _mask_amount(float(value or 0)) for key, value in scenarios.items()
            }

    # Teaser: when every visible line is unpriced, the guest sees only "price on
    # request" rows, which reads as a broken bill. Surface the first priced line.
    visible_items = [
        it for el in guest_elements for it in (el.get("items") or [])[:_GUEST_ITEM_LIMIT]
    ]
    if visible_items and not visible_priced and teaser_item is not None:
        head = guest_elements[0].get("items") or []
        stubs = [it for it in head if it.get("item_code") == "..."]
        body = [it for it in head if it.get("item_code") != "..."]
        guest_elements[0]["items"] = ([teaser_item] + body[:-1] if body else [teaser_item]) + stubs
        items_shown = min(items_total, _GUEST_ITEM_LIMIT * elements_total)

    full["guest_preview"] = {
        "masked": True,
        "elements_total": elements_total,
        "items_total": items_total,
        "items_shown": items_shown,
        "unpriced_items": unpriced_items,
    }
    full["requires_signup"] = True
    full["_id"] = None
    return full


async def _resolve_rate_city(pg_db, current_user) -> str:
    """City whose market rates a BOQ should be checked against.

    Nigerian material prices vary by state, so a signed-in caller's default
    delivery address is the best available signal. Falls back to Abuja, which is
    the rate default used across the app.
    """
    if current_user is None or pg_db is None:
        return "Abuja"
    try:
        from sqlalchemy import select

        from app.models.address import CustomerAddress

        result = await pg_db.execute(
            select(CustomerAddress.city)
            .where(CustomerAddress.user_id == current_user.id)
            .order_by(CustomerAddress.is_default.desc())
            .limit(1)
        )
        city = result.scalar_one_or_none()
        return city or "Abuja"
    except Exception as exc:  # non-fatal: verification still runs at Abuja rates
        logger.warning("Could not resolve rate city for BOQ verification: %s", exc)
        return "Abuja"


def _mask_verification_for_guest(result: Dict[str, Any]) -> Dict[str, Any]:
    """Mask an uploaded-BOQ verification result for anonymous callers.

    Keeps a couple of verified lines and discrepancies as a teaser, masks the
    quoted total and never returns a stored BOQ id. Every list that can carry
    the full bill must be trimmed here — including the per-element breakdown,
    which would otherwise hand a guest the whole verified BOQ.
    """
    parsed = result.get("parsed_boq") or {}
    items = parsed.get("items") or []
    if len(items) > _GUEST_ITEM_LIMIT:
        hidden = len(items) - _GUEST_ITEM_LIMIT
        parsed["items"] = items[:_GUEST_ITEM_LIMIT] + [_locked_item_stub(hidden)]
    if parsed.get("total_quoted") is not None:
        parsed["total_quoted"] = _mask_amount(float(parsed["total_quoted"] or 0))
    parsed["stated"] = _mask_stated(parsed.get("stated"))
    parsed["warnings"] = [_redact_amounts(w) for w in (parsed.get("warnings") or [])]
    elements = parsed.get("elements") or []
    if elements:
        parsed["elements"] = [
            {
                "element_name": element.get("element_name"),
                "item_count": element.get("item_count"),
                "element_total": _mask_amount(float(element.get("element_total") or 0)),
                "items": [],
            }
            for element in elements
        ]
    result["parsed_boq"] = parsed

    analysis = result.get("analysis") or {}
    verified = analysis.get("verified_items") or []
    discrepancies = analysis.get("discrepancies") or []
    analysis["verified_items"] = verified[:_GUEST_ITEM_LIMIT]
    analysis["discrepancies"] = discrepancies[:_GUEST_DISCREPANCY_LIMIT]
    analysis["hidden_items"] = max(len(verified) - _GUEST_ITEM_LIMIT, 0)
    analysis["stated"] = _mask_stated(analysis.get("stated"))
    analysis["warnings"] = [_redact_amounts(w) for w in (analysis.get("warnings") or [])]
    # The element breakdown repeats the whole bill; mask its figures too.
    analysis["elements"] = [
        {
            "element_name": element.get("element_name"),
            "item_count": element.get("item_count"),
            "computed_total": _mask_amount(float(element.get("computed_total") or 0)),
            "stated_total": _mask_amount(float(element.get("stated_total") or 0)),
            "difference": None,
        }
        for element in (analysis.get("elements") or [])
    ]
    for key in ("original_total", "adjusted_total", "net_variance"):
        if analysis.get(key) is not None:
            analysis[key] = _mask_amount(float(analysis[key] or 0))
    analysis["flagged_items"] = (analysis.get("flagged_items") or [])[:_GUEST_DISCREPANCY_LIMIT]
    result["analysis"] = analysis

    # The arithmetic report restates every element total and the contract sum.
    arithmetic = result.get("arithmetic") or analysis.get("arithmetic")
    if arithmetic:
        masked_arithmetic = {
            **arithmetic,
            "elements": [
                {
                    **element,
                    "computed_total": _mask_amount(float(element.get("computed_total") or 0)),
                    "stated_total": _mask_amount(float(element.get("stated_total") or 0)),
                    "difference": None,
                }
                for element in (arithmetic.get("elements") or [])
            ],
            "items_total": _mask_amount(float(arithmetic.get("items_total") or 0)),
            "stated_elements_total": _mask_amount(float(arithmetic.get("stated_elements_total") or 0)),
            "items_total_difference": None,
            "vat": {
                **(arithmetic.get("vat") or {}),
                "stated": _mask_amount(float((arithmetic.get("vat") or {}).get("stated") or 0)),
                "expected": _mask_amount(float((arithmetic.get("vat") or {}).get("expected") or 0)),
            },
            "contract_sum": {
                **(arithmetic.get("contract_sum") or {}),
                "stated": _mask_amount(float((arithmetic.get("contract_sum") or {}).get("stated") or 0)),
                "expected": _mask_amount(float((arithmetic.get("contract_sum") or {}).get("expected") or 0)),
            },
            # Finding messages spell out the real element totals, and each summary
            # entry carries the stated contract figures — neither may reach a guest.
            "findings": [
                {
                    "scope": finding.get("scope"),
                    "difference": None,
                    "message": (
                        f"{finding.get('scope')}: this element's line items do not add up "
                        "to its stated total."
                    ),
                }
                for finding in (arithmetic.get("findings") or [])
            ],
            "summaries": [
                {"sheet": summary.get("sheet")}
                for summary in (arithmetic.get("summaries") or [])
            ],
        }
        result["arithmetic"] = masked_arithmetic
        analysis["arithmetic"] = masked_arithmetic

    result["boq_id"] = ""
    result["requires_signup"] = True
    return result



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
    http_request: Request,
    file: UploadFile = File(...),
    current_user: Optional[User] = Depends(get_optional_user),
    db = Depends(get_mongodb),
    pg_db: AsyncSession = Depends(get_db),
):
    """Upload an existing BOQ file (Excel/CSV) for verification and analysis.

    Signed-in users get the full itemised analysis, stored under their account.
    Anonymous users get a masked teaser (a few lines, masked total, no storage).
    """
    if current_user is None:
        await _enforce_guest_rate_limit(http_request, "boq-verify")

    allowed_extensions = ['.xlsx', '.xls', '.xlsm', '.csv']
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

    # Rates vary by state (Nigerian material prices move by market and LGA), so
    # compare against the caller's own city when we know it.
    rate_city = await _resolve_rate_city(pg_db, current_user)

    # Guests: verify against DB market rates but persist nothing, then mask the
    # response so the full analysis stays behind the sign-up wall.
    if current_user is None:
        guest_generator = BOQGenerator(db=None, pg_db=pg_db)
        guest_result = await guest_generator.upload_and_verify(
            file=file,
            uploaded_by="anonymous",
            city=rate_city,
        )
        return _mask_verification_for_guest(guest_result)

    boq_generator = BOQGenerator(db)
    result = await boq_generator.upload_and_verify(
        file=file,
        uploaded_by=str(current_user.id),
        city=rate_city,
    )
    
    return result


### Get BOQ by ID
@router.get("/{boq_id}", response_model=BOQResponse)
async def get_boq(
    boq_id: str,
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    """List all BOQs for the current user, with optional status filter."""
    boq_repo = BOQRepository(db)
    boqs = await boq_repo.list_by_user(
        user_id=str(current_user.id),
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
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
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
        user_id=str(current_user.id)
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
    current_user: User = Depends(get_current_user),
    db = Depends(get_mongodb)
):
    boq_generator = BOQGenerator(db)
    approved_boq = await boq_generator.approve_boq(
        boq_id=boq_id,
        approved_by=str(current_user.id)
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
    current_user: User = Depends(get_current_user),
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
        user_id=str(current_user.id)
    )
    
    return result


### Export BOQ to PDF, Excel, or CSV
@router.post("/{boq_id}/export/{format}")
async def export_boq(
    boq_id: str,
    format: str,
    current_user: User = Depends(get_current_user),
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
        user_id=str(current_user.id),
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
    current_user: User = Depends(get_current_user),
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

    from sqlalchemy import text

    # Phase 10: idempotency — order_number is derived from the BOQ + user so a
    # retry returns the existing order instead of duplicating it.
    order_number = f"ORD-{boq_id[:8].upper()}-{str(current_user.id)[:8]}"
    existing = await pg_db.execute(
        text("SELECT id, order_number FROM orders WHERE order_number = :on"),
        {"on": order_number},
    )
    existing_row = existing.fetchone()
    if existing_row:
        existing_order = await pg_db.execute(
            text("""
                SELECT id, order_number, status, total_amount, payment_status
                FROM orders WHERE id = :oid
            """),
            {"oid": existing_row[0]},
        )
        row = existing_order.fetchone()
        return BOQOrderResponse(
            success=True,
            order_id=str(row[0]),
            order_number=str(row[1]),
            message="This BOQ has already been ordered — returning the existing order.",
            items_ordered=len(order_request.items),
            total_amount=float(row[3] or 0),
        )

    try:
        # Calculate total
        total_amount = sum(item.quantity * item.rate for item in order_request.items)

        # Create order in PostgreSQL
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
                "user_id": str(current_user.id),
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
    current_user: User = Depends(get_current_user),
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
                "user_id": str(current_user.id),
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
    current_user: User = Depends(get_current_user),
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
