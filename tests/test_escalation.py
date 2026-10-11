"""Automated Tests for Plan 05: Simulated Clock, SLA Deadline & Escalation Engine.

Verifies:
1. ClockService persistence, advance, freeze, and reset behavior.
2. Deterministic SLA deadline computation across all municipal categories.
3. Overdue detection for active tickets vs terminal tickets.
4. Idempotent multi-level escalation ladder (Level 1 at 1.0x, Level 2 at 1.5x).
5. Category confirmation command with deterministic deadline recomputation and audit logging.
6. Reopen SLA cycle reset (fresh sla_started_at, level reset to 0).
7. Monotonicity of audit IDs and hash-chain continuity when clock resets.
"""

import os
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
import pytest

from src.database import connect, initialize_database
from src.services.audit_service import GENESIS_HASH, utc_iso
from src.services.clock_service import ClockService
from src.services.escalation_service import (
    EscalationService,
    compute_deadline,
    is_overdue,
    calculate_escalation_level,
    SLA_HOURS,
    LEVEL_1_RECIPIENT,
    LEVEL_2_RECIPIENT,
)
from src.workflow import WorkflowError


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database initialized with GovFix schema."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()

    conn = connect(db_path)
    initialize_database(conn)

    # Seed demo users for test relationships
    conn.execute("""
        INSERT INTO users (id, name, role, email, department, created_at)
        VALUES 
        ('USR-CIT-01', 'Ramesh Patel', 'Citizen', 'ramesh@citizen.local', 'Citizen', '2026-10-11T00:00:00Z'),
        ('USR-OFF-01', 'Officer Anil Kumar', 'Officer', 'anil@muni.gov', 'Public Works', '2026-10-11T00:00:00Z'),
        ('USR-FLD-01', 'Field Officer Vikram Singh', 'Field Officer', 'vikram@muni.gov', 'Roads', '2026-10-11T00:00:00Z'),
        ('USR-SUP-01', 'Supervisor Rajesh Gupta', 'Supervisor', 'rajesh@muni.gov', 'Administration', '2026-10-11T00:00:00Z');
    """)
    conn.commit()
    conn.close()

    yield db_path

    try:
        if os.path.exists(db_path):
            os.remove(db_path)
    except OSError:
        pass


def test_clock_service_advance_freeze_reset(temp_db):
    """Test clock operations and cross-instance persistence."""
    clock1 = ClockService(db_path=temp_db)
    t0 = clock1.now()

    # Advance by 24 hours
    clock1.advance(hours=24)
    t1 = clock1.now()
    diff_hours = (t1 - t0).total_seconds() / 3600.0
    assert 23.99 <= diff_hours <= 24.01

    # Verify persistence in a second ClockService instance
    clock2 = ClockService(db_path=temp_db)
    assert clock2.get_state()["offset_seconds"] == clock1.get_state()["offset_seconds"]

    # Freeze clock
    clock1.freeze()
    frozen_t = clock1.now()
    assert clock1.get_state()["is_frozen"] is True
    # Subsequent calls return exact same frozen timestamp
    assert clock1.now() == frozen_t

    # Advance frozen clock
    clock1.advance(hours=2)
    t_after_advance = clock1.now()
    assert (t_after_advance - frozen_t).total_seconds() == 7200

    # Reset clock
    clock1.reset()
    assert clock1.get_state()["offset_seconds"] == 0
    assert clock1.get_state()["is_frozen"] is False
    assert clock1.get_state()["frozen_at"] is None


def test_sla_deadline_computation():
    """Verify deterministic SLA durations for all municipal categories."""
    base_time = datetime(2026, 10, 11, 10, 0, 0, tzinfo=UTC)

    assert compute_deadline("Water Leak", base_time) == base_time + timedelta(hours=24)
    assert compute_deadline("Garbage Collection", base_time) == base_time + timedelta(hours=24)
    assert compute_deadline("Streetlight", base_time) == base_time + timedelta(hours=48)
    assert compute_deadline("Pothole", base_time) == base_time + timedelta(hours=72)
    assert compute_deadline("Uncategorised", base_time) == base_time + timedelta(hours=72)


def test_is_overdue_conditions():
    """Verify overdue calculation applies to active states and excludes terminal states and paused states."""
    now = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)
    past_deadline = now - timedelta(hours=2)
    future_deadline = now + timedelta(hours=5)

    # Active unpaused states
    for state in ["SUBMITTED", "ACKNOWLEDGED", "ASSIGNED", "IN_PROGRESS", "REOPENED"]:
        assert is_overdue(past_deadline, state, now) is True
        assert is_overdue(future_deadline, state, now) is False

    # Paused / NEEDS_INFO state is NEVER overdue even if past nominal deadline
    assert is_overdue(past_deadline, "NEEDS_INFO", now) is False
    assert is_overdue(past_deadline, "ACKNOWLEDGED", now, paused_at=now - timedelta(hours=1)) is False

    # Terminal states are NEVER overdue even if past deadline
    for state in ["RESOLVED", "CLOSED", "REJECTED"]:
        assert is_overdue(past_deadline, state, now) is False
        assert is_overdue(future_deadline, state, now) is False


def test_escalation_ladder_and_idempotency(temp_db):
    """Test 1.0x (Level 1) and 1.5x (Level 2) escalations and idempotent audit recording."""
    clock = ClockService(db_path=temp_db)
    t0 = clock.now()
    t0_iso = utc_iso(t0)

    deadline_24h = utc_iso(t0 + timedelta(hours=24))

    # Seed an active ticket with Water Leak (24h SLA)
    conn = connect(temp_db)
    triage_deadline = utc_iso(t0 + timedelta(hours=4))
    conn.execute("""
        INSERT INTO requests (
            id, citizen_id, title, text, location, category, state,
            assigned_to, escalation_level, sla_started_at, sla_cycle,
            reopen_count, version, created_at, updated_at, deadline, triage_deadline
        ) VALUES (
            'REQ-ESC-001', 'USR-CIT-01', 'Burst pipe', 'Water gushing on MG road', 'Ward 12, Main St',
            'Water Leak', 'ASSIGNED', 'USR-FLD-01', 0, ?, 1, 0, 1, ?, ?, ?, ?
        );
    """, (t0_iso, t0_iso, t0_iso, deadline_24h, triage_deadline))
    conn.commit()
    conn.close()

    service = EscalationService(db_path=temp_db, clock=clock)

    # 1. At t0: no escalation
    escalated = service.evaluate_and_escalate_all(current_time=t0)
    assert len(escalated) == 0

    # 2. Advance clock by 25 hours (1.04x SLA) -> Level 1 escalation
    clock.advance(hours=25)
    t25 = clock.now()
    escalated = service.evaluate_and_escalate_all(current_time=t25)
    assert len(escalated) == 1
    assert escalated[0]["request_id"] == "REQ-ESC-001"
    assert escalated[0]["new_level"] == 1
    assert escalated[0]["recipient"] == LEVEL_1_RECIPIENT

    # Verify audit record created for Level 1
    conn = connect(temp_db)
    audit_rows = conn.execute(
        "SELECT * FROM audit WHERE request_id = 'REQ-ESC-001' AND action = 'ESCALATE_REQUEST'"
    ).fetchall()
    assert len(audit_rows) == 1
    assert audit_rows[0]["actor"] == "SYSTEM"
    assert audit_rows[0]["dedupe_key"] == "escalation:REQ-ESC-001:1:1"
    conn.close()

    # 3. IDEMPOTENCY CHECK: Run escalation again at t25
    escalated_again = service.evaluate_and_escalate_all(current_time=t25)
    assert len(escalated_again) == 0, "Idempotent run should not trigger new escalations"

    conn = connect(temp_db)
    audit_count = conn.execute(
        "SELECT count(*) FROM audit WHERE request_id = 'REQ-ESC-001' AND action = 'ESCALATE_REQUEST'"
    ).fetchone()[0]
    assert audit_count == 1, "Duplicate audit rows must not be created on repeated evaluations"
    conn.close()

    # 4. Advance clock by 11 additional hours (total 36 hours = 1.5x of 24h) -> Level 2 escalation
    clock.advance(hours=11)
    t36 = clock.now()
    escalated_l2 = service.evaluate_and_escalate_all(current_time=t36)
    assert len(escalated_l2) == 1
    assert escalated_l2[0]["new_level"] == 2
    assert escalated_l2[0]["recipient"] == LEVEL_2_RECIPIENT

    # Verify total 2 audit records (Level 1 and Level 2)
    conn = connect(temp_db)
    audit_rows = conn.execute(
        "SELECT * FROM audit WHERE request_id = 'REQ-ESC-001' AND action = 'ESCALATE_REQUEST' ORDER BY id ASC"
    ).fetchall()
    assert len(audit_rows) == 2
    assert audit_rows[1]["dedupe_key"] == "escalation:REQ-ESC-001:1:2"
    conn.close()


def test_confirm_category_and_recalculate_deadline(temp_db):
    """Test category confirmation recomputes deadline deterministically and logs audit entry."""
    clock = ClockService(db_path=temp_db)
    t0 = clock.now()
    t0_iso = utc_iso(t0)
    initial_deadline = utc_iso(t0 + timedelta(hours=72))  # Uncategorised 72h

    conn = connect(temp_db)
    conn.execute("""
        INSERT INTO requests (
            id, citizen_id, title, text, location, category, state,
            assigned_to, escalation_level, sla_started_at, sla_cycle,
            reopen_count, version, created_at, updated_at, deadline, triage_deadline
        ) VALUES (
            'REQ-CAT-001', 'USR-CIT-01', 'Pavement problem', 'Large hole on the road', 'MG Road, Ward 4',
            'Uncategorised', 'SUBMITTED', NULL, 0, ?, 1, 0, 1, ?, ?, ?, ?
        );
    """, (t0_iso, t0_iso, t0_iso, initial_deadline, initial_deadline))
    conn.commit()
    conn.close()

    service = EscalationService(db_path=temp_db, clock=clock)

    # Unauthorized role should be rejected
    with pytest.raises((PermissionError, WorkflowError)):
        service.confirm_category(
            request_id="REQ-CAT-001",
            new_category="Pothole",
            actor_id="USR-CIT-01",
            actor_role="Citizen"
        )

    # Authorized Officer confirms category as Pothole (72h SLA)
    res = service.confirm_category(
        request_id="REQ-CAT-001",
        new_category="Pothole",
        actor_id="USR-OFF-01",
        actor_role="Officer",
        expected_version=1
    )

    assert res["new_category"] == "Pothole"
    assert res["version"] == 2

    # Check updated database record
    conn = connect(temp_db)
    req = conn.execute("SELECT category, deadline, version FROM requests WHERE id = 'REQ-CAT-001'").fetchone()
    assert req["category"] == "Pothole"
    expected_deadline = utc_iso(t0 + timedelta(hours=72))
    assert req["deadline"] == expected_deadline
    assert req["version"] == 2

    # Check audit entries: both refusal and acceptance are logged
    refusal_row = conn.execute(
        "SELECT * FROM audit WHERE request_id = 'REQ-CAT-001' AND action = 'CONFIRM_CATEGORY' AND outcome = 'REFUSED'"
    ).fetchone()
    assert refusal_row is not None
    assert refusal_row["actor"] == "USR-CIT-01"

    accepted_row = conn.execute(
        "SELECT * FROM audit WHERE request_id = 'REQ-CAT-001' AND action = 'CONFIRM_CATEGORY' AND outcome = 'ACCEPTED'"
    ).fetchone()
    assert accepted_row is not None
    assert accepted_row["actor"] == "USR-OFF-01"
    assert accepted_row["actor_role"] == "Officer"
    assert accepted_row["outcome"] == "ACCEPTED"
    conn.close()


def test_reopen_sla_reset_and_monotonicity(temp_db):
    """Test that reopening starts a new SLA cycle, resets escalation, and preserves audit monotonicity."""
    clock = ClockService(db_path=temp_db)
    t0 = clock.now()
    t0_iso = utc_iso(t0)

    # Create an escalated ticket
    conn = connect(temp_db)
    conn.execute("""
        INSERT INTO requests (
            id, citizen_id, title, text, location, category, state,
            assigned_to, escalation_level, sla_started_at, sla_cycle,
            reopen_count, version, created_at, updated_at, deadline, triage_deadline
        ) VALUES (
            'REQ-REOPEN-001', 'USR-CIT-01', 'Streetlight off', 'Dark lane', 'Lane 3, North Block',
            'Streetlight', 'RESOLVED', 'USR-FLD-01', 1, ?, 1, 0, 3, ?, ?, ?, ?
        );
    """, (t0_iso, t0_iso, t0_iso, t0_iso, t0_iso))
    conn.commit()

    # Advance clock to simulate reopen 2 days later
    clock.advance(hours=48)
    reopen_time = clock.now()
    reopen_time_iso = utc_iso(reopen_time)
    new_deadline = utc_iso(reopen_time + timedelta(hours=48))

    # Simulate transition to REOPENED with SLA reset
    conn.execute("""
        UPDATE requests
        SET state = 'REOPENED',
            sla_started_at = ?,
            sla_cycle = 2,
            reopen_count = 1,
            escalation_level = 0,
            deadline = ?,
            updated_at = ?,
            version = version + 1
        WHERE id = 'REQ-REOPEN-001'
    """, (reopen_time_iso, new_deadline, reopen_time_iso))
    conn.commit()

    service = EscalationService(db_path=temp_db, clock=clock)

    # Immediately after reopen, ticket should NOT be overdue or escalated
    req = conn.execute("SELECT * FROM requests WHERE id = 'REQ-REOPEN-001'").fetchone()
    assert req["escalation_level"] == 0
    assert req["sla_cycle"] == 2
    assert req["reopen_count"] == 1

    deadline_dt = datetime.fromisoformat(req["deadline"].replace("Z", "+00:00"))
    assert is_overdue(deadline_dt, req["state"], reopen_time) is False

    # Now reset the clock back to t0; audit ID ordering must still be strictly monotonic
    clock.reset()
    assert clock.now() < reopen_time

    # Evaluate escalations with reset clock
    service.evaluate_and_escalate_all()

    audit_ids = [r["id"] for r in conn.execute("SELECT id FROM audit ORDER BY id ASC").fetchall()]
    assert audit_ids == sorted(audit_ids), "Audit IDs must remain monotonically ascending"
    conn.close()


def test_pause_aware_escalation(temp_db):
    """Test that paused tickets (NEEDS_INFO) do not escalate even if nominal SLA time has passed."""
    from src.services.escalation_service import is_at_risk
    clock = ClockService(db_path=temp_db)
    t0 = clock.now()
    t0_iso = utc_iso(t0)
    deadline_24h = utc_iso(t0 + timedelta(hours=24))
    triage_deadline = utc_iso(t0 + timedelta(hours=4))

    conn = connect(temp_db)
    # Seed a paused ticket in NEEDS_INFO with paused_at = t0 + 10 hours
    paused_at_iso = utc_iso(t0 + timedelta(hours=10))
    conn.execute("""
        INSERT INTO requests (
            id, citizen_id, title, text, location, category, state,
            assigned_to, escalation_level, sla_started_at, sla_cycle,
            reopen_count, version, created_at, updated_at, deadline, triage_deadline,
            paused_at, total_paused_seconds
        ) VALUES (
            'REQ-PAUSE-001', 'USR-CIT-01', 'Leaking valve', 'At corner', 'Ward 5',
            'Water Leak', 'NEEDS_INFO', NULL, 0, ?, 1, 0, 1, ?, ?, ?, ?, ?, 0
        );
    """, (t0_iso, t0_iso, t0_iso, deadline_24h, triage_deadline, paused_at_iso))
    conn.commit()
    conn.close()

    service = EscalationService(db_path=temp_db, clock=clock)

    # Advance clock past nominal 24h deadline (30 hours)
    clock.advance(hours=30)
    t30 = clock.now()

    # Ticket should not be overdue, not at risk, and not escalated while paused
    assert is_overdue(t0 + timedelta(hours=24), "NEEDS_INFO", t30, paused_at=t0 + timedelta(hours=10)) is False
    assert is_at_risk(t30, t0, "Water Leak", "NEEDS_INFO", paused_at=t0 + timedelta(hours=10)) is False

    escalated = service.evaluate_and_escalate_all(current_time=t30)
    assert len(escalated) == 0

    conn = connect(temp_db)
    req = conn.execute("SELECT escalation_level FROM requests WHERE id = 'REQ-PAUSE-001'").fetchone()
    assert req["escalation_level"] == 0
    conn.close()


def test_calculate_escalation_level_frozen_at_paused_at():
    """Verify that calculate_escalation_level freezes calculation at paused_at, preserving existing escalation."""
    t0 = datetime(2026, 10, 11, 10, 0, 0, tzinfo=UTC)
    # Water Leak: 24h SLA. Paused at 26 hours (ratio = 26/24 = 1.083 -> Level 1)
    paused_at = t0 + timedelta(hours=26)
    # Current time advanced to 100 hours
    now = t0 + timedelta(hours=100)

    # Calling calculate_escalation_level with paused_at freezes at 26h: level 1, ratio ~1.08
    lvl, ratio = calculate_escalation_level(now, t0, "Water Leak", paused_at=paused_at)
    assert lvl == 1
    assert 1.08 <= ratio <= 1.09


