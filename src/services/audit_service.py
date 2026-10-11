"""Append-only cryptographic audit writer used by workflow commands."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any


GENESIS_HASH = "0" * 64


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("audit timestamps must include a timezone")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def append_audit(
    connection: sqlite3.Connection,
    *,
    actor: str,
    actor_role: str,
    action: str,
    request_id: str,
    outcome: str,
    from_state: str | None = None,
    to_state: str | None = None,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
    simulated_at: datetime | None = None,
    timestamp: datetime | None = None,
) -> int:
    """Append one hash-linked audit row using the caller's active transaction."""

    wall_time = timestamp or datetime.now(UTC)
    timestamp_text = utc_iso(wall_time)
    simulated_text = utc_iso(simulated_at) if simulated_at else None
    details_json = canonical_json(details or {})
    previous = connection.execute(
        "SELECT hash FROM audit ORDER BY id DESC LIMIT 1"
    ).fetchone()
    previous_hash = previous["hash"] if previous else GENESIS_HASH
    content = canonical_json(
        {
            "timestamp": timestamp_text,
            "simulated_at": simulated_text or "",
            "actor": actor,
            "actor_role": actor_role,
            "action": action,
            "request_id": request_id,
            "from_state": from_state,
            "to_state": to_state,
            "outcome": outcome,
            "reason": reason or "",
            "details_json": details_json,
            "dedupe_key": dedupe_key or "",
        }
    )
    entry_hash = hashlib.sha256((previous_hash + content).encode("utf-8")).hexdigest()
    cursor = connection.execute(
        """
        INSERT INTO audit (
            timestamp, simulated_at, actor, actor_role, action, request_id,
            from_state, to_state, outcome, reason, details_json, dedupe_key,
            previous_hash, hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            timestamp_text,
            simulated_text,
            actor,
            actor_role,
            action,
            request_id,
            from_state,
            to_state,
            outcome,
            reason,
            details_json,
            dedupe_key,
            previous_hash,
            entry_hash,
        ),
    )
    return int(cursor.lastrowid)


def verify_audit_chain(connection: sqlite3.Connection) -> dict[str, Any]:
    """Verify cryptographic SHA-256 integrity across the entire audit log."""
    rows = connection.execute("SELECT * FROM audit ORDER BY id ASC").fetchall()
    expected_previous_hash = GENESIS_HASH

    for idx, row in enumerate(rows):
        if row["previous_hash"] != expected_previous_hash:
            return {
                "valid": False,
                "is_valid": False,
                "error": "TAMPER_DETECTED",
                "compromised_id": row["id"],
                "tampered_record_id": row["id"],
                "compromised_index": idx,
                "reason": f"Previous hash mismatch at record {row['id']}",
                "total_verified": idx,
                "total_records": len(rows),
                "chain_head": expected_previous_hash,
                "expected_previous_hash": expected_previous_hash,
                "actual_previous_hash": row["previous_hash"],
                "tampered_at": row["timestamp"],
            }

        content = canonical_json(
            {
                "timestamp": row["timestamp"],
                "simulated_at": row["simulated_at"] or "",
                "actor": row["actor"],
                "actor_role": row["actor_role"],
                "action": row["action"],
                "request_id": row["request_id"],
                "from_state": row["from_state"],
                "to_state": row["to_state"],
                "outcome": row["outcome"],
                "reason": row["reason"] or "",
                "details_json": row["details_json"],
                "dedupe_key": row["dedupe_key"] or "",
            }
        )
        expected_hash = hashlib.sha256((expected_previous_hash + content).encode("utf-8")).hexdigest()
        if row["hash"] != expected_hash:
            return {
                "valid": False,
                "is_valid": False,
                "error": "TAMPER_DETECTED",
                "compromised_id": row["id"],
                "tampered_record_id": row["id"],
                "compromised_index": idx,
                "reason": f"Cryptographic SHA-256 hash tampering detected at record {row['id']}",
                "total_verified": idx,
                "total_records": len(rows),
                "chain_head": expected_previous_hash,
                "expected_hash": expected_hash,
                "actual_hash": row["hash"],
                "tampered_at": row["timestamp"],
            }

        expected_previous_hash = row["hash"]

    return {
        "valid": True,
        "is_valid": True,
        "total_records": len(rows),
        "total_verified": len(rows),
        "verified_records_count": len(rows),
        "chain_head": expected_previous_hash if rows else None,
    }

