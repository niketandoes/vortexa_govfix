"""SLA Deadline Computation and Escalation Service for GovFix.

Implements time-governance business logic:
- Deterministic SLA deadline calculation based on category.
- Overdue detection for active grievances.
- Multi-level escalation ladder (Level 1 at 1.0x SLA, Level 2 at 1.5x SLA).
- Atomic, idempotent escalation execution using append_audit with dedupe_key.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.database import connect, transaction, DEFAULT_DATABASE_PATH
from src.services.audit_service import append_audit, utc_iso
from src.services.clock_service import ClockService

SLA_HOURS: dict[str, int] = {
    "Water Leak": 24,
    "Garbage Collection": 48,
    "Streetlight": 72,
    "Pothole": 168,
    "Uncategorised": 72,
}

ACTIVE_STATES = {"SUBMITTED", "ACKNOWLEDGED", "ASSIGNED", "IN_PROGRESS", "REOPENED"}
TERMINAL_STATES = {"RESOLVED", "CLOSED", "REJECTED"}

LEVEL_1_RECIPIENT = "USR-SUP-01"  # Supervisor
LEVEL_2_RECIPIENT = "USR-SUP-01"  # Municipal Head (role Supervisor)


def compute_deadline(category: str, start_time: datetime) -> datetime:
    """Compute deadline deterministically: start_time + SLA[category]."""
    sla = SLA_HOURS.get(category, 72)
    return start_time.astimezone(UTC) + timedelta(hours=sla)


def is_overdue(deadline: datetime, state: str, current_time: datetime) -> bool:
    """Return True if grievance is active and current time is past SLA deadline."""
    if state not in ACTIVE_STATES:
        return False
    return current_time.astimezone(UTC) > deadline.astimezone(UTC)


def calculate_escalation_level(
    current_time: datetime,
    sla_started_at: datetime,
    category: str,
) -> tuple[int, float]:
    """Calculate target escalation level and elapsed SLA ratio.

    Returns: (target_level: int, elapsed_ratio: float)
    - Level 0: ratio < 1.0
    - Level 1: ratio >= 1.0 (Supervisor)
    - Level 2: ratio >= 1.5 (Department/Municipal Head)
    """
    sla = SLA_HOURS.get(category, 72)
    elapsed_seconds = (current_time.astimezone(UTC) - sla_started_at.astimezone(UTC)).total_seconds()
    ratio = max(0.0, elapsed_seconds / (sla * 3600.0))

    if ratio >= 1.5:
        return 2, ratio
    elif ratio >= 1.0:
        return 1, ratio
    return 0, ratio


class EscalationService:
    """Evaluates grievances and executes idempotent SLA escalations."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        clock: ClockService | None = None,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        self.db_path = db_path if db_path is not None else DEFAULT_DATABASE_PATH
        self._shared_connection = connection
        self.clock = clock or ClockService(db_path=self.db_path, connection=self._shared_connection)

    def _get_conn(self) -> sqlite3.Connection:
        if self._shared_connection is not None:
            return self._shared_connection
        return connect(self.db_path)

    def evaluate_and_escalate_all(
        self,
        current_time: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Scan all active grievances, escalate if elapsed SLA ratio exceeded, and audit.

        Idempotent: Only updates if target_level > current escalation_level.
        """
        now_sim = current_time or self.clock.now()
        now_sim_utc = now_sim.astimezone(UTC)
        escalated_records: list[dict[str, Any]] = []

        conn = self._get_conn()
        try:
            with transaction(conn, immediate=True):
                cursor = conn.execute(
                    """
                    SELECT id, category, state, escalation_level, sla_started_at, sla_cycle, deadline
                    FROM requests
                    WHERE state IN ('SUBMITTED', 'ACKNOWLEDGED', 'ASSIGNED', 'IN_PROGRESS', 'REOPENED')
                    """
                )
                active_requests = cursor.fetchall()

                for req in active_requests:
                    req_id = req["id"]
                    category = req["category"]
                    current_level = req["escalation_level"]
                    sla_cycle = req["sla_cycle"]

                    sla_started_dt = datetime.fromisoformat(req["sla_started_at"].replace("Z", "+00:00")).astimezone(UTC)
                    target_level, ratio = calculate_escalation_level(now_sim_utc, sla_started_dt, category)

                    if target_level > current_level:
                        upd = conn.execute(
                            """
                            UPDATE requests
                            SET escalation_level = ?, updated_at = ?
                            WHERE id = ? AND escalation_level < ?
                            """,
                            (target_level, utc_iso(now_sim_utc), req_id, target_level)
                        )

                        if upd.rowcount == 1:
                            recipient = LEVEL_2_RECIPIENT if target_level == 2 else LEVEL_1_RECIPIENT
                            recipient_label = "Municipal Head" if target_level == 2 else "Supervisor"

                            reason = (
                                f"SLA elapsed ratio reached {ratio:.2f}x: "
                                f"Escalated to Level {target_level} ({recipient_label} {recipient})"
                            )

                            details = {
                                "target_level": target_level,
                                "previous_level": current_level,
                                "elapsed_ratio": round(ratio, 2),
                                "sla_cycle": sla_cycle,
                                "recipient": recipient,
                                "category": category,
                            }
                            dedupe_key = f"escalation:{req_id}:{sla_cycle}:{target_level}"

                            append_audit(
                                conn,
                                actor="SYSTEM",
                                actor_role="System",
                                action="ESCALATE_REQUEST",
                                request_id=req_id,
                                from_state=req["state"],
                                to_state=req["state"],
                                outcome="ACCEPTED",
                                reason=reason,
                                details=details,
                                dedupe_key=dedupe_key,
                                simulated_at=now_sim_utc,
                            )

                            escalated_records.append({
                                "request_id": req_id,
                                "old_level": current_level,
                                "new_level": target_level,
                                "elapsed_ratio": round(ratio, 2),
                                "recipient": recipient,
                            })
        finally:
            if self._shared_connection is None:
                conn.close()

        return escalated_records

    def confirm_category(
        self,
        request_id: str,
        new_category: str,
        actor_id: str,
        actor_role: str,
        expected_version: int = 1,
    ) -> dict[str, Any]:
        """Delegate category confirmation to WorkflowService and evaluate escalations."""
        from src.models import Category, CategoryCommand
        from src.workflow import WorkflowService

        conn = self._get_conn()
        try:
            cmd = CategoryCommand(
                expected_version=expected_version,
                category=Category(new_category)
            )
            wf = WorkflowService(conn, now_provider=self.clock.now)
            res = wf.confirm_category(request_id=request_id, actor_id=actor_id, command=cmd)
            self.evaluate_and_escalate_all(current_time=self.clock.now())
            return {
                "request_id": res["id"],
                "new_category": res["category"],
                "new_deadline": res["deadline"],
                "version": res["version"]
            }
        finally:
            if self._shared_connection is None:
                conn.close()

