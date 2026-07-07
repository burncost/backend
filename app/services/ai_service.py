
"""AI Service - Real AI analysis using Gemini API for document processing and Qwen chat."""
from typing import Dict, Any, Optional, List
import logging
import json
import os

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)


class AIService:
    """Service for AI-powered document analysis using Gemini API."""

    def __init__(self):
        self.api_key = os.getenv("AI_SERVICE_API_KEY", "")
        self.api_url = os.getenv("AI_SERVICE_URL", "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent")
        self.model = os.getenv("AI_MODEL", "gemini-2.0-flash")

    async def analyze_document(
        self,
        file_content: bytes,
        file_type: str,
        extracted_metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze a construction document using AI to extract building elements."""
        logger.info(f"AI analyzing {file_type} document ({len(file_content)} bytes)")

        if not self.api_key:
            logger.warning("No AI API key configured, returning empty analysis")
            return self._empty_analysis("AI service not configured")

        try:
            import httpx
            prompt = self._build_analysis_prompt(file_type, extracted_metadata)

            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self.api_url}?key={self.api_key}",
                    json={
                        "contents": [{
                            "parts": [{"text": prompt}]
                        }],
                        "generationConfig": {
                            "temperature": 0.1,
                            "maxOutputTokens": 4096,
                        }
                    },
                    headers={"Content-Type": "application/json"}
                )

                if response.status_code != 200:
                    logger.error(f"AI API error: {response.status_code} {response.text}")
                    return self._empty_analysis(f"AI API error: {response.status_code}")

                result = response.json()
                text = result.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")

                # Extract JSON from response
                json_str = self._extract_json(text)
                if json_str:
                    analysis = json.loads(json_str)
                    analysis["processed"] = True
                    analysis["processedAt"] = __import__("datetime").datetime.utcnow().isoformat()
                    return analysis

                return self._empty_analysis("Failed to parse AI response")

        except ImportError:
            logger.warning("httpx not installed, returning empty analysis")
            return self._empty_analysis("httpx library not available")
        except Exception as e:
            logger.error(f"AI analysis failed: {str(e)}")
            return self._empty_analysis(str(e))

    def _build_analysis_prompt(self, file_type: str, metadata: Dict[str, Any]) -> str:
        """Build the analysis prompt for Gemini."""
        return f"""You are a construction quantity surveyor AI. Analyze this {file_type} document metadata and extract building elements.

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

    def _extract_json(self, text: str) -> Optional[str]:
        """Extract JSON from AI response text."""
        import re
        # Try to find JSON block
        match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        if match:
            return match.group(1)
        # Try to find { ... } directly
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            return match.group(0)
        return None

    def _empty_analysis(self, error: str) -> Dict[str, Any]:
        """Return empty analysis with error info."""
        return {
            "processed": False,
            "processedAt": __import__("datetime").datetime.utcnow().isoformat(),
            "detectedElements": [],
            "rooms": [],
            "detectedMaterials": [],
            "processingErrors": [error]
        }


class ChatAIService:
    """Conversational AI agent using Qwen via OpenAI-compatible API."""

    def __init__(self):
        self.api_key = settings.AI_SERVICE_API_KEY
        self.base_url = settings.AI_BASE_URL
        self.model = settings.AI_MODEL

        if not self.api_key:
            logger.warning("ChatAIService: No API key configured")

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )

    def chat_completion(
        self,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
    ):
        """Send a chat completion request with optional function calling."""
        kwargs = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 150,
            "extra_body": {"enable_thinking": True},
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        logger.debug(f"ChatAIService: sending {len(messages)} messages to {self.model}")
        response = self.client.chat.completions.create(**kwargs)
        return response
