"""AI Service — Gemini-powered document analysis and chat."""
import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from google.api_core import exceptions as google_exceptions
from google.genai import types as genai_types

from app.services.gemini_client import get_gemini_client

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

_MIME_MAP: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}


def _infer_mime(file_name: str, fallback: str = "image/png") -> str:
    ext = (file_name or "").rsplit(".", 1)[-1].lower() if file_name else ""
    return _MIME_MAP.get(f".{ext}", fallback)


def _extract_json(text: str) -> Optional[str]:
    """Extract JSON block from Gemini text response."""
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        return match.group(1)
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return match.group(0)
    return None


def _empty_analysis(error: str) -> Dict[str, Any]:
    return {
        "processed": False,
        "processedAt": datetime.utcnow().isoformat(),
        "detectedElements": [],
        "rooms": [],
        "detectedMaterials": [],
        "processingErrors": [error],
    }


# ── Document Analysis Service ────────────────────────────────────────────────

class AIService:
    """Analyse construction documents (PDF / image) via Gemini Vision."""

    def __init__(self):
        self.client = get_gemini_client()
        self.model = "gemini-2.5-pro"

    async def analyze_document(
        self,
        file_content: bytes,
        file_type: str,
        extracted_metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Analyse a construction document (PDF or image) and extract building elements."""
        logger.info("AI analysing %s document (%d bytes)", file_type, len(file_content))

        mime = _infer_mime(file_type)
        prompt = self._build_analysis_prompt(file_type, extracted_metadata)

        try:
            part = genai_types.Part.from_bytes(data=file_content, mime_type=mime)
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model,
                contents=[part, prompt],
                config=genai_types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=8192,
                ),
            )


            text = response.text
            if not text:
                return _empty_analysis("Empty Gemini response")

            json_str = _extract_json(text)
            if json_str:
                analysis = json.loads(json_str)
                analysis["processed"] = True
                analysis["processedAt"] = datetime.utcnow().isoformat()
                return analysis

            return _empty_analysis("Failed to parse AI response as JSON")

        except Exception as exc:
            logger.error("AI analysis failed: %s", exc)
            return _empty_analysis(str(exc))

    def _build_analysis_prompt(self, file_type: str, metadata: Dict[str, Any]) -> str:
        return f"""You are a construction quantity surveyor AI. Analyse this {file_type} document and extract building elements.

Document metadata:
{json.dumps(metadata, indent=2)}

Return a JSON object with:
1. "detectedElements": array of {{
    "elementType": string (e.g. "external_wall", "slab", "foundation", "column", "beam", "roof"),
    "count": number,
    "totalQuantity": number,
    "unit": string (e.g. "m²", "m³", "m", "nr"),
    "attributes": {{ key: value }},
    "confidence": number (0-1)
  }}
2. "rooms": array of {{
    "roomName": string,
    "roomType": string,
    "floor": string,
    "area": number,
    "perimeter": number,
    "height": number,
    "volume": number,
    "finishes": {{ floor, wall, ceiling }}
  }}
3. "detectedMaterials": array of {{
    "materialName": string,
    "category": string,
    "specification": string,
    "mentions": number
  }}
4. "processingErrors": array of strings

Return ONLY valid JSON, no markdown formatting."""


# ── Chat AI Service (function calling) ───────────────────────────────────────

class ChatAIService:
    """Conversational AI agent using Gemini with function calling."""

    def __init__(self):
        self.client = get_gemini_client()
        self.model = "gemini-2.5-flash"

    async def chat_completion(
        self,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
    ):
        """
        Send a chat completion request with optional function calling.
        Returns an object duck-typed like an OpenAI ChatCompletion response
        so the existing ChatService loop works without changes.
        """
        # Convert OpenAI-style messages to Gemini contents
        contents = self._messages_to_contents(messages)

        # Convert OpenAI-style tool definitions to Gemini FunctionDeclarations
        gemini_tools = None
        if tools:
            gemini_tools = [
                genai_types.Tool(function_declarations=self._tools_to_functions(tools))
            ]

        try:
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model,
                contents=contents,
                config=genai_types.GenerateContentConfig(
                    temperature=0.3,
                    max_output_tokens=1024,
                    tools=gemini_tools,
                ),
            )

            return self._gemini_to_openai_response(response, messages)


        except google_exceptions.GoogleAPIError as exc:
            logger.exception("Gemini API error (chat_completion): code=%s, details=%s", exc.code, exc.message)
            return _DuckResponse(
                choices=[_DuckChoice(message=_DuckMessage(
                    content="I'm sorry, our AI assistant is temporarily unavailable. Please try again shortly.",
                    tool_calls=None,
                ))],
                usage=_DuckUsage(total_tokens=0, prompt_tokens=0, completion_tokens=0),
            )
        except Exception as exc:
            logger.exception("Unexpected error in ChatAIService.chat_completion: %s", exc)
            return _DuckResponse(
                choices=[_DuckChoice(message=_DuckMessage(
                    content="I'm sorry, something went wrong. Please try again.",
                    tool_calls=None,
                ))],
                usage=_DuckUsage(total_tokens=0, prompt_tokens=0, completion_tokens=0),
            )

    # ── Internal helpers ─────────────────────────────────────────────────

    def _messages_to_contents(self, messages: List[dict]) -> List[dict]:
        """Convert OpenAI-format messages to Gemini contents list."""
        contents: List[dict] = []
        system_parts: List[str] = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "") or ""

            if role == "system":
                system_parts.append(content)
                continue

            if role == "user":
                contents.append({"role": "user", "parts": [{"text": content}]})
            elif role == "assistant":
                # Check for tool_calls in assistant message
                tc = msg.get("tool_calls")
                if tc:
                    parts = []
                    if content:
                        parts.append({"text": content})
                    for call in tc:
                        parts.append({
                            "function_call": {
                                "name": call["function"]["name"],
                                "args": json.loads(call["function"]["arguments"]),
                            }
                        })
                    contents.append({"role": "model", "parts": parts})
                else:
                    contents.append({"role": "model", "parts": [{"text": content}]})
            elif role == "tool":
                # Tool response → Gemini function_response
                contents.append({
                    "role": "user",
                    "parts": [{
                        "function_response": {
                            "name": msg.get("tool_name", "unknown_tool"),
                            "response": {"result": content},
                        }
                    }],
                })

        # Prepend system prompt as first user message if present
        if system_parts:
            system_text = "\n\n".join(system_parts)
            contents.insert(0, {"role": "user", "parts": [{"text": system_text}]})

        return contents

    def _tools_to_functions(self, tools: List[dict]) -> List[dict]:
        """Convert OpenAI tool definitions to Gemini FunctionDeclaration dicts."""
        functions = []
        for tool in tools:
            func = tool.get("function", tool)
            params = func.get("parameters", {})
            functions.append({
                "name": func["name"],
                "description": func.get("description", ""),
                "parameters": {
                    "type": params.get("type", "OBJECT"),
                    "properties": params.get("properties", {}),
                    "required": params.get("required", []),
                },
            })
        return functions

    def _gemini_to_openai_response(self, gemini_response, original_messages: List[dict]):
        """Wrap a Gemini response in a duck-typed OpenAI-like object."""
        candidate = gemini_response.candidates[0] if gemini_response.candidates else None
        if not candidate:
            return _DuckResponse(
                choices=[_DuckChoice(message=_DuckMessage(content="", tool_calls=None))],
                usage=_DuckUsage(total_tokens=0, prompt_tokens=0, completion_tokens=0),
            )

        content = candidate.content
        text = ""
        tool_calls = []

        if content and content.parts:
            for part in content.parts:
                if hasattr(part, "text") and part.text:
                    text = part.text
                if hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    tool_calls.append(
                        _DuckToolCall(
                            id=fc.name,
                            function=_DuckFunction(name=fc.name, arguments=json.dumps(fc.args)),
                        )
                    )

        # Estimate token usage
        usage = _DuckUsage(
            total_tokens=0,
            prompt_tokens=0,
            completion_tokens=0,
        )
        if hasattr(gemini_response, "usage_metadata") and gemini_response.usage_metadata:
            um = gemini_response.usage_metadata
            usage = _DuckUsage(
                total_tokens=(um.prompt_token_count or 0) + (um.candidates_token_count or 0),
                prompt_tokens=um.prompt_token_count or 0,
                completion_tokens=um.candidates_token_count or 0,
            )

        return _DuckResponse(
            choices=[_DuckChoice(message=_DuckMessage(content=text, tool_calls=tool_calls or None))],
            usage=usage,
        )


# ── Duck-typed response objects (mimic OpenAI SDK shape) ─────────────────────

class _DuckFunction:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class _DuckToolCall:
    def __init__(self, id: str, function: _DuckFunction):
        self.id = id
        self.type = "function"
        self.function = function


class _DuckMessage:
    def __init__(self, content: Optional[str], tool_calls: Optional[List]):
        self.content = content or ""
        self.tool_calls = tool_calls


class _DuckChoice:
    def __init__(self, message: _DuckMessage):
        self.message = message
        self.finish_reason = "stop"


class _DuckUsage:
    def __init__(self, total_tokens: int, prompt_tokens: int, completion_tokens: int):
        self.total_tokens = total_tokens
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _DuckResponse:
    def __init__(self, choices: List[_DuckChoice], usage: _DuckUsage):
        self.choices = choices
        self.usage = usage
