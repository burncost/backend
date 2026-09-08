"""Payout / disbursement layer (Phase 5) — flag- and config-gated.

Guarantees:
- `run()` does NOTHING unless `SETTLEMENT_ENABLED` is truthy AND the chosen
  gateway is configured with keys.
- Even then, actual account resolution + the concrete Flutterwave/Monnify request
  bodies must be validated on staging with test keys before any real transfer.
  This module intentionally raises a clear, safe error if it would need to fire
  without that wiring, so nothing can accidentally move money.
"""
import os

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.payment_capture import PaymentCapture
from app.models.settlement_ledger import VendorSettlementLedger
from app.models.vendor_bank_account import VendorBankAccount
from app.services.settlement_service import is_enabled
from app.config import settings

VALID_GATEWAYS = ("flutterwave", "monnify")


def configured(gateway: str) -> bool:
    if gateway == "flutterwave":
        return bool(settings.FLUTTERWAVE_SECRET_KEY and settings.FLUTTERWAVE_PUBLIC_KEY)
    if gateway == "monnify":
        return bool(settings.MONNIFY_SECRET_KEY and settings.MONNIFY_PUBLIC_KEY)
    return False


class PayoutService:
    async def preview(self, db: AsyncSession, gateway: str | None = None):
        """Read-only pending totals by vendor + captured gateway (safe)."""
        stmt = (
            select(
                VendorSettlementLedger.vendor_id,
                PaymentCapture.gateway,
                VendorSettlementLedger.period,
                func.count(VendorSettlementLedger.id),
                func.sum(VendorSettlementLedger.net_amount),
            )
            .join(PaymentCapture, PaymentCapture.order_id == VendorSettlementLedger.order_id)
            .where(VendorSettlementLedger.status == "pending")
        )
        if gateway:
            stmt = stmt.where(PaymentCapture.gateway == gateway)
        rows = (await db.execute(
            stmt.group_by(
                VendorSettlementLedger.vendor_id,
                PaymentCapture.gateway,
                VendorSettlementLedger.period,
            )
        )).all()
        return {
            "gateway_filter": gateway,
            "groups": [
                {"vendor_id": str(v), "gateway": g, "period": p, "orders": int(n), "total": float(t or 0)}
                for v, g, p, n, t in rows
            ],
        }

    async def resolve_account(self, db: AsyncSession, vendor_id):
        """Pick the vendor's primary verified bank account for payout (or None)."""
        return (await db.execute(
            select(VendorBankAccount)
            .where(
                VendorBankAccount.vendor_id == vendor_id,
                VendorBankAccount.verified.is_(True),
            )
            .order_by(VendorBankAccount.is_primary.desc(), VendorBankAccount.created_at.asc())
            .limit(1)
        )).scalar_one_or_none()

    async def run(self, db: AsyncSession, gateway: str, vendor_id: str | None = None):
        """Execute pending payouts for one gateway.

        Never fires unless the feature flag is on and the gateway is configured.
        When enabled/configured, this resolves each vendor's payout account and —
        only with account data present — would submit a real provider request.
        The concrete Flutterwave/Monnify request bodies must be confirmed against
        your provider's test docs/payloads on staging before enabling real money.
        """
        if not is_enabled():
            return {"status": "disabled", "message": "SETTLEMENT_ENABLED is off"}
        if gateway not in VALID_GATEWAYS:
            return {"status": "invalid_gateway", "message": f"Use {list(VALID_GATEWAYS)}"}
        if not configured(gateway):
            return {"status": "not_configured", "message": f"{gateway} keys are not configured"}

        pending = (await db.execute(
            select(VendorSettlementLedger).where(VendorSettlementLedger.status == "pending")
        )).scalars().all()
        if vendor_id:
            pending = [p for p in pending if str(p.vendor_id) == str(vendor_id)]

        # Resolve payout accounts so we know what would be paid to whom.
        readiness = []
        missing_account_vendors = set()
        for ledger in pending:
            acc = await self.resolve_account(db, ledger.vendor_id)
            if acc is None:
                missing_account_vendors.add(str(ledger.vendor_id))
                continue
            readiness.append({
                "vendor_id": str(ledger.vendor_id),
                "ledger_id": str(ledger.id),
                "bank_name": acc.bank_name,
                "bank_code": acc.bank_code,
                "account_number": acc.account_number,
                "account_name": acc.account_name,
                "net_amount": float(ledger.net_amount),
            })

        return {
            "status": "staging_required",
            "gateway": gateway,
            "pending_entries": len(pending),
            "ready_for_payout": len(readiness),
            "vendors_missing_account": len(missing_account_vendors),
            "message": (
                "Disbursement is staged: accounts resolved. Submit the provider transfer "
                "bodies (confirmed against Monnify/Flutterwave test docs) to complete the run."
            ),
            "readiness": readiness[:200],
        }
