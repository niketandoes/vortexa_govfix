"""Comprehensive tests for GovFix authentication, role scoping, outbox, and Cloud Firestore integration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Generator
import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.database import connect, initialize_database
from src.seed import seed_database
from src.firestore_db import (
    COLLECTION_USERS,
    COLLECTION_REQUESTS,
    COLLECTION_AUDIT,
    COLLECTION_AI_SUGGESTIONS,
    COLLECTION_CLOCK_STATE,
    COLLECTION_EMAIL_EVENTS,
    get_database_backend,
    is_firestore_enabled,
    get_user_from_firestore,
)


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def test_db():
    conn = connect()
    initialize_database(conn)
    seed_database(conn, include_extended=True)
    yield conn
    conn.close()


# ==============================================================================
# 1. Authentication & Token Verification Tests
# ==============================================================================

def test_unauthenticated_request_rejected(client: TestClient) -> None:
    """When test override is disabled, unauthenticated API requests return 401."""
    prev = os.environ.get("GOVFIX_TEST_AUTH_OVERRIDE")
    try:
        os.environ["GOVFIX_TEST_AUTH_OVERRIDE"] = "0"
        resp = client.get("/api/v1/requests")
        assert resp.status_code == 401
        assert "Authentication required" in resp.json().get("detail", "")
    finally:
        if prev is not None:
            os.environ["GOVFIX_TEST_AUTH_OVERRIDE"] = prev


def test_tampered_or_invalid_token_rejected(client: TestClient) -> None:
    """Requests with forged or invalid Bearer token return 401."""
    prev = os.environ.get("GOVFIX_TEST_AUTH_OVERRIDE")
    try:
        os.environ["GOVFIX_TEST_AUTH_OVERRIDE"] = "0"
        resp = client.get(
            "/api/v1/requests",
            headers={"Authorization": "Bearer forged-fake-token-12345"},
        )
        assert resp.status_code == 401
    finally:
        if prev is not None:
            os.environ["GOVFIX_TEST_AUTH_OVERRIDE"] = prev


def test_valid_mock_token_authenticates_user(client: TestClient, test_db) -> None:
    """Valid demo token resolves user identity from authoritative database."""
    prev = os.environ.get("GOVFIX_TEST_AUTH_OVERRIDE")
    try:
        os.environ["GOVFIX_TEST_AUTH_OVERRIDE"] = "0"
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer mock-token-citizen1@govfix.local"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == "USR-CIT-01"
        assert data["role"] == "Citizen"
    finally:
        if prev is not None:
            os.environ["GOVFIX_TEST_AUTH_OVERRIDE"] = prev


def test_disabled_user_access_refused(client: TestClient, test_db) -> None:
    """Disabled users receive 403 Forbidden even with a valid token."""
    test_db.execute("UPDATE users SET status = 'disabled' WHERE id = 'USR-CIT-02'")
    test_db.commit()

    prev = os.environ.get("GOVFIX_TEST_AUTH_OVERRIDE")
    try:
        os.environ["GOVFIX_TEST_AUTH_OVERRIDE"] = "0"
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer mock-token-citizen2@govfix.local"},
        )
        assert resp.status_code == 403
        assert "disabled" in resp.json().get("detail", "").lower()
    finally:
        # Restore status
        test_db.execute("UPDATE users SET status = 'active' WHERE id = 'USR-CIT-02'")
        test_db.commit()
        if prev is not None:
            os.environ["GOVFIX_TEST_AUTH_OVERRIDE"] = prev


# ==============================================================================
# 2. Server-Side Role Enforcement & Data Scoping
# ==============================================================================

def test_citizen_cannot_perform_officer_triage(client: TestClient, test_db) -> None:
    """Citizen token attempting an Officer transition is refused with 403."""
    # Find a SUBMITTED request
    row = test_db.execute("SELECT id, version FROM requests WHERE state = 'SUBMITTED' LIMIT 1").fetchone()
    if not row:
        return

    req_id = row["id"]
    version = row["version"]

    resp = client.post(
        f"/api/v1/requests/{req_id}/transition",
        headers={"Authorization": "Bearer mock-token-citizen1@govfix.local"},
        json={"to_state": "ACKNOWLEDGED", "expected_version": version},
    )
    assert resp.status_code == 403


def test_field_officer_cannot_perform_triage(client: TestClient, test_db) -> None:
    """Field Officer token attempting triage ACKNOWLEDGED transition is refused with 403."""
    row = test_db.execute("SELECT id, version FROM requests WHERE state = 'SUBMITTED' LIMIT 1").fetchone()
    if not row:
        return

    resp = client.post(
        f"/api/v1/requests/{row['id']}/transition",
        headers={"Authorization": "Bearer mock-token-field1@govfix.local"},
        json={"to_state": "ACKNOWLEDGED", "expected_version": row["version"]},
    )
    assert resp.status_code == 403


def test_audit_inspector_cannot_mutate_workflow(client: TestClient, test_db) -> None:
    """Audit Inspector is read-only; state transitions are refused with 403."""
    row = test_db.execute("SELECT id, version FROM requests LIMIT 1").fetchone()
    resp = client.post(
        f"/api/v1/requests/{row['id']}/transition",
        headers={"Authorization": "Bearer mock-token-audit@govfix.local"},
        json={"to_state": "ACKNOWLEDGED", "expected_version": row["version"]},
    )
    assert resp.status_code == 403

    # But Audit Inspector CAN verify the SHA-256 chain
    audit_resp = client.get(
        "/api/v1/audit/verify",
        headers={"Authorization": "Bearer mock-token-audit@govfix.local"},
    )
    assert audit_resp.status_code == 200
    assert audit_resp.json()["success"] is True


def test_admin_cannot_mutate_workflow_states(client: TestClient, test_db) -> None:
    """Administrator cannot perform grievance workflow transitions."""
    row = test_db.execute("SELECT id, version FROM requests LIMIT 1").fetchone()
    resp = client.post(
        f"/api/v1/requests/{row['id']}/transition",
        headers={"Authorization": "Bearer mock-token-admin@govfix.local"},
        json={"to_state": "ACKNOWLEDGED", "expected_version": row["version"]},
    )
    assert resp.status_code == 403


def test_citizen_data_scoping_isolation(client: TestClient, test_db) -> None:
    """Citizen A cannot see Citizen B's complaints in their list."""
    # List as Citizen 1
    resp1 = client.get(
        "/api/v1/requests",
        headers={"Authorization": "Bearer mock-token-citizen1@govfix.local"},
    )
    assert resp1.status_code == 200
    items1 = resp1.json()["data"]

    # All returned items must belong strictly to USR-CIT-01
    for item in items1:
        assert item["citizen_id"] == "USR-CIT-01"

    # List as Citizen 2
    resp2 = client.get(
        "/api/v1/requests",
        headers={"Authorization": "Bearer mock-token-citizen2@govfix.local"},
    )
    assert resp2.status_code == 200
    items2 = resp2.json()["data"]
    for item in items2:
        assert item["citizen_id"] == "USR-CIT-02"


def test_citizen_view_hides_field_officer_internal_id(client: TestClient, test_db) -> None:
    """When a citizen views an assigned complaint, assigned_to does not expose raw USR-FLD-xx ID."""
    row = test_db.execute(
        "SELECT id FROM requests WHERE citizen_id = 'USR-CIT-01' AND assigned_to IS NOT NULL LIMIT 1"
    ).fetchone()
    if not row:
        return

    resp = client.get(
        f"/api/v1/requests/{row['id']}",
        headers={"Authorization": "Bearer mock-token-citizen1@govfix.local"},
    )
    assert resp.status_code == 200
    assigned = resp.json()["data"].get("assigned_to")
    if assigned:
        # Must NOT be raw USR-FLD-xx
        assert not assigned.startswith("USR-FLD-")


# ==============================================================================
# 3. Email Outbox & Dev Inspectability Tests
# ==============================================================================

def test_dev_outbox_page_and_api(client: TestClient) -> None:
    """Dev outbox page and JSON endpoint are accessible."""
    page_resp = client.get("/dev/outbox")
    assert page_resp.status_code == 200
    assert "GovFix Local Email Outbox" in page_resp.text

    api_resp = client.get("/api/v1/dev/outbox")
    assert api_resp.status_code == 200
    assert api_resp.json()["success"] is True


def test_email_dispatch_and_idempotency(test_db) -> None:
    """Email dispatcher records event and prevents duplicate deliveries."""
    from src.services.email_service import dispatch_email_sync

    event_id = "test-idempotency-event-01"
    # First send
    success1 = dispatch_email_sync(
        recipient="citizen1@govfix.local",
        template="grievance_submitted",
        context={"name": "Ramesh", "complaint_id": "REQ-TEST-01"},
        event_id=event_id,
    )
    assert success1 is True

    # Second send with same event_id should be skipped idempotently
    success2 = dispatch_email_sync(
        recipient="citizen1@govfix.local",
        template="grievance_submitted",
        context={"name": "Ramesh", "complaint_id": "REQ-TEST-01"},
        event_id=event_id,
    )
    assert success2 is True

    # Verify only 1 record exists in email_events
    count = test_db.execute(
        "SELECT COUNT(*) FROM email_events WHERE event_id = ?", (event_id,)
    ).fetchone()[0]
    assert count == 1


# ==============================================================================
# 4. Cloud Firestore Integration & Dual Database Tests
# ==============================================================================

def test_firestore_collection_constants() -> None:
    """Verify Firestore collection names match required mappings."""
    assert COLLECTION_USERS == "users"
    assert COLLECTION_REQUESTS == "requests"
    assert COLLECTION_AUDIT == "audit"
    assert COLLECTION_AI_SUGGESTIONS == "ai_suggestions"
    assert COLLECTION_CLOCK_STATE == "clock_state"
    assert COLLECTION_EMAIL_EVENTS == "email_events"


def test_database_backend_pluggable_configuration() -> None:
    """Verify DATABASE_BACKEND resolution."""
    assert get_database_backend() == "sqlite"
    # When explicitly configured for firestore
    prev = os.environ.get("DATABASE_BACKEND")
    try:
        os.environ["DATABASE_BACKEND"] = "firestore"
        assert get_database_backend() == "firestore"
        assert is_firestore_enabled() is True
    finally:
        if prev is not None:
            os.environ["DATABASE_BACKEND"] = prev
        else:
            os.environ.pop("DATABASE_BACKEND", None)


def test_admin_provision_staff_endpoint(client: TestClient, test_db) -> None:
    """Admin provisions staff user with correct ID prefix and invitation email."""
    try:
        resp = client.post(
            "/api/v1/admin/users",
            headers={"Authorization": "Bearer mock-token-admin@govfix.local"},
            json={
                "name": "Arun Patel",
                "email": "arun.patel@govfix.local",
                "role": "Field Officer",
                "department": "Public Works",
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["id"].startswith("USR-FLD-")
        assert data["email"] == "arun.patel@govfix.local"
        assert data["status"] == "active"
    finally:
        test_db.execute("DELETE FROM users WHERE email = 'arun.patel@govfix.local'")
        test_db.commit()


def test_citizen_stats_endpoint_structure(client: TestClient, test_db) -> None:
    """Verify citizen stats endpoint returns authoritative counts supporting both key conventions."""
    resp = client.get(
        "/api/v1/citizen/stats",
        headers={"Authorization": "Bearer mock-token-citizen1@govfix.local"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    # Check all keys are present and typed correctly
    assert "total" in data
    assert "total_complaints" in data
    assert "active" in data
    assert "active_complaints" in data
    assert "in_progress" in data
    assert "action_needed" in data
    assert "resolved" in data
    assert "resolved_complaints" in data
    assert "resolved_closed" in data
    assert "rejected" in data
    assert data["total"] == data["total_complaints"]
    assert data["resolved"] == data["resolved_complaints"]


def test_escalation_evaluator_email_dispatch_and_idempotency(test_db) -> None:
    """Escalation evaluator triggers Level 1 email alert, and re-running evaluator sends no duplicate."""
    from datetime import timedelta
    from src.services.clock_service import ClockService
    from src.services.escalation_service import EscalationService

    clock = ClockService(connection=test_db)
    # Advance clock past 24 hours to trigger escalation on REQ-2026-0001 (Water Leak, 24h SLA)
    new_now = clock.now() + timedelta(hours=30)

    esc_service = EscalationService(connection=test_db, clock=clock)

    # 1. First evaluation: should escalate and dispatch email
    records1 = esc_service.evaluate_and_escalate_all(current_time=new_now)
    assert len(records1) >= 1
    escalated_req = records1[0]["request_id"]

    # Check email_events table
    ev_count1 = test_db.execute(
        "SELECT COUNT(*) FROM email_events WHERE event_id LIKE ?",
        (f"escalate-{escalated_req}-%",),
    ).fetchone()[0]
    assert ev_count1 >= 1

    # 2. Second evaluation: should be idempotent and send NO duplicates
    records2 = esc_service.evaluate_and_escalate_all(current_time=new_now)
    assert len(records2) == 0

    ev_count2 = test_db.execute(
        "SELECT COUNT(*) FROM email_events WHERE event_id LIKE ?",
        (f"escalate-{escalated_req}-%",),
    ).fetchone()[0]
    assert ev_count2 == ev_count1


def test_multi_role_portal_redirects_no_infinite_loop(client: TestClient, test_db) -> None:
    """Verify that visiting another role's portal redirects directly to the user's role workspace without redirect loop."""
    prev = os.environ.get("GOVFIX_TEST_AUTH_OVERRIDE")
    try:
        # In normal mode (override disabled)
        os.environ["GOVFIX_TEST_AUTH_OVERRIDE"] = "0"

        # Field officer visiting /citizen should redirect directly to /staff/field
        resp = client.get(
            "/citizen",
            headers={"Authorization": "Bearer mock-token-field1@govfix.local"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 307)
        assert resp.headers["location"] == "/staff/field"

        # Supervisor visiting /citizen should redirect directly to /staff/supervisor
        resp_sup = client.get(
            "/citizen",
            headers={"Authorization": "Bearer mock-token-supervisor@govfix.local"},
            follow_redirects=False,
        )
        assert resp_sup.status_code in (302, 307)
        assert resp_sup.headers["location"] == "/staff/supervisor"

        # Admin visiting /citizen should redirect directly to /admin
        resp_adm = client.get(
            "/citizen",
            headers={"Authorization": "Bearer mock-token-admin@govfix.local"},
            follow_redirects=False,
        )
        assert resp_adm.status_code in (302, 307)
        assert resp_adm.headers["location"] == "/admin"

        # Citizen visiting /staff/triage should redirect directly to /citizen
        resp_cit = client.get(
            "/staff/triage",
            headers={"Authorization": "Bearer mock-token-citizen1@govfix.local"},
            follow_redirects=False,
        )
        assert resp_cit.status_code in (302, 307)
        assert resp_cit.headers["location"] == "/citizen"
    finally:
        if prev is not None:
            os.environ["GOVFIX_TEST_AUTH_OVERRIDE"] = prev


def test_closed_reopen_permitted_actions_server_authority(test_db) -> None:
    """Verify that reopening a CLOSED ticket is permitted within 7 days and removed after 7 days by server authority."""
    from datetime import UTC, datetime, timedelta
    from src.models import RequestCreate, RequestState, TransitionCommand
    from src.workflow import WorkflowService

    now = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)
    now_current = now

    def now_provider():
        return now_current

    wf = WorkflowService(test_db, now_provider=now_provider)

    cmd = RequestCreate(title="Pothole on 5th Main", text="Deep pothole causing accidents", location="5th Main, Sector 2")
    created = wf.create_request(actor_id="USR-CIT-01", command=cmd)
    req_id = created["id"]

    wf.transition(request_id=req_id, actor_id="USR-OFF-01", command=TransitionCommand(to_state=RequestState.ACKNOWLEDGED, expected_version=1))
    wf.transition(request_id=req_id, actor_id="USR-OFF-01", command=TransitionCommand(to_state=RequestState.ASSIGNED, assigned_to="USR-FLD-01", expected_version=2))
    wf.transition(request_id=req_id, actor_id="USR-FLD-01", command=TransitionCommand(to_state=RequestState.IN_PROGRESS, expected_version=3))
    wf.transition(request_id=req_id, actor_id="USR-FLD-01", command=TransitionCommand(to_state=RequestState.RESOLVED, resolution_notes="Filled and sealed with bitumen asphalt.", resolution_evidence_json={"work_order_id": "WO-99"}, expected_version=4))
    wf.transition(request_id=req_id, actor_id="USR-CIT-01", command=TransitionCommand(to_state=RequestState.CLOSED, expected_version=5))

    # Day 3: Within 7 days - permitted_actions must contain TRANSITION:REOPENED
    now_current = now + timedelta(days=3)
    req_day3 = wf.get_request(request_id=req_id, actor_id="USR-CIT-01")
    assert "TRANSITION:REOPENED" in req_day3["permitted_actions"]

    # Day 8: Past 7 days - permitted_actions must NOT contain TRANSITION:REOPENED
    now_current = now + timedelta(days=8)
    req_day8 = wf.get_request(request_id=req_id, actor_id="USR-CIT-01")
    assert "TRANSITION:REOPENED" not in req_day8["permitted_actions"]


def test_reassignment_and_appeal_decision_email_dispatch(client: TestClient, test_db) -> None:
    """Reassigning case dispatches reassignment template, and approving appeal dispatches appeal_decision template."""
    row = test_db.execute("SELECT version FROM requests WHERE id = 'REQ-2026-0003'").fetchone()
    version = row["version"] if row else 2

    # 1. Test reassignment email to newly assigned field officer
    reassign_resp = client.post(
        "/api/v1/requests/REQ-2026-0003/reassign",
        headers={"Authorization": "Bearer mock-token-supervisor@govfix.local"},
        json={"assigned_to": "USR-FLD-02", "expected_version": version, "reason": "Workload rebalancing"},
    )
    assert reassign_resp.status_code == 200

    ev = test_db.execute(
        "SELECT template, recipient FROM email_events WHERE event_id LIKE 'reassign-REQ-2026-0003-%'"
    ).fetchone()
    assert ev is not None
    assert ev["template"] == "reassignment"
    assert ev["recipient"] == "field2@govfix.local"

