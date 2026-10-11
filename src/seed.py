"""Deterministic fictional seed data for the GovFix demonstration."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.database import connect, initialize_database


GENESIS_HASH = "0" * 64


def utc_iso(value: datetime) -> str:
    """Serialize an aware datetime as canonical UTC ISO-8601 text."""

    if value.tzinfo is None:
        raise ValueError("seed timestamps must include a timezone")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _append_seed_audit(
    connection: sqlite3.Connection,
    *,
    timestamp: str,
    request_id: str,
    to_state: str,
    details: dict[str, Any],
) -> None:
    previous = connection.execute(
        "SELECT hash FROM audit ORDER BY id DESC LIMIT 1"
    ).fetchone()
    previous_hash = previous["hash"] if previous else GENESIS_HASH
    details_json = canonical_json(details)
    content = canonical_json(
        {
            "timestamp": timestamp,
            "simulated_at": timestamp,
            "actor": "SYSTEM",
            "actor_role": "System",
            "action": "SEED_FIXTURE",
            "request_id": request_id,
            "from_state": None,
            "to_state": to_state,
            "outcome": "ACCEPTED",
            "reason": "Created deterministic fictional demo fixture",
            "details_json": details_json,
            "dedupe_key": "",
        }
    )
    entry_hash = hashlib.sha256((previous_hash + content).encode("utf-8")).hexdigest()
    connection.execute(
        """
        INSERT INTO audit (
            timestamp, simulated_at, actor, actor_role, action, request_id,
            from_state, to_state, outcome, reason, details_json, dedupe_key,
            previous_hash, hash
        ) VALUES (?, ?, 'SYSTEM', 'System', 'SEED_FIXTURE', ?, NULL, ?,
                  'ACCEPTED', ?, ?, NULL, ?, ?)
        """,
        (
            timestamp,
            timestamp,
            request_id,
            to_state,
            "Created deterministic fictional demo fixture",
            details_json,
            previous_hash,
            entry_hash,
        ),
    )


def clear_schema(connection: sqlite3.Connection) -> None:
    """Drop only GovFix-owned objects so a development database can be reseeded."""

    connection.executescript(
        """
        DROP TABLE IF EXISTS audit;
        DROP TABLE IF EXISTS ai_suggestions;
        DROP TABLE IF EXISTS requests;
        DROP TABLE IF EXISTS clock_state;
        DROP TABLE IF EXISTS users;
        """
    )


def seed_extended_users(
    connection: sqlite3.Connection, *, reference_time: datetime | None = None
) -> None:
    now = (reference_time or datetime.now(UTC)).astimezone(UTC)
    now_text = utc_iso(now)
    extended = (
        (
            "USR-FLD-01",
            "Field Officer Vikram Singh",
            "Field Officer",
            "vikram.singh@municipality.gov",
            "Public Works",
            now_text,
        ),
        (
            "USR-FLD-02",
            "Field Officer Sunita Rao",
            "Field Officer",
            "sunita.rao@municipality.gov",
            "Sanitation",
            now_text,
        ),
        (
            "USR-AUD-01",
            "Audit Inspector Meera Joshi",
            "Audit Inspector",
            "meera.joshi@audit.gov",
            "Municipal Audit Commission",
            now_text,
        ),
    )
    connection.executemany(
        """
        INSERT OR IGNORE INTO users (id, name, role, email, department, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        extended,
    )
    connection.commit()


def seed_database(
    connection: sqlite3.Connection,
    *,
    reference_time: datetime | None = None,
    include_extended: bool = False,
) -> None:
    """Reset and populate a database with fictional, reproducible fixtures."""

    now = (reference_time or datetime.now(UTC)).astimezone(UTC)
    clear_schema(connection)
    initialize_database(connection)

    now_text = utc_iso(now)
    base_users = [
        ("USR-CIT-01", "Ramesh Patel", "Citizen", "ramesh@citizen.local", None, now_text),
        ("USR-CIT-02", "Priya Sharma", "Citizen", "priya@citizen.local", None, now_text),
        (
            "USR-OFF-01",
            "Officer Anil Kumar",
            "Officer",
            "anil.kumar@municipality.gov",
            "Public Works",
            now_text,
        ),
        (
            "USR-OFF-02",
            "Officer Sunita Verma",
            "Officer",
            "sunita.verma@municipality.gov",
            "Sanitation",
            now_text,
        ),
        (
            "USR-SUP-01",
            "Supervisor Rajesh Gupta",
            "Supervisor",
            "rajesh.gupta@municipality.gov",
            "Municipal Head",
            now_text,
        ),
    ]

    if include_extended:
        base_users.extend(
            [
                (
                    "USR-FLD-01",
                    "Field Officer Vikram Singh",
                    "Field Officer",
                    "vikram.singh@municipality.gov",
                    "Public Works",
                    now_text,
                ),
                (
                    "USR-FLD-02",
                    "Field Officer Sunita Rao",
                    "Field Officer",
                    "sunita.rao@municipality.gov",
                    "Sanitation",
                    now_text,
                ),
                (
                    "USR-AUD-01",
                    "Audit Inspector Meera Joshi",
                    "Audit Inspector",
                    "meera.joshi@audit.gov",
                    "Municipal Audit Commission",
                    now_text,
                ),
            ]
        )

    connection.executemany(
        """
        INSERT INTO users (id, name, role, email, department, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        base_users,
    )

    submitted_at = now
    assigned_at = now - timedelta(hours=8)
    escalation_at = now - timedelta(hours=23)
    requests = (
        (
            "REQ-2026-0001",
            "USR-CIT-01",
            "Water leaking near the market",
            "A pipe is leaking beside the central market entrance.",
            "Ward 3, Central Market Gate 2",
            "Water Leak",
            "Uncategorised",
            "SUBMITTED",
            None,
            utc_iso(submitted_at),
            utc_iso(submitted_at),
            utc_iso(submitted_at),
            utc_iso(submitted_at + timedelta(hours=72)),
        ),
        (
            "REQ-2026-0002",
            "USR-CIT-02",
            "Streetlight not working",
            "The streetlight outside Ward 8 community hall is dark.",
            "Ward 8, Outside Community Hall",
            "Streetlight",
            "Streetlight",
            "ASSIGNED",
            "USR-OFF-01",
            utc_iso(assigned_at),
            utc_iso(assigned_at),
            utc_iso(now - timedelta(hours=1)),
            utc_iso(assigned_at + timedelta(hours=72)),
        ),
        (
            "REQ-2026-0003",
            "USR-CIT-01",
            "Burst pipe near school",
            "Water is flowing from a burst pipe opposite the primary school.",
            "Ward 12, Opposite Primary School",
            "Water Leak",
            "Water Leak",
            "ASSIGNED",
            "USR-OFF-02",
            utc_iso(escalation_at),
            utc_iso(escalation_at),
            utc_iso(escalation_at + timedelta(hours=1)),
            utc_iso(escalation_at + timedelta(hours=24)),
        ),
    )
    connection.executemany(
        """
        INSERT INTO requests (
            id, citizen_id, title, text, location, reported_category, category, state,
            assigned_to, sla_started_at, created_at, updated_at, deadline
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        requests,
    )

    connection.execute(
        """
        INSERT INTO ai_suggestions (
            id, request_id, kind, payload, confidence, source, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "SUG-0001",
            "REQ-2026-0001",
            "categorisation",
            canonical_json({"category": "Water Leak"}),
            0.94,
            "local_fallback",
            now_text,
        ),
    )

    connection.execute(
        """
        INSERT INTO clock_state (
            id, offset_seconds, is_frozen, frozen_at, updated_at
        ) VALUES (1, 0, 0, NULL, ?)
        """,
        (now_text,),
    )

    for request_id, state in (
        ("REQ-2026-0001", "SUBMITTED"),
        ("REQ-2026-0002", "ASSIGNED"),
        ("REQ-2026-0003", "ASSIGNED"),
    ):
        _append_seed_audit(
            connection,
            timestamp=now_text,
            request_id=request_id,
            to_state=state,
            details={"fixture": True, "state": state},
        )

    connection.commit()


def reset_and_seed(
    database_path: str | Path, *, reference_time: datetime | None = None
) -> None:
    connection = connect(database_path)
    try:
        seed_database(connection, reference_time=reference_time)
    finally:
        connection.close()


def parse_reference_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("reference time must include a timezone")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset and seed a GovFix development database")
    parser.add_argument("--database", default="data/govfix.db")
    parser.add_argument(
        "--reference-time",
        type=parse_reference_time,
        help="Optional reproducible ISO-8601 time, for example 2026-10-11T12:00:00Z",
    )
    args = parser.parse_args()
    reset_and_seed(args.database, reference_time=args.reference_time)
    print(f"Seeded GovFix database at {Path(args.database).resolve()}")


if __name__ == "__main__":
    main()
