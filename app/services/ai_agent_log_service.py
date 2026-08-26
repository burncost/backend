"""AI Agent Log Service — records AI agent turns for observability.

Persists to the `ai_agent_logs` table (added in Phase 2) capturing intent,
tool, args, result, price_source, confidence, fallback, tokens, latency.
"""
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_agent_log import AIAgentLog

logger = logging.getLogger(__name__)


class AIAgentLogService:
    """Observability recorder for AI agent interactions."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def log_turn(
        self,
        *,
        user_id: Optional[str],
        conversation_id: Optional[str],
        intent: Optional[str],
        tool_name: Optional[str],
        tool_args: Optional[Dict[str, Any]] = None,
        execution_status: str = "success",
        result_summary: Optional[str] = None,
        execution_result: Optional[Dict[str, Any]] = None,
        price_source: Optional[str] = None,
        quantity_source: Optional[str] = None,
        confidence: Optional[int] = None,
        fallback_used: Optional[str] = None,
        estimated_items: int = 0,
        tokens_used: int = 0,
        latency_ms: int = 0,
    ) -> None:
        """Insert one agent-log row (best-effort; never breaks the chat flow)."""
        try:
            self.db.add(AIAgentLog(
                user_id=user_id,
                conversation_id=conversation_id,
                intent=intent,
                tool_name=tool_name,
                tool_args=tool_args or {},
                execution_status=execution_status[:20],
                result_summary=(result_summary or "")[:1000],
                execution_result=execution_result or {},
                price_source=price_source,
                quantity_source=quantity_source,
                confidence=confidence,
                fallback_used=fallback_used,
                estimated_items=estimated_items,
                tokens_used=tokens_used,
                latency_ms=latency_ms,
                created_at=datetime.utcnow(),
            ))
            await self.db.commit()
        except Exception as e:
            logger.warning(f"AI agent log insert failed: {e}")
            try:
                await self.db.rollback()
            except Exception:
                pass