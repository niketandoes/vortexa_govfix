"""AI Engine Interface and Shared Data Models for GovFix.

This module defines the contract that all AI engines (Gemini LLM and offline
local classifier fallback) must implement.

CRITICAL ARCHITECTURAL BOUNDARY:
- AI engines only accept pure text inputs and return SuggestionPayload.
- AI engines must NEVER import workflow service or database mutation logic.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel, Field


class SuggestionPayload(BaseModel):
    """Normalized payload output by any AI engine (Gemini or Local Fallback)."""
    category: str = Field(description="Predicted grievance category")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0")
    location: Optional[str] = Field(default=None, description="Extracted physical location or landmark")
    urgency: Literal["HIGH", "MEDIUM", "LOW"] = Field(default="MEDIUM", description="Assessed urgency level")
    draft_reply: str = Field(description="Polite draft reply for municipal officer review")
    source: Literal["gemini_llm", "local_fallback"] = Field(description="Origin of the suggestion")
    raw_details: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata or entity cues")


class AIEngine(ABC):
    """Abstract Base Class for GovFix advisory AI engines."""

    @abstractmethod
    def suggest(self, complaint_text: str) -> SuggestionPayload:
        """Analyze grievance text and return structured advisory suggestions.

        Must never raise unhandled exceptions; if external APIs fail, it must
        return an offline local fallback suggestion.
        """
        pass
