"""Google Gemini LLM Engine for GovFix with Automatic Offline Fallback.

If GEMINI_API_KEY is configured and valid, this engine queries Gemini
for zero-shot classification, entity extraction, and contextual reply drafting.
If the API key is missing or any API/network failure occurs, it seamlessly
falls back to the offline LocalClassifier without throwing unhandled exceptions.

STRICT INVARIANT:
The output is purely advisory and written only to ai_suggestions table.
"""

import json
import logging
import os
from typing import Optional, Dict, Any

from .interface import AIEngine, SuggestionPayload
from .local_classifier import LocalClassifier

logger = logging.getLogger(__name__)

GEMINI_SYSTEM_INSTRUCTION = """You are a Municipal Grievance Advisory Assistant for a City Corporation.
Analyze the citizen's complaint and output a valid JSON object matching this schema:
{
  "category": "Water Leak" | "Garbage Collection" | "Streetlight" | "Pothole",
  "confidence": float between 0.0 and 1.0,
  "location": string or null (extracted street, ward, sector, or landmark),
  "urgency": "HIGH" | "MEDIUM" | "LOW",
  "draft_reply": string (polite, empathetic municipal acknowledgment mentioning the issue and standard procedure)
}
Guidelines:
- Choose ONLY from the 4 allowed categories.
- HIGH urgency is for active flooding, electrical hazards, deep road craters, hospital vicinities, or immediate danger.
- Keep the draft reply professional, empathetic, and under 3 sentences.
Return ONLY raw JSON. No markdown code fences.
"""


class GeminiEngine(AIEngine):
    """Gemini LLM engine with automatic graceful fallback."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gemini-1.5-flash",
        fallback_engine: Optional[AIEngine] = None
    ):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model_name = model_name
        self.fallback_engine = fallback_engine or LocalClassifier()
        self._client = None

        if self.api_key and self.api_key.strip() and not self.api_key.startswith("mock_"):
            try:
                from google import genai
                self._client = genai.Client(api_key=self.api_key.strip())
            except Exception as e:
                logger.warning("Failed to initialize Google GenAI client: %s. Using local fallback.", e)
                self._client = None

    def suggest(self, complaint_text: str) -> SuggestionPayload:
        """Query Gemini API if available, else gracefully fall back to local model."""
        if not self._client:
            return self.fallback_engine.suggest(complaint_text)

        try:
            from google.genai import types

            response = self._client.models.generate_content(
                model=self.model_name,
                contents=complaint_text,
                config=types.GenerateContentConfig(
                    system_instruction=GEMINI_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )

            response_text = response.text or "{}"
            data: Dict[str, Any] = json.loads(response_text)

            category = data.get("category", "Uncategorised")
            if category not in {"Water Leak", "Garbage Collection", "Streetlight", "Pothole"}:
                category = "Uncategorised"

            confidence = float(data.get("confidence", 0.90))
            confidence = max(0.0, min(1.0, confidence))

            urgency = data.get("urgency", "MEDIUM")
            if urgency not in {"HIGH", "MEDIUM", "LOW"}:
                urgency = "MEDIUM"

            location = data.get("location")
            if location and isinstance(location, str):
                location = location.strip()
            else:
                location = None

            draft_reply = data.get("draft_reply") or self.fallback_engine.draft_reply(category, location, urgency)  # type: ignore

            return SuggestionPayload(
                category=category,
                confidence=confidence,
                location=location,
                urgency=urgency,  # type: ignore
                draft_reply=draft_reply,
                source="gemini_llm",
                raw_details={
                    "model": self.model_name,
                    "extracted_location": location,
                    "urgency_assessed": urgency
                }
            )

        except Exception as e:
            logger.warning(
                "Gemini API invocation failed: %s. Seamlessly switching to local offline fallback.",
                e
            )
            return self.fallback_engine.suggest(complaint_text)
