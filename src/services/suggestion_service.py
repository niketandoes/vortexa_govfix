"""Suggestion Service & Narrow Suggestion Repository for GovFix.

ARCHITECTURAL FIREWALL ENFORCEMENT:
- Owns the isolated `ai_suggestions` data access.
- Invokes AI engines with pure complaint text.
- AI engines NEVER receive the database connection or request repository.
- Stores advisory outputs as inert suggestions for human officer review.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Protocol

from src.ai.interface import AIEngine, SuggestionPayload
from src.ai.gemini_engine import GeminiEngine

from src.database import connect, DEFAULT_DATABASE_PATH


class SuggestionRepository(Protocol):
    """Narrow capability interface: Can ONLY write and read ai_suggestions."""

    def insert(
        self,
        suggestion_id: str,
        request_id: str,
        kind: str,
        payload_json: str,
        confidence: float,
        source: str,
        created_at: str
    ) -> None:
        ...

    def get_by_request_id(self, request_id: str) -> List[Dict[str, Any]]:
        ...


class SQLiteSuggestionRepository:
    """Default SQLite implementation of the narrow suggestion repository."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        *,
        connection: Optional[sqlite3.Connection] = None
    ):
        self.db_path = db_path if db_path is not None else str(DEFAULT_DATABASE_PATH)
        self._shared_connection = connection
        self._ensure_table()

    def _get_connection(self) -> sqlite3.Connection:
        if self._shared_connection is not None:
            return self._shared_connection
        return connect(self.db_path)

    def _ensure_table(self) -> None:
        """Create ai_suggestions table if it does not exist yet."""
        conn = self._get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_suggestions (
                    id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('categorisation', 'extraction', 'reply_draft', 'duplicate_detection')),
                    payload TEXT NOT NULL,
                    confidence REAL,
                    source TEXT NOT NULL CHECK(source IN ('gemini_llm', 'local_fallback')),
                    created_at TIMESTAMP NOT NULL
                );
            """)
            conn.commit()
        finally:
            if self._shared_connection is None:
                conn.close()


    def insert(
        self,
        suggestion_id: str,
        request_id: str,
        kind: str,
        payload_json: str,
        confidence: float,
        source: str,
        created_at: str
    ) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO ai_suggestions (id, request_id, kind, payload, confidence, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (suggestion_id, request_id, kind, payload_json, confidence, source, created_at)
            )
            conn.commit()

    def get_by_request_id(self, request_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, request_id, kind, payload, confidence, source, created_at
                FROM ai_suggestions
                WHERE request_id = ?
                ORDER BY created_at ASC
                """,
                (request_id,)
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]


class SuggestionService:
    """Coordinates advisory suggestion generation and isolated storage."""

    def __init__(
        self,
        engine: Optional[AIEngine] = None,
        repository: Optional[SuggestionRepository] = None
    ):
        self.engine = engine or GeminiEngine()
        self.repository = repository or SQLiteSuggestionRepository()

    def generate_and_store_suggestions(
        self,
        request_id: str,
        complaint_text: str,
        created_at: Optional[str] = None
    ) -> SuggestionPayload:
        """Process complaint text and store advisory suggestions in isolated table.

        Crucially, the engine is passed ONLY the raw text string.
        """
        timestamp = created_at or datetime.now(timezone.utc).isoformat()
        suggestion = self.engine.suggest(complaint_text)

        # 1. Store categorisation suggestion
        self.repository.insert(
            suggestion_id=f"SUG-CAT-{uuid.uuid4().hex[:8].upper()}",
            request_id=request_id,
            kind="categorisation",
            payload_json=json.dumps({
                "suggested_category": suggestion.category,
                "confidence": suggestion.confidence
            }),
            confidence=suggestion.confidence,
            source=suggestion.source,
            created_at=timestamp
        )

        # 2. Store extraction suggestion
        self.repository.insert(
            suggestion_id=f"SUG-EXT-{uuid.uuid4().hex[:8].upper()}",
            request_id=request_id,
            kind="extraction",
            payload_json=json.dumps({
                "location": suggestion.location,
                "urgency": suggestion.urgency
            }),
            confidence=suggestion.confidence,
            source=suggestion.source,
            created_at=timestamp
        )

        # 3. Store draft reply suggestion
        self.repository.insert(
            suggestion_id=f"SUG-REP-{uuid.uuid4().hex[:8].upper()}",
            request_id=request_id,
            kind="reply_draft",
            payload_json=json.dumps({
                "draft_reply": suggestion.draft_reply
            }),
            confidence=suggestion.confidence,
            source=suggestion.source,
            created_at=timestamp
        )

        return suggestion

    def get_suggestions_for_request(self, request_id: str) -> List[Dict[str, Any]]:
        """Fetch all advisory records for a grievance."""
        records = self.repository.get_by_request_id(request_id)
        for r in records:
            try:
                r["parsed_payload"] = json.loads(r["payload"])
            except Exception:
                r["parsed_payload"] = {}
        return records
