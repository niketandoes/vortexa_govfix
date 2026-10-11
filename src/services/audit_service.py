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
