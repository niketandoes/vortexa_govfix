"""GovFix AI Advisory Package.

Exposes the AI Engine interface and implementations.
Architecturally isolated from the workflow engine.
"""

from .interface import AIEngine, SuggestionPayload

__all__ = ["AIEngine", "SuggestionPayload"]
