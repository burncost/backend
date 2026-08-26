"""AI Procurement Intelligence endpoints (Phase 4).

All operations are DB-verified — no hallucinated prices. Insufficient-data
flags are returned instead of invented numbers.
"""
from typing import List, Dict, Any, Optional
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.ratelimit import rate_limit
from app.api.deps import get_current_user
from app.services.procurement_intelligence_service import ProcurementIntelligenceService

logger = logging.getLogger(__name__)

router = APIRouter()

# Phase 10: burst rate limit (requests per minute) for AI intelligence endpoints
AI_RATE_LIMIT = 30
AI_RATE_WINDOW = 60


async def _enforce_ai_rate_limit(request: Request, current_user: dict) -> None:
    """Per-user (and per-IP fallback) rate limit for AI procurement endpoints."""
    key = current_user["id"] if current_user else (request.client.host if request.client else "unknown")
    if not await rate_limit(AI_RATE_LIMIT, AI_RATE_WINDOW, "ai", key):
        raise HTTPException(status_code=429, detail="Too many requests. Please slow down and try again shortly.")


# ── Request schemas ──────────────────────────────────────────────────────────

class QuotationLine(BaseModel):
    description: str
    quantity: float = 1.0
    unit: Optional[str] = None
    quoted_rate: float = 0.0


class QuotationAnalysisRequest(BaseModel):
    quote_text: Optional[str] = None
    items: Optional[List[QuotationLine]] = None
    supplier_name: Optional[str] = None
    city: str = "Abuja"


class SavingsRequest(BaseModel):
    items: List[QuotationLine]
    supplier_a: Dict[str, float]
    supplier_b: Dict[str, float]


class ProcurementScoreRequest(BaseModel):
    items: List[QuotationLine]
    city: str = "Abuja"
    boq_id: Optional[str] = None   # when provided, persists score to boq_analyses


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/compare-prices")
async def compare_prices(
    request: Request,
    description: str,
    quantity: float = Query(1.0, ge=0.01),
    city: str = Query("Abuja"),
    current_user: dict = Depends(get_current_user),
    pg_db: AsyncSession = Depends(get_db),
):
    """Compare verified DB offers for a material (incl. total procurement cost)."""
    await _enforce_ai_rate_limit(request, current_user)
    service = ProcurementIntelligenceService(pg_db)
    return await service.compare_prices(description, quantity, city)


@router.get("/price-range")
async def price_range(
    request: Request,
    description: str,
    city: str = Query("Abuja"),
    current_user: dict = Depends(get_current_user),
    pg_db: AsyncSession = Depends(get_db),
):
    """Verified price range (min/max) from DB offers."""
    await _enforce_ai_rate_limit(request, current_user)
    service = ProcurementIntelligenceService(pg_db)
    return await service.get_price_range(description, city)


@router.get("/price-history")
async def price_history(
    request: Request,
    description: str,
    city: str = Query("Abuja"),
    limit: int = Query(12, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    pg_db: AsyncSession = Depends(get_db),
):
    """Price history/trend from material_rate_history."""
    await _enforce_ai_rate_limit(request, current_user)
    service = ProcurementIntelligenceService(pg_db)
    return await service.get_price_history(description, city, limit)


@router.post("/analyse-quotation")
async def analyse_quotation(
    request: Request,
    req: QuotationAnalysisRequest,
    current_user: dict = Depends(get_current_user),
    pg_db: AsyncSession = Depends(get_db),
):
    """DB-verified quotation analysis with inflation flags (never fraud accusations)."""
    await _enforce_ai_rate_limit(request, current_user)
    if not req.items and not req.quote_text:
        raise HTTPException(status_code=400, detail="Provide either 'items' or 'quote_text'.")

    items = req.items
    if not items and req.quote_text:
        # Parse quote text via the BOQ generator (DB-verified parsing).
        from app.services.boq_generator import BOQGenerator
        boq_gen = BOQGenerator(pg_db=pg_db)
        parsed = await boq_gen.verify_quote_text(req.quote_text, current_user["id"])
        items = [
            QuotationLine(
                description=i.get("description", ""),
                quantity=i.get("quantity", 0),
                unit=i.get("unit"),
                quoted_rate=i.get("quoted_rate", 0),
            )
            for i in parsed.get("items", [])
        ]
        if not items:
            return parsed

    service = ProcurementIntelligenceService(pg_db)
    return await service.analyse_quotation(
        quoted_items=[i.model_dump() for i in items],
        supplier_name=req.supplier_name,
        user_id=current_user["id"],
        city=req.city,
    )


@router.post("/procurement-score")
async def procurement_score(
    req: ProcurementScoreRequest,
    current_user: dict = Depends(get_current_user),
    pg_db: AsyncSession = Depends(get_db),
):
    """Explainable, DB-based procurement score (0-100)."""
    service = ProcurementIntelligenceService(pg_db)
    return await service.get_procurement_score(
        items=[i.model_dump() for i in req.items],
        city=req.city,
        boq_id=req.boq_id,
        created_by=current_user["id"],
    )


@router.post("/savings")
async def savings(
    req: SavingsRequest,
    current_user: dict = Depends(get_current_user),
    pg_db: AsyncSession = Depends(get_db),
):
    """DB-based savings comparison between two suppliers."""
    service = ProcurementIntelligenceService(pg_db)
    return await service.calculate_savings(
        items=[i.model_dump() for i in req.items],
        supplier_a=req.supplier_a,
        supplier_b=req.supplier_b,
    )