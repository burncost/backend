"""
Token management endpoints — balance, purchase, pricing.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict, Any, List

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import get_current_user
from app.services.token_service import (
    TokenService,
    TOKEN_COSTS,
    TOKEN_PACKS,
    MIN_PURCHASE_TOKENS,
)
from app.models.user import User

router = APIRouter()


def _credit_response(result: Dict[str, Any]) -> Dict[str, Any]:
    """Turn a `credit_pending_purchase` outcome into an HTTP response.

    Refusals raise: a reference that is unknown/foreign can't be credited from the
    client side, and a mismatched quantity or amount means the quote was tampered
    with (or the charge was for something else).
    """
    outcome = result.get("status")

    if outcome == "credited":
        return {
            "status": "success",
            "message": f"{result['tokens']} tokens credited",
            "tokens": result["tokens"],
            "balance": result.get("balance"),
        }
    if outcome == "already_credited":
        return {
            "status": "success",
            "message": f"{result['tokens']} tokens already credited",
            "tokens": result["tokens"],
        }
    if outcome == "forbidden":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This purchase belongs to another account.")
    if outcome == "tokens_mismatch":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Quantity does not match this purchase reference.")
    if outcome == "amount_mismatch":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The amount paid does not match the quoted price.")

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown purchase reference.")


@router.get("/balance")
async def get_token_balance(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current user's token balance and info."""
    service = TokenService(db)
    info = await service.get_user_token_info(str(current_user.id))
    return info


@router.get("/pricing")
async def get_token_pricing():
    """Get available token packs and pricing."""
    return {
        "packs": TOKEN_PACKS,
        "token_costs": TOKEN_COSTS,
        "free_tier_monthly": 2,
        # Smallest quantity a custom purchase may cover (mirrors the backend rule
        # so the UI can stop a 0-token purchase before it is ever sent).
        "min_tokens": MIN_PURCHASE_TOKENS,
    }


@router.post("/purchase")
async def initiate_token_purchase(
    pack_tokens: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Initiate a purchase of `pack_tokens` tokens (any amount >= min_tokens)."""
    service = TokenService(db)
    result = await service.initiate_purchase(
        str(current_user.id), pack_tokens, email=current_user.email
    )
    if not result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Enter at least {MIN_PURCHASE_TOKENS} token to purchase.",
        )
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result.get("error") or "Payment initialization failed. Please try again.",
        )
    return result


@router.post("/confirm-payment")
async def confirm_token_purchase(
    reference: str,
    tokens: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Credit a purchase when no gateway is in play (mock/dev mode only).

    With a real gateway configured the tokens are credited by the gateway webhook
    or by /verify-payment, so this endpoint refuses rather than mint tokens on a
    bare client call. The quantity is still validated against the stored quote.
    """
    from app.services.payment_service import PaymentService

    if not PaymentService().mock_mode:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This purchase is credited once the payment gateway confirms it.",
        )

    service = TokenService(db)
    result = await service.credit_pending_purchase(
        reference,
        user_id=str(current_user.id),
        expected_tokens=tokens,
        provider="mock",
    )
    return _credit_response(result)


@router.post("/verify-payment")
async def verify_token_purchase(
    reference: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ask the gateway about `reference` and credit the purchase if it was paid.

    Used when the buyer returns from the checkout page: a locally-hosted server
    cannot receive gateway webhooks, so the return trip is the trigger. Safe to
    call any number of times — crediting is idempotent by reference, and the
    webhook may already have done it.
    """
    from app.services.payment_service import PaymentService

    service = TokenService(db)
    payment_service = PaymentService()

    if payment_service.mock_mode:
        result = await service.credit_pending_purchase(
            reference, user_id=str(current_user.id), provider="mock"
        )
        return _credit_response(result)

    verification = await payment_service.verify_payment(reference)
    if not verification.get("success"):
        # 409 (not 402) so the client shows this text rather than the generic
        # "insufficient tokens" message that 402 maps to.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The payment gateway has not confirmed this payment yet.",
        )

    result = await service.credit_pending_purchase(
        reference,
        user_id=str(current_user.id),
        provider=verification.get("provider"),
        paid_amount=verification.get("amount"),
    )
    return _credit_response(result)


@router.get("/costs")
async def get_token_costs():
    """Get token costs for all action types."""
    return TOKEN_COSTS
