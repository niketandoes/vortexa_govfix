"""Seed and synchronize Cloud Firestore collections with GovFix demo data.

Usage:
    python scripts/seed_firestore.py

Environment variables supported:
    FIREBASE_PROJECT_ID: Target Firebase project ID (default: govfix-demo)
    FIRESTORE_EMULATOR_HOST: If running with Firebase local emulator (e.g. 127.0.0.1:8080)
    GOOGLE_APPLICATION_CREDENTIALS / FIREBASE_CREDENTIALS_PATH: Path to service account JSON
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.database import connect, initialize_database
from src.seed import seed_database
from src.firestore_db import (
    get_firestore_client,
    COLLECTION_USERS,
    COLLECTION_REQUESTS,
    COLLECTION_AUDIT,
    COLLECTION_AI_SUGGESTIONS,
    COLLECTION_CLOCK_STATE,
    COLLECTION_EMAIL_EVENTS,
    save_user_to_firestore,
    save_request_to_firestore,
    save_audit_event_to_firestore,
    save_ai_suggestion_to_firestore,
    save_email_event_to_firestore,
    sync_sqlite_to_firestore,
)


def seed_firestore_direct() -> dict[str, int]:
    """Seed Firestore collections directly from SQLite database."""
    print("=" * 60)
    print("GovFix Cloud Firestore Seeding & Synchronization")
    print("=" * 60)

    # 1. Initialize SQLite connection and ensure seed data exists
    conn = connect()
    try:
        initialize_database(conn)
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if user_count == 0:
            print("[INFO] Seeding baseline SQLite database first...")
            seed_database(conn, include_extended=True)
        else:
            from src.seed import seed_extended_users
            seed_extended_users(conn)
    finally:
        pass

    # 2. Connect to Firestore
    client = get_firestore_client()
    if not client:
        print("[ERROR] Could not connect to Cloud Firestore. Please check credentials or emulator host.")
        return {}

    print(f"[INFO] Connected to Firestore project: {client.project}")

    # 3. Perform synchronization from SQLite to Firestore
    stats = sync_sqlite_to_firestore(conn)

    # 4. Save clock state document
    try:
        from src.services.clock_service import ClockService
        now_iso = ClockService(connection=conn).now_iso()
        client.collection(COLLECTION_CLOCK_STATE).document("current").set({
            "current_time": now_iso,
            "is_frozen": False,
            "updated_at": now_iso,
        }, merge=True)
        stats["clock_state"] = 1
    except Exception as exc:
        print(f"[WARNING] Could not save clock state: {exc}")

    conn.close()

    print("\n--- Seeding Summary ---")
    for coll, count in stats.items():
        print(f"  Collection '{coll}': {count} documents synchronized")
    print("=" * 60)
    print("[SUCCESS] Cloud Firestore seeding completed successfully.")
    return stats


def main():
    parser = argparse.ArgumentParser(description="Seed Cloud Firestore with GovFix demo data")
    parser.add_argument("--emulator", help="Firestore emulator host (e.g. 127.0.0.1:8080)", default=None)
    parser.add_argument("--project", help="Firebase project ID", default=None)
    args = parser.parse_args()

    if args.emulator:
        os.environ["FIRESTORE_EMULATOR_HOST"] = args.emulator
    if args.project:
        os.environ["FIREBASE_PROJECT_ID"] = args.project

    seed_firestore_direct()


if __name__ == "__main__":
    main()
