from pydantic import BaseModel
from typing import Optional, List, Any


class ToolCall(BaseModel):
    id: str
    type: str = "function"
    function: dict


class ChatMessage(BaseModel):
    role: str  # user, assistant, system, tool
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None


class ActionButton(BaseModel):
    label: str
    type: str  # "add_to_cart" | "checkout" | "signup" | "view_product" | "create_boq"
    data: Optional[dict] = None


class ChatResponse(BaseModel):
    reply: str
    conversation_id: str
    action: Optional[str] = None  # "auth_required" | "signup_required" | None
    actions: Optional[List[ActionButton]] = None
    tool_results: Optional[List[dict]] = None
    has_tool_results: bool = False
