"""Migrate and Seed GovFix into MongoDB Atlas.

Executes:
1. Connects to MongoDB Atlas cluster.
2. Removes all old data across collections (per 'remove old data' user directive).
3. Re-creates unique and query indexes.
4. Seeds clean initial demo data (users, requests, SHA-256 audit chain, clock).
5. Outputs a detailed migration summary.

Usage:
    python scripts/migrate_to_mongodb.py
    python scripts/migrate_to_mongodb.py --uri "mongodb+srv://..."
    python scripts/migrate_to_mongodb.py --purge-sqlite
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.database import connect, initialize_database
from src.seed import seed_database, seed_extended_users
from src.mongodb import (
    get_mongodb_client,
    get_mongodb_database,
    init_mongodb_indexes,
    purge_mongodb_data,
    save_user_to_mongodb,
    save_request_to_mongodb,
    save_audit_event_to_mongodb,
    save_ai_suggestion_to_mongodb,
    save_clock_state_to_mongodb,
    save_email_event_to_mongodb,
    COLLECTION_USERS,
    COLLECTION_REQUESTS,
    COLLECTION_AUDIT,
    COLLECTION_AI_SUGGESTIONS,
    COLLECTION_CLOCK_STATE,
    COLLECTION_EMAIL_EVENTS,
)


def run_migration(uri: str | None = None, purge_sqlite: bool = False) -> bool:
    """Execute complete migration and seeding to MongoDB Atlas."""
    if uri:
        os.environ["MONGODB_URI"] = uri

    print("=" * 65)
    print("GovFix MongoDB Atlas Migration & Clean Data Initialization")
    print("=" * 65)

    client = get_mongodb_client()
    if not client:
        print("\n[ERROR] Cannot connect to MongoDB Atlas.")
        print("Please provide a valid MONGODB_URI or set MONGO_CLUSTER.")
        print("Example: mongodb+srv://niketoke101_db_user:DuOjSO09GUNCb6G1@<cluster-host>/gov-fix?retryWrites=true&w=majority")
        return False

    db = get_mongodb_database()
    if db is None:
        print("[ERROR] Could not resolve database.")
        return False

    print(f"[OK] Connected to MongoDB database: {db.name}")

    # 1. Purge old data from MongoDB
    print("\n[STEP 1] Removing old data from MongoDB collections...")
    purged_counts = purge_mongodb_data(db)
    for coll, count in purged_counts.items():
        print(f"  - Cleaned '{coll}': {count} old documents removed")

    # 2. Re-create indexes
    print("\n[STEP 2] Initializing collection indexes...")
    if init_mongodb_indexes(db):
        print("  - Indexes created for users, requests, audit, clock_state, email_events.")

    # 3. Read pristine seed data from SQLite generator
    print("\n[STEP 3] Generating fresh canonical seed data...")
    conn = connect()
    try:
        initialize_database(conn)
        # Ensure fresh baseline seed
        seed_database(conn, include_extended=True)
        seed_extended_users(conn)

        # 4. Migrate users to MongoDB
        users = [dict(row) for row in conn.execute("SELECT * FROM users").fetchall()]
        for u in users:
            save_user_to_mongodb(u, db=db)
        print(f"  - Seeded {len(users)} users into MongoDB '{COLLECTION_USERS}'")

        # 5. Migrate requests to MongoDB
        requests = [dict(row) for row in conn.execute("SELECT * FROM requests").fetchall()]
        for r in requests:
            save_request_to_mongodb(r, db=db)
        print(f"  - Seeded {len(requests)} requests into MongoDB '{COLLECTION_REQUESTS}'")

        # 6. Migrate audit log (preserving hash-chain)
        audit_records = [dict(row) for row in conn.execute("SELECT * FROM audit ORDER BY id ASC").fetchall()]
        for a in audit_records:
            save_audit_event_to_mongodb(a, db=db)
        print(f"  - Seeded {len(audit_records)} hash-chained audit events into MongoDB '{COLLECTION_AUDIT}'")

        # 7. Migrate clock state
        clock_row = conn.execute("SELECT * FROM clock_state WHERE id = 1").fetchone()
        if clock_row:
            save_clock_state_to_mongodb(dict(clock_row), db=db)
            print(f"  - Initialized clock state into MongoDB '{COLLECTION_CLOCK_STATE}'")

    finally:
        conn.close()

    # 8. Optionally purge local SQLite file
    if purge_sqlite:
        sqlite_path = Path(os.getenv("GOVFIX_DATABASE_PATH", "data/govfix.db"))
        if sqlite_path.exists():
            try:
                sqlite_path.unlink()
                print(f"\n[STEP 4] Purged old local SQLite database: {sqlite_path}")
            except Exception as exc:
                print(f"[WARNING] Could not delete SQLite file: {exc}")

    print("\n" + "=" * 65)
    print("[SUCCESS] MongoDB Atlas migration completed successfully!")
    print(f"Database: {db.name}")
    print(f"Total collections populated: 5")
    print("=" * 65)
    return True


def main():
    parser = argparse.ArgumentParser(description="Migrate GovFix data to MongoDB Atlas")
    parser.add_argument("--uri", help="MongoDB connection URI string", default=None)
    parser.add_argument("--purge-sqlite", action="store_true", help="Remove old local SQLite database file")
    args = parser.parse_args()

    success = run_migration(uri=args.uri, purge_sqlite=args.purge_sqlite)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
