"""Chat endpoint - Conversational AI assistant."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService

import logging

logger = logging.getLogger(__name__)

router = APIRouter()


async def _resolve_user(request: Request) -> tuple:
    """Resolve user identity from cookie or return anonymous."""
    from app.core.security import decode_token
    token = request.cookies.get("access_token")
    if token:
        try:
            payload = decode_token(token)
            user_id = payload.get("sub")
            if user_id:
                return True, user_id
        except Exception:
            pass
    return False, None


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    request: ChatRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Send a message to the AI chat assistant.
    Works for both authenticated and anonymous users.
    """
    is_authenticated, user_id = await _resolve_user(http_request)

    service = ChatService(db=db, is_authenticated=is_authenticated)
    result = await service.handle_message(
        message=request.message,
        conversation_id=request.conversation_id,
        user_id=user_id,
    )
    return result
