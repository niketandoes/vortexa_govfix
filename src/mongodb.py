"""MongoDB Atlas integration for GovFix.

Provides MongoDB persistence, collection schemas, indexing, and migration utilities:
- Primary MongoDB Atlas client with connection pooling and DNS SRV support.
- Configurable via MONGODB_URI or credentials (user: niketoke101_db_user, app: gov-fix).
- Collections: users, requests, audit, ai_suggestions, clock_state, email_events.
- Migration and data-purging utilities (remove old data).
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from typing import Any

import pymongo
from pymongo import MongoClient
from pymongo.database import Database

logger = logging.getLogger("govfix.mongodb")

# Default MongoDB configuration
DEFAULT_DB_NAME = "gov-fix"
COLLECTION_USERS = "users"
COLLECTION_REQUESTS = "requests"
COLLECTION_AUDIT = "audit"
COLLECTION_AI_SUGGESTIONS = "ai_suggestions"
COLLECTION_CLOCK_STATE = "clock_state"
COLLECTION_EMAIL_EVENTS = "email_events"

_mongo_client: MongoClient | None = None
_client_initialized: bool = False


def get_mongodb_uri() -> str | None:
    """Resolve MongoDB connection string from environment variables or credentials."""
    uri = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
    if uri:
        return uri.strip()

    # Build from components if provided
    user = os.getenv("MONGO_USER", "niketoke101_db_user")
    password = os.getenv("MONGO_PASSWORD", "DuOjSO09GUNCb6G1")
    cluster = os.getenv("MONGO_CLUSTER")  # e.g. "cluster0.xxxxx.mongodb.net" or "gov-fix.xxxxx.mongodb.net"

    if cluster:
        encoded_user = urllib.parse.quote_plus(user)
        encoded_pass = urllib.parse.quote_plus(password)
        db_name = os.getenv("MONGO_DB_NAME", DEFAULT_DB_NAME)
        return f"mongodb+srv://{encoded_user}:{encoded_pass}@{cluster}/{db_name}?retryWrites=true&w=majority&appName=gov-fix"

    return None


def get_database_name() -> str:
    """Get the target MongoDB database name."""
    return os.getenv("MONGO_DB_NAME", DEFAULT_DB_NAME)


def is_mongodb_enabled() -> bool:
    """Check if MongoDB backend is configured and enabled."""
    backend = os.getenv("DATABASE_BACKEND", "").lower()
    if backend == "mongodb" or backend == "mongo":
        return True
    return bool(get_mongodb_uri())


def get_mongodb_client() -> MongoClient | None:
    """Initialize and return singleton MongoClient."""
    global _mongo_client, _client_initialized

    if _client_initialized and _mongo_client is not None:
        return _mongo_client

    uri = get_mongodb_uri()
    if not uri:
        logger.debug("No MONGODB_URI configured.")
        _client_initialized = True
        return None

    try:
        logger.info("Connecting to MongoDB Atlas...")
        _mongo_client = MongoClient(
            uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            retryWrites=True,
        )
        # Verify connection
        _mongo_client.admin.command("ping")
        logger.info("Successfully connected to MongoDB Atlas.")
    except Exception as exc:
        logger.warning("Could not connect to MongoDB Atlas: %s", exc)
        _mongo_client = None

    _client_initialized = True
    return _mongo_client


def get_mongodb_database() -> Database | None:
    """Return the GovFix MongoDB database instance."""
    client = get_mongodb_client()
    if not client:
        return None
    db_name = get_database_name()
    return client[db_name]


def init_mongodb_indexes(db: Database | None = None) -> bool:
    """Ensure required unique and query indexes exist across collections."""
    if db is None:
        db = get_mongodb_database()
    if db is None:
        return False

    try:
        # 1. users: unique on id, unique on email, unique sparse on firebase_uid
        users = db[COLLECTION_USERS]
        users.create_index("id", unique=True)
        users.create_index("email", unique=True)
        users.create_index("firebase_uid", unique=True, sparse=True)
        users.create_index("role")

        # 2. requests: unique on id, index on citizen_id, state, assigned_to
        requests = db[COLLECTION_REQUESTS]
        requests.create_index("id", unique=True)
        requests.create_index("citizen_id")
        requests.create_index("state")
        requests.create_index("assigned_to")
        requests.create_index("deadline")
        requests.create_index("escalation_level")

        # 3. audit: unique monotonic id, index on request_id, actor, unique sparse dedupe_key
        audit = db[COLLECTION_AUDIT]
        audit.create_index("id", unique=True)
        audit.create_index("request_id")
        audit.create_index("actor")
        audit.create_index("dedupe_key", unique=True, sparse=True)
        audit.create_index("hash")

        # 4. ai_suggestions: index on request_id
        ai_sug = db[COLLECTION_AI_SUGGESTIONS]
        ai_sug.create_index("request_id")

        # 5. clock_state: unique on id
        clock = db[COLLECTION_CLOCK_STATE]
        clock.create_index("id", unique=True)

        # 6. email_events: unique sparse on event_id, index on recipient
        emails = db[COLLECTION_EMAIL_EVENTS]
        emails.create_index("event_id", unique=True, sparse=True)
        emails.create_index("recipient")

        logger.info("MongoDB indexes verified successfully.")
        return True
    except Exception as exc:
        logger.error("Failed to create MongoDB indexes: %s", exc)
        return False


def purge_mongodb_data(db: Database | None = None) -> dict[str, int]:
    """Remove old data from MongoDB collections (per 'remove old data' directive)."""
    if db is None:
        db = get_mongodb_database()
    if db is None:
        return {}

    counts = {}
    collections = [
        COLLECTION_USERS,
        COLLECTION_REQUESTS,
        COLLECTION_AUDIT,
        COLLECTION_AI_SUGGESTIONS,
        COLLECTION_CLOCK_STATE,
        COLLECTION_EMAIL_EVENTS,
    ]
    for coll_name in collections:
        try:
            cnt = db[coll_name].count_documents({})
            db[coll_name].drop()
            counts[coll_name] = cnt
        except Exception as exc:
            logger.warning("Error purging collection %s: %s", coll_name, exc)
            counts[coll_name] = 0

    logger.info("Purged old data from MongoDB: %s", counts)
    return counts


# ==============================================================================
# Document CRUD Operations
# ==============================================================================

def save_user_to_mongodb(user_data: dict[str, Any], db: Database | None = None) -> bool:
    """Save or update user in MongoDB."""
    if db is None:
        db = get_mongodb_database()
    if db is None or not user_data.get("id"):
        return False

    doc = dict(user_data)
    doc["_id"] = doc["id"]
    if doc.get("firebase_uid") is None:
        doc.pop("firebase_uid", None)
    try:
        db[COLLECTION_USERS].replace_one({"_id": doc["_id"]}, doc, upsert=True)
        return True
    except Exception as exc:
        logger.error("Error saving user %s to MongoDB: %s", doc["_id"], exc)
        return False


def get_user_from_mongodb(identifier: str, db: Database | None = None) -> dict[str, Any] | None:
    """Find user by ID, email, or firebase_uid."""
    if db is None:
        db = get_mongodb_database()
    if db is None:
        return None

    try:
        return db[COLLECTION_USERS].find_one({
            "$or": [
                {"_id": identifier},
                {"id": identifier},
                {"email": identifier},
                {"firebase_uid": identifier},
            ]
        })
    except Exception as exc:
        logger.warning("Error fetching user from MongoDB: %s", exc)
        return None


def save_request_to_mongodb(request_data: dict[str, Any], db: Database | None = None) -> bool:
    """Save or update complaint request in MongoDB."""
    if db is None:
        db = get_mongodb_database()
    if db is None or not request_data.get("id"):
        return False

    doc = dict(request_data)
    doc["_id"] = doc["id"]
    try:
        db[COLLECTION_REQUESTS].replace_one({"_id": doc["_id"]}, doc, upsert=True)
        return True
    except Exception as exc:
        logger.error("Error saving request %s to MongoDB: %s", doc["_id"], exc)
        return False


def get_request_from_mongodb(request_id: str, db: Database | None = None) -> dict[str, Any] | None:
    """Find request by ID."""
    if db is None:
        db = get_mongodb_database()
    if db is None:
        return None

    try:
        return db[COLLECTION_REQUESTS].find_one({"_id": request_id})
    except Exception as exc:
        logger.warning("Error fetching request %s from MongoDB: %s", request_id, exc)
        return None


def save_audit_event_to_mongodb(audit_data: dict[str, Any], db: Database | None = None) -> bool:
    """Append immutable audit log entry to MongoDB."""
    if db is None:
        db = get_mongodb_database()
    if db is None:
        return False

    doc = dict(audit_data)
    if "id" in doc and doc["id"] is not None:
        doc["_id"] = doc["id"]
    if doc.get("dedupe_key") is None:
        doc.pop("dedupe_key", None)

    try:
        db[COLLECTION_AUDIT].insert_one(doc)
        return True
    except Exception as exc:
        logger.error("Error writing audit entry to MongoDB: %s", exc)
        return False


def save_ai_suggestion_to_mongodb(suggestion_data: dict[str, Any], db: Database | None = None) -> bool:
    """Save AI advisory suggestion to MongoDB."""
    if db is None:
        db = get_mongodb_database()
    if db is None:
        return False

    doc = dict(suggestion_data)
    sug_id = doc.get("id")
    if sug_id:
        doc["_id"] = sug_id
    try:
        if sug_id:
            db[COLLECTION_AI_SUGGESTIONS].replace_one({"_id": sug_id}, doc, upsert=True)
        else:
            db[COLLECTION_AI_SUGGESTIONS].insert_one(doc)
        return True
    except Exception as exc:
        logger.error("Error saving AI suggestion to MongoDB: %s", exc)
        return False


def save_clock_state_to_mongodb(clock_data: dict[str, Any], db: Database | None = None) -> bool:
    """Save singleton clock state to MongoDB."""
    if db is None:
        db = get_mongodb_database()
    if db is None:
        return False

    doc = dict(clock_data)
    doc["_id"] = 1
    doc["id"] = 1
    try:
        db[COLLECTION_CLOCK_STATE].replace_one({"_id": 1}, doc, upsert=True)
        return True
    except Exception as exc:
        logger.error("Error saving clock state to MongoDB: %s", exc)
        return False


def save_email_event_to_mongodb(email_data: dict[str, Any], db: Database | None = None) -> bool:
    """Record email dispatch event in MongoDB."""
    if db is None:
        db = get_mongodb_database()
    if db is None:
        return False

    doc = dict(email_data)
    event_id = doc.get("event_id")
    try:
        if event_id:
            db[COLLECTION_EMAIL_EVENTS].replace_one({"event_id": event_id}, doc, upsert=True)
        else:
            db[COLLECTION_EMAIL_EVENTS].insert_one(doc)
        return True
    except Exception as exc:
        logger.error("Error saving email event to MongoDB: %s", exc)
        return False
