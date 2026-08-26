"""AI Agent Log — observability for AI agent tool/intent execution."""
from sqlalchemy import Column, String, DateTime, Integer, JSON, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
import uuid

from app.core.database import Base


class AIAgentLog(Base):
    """Captures each AI agent turn: intent, tool, args, result, source, confidence."""
    __tablename__ = "ai_agent_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    conversation_id = Column(String(100), index=True, nullable=True)

    # Intent + tool
    intent = Column(String(100), index=True)          # "price_query", "boq_generation", "quotation_analysis", ...
    tool_name = Column(String(100), index=True)       # "search_products", "compare_prices", ...
    tool_args = Column(JSON, nullable=True)           # normalized tool arguments

    # Execution result
    execution_status = Column(String(20), default="success")  # success / error / skipped
    result_summary = Column(String(1000), nullable=True)
    execution_result = Column(JSON, nullable=True)    # persisted tool result snapshot

    # Provenance / trust
    price_source = Column(String(20), nullable=True)  # "database" | "ai_estimate" | "unavailable"
    quantity_source = Column(String(20), nullable=True)  # "drawing" | "mitm" | "user" | "ai"
    confidence = Column(Integer, nullable=True)       # 0-100
    fallback_used = Column(String(50), nullable=True) # "mitm_default", "ai_estimate", ...
    estimated_items = Column(Integer, default=0)      # count of flagged-estimate items

    # Cost / token
    tokens_used = Column(Integer, default=0)
    latency_ms = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (Index("ix_ai_agent_logs_user_created", "user_id", "created_at"),)