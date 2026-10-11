"""Targeted tests for Phase 1.5 specification audit and integration gaps.

Verifies:
1. Separate Triage Officer and Field Officer identities and permissions.
2. Complaint assignment ownership and list/read isolation between Field Officers.
3. Unauthorized action rejection and refusal audit logging without ticket mutation.
4. Audit Inspector read access to audit log and chain verification.
5. Audit Inspector mutation refusal (read-only enforcement on all mutation endpoints).
6. Complaint location persistence across creation and retrieval.
7. Real backend audit-chain verification (valid and tampered detection).
8. Missing information flow (ACKNOWLEDGED -> NEEDS_INFO -> ACKNOWLEDGED) with note enforcement,
   SLA pause, and deadline extension.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.database import connect
from src.main import app
from src.models import (
    Category,
    RequestCreate,
    RequestState,
    TransitionCommand,
    UserRole,
)
from src.seed import seed_database, seed_extended_users
from src.services.audit_service import verify_audit_chain
from src.workflow import WorkflowError, WorkflowService


REFERENCE_TIME = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def workflow_setup():
    conn = connect(":memory:")
    seed_database(conn, reference_time=REFERENCE_TIME, include_extended=True)
    clock = MutableClock(REFERENCE_TIME)
    service = WorkflowService(conn, now_provider=clock.now)
    yield conn, service, clock
    conn.close()


@pytest.fixture
def api_client():
    with TestClient(app) as client:
        yield client


# --- 1. Field Officer Identities, Permissions, and Assignment Isolation ---

def test_triage_officer_can_assign_to_field_officer(workflow_setup) -> None:
    conn, service, _ = workflow_setup

    # Triage Officer acknowledges
    req = service.transition(
        request_id="REQ-2026-0001",
        actor_id="USR-OFF-01",
        command=TransitionCommand(
            to_state=RequestState.ACKNOWLEDGED,
            expected_version=1,
        ),
    )
    assert req["state"] == "ACKNOWLEDGED"

    # Triage Officer assigns to Field Officer Vikram Singh
    req = service.transition(
        request_id="REQ-2026-0001",
        actor_id="USR-OFF-01",
        command=TransitionCommand(
            to_state=RequestState.ASSIGNED,
            assigned_to="USR-FLD-01",
            expected_version=2,
        ),
    )
    assert req["state"] == "ASSIGNED"
    assert req["assigned_to"] == "USR-FLD-01"


def test_field_officer_assignment_visibility_isolation(workflow_setup) -> None:
    conn, service, _ = workflow_setup

    # Assign REQ-2026-0001 to USR-FLD-01
    service.transition(
        request_id="REQ-2026-0001",
        actor_id="USR-SUP-01",
        command=TransitionCommand(to_state=RequestState.ACKNOWLEDGED, expected_version=1),
    )
    service.transition(
        request_id="REQ-2026-0001",
        actor_id="USR-SUP-01",
        command=TransitionCommand(to_state=RequestState.ASSIGNED, assigned_to="USR-FLD-01", expected_version=2),
    )

    # USR-FLD-01 sees the complaint in their list
    fld1_list = service.list_requests(actor_id="USR-FLD-01")
    fld1_ids = [r["id"] for r in fld1_list]
    assert "REQ-2026-0001" in fld1_ids

    # USR-FLD-02 does NOT see the complaint in their list
    fld2_list = service.list_requests(actor_id="USR-FLD-02")
    fld2_ids = [r["id"] for r in fld2_list]
    assert "REQ-2026-0001" not in fld2_ids

    # USR-FLD-02 cannot read USR-FLD-01's complaint directly
    with pytest.raises(WorkflowError) as exc:
        service.get_request(request_id="REQ-2026-0001", actor_id="USR-FLD-02")
    assert exc.value.status_code == 403


def test_unauthorized_field_officer_cannot_start_work(workflow_setup) -> None:
    conn, service, _ = workflow_setup

    service.transition(
        request_id="REQ-2026-0001",
        actor_id="USR-SUP-01",
        command=TransitionCommand(to_state=RequestState.ACKNOWLEDGED, expected_version=1),
    )
    service.transition(
        request_id="REQ-2026-0001",
        actor_id="USR-SUP-01",
        command=TransitionCommand(to_state=RequestState.ASSIGNED, assigned_to="USR-FLD-01", expected_version=2),
    )

    audit_before = conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]

    # USR-FLD-02 attempts to transition Vikram's ticket to IN_PROGRESS
    with pytest.raises(WorkflowError) as exc:
        service.transition(
            request_id="REQ-2026-0001",
            actor_id="USR-FLD-02",
            command=TransitionCommand(to_state=RequestState.IN_PROGRESS, expected_version=3),
        )
    assert exc.value.status_code == 403
    assert exc.value.code == "ASSIGNMENT_REQUIRED"

    # Ticket remains unchanged at ASSIGNED v3
    current = service.get_request(request_id="REQ-2026-0001", actor_id="USR-SUP-01")
    assert current["state"] == "ASSIGNED"
    assert current["version"] == 3

    # Refusal was logged to audit
    audit_after = conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    assert audit_after == audit_before + 1

    last_audit = conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 1").fetchone()
    assert last_audit["outcome"] == "REFUSED"
    assert last_audit["actor"] == "USR-FLD-02"


def test_assigned_field_officer_lifecycle_to_resolution(workflow_setup) -> None:
    conn, service, _ = workflow_setup

    service.transition(
        request_id="REQ-2026-0001",
        actor_id="USR-OFF-01",
        command=TransitionCommand(to_state=RequestState.ACKNOWLEDGED, expected_version=1),
    )
    service.transition(
        request_id="REQ-2026-0001",
        actor_id="USR-OFF-01",
        command=TransitionCommand(to_state=RequestState.ASSIGNED, assigned_to="USR-FLD-01", expected_version=2),
    )

    # Assigned officer starts work
    in_prog = service.transition(
        request_id="REQ-2026-0001",
        actor_id="USR-FLD-01",
        command=TransitionCommand(to_state=RequestState.IN_PROGRESS, expected_version=3),
    )
    assert in_prog["state"] == "IN_PROGRESS"

    # Assigned officer completes work with resolution notes
    resolved = service.transition(
        request_id="REQ-2026-0001",
        actor_id="USR-FLD-01",
        command=TransitionCommand(
            to_state=RequestState.RESOLVED,
            resolution_notes="Replaced leaking valve and pressurized supply line.",
            expected_version=4,
        ),
    )
    assert resolved["state"] == "RESOLVED"
    assert resolved["resolution_notes"] == "Replaced leaking valve and pressurized supply line."


# --- 2. Complaint Location Support ---

def test_complaint_location_persisted_and_returned(workflow_setup) -> None:
    conn, service, _ = workflow_setup

    result = service.create_request(
        actor_id="USR-CIT-01",
        command=RequestCreate(
            title="Broken drain cover",
            text="Cover broken on main footpath causing hazard.",
            location="Ward 7, Beside Primary Health Centre",
            reported_category=Category.WATER_LEAK,
        ),
    )
    assert result["location"] == "Ward 7, Beside Primary Health Centre"

    # Verify retrieval
    retrieved = service.get_request(request_id=result["id"], actor_id="USR-CIT-01")
    assert retrieved["location"] == "Ward 7, Beside Primary Health Centre"


# --- 3. Missing Information & SLA Pause/Resume ---

def test_needs_info_requires_note_and_pauses_sla(workflow_setup) -> None:
    conn, service, clock = workflow_setup

    # Acknowledge first
    service.transition(
        request_id="REQ-2026-0001",
        actor_id="USR-OFF-01",
        command=TransitionCommand(to_state=RequestState.ACKNOWLEDGED, expected_version=1),
    )

    # Attempt NEEDS_INFO without note -> fails
    with pytest.raises(WorkflowError) as exc:
        service.transition(
            request_id="REQ-2026-0001",
            actor_id="USR-OFF-01",
            command=TransitionCommand(to_state=RequestState.NEEDS_INFO, expected_version=2),
        )
    assert exc.value.status_code == 422
    assert exc.value.code == "MISSING_REQUIRED_FIELD"

    # Transition to NEEDS_INFO with note
    paused = service.transition(
        request_id="REQ-2026-0001",
        actor_id="USR-OFF-01",
        command=TransitionCommand(
            to_state=RequestState.NEEDS_INFO,
            info_request_note="Please specify which entrance door the leak is adjacent to.",
            expected_version=2,
        ),
    )
    assert paused["state"] == "NEEDS_INFO"
    assert paused["info_request_note"] == "Please specify which entrance door the leak is adjacent to."
    assert paused["paused_at"] is not None
    assert paused["is_overdue"] is False

    original_deadline = datetime.fromisoformat(paused["deadline"].replace("Z", "+00:00"))

    # Advance clock by 3 hours while paused
    clock.advance(3 * 3600)

    # Citizen submits additional information
    resumed = service.transition(
        request_id="REQ-2026-0001",
        actor_id="USR-CIT-01",
        command=TransitionCommand(
            to_state=RequestState.ACKNOWLEDGED,
            additional_info="Gate 2, directly next to municipal vegetable stall #4.",
            expected_version=3,
        ),
    )
    assert resumed["state"] == "ACKNOWLEDGED"
    assert resumed["additional_info"] == "Gate 2, directly next to municipal vegetable stall #4."
    assert resumed["paused_at"] is None
    assert resumed["total_paused_seconds"] == 3 * 3600

    new_deadline = datetime.fromisoformat(resumed["deadline"].replace("Z", "+00:00"))
    assert new_deadline == original_deadline + timedelta(hours=3)


# --- 4. Audit Inspector Read Access and Mutation Immunity ---

def test_audit_inspector_read_access_and_mutation_denial(workflow_setup) -> None:
    conn, service, _ = workflow_setup

    # Audit Inspector can read audit log
    audit_data = service.list_audit(actor_id="USR-AUD-01")
    assert len(audit_data["records"]) > 0

    # Audit Inspector can verify audit chain
    verification = service.verify_audit(actor_id="USR-AUD-01")
    assert verification["valid"] is True

    # Audit Inspector cannot submit a complaint (must be Citizen)
    with pytest.raises(WorkflowError) as exc:
        service.create_request(
            actor_id="USR-AUD-01",
            command=RequestCreate(title="Test", text="Test text"),
        )
    assert exc.value.status_code == 403
    assert exc.value.code == "ROLE_NOT_PERMITTED"

    # Audit Inspector cannot transition complaints
    with pytest.raises(WorkflowError) as exc:
        service.transition(
            request_id="REQ-2026-0001",
            actor_id="USR-AUD-01",
            command=TransitionCommand(to_state=RequestState.ACKNOWLEDGED, expected_version=1),
        )
    assert exc.value.status_code == 403
    assert exc.value.code == "ROLE_NOT_PERMITTED"

    # Audit Inspector cannot reassign complaints
    from src.models import ReassignCommand
    with pytest.raises(WorkflowError) as exc:
        service.reassign(
            request_id="REQ-2026-0002",
            actor_id="USR-AUD-01",
            command=ReassignCommand(assigned_to="USR-FLD-01", reason="Audit override", expected_version=1),
        )
    assert exc.value.status_code == 403


# --- 5. Real Backend Cryptographic Audit Chain Verification ---

def test_audit_chain_verification_integrity_and_tamper_detection(workflow_setup) -> None:
    conn, service, _ = workflow_setup

    # Valid chain verification
    result = verify_audit_chain(conn)
    assert result["valid"] is True
    assert result["total_records"] > 0

    # Tamper simulation: drop trigger to simulate unauthorized SQL-level mutation
    conn.execute("DROP TRIGGER IF EXISTS abort_audit_update")
    conn.execute("UPDATE audit SET reason = 'TAMPERED REASON' WHERE id = 1")

    # Verification must detect the tampering
    tampered_result = verify_audit_chain(conn)
    assert tampered_result["valid"] is False
    assert tampered_result["compromised_id"] == 1
    assert "tampering" in tampered_result["reason"].lower() or "mismatch" in tampered_result["reason"].lower()


# --- 6. End-to-End API Routes for Verification and Location ---

def test_api_audit_verify_endpoint(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/audit/verify", headers={"X-Actor-ID": "USR-AUD-01"})
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["success"] is True
    assert json_data["data"]["valid"] is True
    assert json_data["data"]["total_records"] > 0


def test_api_audit_verify_unauthorized_role(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/audit/verify", headers={"X-Actor-ID": "USR-CIT-01"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AUDIT_READ_NOT_PERMITTED"
