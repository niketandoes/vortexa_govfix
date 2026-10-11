"""Simulated Clock Service for GovFix.

Provides centralized time governance:
- All services query ClockService.now() rather than datetime.now() directly.
- Persisted in singleton SQLite table `clock_state` (id = 1).
- Supports advance (hours/minutes), freeze (fixed point), and reset (sync to wall-clock).
- Thread-safe and persistent across application lifecycles.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.database import connect, initialize_database, DEFAULT_DATABASE_PATH


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("clock timestamps must include a timezone")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class ClockService:
    """Thread-safe time provider with SQLite persistence."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        connection: sqlite3.Connection | None = None
    ) -> None:
        self.db_path = db_path if db_path is not None else DEFAULT_DATABASE_PATH
        self._shared_connection = connection
        self._lock = threading.RLock()
        self._ensure_initialized()


    def _get_conn(self) -> sqlite3.Connection:
        if self._shared_connection is not None:
            return self._shared_connection
        return connect(self.db_path)

    def _ensure_initialized(self) -> None:
        """Ensure schema and singleton clock_state row exist."""
        conn = self._get_conn()
        try:
            # Check if clock_state table exists
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='clock_state'"
            ).fetchone()
            if not exists:
                initialize_database(conn)

            now_text = utc_iso(datetime.now(UTC))
            conn.execute(
                """
                INSERT OR IGNORE INTO clock_state (id, offset_seconds, is_frozen, frozen_at, updated_at)
                VALUES (1, 0, 0, NULL, ?)
                """,
                (now_text,)
            )
            conn.commit()
        finally:
            if self._shared_connection is None:
                conn.close()

    def _fetch_state(self) -> dict[str, Any]:
        """Fetch current clock_state row."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT offset_seconds, is_frozen, frozen_at, updated_at FROM clock_state WHERE id = 1"
            ).fetchone()
            if not row:
                now_text = utc_iso(datetime.now(UTC))
                conn.execute(
                    """
                    INSERT INTO clock_state (id, offset_seconds, is_frozen, frozen_at, updated_at)
                    VALUES (1, 0, 0, NULL, ?)
                    """,
                    (now_text,)
                )
                conn.commit()
                return {"offset_seconds": 0, "is_frozen": 0, "frozen_at": None}
            return dict(row)
        finally:
            if self._shared_connection is None:
                conn.close()

    def now(self) -> datetime:
        """Return the current simulated UTC datetime."""
        with self._lock:
            state = self._fetch_state()
            if state["is_frozen"] and state["frozen_at"]:
                dt = datetime.fromisoformat(state["frozen_at"].replace("Z", "+00:00"))
                return dt.astimezone(UTC)

            offset = timedelta(seconds=state["offset_seconds"])
            return datetime.now(UTC) + offset

    def now_iso(self) -> str:
        """Return current simulated UTC timestamp in canonical ISO format."""
        return utc_iso(self.now())

    def advance(self, hours: float = 0.0, minutes: float = 0.0) -> datetime:
        """Advance the simulated clock by hours and/or minutes."""
        delta_seconds = int(round(hours * 3600 + minutes * 60))
        wall_now = utc_iso(datetime.now(UTC))

        with self._lock:
            state = self._fetch_state()
            conn = self._get_conn()
            try:
                if state["is_frozen"] and state["frozen_at"]:
                    current_dt = datetime.fromisoformat(state["frozen_at"].replace("Z", "+00:00"))
                    new_dt = current_dt + timedelta(seconds=delta_seconds)
                    conn.execute(
                        "UPDATE clock_state SET frozen_at = ?, updated_at = ? WHERE id = 1",
                        (utc_iso(new_dt), wall_now)
                    )
                else:
                    new_offset = state["offset_seconds"] + delta_seconds
                    conn.execute(
                        "UPDATE clock_state SET offset_seconds = ?, is_frozen = 0, frozen_at = NULL, updated_at = ? WHERE id = 1",
                        (new_offset, wall_now)
                    )
                conn.commit()
            finally:
                if self._shared_connection is None:
                    conn.close()

        return self.now()

    def freeze(self) -> datetime:
        """Freeze simulated time at the current moment."""
        with self._lock:
            current_sim = utc_iso(self.now())
            wall_now = utc_iso(datetime.now(UTC))
            conn = self._get_conn()
            try:
                conn.execute(
                    "UPDATE clock_state SET is_frozen = 1, frozen_at = ?, updated_at = ? WHERE id = 1",
                    (current_sim, wall_now)
                )
                conn.commit()
            finally:
                if self._shared_connection is None:
                    conn.close()

        return self.now()

    def reset(self) -> datetime:
        """Reset simulated clock offsets back to real UTC system time."""
        wall_now = utc_iso(datetime.now(UTC))
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "UPDATE clock_state SET offset_seconds = 0, is_frozen = 0, frozen_at = NULL, updated_at = ? WHERE id = 1",
                    (wall_now,)
                )
                conn.commit()
            finally:
                if self._shared_connection is None:
                    conn.close()

        return self.now()

    def get_state(self) -> dict[str, Any]:
        """Return structured summary of the clock state."""
        state = self._fetch_state()
        curr = self.now()
        return {
            "current_time": utc_iso(curr),
            "offset_seconds": state["offset_seconds"],
            "offset_hours": round(state["offset_seconds"] / 3600.0, 2),
            "is_frozen": bool(state["is_frozen"]),
            "frozen_at": state["frozen_at"]
        }
