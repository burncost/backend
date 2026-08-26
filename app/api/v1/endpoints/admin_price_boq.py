"""Phase 4 admin endpoints — Price Integrity + BOQ Analysis.

Powered by the new price_boq models.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from datetime import datetime
from uuid import UUID
from typing import Optional

from app.core.database import get_db
from app.api.deps import require_roles
from app.models.price_boq import PriceAnomaly, PriceAnomalyHistory, BOQAnalysis, BOQAnalysisItem, BOQAnalysisFlag

router = APIRouter()
admin_guard = require_roles("manager", "support", "marketing")


def _anomaly_dict(a: PriceAnomaly) -> dict:
    return {
        "id": str(a.id),
        "anomaly_number": a.anomaly_number,
        "item": a.item_name,
        "supplier": a.supplier_name,
        "market_price": float(a.market_price or 0),
        "quoted_price": float(a.quoted_price or 0),
        "variance": f"{'+' if (a.variance_pct or 0) >= 0 else ''}{(a.variance_pct or 0)}%",
        "variance_pct": float(a.variance_pct or 0),
        "status": a.status,
        "detected_at": a.detected_at.isoformat() if a.detected_at else None,
        "last_checked": a.detected_at.strftime("%Y-%m-%d") if a.detected_at else "—",
        "history": [{"price": float(h.price or 0), "recorded_at": h.recorded_at.isoformat() if h.recorded_at else None}
                    for h in a.history],
    }


@router.get("/price-anomalies")
async def admin_list_price_anomalies(
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """All price anomalies."""
    rows = (await db.execute(select(PriceAnomaly).options(selectinload(PriceAnomaly.history))
                             .order_by(PriceAnomaly.detected_at.desc()).limit(limit))).scalars().all()
    anomalies = [_anomaly_dict(a) for a in rows]
    flagged = sum(1 for a in anomalies if a["status"] == "flagged")
    avg_variance = sum(a["variance_pct"] for a in anomalies) / len(anomalies) if anomalies else 0
    verified = sum(1 for a in anomalies if a["status"] in ("approved", "normal"))
    return {"anomalies": anomalies, "flagged_count": flagged, "avg_variance_pct": round(avg_variance, 1),
            "verified_count": verified}


@router.get("/price-anomalies/{anomaly_id}")
async def admin_price_anomaly_detail(
    anomaly_id: UUID,
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """A single price anomaly with price history."""
    a = (await db.execute(select(PriceAnomaly).options(selectinload(PriceAnomaly.history)).where(PriceAnomaly.id == anomaly_id))).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Price anomaly not found")
    return _anomaly_dict(a)


@router.post("/price-anomalies/{anomaly_id}/review")
async def admin_price_anomaly_review(
    anomaly_id: UUID,
    payload: dict,
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Approve or reject a price anomaly."""
    action = (payload or {}).get("action")
    a = (await db.execute(select(PriceAnomaly).where(PriceAnomaly.id == anomaly_id))).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Price anomaly not found")
    if action == "approve":
        a.status = "approved"
    elif action == "reject":
        a.status = "rejected"
    else:
        raise HTTPException(400, "action must be 'approve' or 'reject'")
    a.reviewed_at = datetime.utcnow()
    a.reviewed_by = str(current_user.get("id", "admin"))
    await db.commit()
    return {"anomaly_id": str(a.id), "status": a.status}


def _boq_summary(b: BOQAnalysis) -> dict:
    return {
        "id": str(b.id),
        "boq_number": b.boq_number,
        "title": b.title,
        "status": b.status,
        "created_by": b.created_by,
        "version": b.version,
        "confidence": float(b.confidence or 0),
        "total_items": b.total_items or 0,
        "flagged_items": b.flagged_items or 0,
        "total_value": float(b.total_value or 0),
        "quoted_value": float(b.quoted_value or 0),
        "potential_savings": float(b.potential_savings or 0),
        "avg_variance": float(b.avg_variance or 0),
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }


def _boq_full(b: BOQAnalysis) -> dict:
    d = _boq_summary(b)
    d["items"] = [
        {"id": str(i.id), "category": i.category, "item": i.item_name,
         "quantity": float(i.quantity or 0), "quoted_price": float(i.quoted_price or 0),
         "market_price": float(i.market_price or 0), "variance_pct": float(i.variance_pct or 0),
         "potential_saving": float(i.potential_saving or 0), "status": i.status}
        for i in b.items
    ]
    return d


@router.get("/boqs/{boq_id}/report")
async def admin_boq_report(
    boq_id: UUID,
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Full BOQ analysis report (items)."""
    b = (await db.execute(select(BOQAnalysis).options(selectinload(BOQAnalysis.items)).where(BOQAnalysis.id == boq_id))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "BOQ analysis not found")
    return _boq_full(b)


@router.get("/boqs/{boq_id}/status")
async def admin_boq_status(
    boq_id: UUID,
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """BOQ analysis status overview."""
    b = (await db.execute(select(BOQAnalysis).where(BOQAnalysis.id == boq_id))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "BOQ analysis not found")
    return _boq_summary(b)


@router.get("/boqs/{boq_id}/flags")
async def admin_boq_flags(
    boq_id: UUID,
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """AI-flagged items for a BOQ analysis."""
    flags = (await db.execute(select(BOQAnalysisFlag).where(BOQAnalysisFlag.boq_id == boq_id))).scalars().all()
    return {"flags": [
        {"id": str(f.id), "severity": f.severity, "issue": f.issue,
         "recommendation": f.recommendation, "status": f.status}
        for f in flags
    ]}