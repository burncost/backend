"""
Token management endpoints — balance, purchase, pricing.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict, Any, List

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import get_current_user
from app.services.token_service import TokenService, TOKEN_COSTS, TOKEN_PACKS

router = APIRouter()


@router.get("/balance")
async def get_token_balance(
    current_user: dict = Depends(get_current_user),
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
    }


@router.post("/purchase")
async def initiate_token_purchase(
    pack_tokens: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Initiate a token pack purchase."""
    service = TokenService(db)
    result = await service.initiate_purchase(str(current_user.id), pack_tokens)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid token pack: {pack_tokens}. Available: 10, 50, 200",
        )
    return result


@router.post("/confirm-payment")
async def confirm_token_purchase(
    reference: str,
    tokens: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Confirm a token purchase after payment verification."""
    service = TokenService(db)
    success = await service.confirm_purchase(str(current_user.id), reference, tokens)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to confirm purchase",
        )
    return {"status": "success", "message": f"{tokens} tokens credited"}


@router.get("/costs")
async def get_token_costs():
    """Get token costs for all action types."""
    return TOKEN_COSTS
