"""Chat endpoint - Conversational AI assistant."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService
from app.models.user import User, UserProfile

import logging

logger = logging.getLogger(__name__)

router = APIRouter()


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


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    request: ChatRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Send a message to the AI chat assistant.
    Works for both authenticated and anonymous users.
    """
    is_authenticated, user_id, user_location = await _resolve_user(http_request, db)

    service = ChatService(db=db, is_authenticated=is_authenticated)
    result = await service.handle_message(
        message=request.message,
        conversation_id=request.conversation_id,
        user_id=user_id,
        user_location=user_location,
    )
    return result
