"""Cloud Firestore integration for GovFix.

Provides dual/pluggable database support:
- Primary / Dual Cloud Firestore client for production and cloud deployments.
- Seamless SQLite fallback for offline development and local automated tests.
- Collection mappings for users, requests, audit, ai_suggestions, clock_state, and email_events.
- Bidirectional synchronization utilities.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from google.auth.credentials import AnonymousCredentials
from google.cloud import firestore
import firebase_admin
from firebase_admin import credentials as fa_credentials

logger = logging.getLogger("govfix.firestore")

# Firestore Collection Names
COLLECTION_USERS = "users"
COLLECTION_REQUESTS = "requests"
COLLECTION_AUDIT = "audit"
COLLECTION_AI_SUGGESTIONS = "ai_suggestions"
COLLECTION_CLOCK_STATE = "clock_state"
COLLECTION_EMAIL_EVENTS = "email_events"

_firestore_client: firestore.Client | None = None
_client_initialized: bool = False


def get_database_backend() -> str:
    """Return configured database backend: 'firestore' or 'sqlite'.
    
    Default is 'sqlite' unless explicitly set to 'firestore'.
    Automated test runs always use 'sqlite' to ensure deterministic tests without cloud dependencies.
    """
    if os.getenv("GOVFIX_TEST_AUTH_OVERRIDE") == "1" and os.getenv("DATABASE_BACKEND") != "firestore":
        return "sqlite"
    return os.getenv("DATABASE_BACKEND", "sqlite").lower()


def is_firestore_enabled() -> bool:
    """Check if Firestore should be used or synced with."""
    backend = get_database_backend()
    if backend == "firestore":
        return True
    if os.getenv("FIRESTORE_SYNC_ENABLED") == "1":
        return True
    # If explicit credentials or emulator host are configured, Firestore is available
    if os.getenv("FIRESTORE_EMULATOR_HOST") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv("FIREBASE_CREDENTIALS_PATH"):
        return True
    return False


def get_firestore_client() -> firestore.Client | None:
    """Initialize and return Google Cloud Firestore client.
    
    Reads credentials from:
    1. FIREBASE_CREDENTIALS_PATH or GOOGLE_APPLICATION_CREDENTIALS (Service Account JSON)
    2. FIRESTORE_EMULATOR_HOST (Local emulator via AnonymousCredentials)
    3. Firebase Admin default credentials / projectId
    """
    global _firestore_client, _client_initialized

    if _client_initialized:
        return _firestore_client

    project_id = os.getenv("FIREBASE_PROJECT_ID", "govfix-demo")
    cred_path = (
        os.getenv("FIREBASE_CREDENTIALS_PATH")
        or os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    )
    emulator_host = os.getenv("FIRESTORE_EMULATOR_HOST")

    try:
        if emulator_host:
            logger.info("Connecting to Firestore Emulator at %s (project: %s)", emulator_host, project_id)
            _firestore_client = firestore.Client(
                project=project_id,
                credentials=AnonymousCredentials(),
            )
        elif cred_path and os.path.exists(cred_path):
            logger.info("Initializing Firestore with service account credentials from %s", cred_path)
            _firestore_client = firestore.Client.from_service_account_json(
                cred_path,
                project=project_id,
            )
        else:
            # Fallback to Application Default Credentials or Anonymous Credentials for offline
            try:
                _firestore_client = firestore.Client(project=project_id)
            except Exception:
                # If cloud credentials missing, create client with anonymous credentials for local dev
                logger.info("Using local AnonymousCredentials for Firestore client (project: %s)", project_id)
                _firestore_client = firestore.Client(
                    project=project_id,
                    credentials=AnonymousCredentials(),
                )
    except Exception as exc:
        logger.warning("Could not initialize Cloud Firestore client: %s. Falling back to SQLite.", exc)
        _firestore_client = None

    _client_initialized = True
    return _firestore_client


# ==============================================================================
# Firestore Entity Operations
# ==============================================================================

def get_user_from_firestore(user_id_or_email: str) -> dict[str, Any] | None:
    """Retrieve user document from Firestore by user ID or email."""
    client = get_firestore_client()
    if not client:
        return None

    try:
        # Check by Document ID (user_id)
        doc = client.collection(COLLECTION_USERS).document(user_id_or_email).get()
        if doc.exists:
            data = doc.to_dict() or {}
            data["id"] = doc.id
            return data

        # Check by email field
        query = client.collection(COLLECTION_USERS).where("email", "==", user_id_or_email).limit(1).stream()
        for d in query:
            data = d.to_dict()
            data["id"] = d.id
            return data

        # Check by firebase_uid
        query_uid = client.collection(COLLECTION_USERS).where("firebase_uid", "==", user_id_or_email).limit(1).stream()
        for d in query_uid:
            data = d.to_dict()
            data["id"] = d.id
            return data
    except Exception as exc:
        logger.warning("Error fetching user from Firestore: %s", exc)

    return None


def save_user_to_firestore(user_data: dict[str, Any]) -> bool:
    """Upsert user document in Firestore."""
    client = get_firestore_client()
    if not client:
        return False

    user_id = user_data.get("id")
    if not user_id:
        return False

    try:
        doc_ref = client.collection(COLLECTION_USERS).document(user_id)
        # Clean data for Firestore
        clean_data = dict(user_data)
        doc_ref.set(clean_data, merge=True)
        return True
    except Exception as exc:
        logger.warning("Error writing user to Firestore: %s", exc)
        return False


def get_request_from_firestore(request_id: str) -> dict[str, Any] | None:
    """Retrieve complaint request from Firestore."""
    client = get_firestore_client()
    if not client:
        return None

    try:
        doc = client.collection(COLLECTION_REQUESTS).document(request_id).get()
        if doc.exists:
            data = doc.to_dict() or {}
            data["id"] = doc.id
            return data
    except Exception as exc:
        logger.warning("Error fetching request %s from Firestore: %s", request_id, exc)

    return None


def save_request_to_firestore(request_data: dict[str, Any]) -> bool:
    """Upsert complaint request in Firestore."""
    client = get_firestore_client()
    if not client:
        return False

    req_id = request_data.get("id")
    if not req_id:
        return False

    try:
        doc_ref = client.collection(COLLECTION_REQUESTS).document(req_id)
        doc_ref.set(dict(request_data), merge=True)
        return True
    except Exception as exc:
        logger.warning("Error saving request %s to Firestore: %s", req_id, exc)
        return False


def list_requests_from_firestore(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Query complaints from Firestore with optional filtering."""
    client = get_firestore_client()
    if not client:
        return []

    try:
        query = client.collection(COLLECTION_REQUESTS)
        filters = filters or {}
        if filters.get("citizen_id"):
            query = query.where("citizen_id", "==", filters["citizen_id"])
        if filters.get("assigned_to"):
            query = query.where("assigned_to", "==", filters["assigned_to"])
        if filters.get("state"):
            query = query.where("state", "==", filters["state"])
        if filters.get("category"):
            query = query.where("category", "==", filters["category"])

        results = []
        for doc in query.stream():
            data = doc.to_dict()
            data["id"] = doc.id
            results.append(data)
        return results
    except Exception as exc:
        logger.warning("Error querying requests from Firestore: %s", exc)
        return []


def save_audit_event_to_firestore(event_data: dict[str, Any]) -> bool:
    """Save immutable audit log block to Firestore."""
    client = get_firestore_client()
    if not client:
        return False

    audit_id = event_data.get("id")
    doc_id = f"block-{audit_id:06d}" if audit_id is not None else None

    try:
        coll = client.collection(COLLECTION_AUDIT)
        if doc_id:
            coll.document(doc_id).set(dict(event_data), merge=True)
        else:
            coll.add(dict(event_data))
        return True
    except Exception as exc:
        logger.warning("Error logging audit event to Firestore: %s", exc)
        return False


def save_ai_suggestion_to_firestore(suggestion_data: dict[str, Any]) -> bool:
    """Save AI copilot suggestion to Firestore."""
    client = get_firestore_client()
    if not client:
        return False

    sug_id = suggestion_data.get("id") or suggestion_data.get("request_id")
    try:
        coll = client.collection(COLLECTION_AI_SUGGESTIONS)
        if sug_id:
            coll.document(str(sug_id)).set(dict(suggestion_data), merge=True)
        else:
            coll.add(dict(suggestion_data))
        return True
    except Exception as exc:
        logger.warning("Error writing AI suggestion to Firestore: %s", exc)
        return False


def save_email_event_to_firestore(email_data: dict[str, Any]) -> bool:
    """Record outbound email notification event in Firestore."""
    client = get_firestore_client()
    if not client:
        return False

    event_name = email_data.get("event")
    try:
        coll = client.collection(COLLECTION_EMAIL_EVENTS)
        if event_name:
            coll.document(str(event_name)).set(dict(email_data), merge=True)
        else:
            coll.add(dict(email_data))
        return True
    except Exception as exc:
        logger.warning("Error recording email event in Firestore: %s", exc)
        return False


def save_clock_state_to_firestore(clock_data: dict[str, Any]) -> bool:
    """Record current simulated clock state in Firestore."""
    client = get_firestore_client()
    if not client:
        return False

    try:
        coll = client.collection(COLLECTION_CLOCK_STATE)
        coll.document("current").set(dict(clock_data), merge=True)
        return True
    except Exception as exc:
        logger.warning("Error saving clock state to Firestore: %s", exc)
        return False


def sync_sqlite_to_firestore(connection=None) -> dict[str, int]:
    """Sync all rows from local SQLite database into Cloud Firestore collections."""
    from src.database import connect

    conn = connection or connect()
    stats = {
        "users": 0,
        "requests": 0,
        "audit": 0,
        "ai_suggestions": 0,
        "email_events": 0,
        "clock_state": 0,
    }

    client = get_firestore_client()
    if not client:
        logger.warning("Firestore client unavailable. Cannot sync.")
        return stats

    try:
        # 1. Sync Users
        users = conn.execute("SELECT * FROM users").fetchall()
        for u in users:
            u_dict = dict(u)
            save_user_to_firestore(u_dict)
            stats["users"] += 1

        # 2. Sync Requests
        requests = conn.execute("SELECT * FROM requests").fetchall()
        for r in requests:
            r_dict = dict(r)
            save_request_to_firestore(r_dict)
            stats["requests"] += 1

        # 3. Sync Audit Log
        audit_events = conn.execute("SELECT * FROM audit ORDER BY id ASC").fetchall()
        for a in audit_events:
            a_dict = dict(a)
            save_audit_event_to_firestore(a_dict)
            stats["audit"] += 1

        # 4. Sync AI Suggestions
        ai_sugs = conn.execute("SELECT * FROM ai_suggestions").fetchall()
        for s in ai_sugs:
            s_dict = dict(s)
            save_ai_suggestion_to_firestore(s_dict)
            stats["ai_suggestions"] += 1

        # 5. Sync Email Events
        try:
            email_events = conn.execute("SELECT * FROM email_events").fetchall()
            for e in email_events:
                e_dict = dict(e)
                save_email_event_to_firestore(e_dict)
                stats["email_events"] += 1
        except Exception:
            pass

        # 6. Sync Clock State
        try:
            from src.services.clock_service import ClockService
            clock_svc = ClockService(connection=conn)
            clock_info = clock_svc.get_state()
            save_clock_state_to_firestore(clock_info)
            stats["clock_state"] += 1
        except Exception:
            pass

    except Exception as exc:
        logger.error("Sync to Firestore encountered error: %s", exc)
    finally:
        if not connection:
            conn.close()

    return stats
