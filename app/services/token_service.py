"""
Token Service — manages user token balance, consumption, and purchases.
"""
from typing import Optional, Dict, Any, List
import logging
import asyncio
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.models.token_usage import TokenUsage, TokenTransaction, TransactionType
from app.config import settings

logger = logging.getLogger(__name__)

# Phase 10: per-user in-process lock so concurrent token deductions can't
# double-spend the same balance/free-tier allowance.
_REDIS_LOCKS: Dict[str, asyncio.Lock] = {}
_REDIS_LOCKS_GUARD = asyncio.Lock()

# ── Token cost configuration ────────────────────────────────────────────────

TOKEN_COSTS: Dict[str, int] = {
    "boq_generate_manual": 1,
    "boq_generate_drawing": 2,
    "export_pdf": 1,
    "export_excel": 0.5,
    "export_docx": 0.5,
    "boq_regenerate": 1,
    # Phase 4/8 — AI procurement intelligence operations
    "drawing_analysis": 1,
    "quotation_analysis": 1,
    "supplier_optimisation": 2,
    "procurement_intelligence": 2,
    # Server-side chat message allowance (differentiated per tier)
    "chat_message": 0,  # 0 token cost; limits enforced separately per tier
}

FREE_TIER_MONTHLY_TOKENS = 2
SIGNUP_FREE_TOKENS = 100

# Server-side chat message limits per tier (Phase 8)
CHAT_MESSAGE_LIMITS = {
    "anonymous": 20,      # per month per IP
    "authenticated": 200, # free accounts per month
    "premium": 500,       # users who have purchased tokens
}

# ── Token pack pricing ──────────────────────────────────────────────────────

TOKEN_PACKS: List[Dict[str, Any]] = [
    {"tokens": 10, "price_ngn": 5_000, "price_per_token": 500},
    {"tokens": 50, "price_ngn": 20_000, "price_per_token": 400},
    {"tokens": 200, "price_ngn": 60_000, "price_per_token": 300},
]


class TokenService:
    """Handles all token-related operations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Balance ──────────────────────────────────────────────────────────────

    async def get_or_create_usage(self, user_id: str) -> TokenUsage:
        """Get user's token usage record, creating if not exists."""
        result = await self.db.execute(
            select(TokenUsage).where(TokenUsage.user_id == user_id)
        )
        usage = result.scalar_one_or_none()
        if not usage:
            usage = TokenUsage(
                user_id=user_id,
                balance=0,
                lifetime_purchased=0,
                lifetime_consumed=0,
                free_tier_used_this_month=0,
                free_tier_month=datetime.utcnow().strftime("%Y-%m"),
            )
            self.db.add(usage)
            await self.db.commit()
            await self.db.refresh(usage)
        return usage

    async def get_balance(self, user_id: str) -> int:
        """Get current token balance."""
        usage = await self.get_or_create_usage(user_id)
        return usage.balance

    async def check_balance(self, user_id: str, required_tokens: int) -> bool:
        """Check if user has sufficient tokens."""
        balance = await self.get_balance(user_id)
        return balance >= required_tokens

    # ── Consumption ──────────────────────────────────────────────────────────

    async def deduct_tokens(
        self,
        user_id: str,
        action_type: str,
        boq_id: Optional[str] = None,
        description: Optional[str] = None,
    ) -> bool:
        """
        Deduct tokens for an action.
        Returns True if successful, False if insufficient balance.
        """
        cost = TOKEN_COSTS.get(action_type)
        if cost is None:
            logger.warning(f"Unknown action type: {action_type}")
            return False

        # Per-user lock: prevents concurrent requests from double-spending.
        async with _REDIS_LOCKS_GUARD:
            lock = _REDIS_LOCKS.get(user_id)
            if lock is None:
                lock = asyncio.Lock()
                _REDIS_LOCKS[user_id] = lock
        async with lock:
            return await self._deduct_locked(user_id, action_type, cost, boq_id, description)

    async def _deduct_locked(
        self,
        user_id: str,
        action_type: str,
        cost: int,
        boq_id: Optional[str] = None,
        description: Optional[str] = None,
    ) -> bool:
        """Deduct tokens while holding the per-user lock (no double-spend)."""
        usage = await self.get_or_create_usage(user_id)

        # Check free tier first
        current_month = datetime.utcnow().strftime("%Y-%m")
        if usage.free_tier_month != current_month:
            usage.free_tier_month = current_month
            usage.free_tier_used_this_month = 0

        if usage.free_tier_used_this_month < FREE_TIER_MONTHLY_TOKENS:
            # Use free tier
            usage.free_tier_used_this_month += 1
            transaction = TokenTransaction(
                user_id=user_id,
                transaction_type=TransactionType.FREE_TIER.value,
                amount=0,
                balance_after=usage.balance,
                action_type=action_type,
                boq_id=boq_id,
                description=description or f"Free tier: {action_type}",
            )
            self.db.add(transaction)
            await self.db.commit()
            return True

        # Check paid balance
        if usage.balance < cost:
            return False

        usage.balance -= cost
        usage.lifetime_consumed += cost

        transaction = TokenTransaction(
            user_id=user_id,
            transaction_type=TransactionType.CONSUMPTION.value,
            amount=-cost,
            balance_after=usage.balance,
            action_type=action_type,
            boq_id=boq_id,
            description=description or f"Consumed {cost} token(s) for {action_type}",
        )
        self.db.add(transaction)
        await self.db.commit()
        return True

    # ── Purchase ─────────────────────────────────────────────────────────────

    async def initiate_purchase(
        self, user_id: str, pack_tokens: int
    ) -> Optional[Dict[str, Any]]:
        """
        Initiate a token purchase.
        Returns payment details including amount and reference.
        """
        pack = next((p for p in TOKEN_PACKS if p["tokens"] == pack_tokens), None)
        if not pack:
            return None

        import uuid
        reference = f"TKN-{uuid.uuid4().hex[:12].upper()}"

        # In production, create a payment via Paystack/Flutterwave here
        # For now, return the payment details
        return {
            "reference": reference,
            "amount_ngn": pack["price_ngn"],
            "tokens": pack["tokens"],
            "currency": "NGN",
            "payment_url": f"/api/v1/tokens/pay/{reference}",  # Placeholder
            "description": f"{pack['tokens']} BuildIQ Tokens",
        }

    async def confirm_purchase(
        self, user_id: str, reference: str, tokens: int
    ) -> bool:
        """
        Confirm a token purchase after payment verification.
        Called by payment webhook.
        """
        usage = await self.get_or_create_usage(user_id)
        usage.balance += tokens
        usage.lifetime_purchased += tokens

        transaction = TokenTransaction(
            user_id=user_id,
            transaction_type=TransactionType.PURCHASE.value,
            amount=tokens,
            balance_after=usage.balance,
            reference=reference,
            description=f"Purchased {tokens} tokens (ref: {reference})",
        )
        self.db.add(transaction)
        await self.db.commit()
        return True

    # ── Signup bonus ─────────────────────────────────────────────────────────

    async def grant_signup_tokens(self, user_id: str) -> TokenUsage:
        """Grant free tokens to a new user on sign up."""
        usage = await self.get_or_create_usage(user_id)
        usage.balance = SIGNUP_FREE_TOKENS
        usage.lifetime_purchased += SIGNUP_FREE_TOKENS

        transaction = TokenTransaction(
            user_id=user_id,
            transaction_type=TransactionType.PURCHASE.value,
            amount=SIGNUP_FREE_TOKENS,
            balance_after=usage.balance,
            description=f"Free signup bonus: {SIGNUP_FREE_TOKENS} tokens",
        )
        self.db.add(transaction)
        await self.db.commit()
        await self.db.refresh(usage)
        return usage

    # ── Chat message limits (Phase 8) ────────────────────────────────────────

    async def increment_chat_messages(self, user_id: str) -> int:
        """Increment monthly chat message count for an authenticated user."""
        usage = await self.get_or_create_usage(user_id)
        current_month = datetime.utcnow().strftime("%Y-%m")
        if usage.chat_messages_month != current_month:
            usage.chat_messages_month = current_month
            usage.chat_messages_used_this_month = 0
        usage.chat_messages_used_this_month += 1
        await self.db.commit()
        return usage.chat_messages_used_this_month

    async def chat_message_limit_for(self, user_id: Optional[str]) -> int:
        """Return the monthly chat message limit for a user tier (None = anonymous)."""
        if user_id is None:
            return CHAT_MESSAGE_LIMITS["anonymous"]
        usage = await self.get_or_create_usage(user_id)
        # Users who have purchased (lifetime_purchased > signup bonus) => premium
        if usage.lifetime_purchased > SIGNUP_FREE_TOKENS:
            return CHAT_MESSAGE_LIMITS["premium"]
        return CHAT_MESSAGE_LIMITS["authenticated"]

    async def chat_messages_remaining(self, user_id: Optional[str]) -> int:
        """Return remaining chat messages for a user/IP in the current month."""
        limit = await self.chat_message_limit_for(user_id)
        if user_id is None:
            return limit  # anonymous enforced per-IP via client_ip dict in endpoint
        usage = await self.get_or_create_usage(user_id)
        current_month = datetime.utcnow().strftime("%Y-%m")
        if usage.chat_messages_month != current_month:
            return limit
        return max(0, limit - usage.chat_messages_used_this_month)

    # ── Free tier ────────────────────────────────────────────────────────────

    async def get_free_tier_remaining(self, user_id: str) -> int:
        """Get remaining free tier tokens for this month."""
        usage = await self.get_or_create_usage(user_id)
        current_month = datetime.utcnow().strftime("%Y-%m")

        if usage.free_tier_month != current_month:
            return FREE_TIER_MONTHLY_TOKENS

        return max(0, FREE_TIER_MONTHLY_TOKENS - usage.free_tier_used_this_month)

    # ── Info ─────────────────────────────────────────────────────────────────

    async def get_user_token_info(self, user_id: str) -> Dict[str, Any]:
        """Get full token info for a user."""
        usage = await self.get_or_create_usage(user_id)
        free_remaining = await self.get_free_tier_remaining(user_id)

        return {
            "balance": usage.balance,
            "lifetime_purchased": usage.lifetime_purchased,
            "lifetime_consumed": usage.lifetime_consumed,
            "free_tier_remaining": free_remaining,
            "free_tier_month": usage.free_tier_month or datetime.utcnow().strftime("%Y-%m"),
        }

    @staticmethod
    def get_pricing() -> List[Dict[str, Any]]:
        """Get available token packs."""
        return TOKEN_PACKS

    @staticmethod
    def get_token_cost(action_type: str) -> Optional[int]:
        """Get token cost for an action type."""
        return TOKEN_COSTS.get(action_type)

    @staticmethod
    def get_all_token_costs() -> Dict[str, int]:
        """Get all token costs."""
        return dict(TOKEN_COSTS)