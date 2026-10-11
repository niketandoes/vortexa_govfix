"""Automated Architectural Boundary and Functionality Tests for Plan 04 (AI Advisory Layer).

Verifies:
1. AST isolation: src/ai contains ZERO imports of workflow engine or database mutations.
2. Runtime capability isolation: AI engines receive pure text and cannot mutate requests/audit.
3. Offline classification accuracy on municipal grievance test cases.
4. Graceful silent fallback to local classifier when Gemini API key is missing or fails.
"""

import ast
import os
import sqlite3
import tempfile
from pathlib import Path
import pytest

from src.ai.interface import AIEngine, SuggestionPayload
from src.ai.local_classifier import LocalClassifier
from src.ai.gemini_engine import GeminiEngine
from src.services.suggestion_service import SuggestionService, SQLiteSuggestionRepository


AI_DIR = Path(__file__).parent.parent / "src" / "ai"


def test_ast_architectural_boundary():
    """Verify that no file in src/ai imports workflow service or database mutation logic."""
    prohibited_modules = {
        "workflow",
        "src.workflow",
        "requests_service",
        "audit_service",
        "src.services.workflow_service",
        "src.services.audit_service"
    }

    ai_python_files = list(AI_DIR.glob("*.py"))
    assert len(ai_python_files) > 0, "No python files found in src/ai to test"

    for py_file in ai_python_files:
        with open(py_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for prohibited in prohibited_modules:
                        assert not alias.name.startswith(prohibited), (
                            f"BOUNDARY VIOLATION: {py_file.name} imports prohibited module '{alias.name}'"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for prohibited in prohibited_modules:
                        assert not node.module.startswith(prohibited), (
                            f"BOUNDARY VIOLATION: {py_file.name} imports from prohibited module '{node.module}'"
                        )


def test_runtime_capability_isolation():
    """Verify AI engines accept only strings and cannot touch requests/audit tables."""
    # Create an engine
    engine = LocalClassifier()

    # Engine accepts string text
    complaint = "Gushing water leak on MG Road near City Hospital"
    payload = engine.suggest(complaint)

    assert isinstance(payload, SuggestionPayload)
    assert payload.category == "Water Leak"
    assert payload.source == "local_fallback"
    assert payload.location is not None

    # Verify that in a clean test database, SuggestionService only writes to ai_suggestions
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()

    try:
        conn = sqlite3.connect(db_path)
        # Create requests, audit, and ai_suggestions tables
        conn.execute("""
            CREATE TABLE requests (id TEXT PRIMARY KEY, state TEXT);
        """)
        conn.execute("""
            CREATE TABLE audit (id INTEGER PRIMARY KEY, action TEXT);
        """)
        conn.execute("""
            CREATE TABLE ai_suggestions (
                id TEXT PRIMARY KEY, request_id TEXT, kind TEXT,
                payload TEXT, confidence REAL, source TEXT, created_at TIMESTAMP
            );
        """)
        conn.commit()
        conn.close()

        repo = SQLiteSuggestionRepository(db_path=db_path)
        service = SuggestionService(engine=engine, repository=repo)

        service.generate_and_store_suggestions(
            request_id="REQ-TEST-001",
            complaint_text=complaint
        )

        # Assert ai_suggestions has records
        stored = service.get_suggestions_for_request("REQ-TEST-001")
        assert len(stored) == 3
        kinds = {s["kind"] for s in stored}
        assert kinds == {"categorisation", "extraction", "reply_draft"}

        # Assert requests and audit tables remained untouched
        conn = sqlite3.connect(db_path)
        req_count = conn.execute("SELECT count(*) FROM requests").fetchone()[0]
        audit_count = conn.execute("SELECT count(*) FROM audit").fetchone()[0]
        conn.close()

        assert req_count == 0, "AI service must NEVER write to requests table"
        assert audit_count == 0, "AI service must NEVER write to audit table"

    finally:
        try:
            if os.path.exists(db_path):
                os.remove(db_path)
        except OSError:
            pass



def test_local_classifier_categories():
    """Test classification on unseen test phrases across all 4 categories."""
    clf = LocalClassifier()

    test_cases = [
        ("Massive pipeline burst on Ring Road near Metro station", "Water Leak"),
        ("Drinking water leaking from main municipal pipe", "Water Leak"),
        ("Garbage bin overflowing near market with terrible smell", "Garbage Collection"),
        ("Sanitation truck skipped waste collection for 3 days", "Garbage Collection"),
        ("Streetlight not working on 5th Cross, area is completely dark", "Streetlight"),
        ("Exposed electrical wires at streetlight pole base", "Streetlight"),
        ("Dangerous crater and pothole on main road causing accidents", "Pothole"),
        ("Asphalt eroded and road caved in outside school", "Pothole")
    ]

    correct = 0
    for text, expected in test_cases:
        cat, conf = clf.predict_category(text)
        if cat == expected:
            correct += 1

    accuracy = correct / len(test_cases)
    assert accuracy >= 0.85, f"Expected classification accuracy >= 85%, got {accuracy * 100}%"


def test_gemini_silent_fallback_when_key_missing():
    """Verify GeminiEngine falls back silently when API key is missing or blank."""
    engine = GeminiEngine(api_key="")
    res = engine.suggest("Streetlight bulb burned out near Patel Nagar")

    assert isinstance(res, SuggestionPayload)
    assert res.source == "local_fallback"
    assert res.category == "Streetlight"


def test_gemini_silent_fallback_when_api_fails():
    """Verify GeminiEngine falls back silently when an invalid key causes an API failure."""
    engine = GeminiEngine(api_key="mock_invalid_api_key_that_fails_immediately")
    res = engine.suggest("Broken water pipe leaking near Park")

    assert isinstance(res, SuggestionPayload)
    assert res.source == "local_fallback"
    assert res.category == "Water Leak"
