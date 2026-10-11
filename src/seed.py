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
            "Roads & Public Works",
            now_text,
        ),
        (
            "USR-FLD-02",
            "Field Officer Sunita Rao",
            "Field Officer",
            "sunita.rao@municipality.gov",
            "Sanitation & Streetlights",
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
    users = (
        ("USR-CIT-01", "Ramesh Patel", "Citizen", "ramesh@citizen.local", None, now_text),
        ("USR-CIT-02", "Priya Sharma", "Citizen", "priya@citizen.local", None, now_text),
        (
            "USR-OFF-01",
            "Officer Anil Kumar",
            "Officer",
            "anil.kumar@municipality.gov",
            "Triage Desk / Public Works",
            now_text,
        ),
        (
            "USR-FLD-01",
            "Field Officer Vikram Singh",
            "Field Officer",
            "vikram.singh@municipality.gov",
            "Roads & Public Works",
            now_text,
        ),
        (
            "USR-FLD-02",
            "Field Officer Sunita Rao",
            "Field Officer",
            "sunita.rao@municipality.gov",
            "Sanitation & Streetlights",
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
        INSERT INTO users (id, name, role, email, department, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        users,
    )

    requests = (
        (
            "REQ-2026-0001",
            "USR-CIT-01",
            "Pothole on Main Market Road",
            "Deep pothole near market entrance causing vehicle damage.",
            "Central Market Entrance, MG Road",
            "Pothole",
            "Uncategorised",
            "SUBMITTED",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            0,
            utc_iso(now),
            1,
            0,
            None,
            0,
            1,
            utc_iso(now),
            utc_iso(now),
            utc_iso(now + timedelta(hours=2)),
            None,
            None,
            utc_iso(now + timedelta(hours=72)),
        ),
        (
            "REQ-2026-0002",
            "USR-CIT-02",
            "Water pipeline leakage",
            "Water pipe leaking continuously near Sector 4.",
            "Sector 4 Residential Area",
            "Water Leak",
            "Water Leak",
            "NEEDS_INFO",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "Please provide the exact house number or nearby landmark.",
            None,
            0,
            utc_iso(now - timedelta(hours=3)),
            1,
            0,
            utc_iso(now - timedelta(hours=1)),
            0,
            2,
            utc_iso(now - timedelta(hours=3)),
            utc_iso(now - timedelta(hours=1)),
            utc_iso(now - timedelta(hours=1)),
            None,
            None,
            utc_iso(now - timedelta(hours=3) + timedelta(hours=24)),
        ),
        (
            "REQ-2026-0003",
            "USR-CIT-01",
            "Overflowing garbage dump",
            "Community garbage bin overflowing into the street.",
            "Corner of 5th Cross, Gandhi Nagar",
            "Garbage Collection",
            "Garbage Collection",
            "ASSIGNED",
            "USR-FLD-01",
            utc_iso(now - timedelta(hours=1)),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            0,
            utc_iso(now - timedelta(hours=2)),
            1,
            0,
            None,
            0,
            2,
            utc_iso(now - timedelta(hours=2)),
            utc_iso(now - timedelta(hours=1)),
            utc_iso(now),
            utc_iso(now - timedelta(hours=1)),
            None,
            utc_iso(now - timedelta(hours=2) + timedelta(hours=24)),
        ),
        (
            "REQ-2026-0004",
            "USR-CIT-02",
            "Streetlight outage",
            "The streetlight outside Ward 8 community hall is dark.",
            "Ward 8 Community Hall, Station Road",
            "Streetlight",
            "Streetlight",
            "IN_PROGRESS",
            "USR-FLD-01",
            utc_iso(now - timedelta(hours=6)),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            0,
            utc_iso(now - timedelta(hours=8)),
            1,
            0,
            None,
            0,
            3,
            utc_iso(now - timedelta(hours=8)),
            utc_iso(now - timedelta(hours=4)),
            utc_iso(now - timedelta(hours=6)),
            utc_iso(now - timedelta(hours=6)),
            None,
            utc_iso(now - timedelta(hours=8) + timedelta(hours=48)),
        ),
        (
            "REQ-2026-0005",
            "USR-CIT-01",
            "Road crater repaired",
            "Large pothole in front of school gate repaired.",
            "Opposite Primary School Gate, Park Street",
            "Pothole",
            "Pothole",
            "RESOLVED",
            "USR-FLD-02",
            utc_iso(now - timedelta(hours=20)),
            None,
            None,
            None,
            None,
            "Pothole filled with cold mix asphalt and leveled.",
            canonical_json({"photos": ["evidence://pothole-repaired-01.jpg"], "work_order": "WO-8821"}),
            None,
            None,
            None,
            0,
            utc_iso(now - timedelta(hours=24)),
            1,
            0,
            None,
            0,
            4,
            utc_iso(now - timedelta(hours=24)),
            utc_iso(now - timedelta(hours=2)),
            utc_iso(now - timedelta(hours=22)),
            utc_iso(now - timedelta(hours=20)),
            None,
            utc_iso(now - timedelta(hours=24) + timedelta(hours=72)),
        ),
        (
            "REQ-2026-0006",
            "USR-CIT-01",
            "Burst pipe near hospital",
            "Water is flowing from a burst pipe opposite the primary hospital.",
            "Opposite City Hospital Gate 3, MG Road",
            "Water Leak",
            "Water Leak",
            "IN_PROGRESS",
            "USR-FLD-01",
            utc_iso(now),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            0,
            utc_iso(now),
            1,
            0,
            None,
            0,
            3,
            utc_iso(now),
            utc_iso(now),
            utc_iso(now + timedelta(hours=2)),
            utc_iso(now),
            None,
            utc_iso(now + timedelta(hours=24)),
        ),
    )
    connection.executemany(
        """
        INSERT INTO requests (
            id, citizen_id, title, text, location, reported_category, category, state,
            assigned_to, assigned_at, rejection_reason, appeal_reason, appealed_at,
            appeal_approval_note, resolution_notes, resolution_evidence_json,
            reopen_reason, info_request_note, additional_info, escalation_level,
            sla_started_at, sla_cycle, reopen_count, paused_at, total_paused_seconds,
            version, created_at, updated_at, triage_deadline, triaged_at, closed_at,
            deadline
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
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
        ("REQ-2026-0002", "NEEDS_INFO"),
        ("REQ-2026-0003", "ASSIGNED"),
        ("REQ-2026-0004", "IN_PROGRESS"),
        ("REQ-2026-0005", "RESOLVED"),
        ("REQ-2026-0006", "IN_PROGRESS"),
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
