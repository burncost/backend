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
from app.models.token_purchase import (
    TokenPurchase,
    PURCHASE_PENDING,
    PURCHASE_COMPLETED,
    PURCHASE_FAILED,
)
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
    "export_csv": 0.5,
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

# Smallest number of tokens a single purchase may cover. Anything below this is
# not a purchase at all, so the API rejects it instead of quoting ₦0.
MIN_PURCHASE_TOKENS = 1


def compute_token_price(tokens: int) -> Optional[Dict[str, Any]]:
    """Price an arbitrary token quantity from the published pack tiers.

    The rate is the cheapest published tier the quantity qualifies for, i.e. the
    largest pack whose size is <= the quantity. Quantities smaller than the
    smallest pack pay that pack's rate. This is the single source of truth for
    the quote: the same number is shown in the UI and charged by the gateway,
    and the client never supplies the amount.
    """
    try:
        tokens = int(tokens)
    except (TypeError, ValueError):
        return None
    if tokens < MIN_PURCHASE_TOKENS:
        return None

    eligible = [p for p in TOKEN_PACKS if p["tokens"] <= tokens]
    tier = (
        max(eligible, key=lambda p: p["tokens"])
        if eligible
        else min(TOKEN_PACKS, key=lambda p: p["tokens"])
    )
    rate = int(tier["price_per_token"])
    return {
        "tokens": tokens,
        "price_per_token": rate,
        "price_ngn": tokens * rate,
    }


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
        self, user_id: str, pack_tokens: int, email: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Initiate a purchase of an arbitrary number of tokens.

        `pack_tokens` is the quantity the user wants to buy (historically a fixed
        pack size; now any amount >= MIN_PURCHASE_TOKENS). The price is computed
        server-side from the published tiers, so a tampered quantity can never
        underpay. Returns None when the quantity is not purchasable, so the
        caller can answer with a 400.
        """
        quote = compute_token_price(pack_tokens)
        if not quote:
            return None

        import uuid
        reference = f"TKN-{uuid.uuid4().hex[:12].upper()}"

        # Hand the server-computed amount to the payment gateway (mock in dev).
        from app.services.payment_service import PaymentService

        payment = await PaymentService().initialize_payment(
            amount=float(quote["price_ngn"]),
            email=email or "",
            reference=reference,
            metadata={
                "type": "token_purchase",
                "user_id": user_id,
                "tokens": quote["tokens"],
            },
            # Send the buyer back to the wallet, not the orders page.
            redirect_url=f"{settings.FRONTEND_URL}/dashboard/tokens?tx_ref={reference}",
        )

        provider = str(payment.get("provider") or "")
        if not payment.get("success"):
            return {
                "success": False,
                "error": payment.get("error") or "Payment initialization failed",
                "reference": reference,
                "provider": provider,
            }

        # Persist the quote BEFORE returning the checkout link: this row is what a
        # webhook / verification call credits later, so the quantity and amount can
        # never be supplied by the client at credit time. The reference column is
        # unique, which makes crediting idempotent by construction.
        self.db.add(TokenPurchase(
            user_id=user_id,
            reference=reference,
            tokens=int(quote["tokens"]),
            amount_ngn=int(quote["price_ngn"]),
            price_per_token=int(quote["price_per_token"]),
            provider=provider or None,
            status=PURCHASE_PENDING,
        ))
        try:
            await self.db.commit()
        except Exception as exc:  # never break the checkout for a bookkeeping row
            await self.db.rollback()
            logger.error("Could not persist pending token purchase %s: %s", reference, exc)

        # Mock mode has no external checkout page to visit - return no URL and let
        # the client confirm locally (dev/test only, as before).
        payment_url = "" if provider == "mock" else (payment.get("authorization_url") or "")

        return {
            "success": True,
            # Always our own reference: it is the key of the pending purchase row,
            # so the client (and the webhook) refer to the purchase by the same id.
            "reference": reference,
            "amount_ngn": quote["price_ngn"],
            "tokens": quote["tokens"],
            "price_per_token": quote["price_per_token"],
            "currency": "NGN",
            "payment_url": payment_url,
            "provider": provider,
            "description": f"{quote['tokens']} BuildIQ Tokens",
        }

    # ── Gateway-confirmed purchase (webhook / return verification) ───────────

    async def credit_pending_purchase(
        self,
        reference: str,
        *,
        provider: Optional[str] = None,
        paid_amount: Optional[float] = None,
        provider_reference: Optional[str] = None,
        user_id: Optional[str] = None,
        expected_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Credit the tokens quoted for `reference` once its payment is confirmed.

        Callers (the Flutterwave webhook, the return-trip verification endpoint) only
        ever report that a payment succeeded — quantity, price and owning user come
        from the pending row written by `initiate_purchase`, so a forged callback can
        never mint tokens. Idempotent: the row is claimed with a conditional UPDATE in
        the same transaction that adds the balance, so a repeated webhook (or a webhook
        racing the buyer's return trip) credits exactly once.

        Returns {"status": ...} — one of:
          credited | already_credited | not_found | forbidden | tokens_mismatch | amount_mismatch
        """
        purchase = (await self.db.execute(
            select(TokenPurchase).where(TokenPurchase.reference == reference)
        )).scalar_one_or_none()

        if not purchase:
            logger.warning("Token credit skipped: unknown reference %s", reference)
            return {"status": "not_found", "reference": reference}

        if user_id and str(purchase.user_id) != str(user_id):
            logger.warning("Token credit refused: %s does not belong to user %s", reference, user_id)
            return {"status": "forbidden", "reference": reference}

        if expected_tokens is not None and int(expected_tokens) != int(purchase.tokens):
            logger.warning(
                "Token credit refused: %s quotes %s tokens but %s were requested",
                reference, purchase.tokens, expected_tokens,
            )
            return {"status": "tokens_mismatch", "reference": reference}

        if purchase.status == PURCHASE_COMPLETED:
            return {"status": "already_credited", "reference": reference, "tokens": int(purchase.tokens)}

        if paid_amount is not None and abs(float(paid_amount) - float(purchase.amount_ngn)) > 0.01:
            logger.error(
                "Token credit refused: gateway reported %.2f for %s but %.2f was quoted",
                float(paid_amount), reference, float(purchase.amount_ngn),
            )
            return {"status": "amount_mismatch", "reference": reference}

        tokens = int(purchase.tokens)
        owner_id = str(purchase.user_id)

        # May insert the usage row (commits internally); nothing else is pending yet.
        usage = await self.get_or_create_usage(owner_id)

        # Atomic claim — a second caller (webhook vs. return trip) gets rowcount 0.
        claimed = await self.db.execute(
            update(TokenPurchase)
            .where(TokenPurchase.id == purchase.id, TokenPurchase.status != PURCHASE_COMPLETED)
            .values(
                status=PURCHASE_COMPLETED,
                completed_at=datetime.utcnow(),
                provider=provider or purchase.provider,
                provider_reference=provider_reference or purchase.provider_reference,
                paid_amount_ngn=(
                    int(round(float(paid_amount))) if paid_amount is not None
                    else purchase.paid_amount_ngn
                ),
            )
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            await self.db.rollback()
            logger.info("Token credit: %s was already credited concurrently", reference)
            return {"status": "already_credited", "reference": reference, "tokens": tokens}

        usage.balance += tokens
        usage.lifetime_purchased += tokens
        self.db.add(TokenTransaction(
            user_id=owner_id,
            transaction_type=TransactionType.PURCHASE.value,
            amount=tokens,
            balance_after=usage.balance,
            reference=reference,
            description=f"Purchased {tokens} tokens (ref: {reference})",
        ))
        await self.db.commit()
        logger.info("Token credit: %s tokens for user %s (ref %s)", tokens, owner_id, reference)
        return {
            "status": "credited",
            "reference": reference,
            "tokens": tokens,
            "balance": usage.balance,
        }

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