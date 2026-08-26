"""Phase 3 admin fraud endpoints — Fraud Detection + Alert Review.

Powered by the new fraud models.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta
from uuid import UUID
from typing import Optional

from app.core.database import get_db
from app.api.deps import require_roles
from app.models.fraud import FraudAlert, FraudAlertAccount, FraudAlertTransaction

router = APIRouter()
admin_guard = require_roles("manager", "support", "marketing")


def _alert_dict(a: FraudAlert) -> dict:
    return {
        "id": str(a.id),
        "alert_number": a.alert_number,
        "type": a.alert_type,
        "severity": a.severity,
        "description": a.description,
        "risk_score": a.risk_score or 0,
        "amount": float(a.amount or 0),
        "status": a.status,
        "is_negotiation": bool(a.is_negotiation),
        "detected_at": a.detected_at.isoformat() if a.detected_at else None,
        "timestamp": a.detected_at.strftime("%b %d, %Y at %I:%M %p") if a.detected_at else "—",
        "accounts": [
            {"id": str(x.id), "name": x.account_name, "email": x.account_email,
             "account_id": x.account_id, "created_at": x.created_at.isoformat() if x.created_at else None}
            for x in a.accounts
        ],
        "transactions": [
            {"id": str(x.id), "transaction_id": x.transaction_id,
             "amount": float(x.amount or 0), "created_at": x.created_at.isoformat() if x.created_at else None}
            for x in a.transactions
        ],
    }


@router.get("/fraud/alerts")
async def admin_list_fraud_alerts(
    is_negotiation: Optional[bool] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """All fraud alerts (optionally filtered by negotiation-fraud or status)."""
    query = (select(FraudAlert)
             .options(selectinload(FraudAlert.accounts), selectinload(FraudAlert.transactions)))
    if is_negotiation is not None:
        query = query.where(FraudAlert.is_negotiation == is_negotiation)
    if status:
        query = query.where(FraudAlert.status == status)
    rows = (await db.execute(query.order_by(FraudAlert.detected_at.desc()).limit(limit))).scalars().all()

    alerts = [_alert_dict(a) for a in rows]
    active_alerts = sum(1 for a in alerts if a["status"] == "under_review")
    blocked = sum(1 for a in alerts if a["status"] == "blocked")
    total_amount = sum(a["amount"] for a in alerts if a["status"] != "cleared")

    return {
        "alerts": alerts,
        "active_count": active_alerts,
        "blocked_count": blocked,
        "under_review_count": sum(1 for a in alerts if a["status"] == "under_review"),
        "total_amount": total_amount,
        "total": len(alerts),
    }


@router.get("/fraud/alerts/{alert_id}")
async def admin_fraud_alert_detail(
    alert_id: UUID,
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """A single fraud alert with its accounts and transactions."""
    a = (await db.execute(select(FraudAlert)
                          .options(selectinload(FraudAlert.accounts), selectinload(FraudAlert.transactions))
                          .where(FraudAlert.id == alert_id))).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Fraud alert not found")
    return _alert_dict(a)


@router.post("/fraud/alerts/{alert_id}/review")
async def admin_fraud_alert_review(
    alert_id: UUID,
    payload: dict,
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Clear or block a fraud alert."""
    action = (payload or {}).get("action")
    a = (await db.execute(select(FraudAlert).where(FraudAlert.id == alert_id))).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Fraud alert not found")
    if action == "clear":
        a.status = "cleared"
    elif action == "block":
        a.status = "blocked"
    else:
        raise HTTPException(400, "action must be 'clear' or 'block'")
    a.resolved_at = datetime.utcnow()
    a.resolved_by = str(current_user.get("id", "admin"))
    await db.commit()
    return {"alert_id": str(a.id), "status": a.status}