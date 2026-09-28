"""AI Service — Gemini-powered document analysis and chat."""
import asyncio
import base64
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from google.api_core import exceptions as google_exceptions
from google.genai import types as genai_types
from json_repair import repair_json as json_repair

from app.services.gemini_client import get_gemini_client

logger = logging.getLogger(__name__)


# --- Tool-result decoding helper -------------------------------------------
# Gemini's function_response.response must be an OBJECT (not a JSON string).
# Decoding the stored JSON lets the model reason over the real data
# (offers / prices / product lists) instead of a quoted blob.
def _decode_tool_content(content: str) -> Dict[str, Any]:
    if not content:
        return {}
    try:
        payload = json.loads(content)
    except (ValueError, TypeError):
        return {"result": content}
    return payload if isinstance(payload, dict) else {"result": payload}


# --- Thought signatures -----------------------------------------------------
# Gemini 3 models (gemini-3.6-flash) attach an opaque, per-part
# `thought_signature` to the reasoning that produced a function call. Replaying
# that function call in the next turn WITHOUT the signature is rejected with
# 400 INVALID_ARGUMENT ("Function call is missing a thought_signature"), which
# is what turned every second turn into a canned apology. The signature is
# carried through our OpenAI-shaped history as base64 text (the SDK decodes a
# base64 string straight back into bytes).
def _encode_signature(signature: Any) -> Optional[str]:
    """bytes → base64 str, so the signature survives JSON/Mongo round-trips."""
    if not signature:
        return None
    if isinstance(signature, str):
        return signature
    try:
        return base64.b64encode(bytes(signature)).decode("ascii")
    except (TypeError, ValueError):
        return None


def _is_missing_signature_error(exc: BaseException) -> bool:
    """True when Gemini refused the replayed history for a missing signature."""
    return "thought_signature" in str(getattr(exc, "message", "") or str(exc)).lower()


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


def _repair_json(raw: str) -> Optional[str]:
    """Attempt to repair common JSON issues from LLM output using json-repair."""
    try:
        repaired = json_repair(raw)
        if repaired:
            return repaired
    except Exception:
        pass
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
        self.model = "gemini-3.6-flash"

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
                repaired = _repair_json(json_str)
                if repaired:
                    analysis = json.loads(repaired)
                    analysis["processed"] = True
                    analysis["processedAt"] = datetime.utcnow().isoformat()
                    return analysis
                return _empty_analysis("Drawing analysis failed. Please enter dimensions manually.")

            return _empty_analysis("Drawing analysis failed. Please enter dimensions manually.")

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
        self.model = "gemini-3.6-flash"

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
        # Convert OpenAI-style messages to Gemini contents. System messages are
        # NOT flattened into the contents — they are delivered separately as the
        # model's native system_instruction, so the model can tell instructions
        # apart from user input.
        contents = self._messages_to_contents(messages)
        system_instruction = self._system_instruction(messages)

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
                    temperature=0.2,
                    max_output_tokens=600,
                    tools=gemini_tools,
                    system_instruction=system_instruction,
                ),
            )

            return self._gemini_to_openai_response(response, messages)


        except google_exceptions.GoogleAPIError as exc:
            if _is_missing_signature_error(exc):
                # History written before signatures were captured replays a
                # function_call that carries none, and Gemini 3 refuses the whole
                # turn. Replay only the text turns so the shopper still gets a
                # real answer instead of the canned fallback.
                logger.warning(
                    "Gemini refused the replayed function-call history "
                    "(missing thought_signature); retrying without function turns"
                )
                try:
                    retry_response = await asyncio.to_thread(
                        self.client.models.generate_content,
                        model=self.model,
                        contents=self._drop_function_turns(contents),
                        config=genai_types.GenerateContentConfig(
                            temperature=0.2,
                            max_output_tokens=600,
                            tools=gemini_tools,
                            system_instruction=system_instruction,
                        ),
                    )
                    return self._gemini_to_openai_response(retry_response, messages)
                except google_exceptions.GoogleAPIError as retry_exc:
                    exc = retry_exc
            logger.exception("Gemini API error (chat_completion): code=%s, details=%s", exc.code, exc.message)
            # is_error tells the caller this is a failure, not model prose: it
            # retries once and then answers from whatever the tools returned.
            return _DuckResponse(
                choices=[_DuckChoice(message=_DuckMessage(
                    content="I'm sorry, our AI assistant is temporarily unavailable. Please try again shortly.",
                    tool_calls=None,
                ))],
                usage=_DuckUsage(total_tokens=0, prompt_tokens=0, completion_tokens=0),
                is_error=True,
            )
        except Exception as exc:
            logger.exception("Unexpected error in ChatAIService.chat_completion: %s", exc)
            return _DuckResponse(
                choices=[_DuckChoice(message=_DuckMessage(
                    content="I'm sorry, something went wrong. Please try again.",
                    tool_calls=None,
                ))],
                usage=_DuckUsage(total_tokens=0, prompt_tokens=0, completion_tokens=0),
                is_error=True,
            )

    # ── Internal helpers ─────────────────────────────────────────────────

    @staticmethod
    def _drop_function_turns(contents: List[dict]) -> List[dict]:
        """Text-only view of a contents list (last-resort history repair).

        Used when Gemini refuses a replayed function_call for lacking its thought
        signature: the function call and every function_response are dropped, so
        the model still sees what was said while the unusable tool turn — and the
        signature-less call it carries — is left out entirely.
        """
        trimmed: List[dict] = []
        for content in contents:
            parts = [
                part for part in content.get("parts", [])
                if "text" in part and part.get("text")
            ]
            if parts:
                trimmed.append({"role": content.get("role", "user"), "parts": parts})
        return trimmed

    def _messages_to_contents(self, messages: List[dict]) -> List[dict]:
        """Convert OpenAI-format messages to Gemini contents list."""
        contents: List[dict] = []
        pending_calls = set()

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "") or ""

            if role == "system":
                # Delivered separately as Gemini's system_instruction.
                continue

            if role == "user":
                if not content:
                    continue
                contents.append({"role": "user", "parts": [{"text": content}]})
            elif role == "assistant":
                # Check for tool_calls in assistant message
                tc = msg.get("tool_calls")
                if tc:
                    parts = []
                    if content:
                        parts.append({
                            "text": content,
                            "thought_signature": msg.get("text_signature"),
                        } if msg.get("text_signature") else {"text": content})
                    for call in tc:
                        part = {
                            "function_call": {
                                "name": call["function"]["name"],
                                "args": json.loads(call["function"]["arguments"]),
                            }
                        }
                        # Gemini 3 rejects the turn (400 INVALID_ARGUMENT) when a
                        # replayed function_call loses the signature it was issued
                        # with, so it is echoed back exactly as received.
                        signature = call.get("thought_signature")
                        if signature:
                            part["thought_signature"] = signature
                        parts.append(part)
                    if not parts:
                        continue
                    contents.append({"role": "model", "parts": parts})
                    pending_calls = {call["function"]["name"] for call in tc}
                else:
                    # Skip blank assistant turns (Gemini rejects empty text parts).
                    if not content:
                        continue
                    contents.append({"role": "model", "parts": [{"text": content}]})
                    pending_calls = set()
            elif role == "tool":
                # Tool response → Gemini function_response. A function_response must
                # follow a function_call with the same name; Gemini silently returns
                # empty text otherwise. Drop orphans left behind by older history.
                name = msg.get("tool_name") or msg.get("tool_call_id") or "unknown_tool"
                if name not in pending_calls:
                    logger.warning("Dropping orphan tool response from history: %s", name)
                    continue
                contents.append({
                    "role": "user",
                    "parts": [{
                        "function_response": {
                            "name": name,
                            "response": _decode_tool_content(content),
                        }
                    }],
                })

        return contents

    @staticmethod
    def _system_instruction(messages: List[dict]) -> Optional[str]:
        """Join OpenAI-style system messages into one Gemini system_instruction."""
        parts = [
            m.get("content", "")
            for m in messages
            if m.get("role") == "system" and m.get("content")
        ]
        return "\n\n".join(parts) if parts else None

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
            # An empty candidate is a transient model hiccup, not an answer.
            return _DuckResponse(
                choices=[_DuckChoice(message=_DuckMessage(content="", tool_calls=None))],
                usage=_DuckUsage(total_tokens=0, prompt_tokens=0, completion_tokens=0),
                is_error=True,
            )

        content = candidate.content
        text = ""
        tool_calls = []
        # Gemini 3 signs the part that produced each function call (and its
        # reasoning text). Both are handed back to the caller so the next turn can
        # replay them verbatim — see _encode_signature.
        text_signature = None

        if content and content.parts:
            for part in content.parts:
                if hasattr(part, "text") and part.text:
                    text = part.text
                    text_signature = _encode_signature(getattr(part, "thought_signature", None))
                if hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    tool_calls.append(
                        _DuckToolCall(
                            id=fc.name,
                            function=_DuckFunction(name=fc.name, arguments=json.dumps(fc.args)),
                            thought_signature=_encode_signature(
                                getattr(part, "thought_signature", None)
                            ),
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
            choices=[_DuckChoice(message=_DuckMessage(
                content=text,
                tool_calls=tool_calls or None,
                text_signature=text_signature,
            ))],
            usage=usage,
        )


# ── Duck-typed response objects (mimic OpenAI SDK shape) ─────────────────────

class _DuckFunction:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class _DuckToolCall:
    def __init__(
        self,
        id: str,
        function: _DuckFunction,
        thought_signature: Optional[str] = None,
    ):
        self.id = id
        self.type = "function"
        self.function = function
        # Base64 Gemini 3 thought signature this call was issued with; replayed
        # with the call in the next turn (None for non-thinking models).
        self.thought_signature = thought_signature


class _DuckMessage:
    def __init__(
        self,
        content: Optional[str],
        tool_calls: Optional[List],
        text_signature: Optional[str] = None,
    ):
        self.content = content or ""
        self.tool_calls = tool_calls
        # Signature carried by the reasoning text part, when the model sent one.
        self.text_signature = text_signature


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
    def __init__(self, choices: List[_DuckChoice], usage: _DuckUsage, is_error: bool = False):
        self.choices = choices
        self.usage = usage
        # True only when the call failed (API/network/empty candidate) rather
        # than the model producing text. Callers use it to retry once and to
        # fall back to a tool-backed answer instead of the canned apology.
        self.is_error = is_error
