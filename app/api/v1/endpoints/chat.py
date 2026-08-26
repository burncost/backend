"""Chat endpoint - Conversational AI assistant."""
from typing import Dict
from datetime import datetime
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.ratelimit import rate_limit
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService
from app.models.user import User, UserProfile

import logging

logger = logging.getLogger(__name__)

router = APIRouter()

# Phase 10: burst rate limit (requests per minute) for /chat
CHAT_RATE_LIMIT = 30
CHAT_RATE_WINDOW = 60


async def _resolve_user(request: Request, db: AsyncSession) -> tuple:
    """Resolve user identity from cookie or return anonymous."""
    from app.core.security import decode_token
    token = request.cookies.get("access_token")
    if token:
        try:
            payload = decode_token(token)
            user_id = payload.get("sub")
            if user_id:
                # Fetch user's location from profile
                user_location = None
                try:
                    result = await db.execute(
                        select(UserProfile).where(UserProfile.user_id == user_id)
                    )
                    profile = result.scalar_one_or_none()
                    if profile and profile.location:
                        user_location = profile.location
                except Exception:
                    pass
                return True, user_id, user_location
        except Exception:
            pass
    return False, None, None


# In-memory anonymous chat counter keyed by client IP (per month). Server-side
# only — a new conversation_id cannot bypass it. Authenticated users use the
# token_usage.chat_messages_* columns instead.
import collections
_ANONYMOUS_CHAT_COUNTS: Dict[tuple, int] = collections.defaultdict(int)


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    request: ChatRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Send a message to the AI chat assistant.
    Works for both authenticated and anonymous users.
    Enforces server-side monthly chat limits (per-user for authenticated,
    per-IP for anonymous). A new conversation_id cannot bypass the limit.
    """
    from app.services.token_service import TokenService

    is_authenticated, user_id, user_location = await _resolve_user(http_request, db)

    # Phase 10: burst rate limit per IP (and per user when authenticated).
    client_ip = http_request.client.host if http_request.client else "unknown"
    rl_key = user_id if is_authenticated and user_id else client_ip
    if not await rate_limit(CHAT_RATE_LIMIT, CHAT_RATE_WINDOW, "chat", rl_key):
        raise HTTPException(status_code=429, detail="Too many requests. Please slow down and try again shortly.")

    token_service = TokenService(db)

    if is_authenticated and user_id:
        remaining = await token_service.chat_messages_remaining(user_id)
        if remaining <= 0:
            return ChatResponse(
                reply=(
                    "You've reached your monthly chat message limit. "
                    "Purchasing tokens raises your limit — or wait for the next month to continue."
                ),
                conversation_id=request.conversation_id or "",
                action="signup_required",
            )
        await token_service.increment_chat_messages(user_id)
    else:
        # Anonymous: per-IP monthly limit.
        month = datetime.utcnow().strftime("%Y-%m")
        client_ip = http_request.client.host if http_request.client else "unknown"
        key = (client_ip, month)
        used = _ANONYMOUS_CHAT_COUNTS[key]
        limit = await token_service.chat_message_limit_for(None)  # anonymous = 20
        if used >= limit:
            return ChatResponse(
                reply=(
                    "You've reached the free anonymous chat limit for this month. "
                    "Create a free account to continue using the assistant."
                ),
                conversation_id=request.conversation_id or "",
                action="signup_required",
            )
        _ANONYMOUS_CHAT_COUNTS[key] = used + 1

    service = ChatService(db=db, is_authenticated=is_authenticated, pg_db=db, user_id=user_id)
    result = await service.handle_message(
        message=request.message,
        conversation_id=request.conversation_id,
        user_id=user_id,
        user_location=user_location,
    )
    return result
