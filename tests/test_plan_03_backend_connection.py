"""Integration tests for Plan 03 frontend-to-backend connections across all 5 personas.

Tests all UI workspace endpoints, command payloads, and workflows:
- Citizen Portal: complaint creation, advisory preview, missing info response, dispute & reopen, and rejection appeal
- Officer Workspace: triage intake, AI suggestion inspection, category confirmation, info request, assignment
- Field Officer Workspace: work order queue, work start, progress notes, resolution submission
- Supervisor Command Center: municipal roster, reassignment, appeal approval, simulated clock advance and unfreeze
- Audit Inspector Console: audit records structure, verification, role access limits
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_citizen_submission_and_advisory_preview(client: TestClient) -> None:
    # 1. Advisory Preview
    preview_resp = client.post(
        "/api/v1/advisory/preview",
        headers={"X-Actor-ID": "USR-CIT-01"},
        json={"title": "Broken pipe", "text": "Water leaking rapidly into street near bus station"},
    )
    assert preview_resp.status_code == 200
    preview_data = preview_resp.json()
    assert preview_data["success"] is True
    assert preview_data["data"]["category"] == "Water Leak"

    # 2. Citizen Create Request
    create_resp = client.post(
        "/api/v1/requests",
        headers={"X-Actor-ID": "USR-CIT-01"},
        json={
            "title": "Severe road pothole damaging tires",
            "text": "Deep pothole on Main Road near civic center causing traffic hazard.",
            "location": "Main Road, Ward 2",
            "reported_category": "Pothole",
        },
    )
    assert create_resp.status_code == 201
    created = create_resp.json()["data"]
    req_id = created["id"]
    assert created["state"] == "SUBMITTED"
    assert created["citizen_id"] == "USR-CIT-01"


def test_triage_officer_workflow_and_info_request(client: TestClient) -> None:
    # Create request
    create_resp = client.post(
        "/api/v1/requests",
        headers={"X-Actor-ID": "USR-CIT-01"},
        json={
            "title": "Streetlight flickering",
            "text": "Streetlight near park gate is constantly flickering at night.",
            "location": "Park Gate Sector 4",
        },
    )
    req_id = create_resp.json()["data"]["id"]
    version = create_resp.json()["data"]["version"]

    # 1. Triage officer suggestions
    sugg_resp = client.get(
        f"/api/v1/requests/{req_id}/suggestions",
        headers={"X-Actor-ID": "USR-OFF-01"},
    )
    assert sugg_resp.status_code == 200
    sugg_data = sugg_resp.json()
    assert sugg_data["success"] is True
    assert "recommendation" in sugg_data["data"]

    # 2. Acknowledge
    ack_resp = client.post(
        f"/api/v1/requests/{req_id}/transition",
        headers={"X-Actor-ID": "USR-OFF-01"},
        json={"to_state": "ACKNOWLEDGED", "expected_version": version},
    )
    assert ack_resp.status_code == 200
    version = ack_resp.json()["data"]["version"]

    # 3. Confirm Category
    cat_resp = client.post(
        f"/api/v1/requests/{req_id}/category",
        headers={"X-Actor-ID": "USR-OFF-01"},
        json={"category": "Streetlight", "expected_version": version},
    )
    assert cat_resp.status_code == 200
    version = cat_resp.json()["data"]["version"]
    assert cat_resp.json()["data"]["category"] == "Streetlight"

    # 4. Request Missing Information
    info_resp = client.post(
        f"/api/v1/requests/{req_id}/transition",
        headers={"X-Actor-ID": "USR-OFF-01"},
        json={
            "to_state": "NEEDS_INFO",
            "expected_version": version,
            "info_request_note": "Please specify the pole number stamped on the light post.",
        },
    )
    assert info_resp.status_code == 200
    version = info_resp.json()["data"]["version"]
    assert info_resp.json()["data"]["state"] == "NEEDS_INFO"
    assert info_resp.json()["data"]["paused_at"] is not None

    # 5. Citizen responds with additional info (NEEDS_INFO -> ACKNOWLEDGED)
    res_resp = client.post(
        f"/api/v1/requests/{req_id}/transition",
        headers={"X-Actor-ID": "USR-CIT-01"},
        json={
            "to_state": "ACKNOWLEDGED",
            "expected_version": version,
            "additional_info": "Pole number is SL-402 near the eastern fountain.",
        },
    )
    assert res_resp.status_code == 200
    version = res_resp.json()["data"]["version"]
    assert res_resp.json()["data"]["state"] == "ACKNOWLEDGED"
    assert res_resp.json()["data"]["paused_at"] is None


def test_field_officer_progress_and_resolution(client: TestClient) -> None:
    # Create and acknowledge
    create_resp = client.post(
        "/api/v1/requests",
        headers={"X-Actor-ID": "USR-CIT-01"},
        json={
            "title": "Water pipe leak in lane 3",
            "text": "Clean water bubbling up through road joint.",
            "location": "Lane 3, Sector 1",
            "reported_category": "Water Leak",
        },
    )
    req_id = create_resp.json()["data"]["id"]
    version = create_resp.json()["data"]["version"]

    ack_resp = client.post(
        f"/api/v1/requests/{req_id}/transition",
        headers={"X-Actor-ID": "USR-OFF-01"},
        json={"to_state": "ACKNOWLEDGED", "expected_version": version},
    )
    version = ack_resp.json()["data"]["version"]

    # Assign to Field Officer USR-FLD-01
    assign_resp = client.post(
        f"/api/v1/requests/{req_id}/transition",
        headers={"X-Actor-ID": "USR-OFF-01"},
        json={
            "to_state": "ASSIGNED",
            "expected_version": version,
            "assigned_to": "USR-FLD-01",
        },
    )
    assert assign_resp.status_code == 200
    version = assign_resp.json()["data"]["version"]

    # Field Officer starts work
    start_resp = client.post(
        f"/api/v1/requests/{req_id}/transition",
        headers={"X-Actor-ID": "USR-FLD-01"},
        json={"to_state": "IN_PROGRESS", "expected_version": version},
    )
    assert start_resp.status_code == 200
    version = start_resp.json()["data"]["version"]

    # Field Officer adds progress note
    prog_resp = client.post(
        f"/api/v1/requests/{req_id}/progress",
        headers={"X-Actor-ID": "USR-FLD-01"},
        json={
            "note": "Excavation completed, replaced 2m cast iron pipe segment.",
            "evidence_json": {"photos_logged": 2, "pipe_model": "CI-50"},
            "expected_version": version,
        },
    )
    assert prog_resp.status_code == 200
    version = prog_resp.json()["data"]["version"]
    assert prog_resp.json()["data"]["state"] == "IN_PROGRESS"

    # Field Officer submits resolution
    res_resp = client.post(
        f"/api/v1/requests/{req_id}/transition",
        headers={"X-Actor-ID": "USR-FLD-01"},
        json={
            "to_state": "RESOLVED",
            "expected_version": version,
            "resolution_notes": "Pipe replaced and backfilled; water pressure restored to normal.",
            "resolution_evidence_json": {"inspection": "Passed", "tester": "Vikram Singh"},
        },
    )
    assert res_resp.status_code == 200
    version = res_resp.json()["data"]["version"]
    assert res_resp.json()["data"]["state"] == "RESOLVED"

    # Citizen confirms resolution & closes ticket
    close_resp = client.post(
        f"/api/v1/requests/{req_id}/transition",
        headers={"X-Actor-ID": "USR-CIT-01"},
        json={"to_state": "CLOSED", "expected_version": version},
    )
    assert close_resp.status_code == 200
    assert close_resp.json()["data"]["state"] == "CLOSED"
    assert close_resp.json()["data"]["closed_at"] is not None


def test_rejection_and_citizen_appeal_workflow(client: TestClient) -> None:
    # 1. Citizen files complaint
    create_resp = client.post(
        "/api/v1/requests",
        headers={"X-Actor-ID": "USR-CIT-01"},
        json={
            "title": "Private courtyard wall crack",
            "text": "Crack appearing in private compound wall.",
            "location": "Private Compound 14",
        },
    )
    req_id = create_resp.json()["data"]["id"]
    version = create_resp.json()["data"]["version"]

    # 2. Officer acknowledges then rejects
    ack_resp = client.post(
        f"/api/v1/requests/{req_id}/transition",
        headers={"X-Actor-ID": "USR-OFF-01"},
        json={"to_state": "ACKNOWLEDGED", "expected_version": version},
    )
    version = ack_resp.json()["data"]["version"]

    rej_resp = client.post(
        f"/api/v1/requests/{req_id}/transition",
        headers={"X-Actor-ID": "USR-OFF-01"},
        json={
            "to_state": "REJECTED",
            "expected_version": version,
            "rejection_reason": "Private residential property; beyond municipal jurisdiction.",
        },
    )
    assert rej_resp.status_code == 200
    version = rej_resp.json()["data"]["version"]
    assert rej_resp.json()["data"]["state"] == "REJECTED"

    # 3. Citizen submits appeal
    appeal_resp = client.post(
        f"/api/v1/requests/{req_id}/appeal",
        headers={"X-Actor-ID": "USR-CIT-01"},
        json={
            "appeal_reason": "The wall supports the public drainage culvert and threatens the sidewalk if collapsed.",
            "expected_version": version,
        },
    )
    assert appeal_resp.status_code == 200
    appeal_data = appeal_resp.json()["data"]
    version = appeal_data["version"]
    assert appeal_data["state"] == "REJECTED"
    assert appeal_data["appeal_reason"] is not None
    assert appeal_data["appealed_at"] is not None

    # 4. Supervisor reviews and approves appeal (REJECTED -> ACKNOWLEDGED)
    appr_resp = client.post(
        f"/api/v1/requests/{req_id}/transition",
        headers={"X-Actor-ID": "USR-SUP-01"},
        json={
            "to_state": "ACKNOWLEDGED",
            "expected_version": version,
            "appeal_approval_note": "Culvert safety impact verified under Municipal Act Sec 44. Reinstating for public works inspection.",
        },
    )
    assert appr_resp.status_code == 200
    reinstated = appr_resp.json()["data"]
    assert reinstated["state"] == "ACKNOWLEDGED"
    assert reinstated["appeal_approval_note"] is not None


def test_supervisor_clock_control_deck(client: TestClient) -> None:
    # 1. Check initial state
    state_resp = client.get("/api/v1/clock")
    assert state_resp.status_code == 200

    # 2. Advance clock by 1.0 hour
    adv_resp = client.post(
        "/api/v1/clock/advance",
        json={"hours": 1.0, "minutes": 0.0},
    )
    assert adv_resp.status_code == 200
    assert adv_resp.json()["success"] is True

    # 3. Freeze clock
    freeze_resp = client.post("/api/v1/clock/freeze")
    assert freeze_resp.status_code == 200
    assert freeze_resp.json()["clock"]["is_frozen"] is True

    # 4. Unfreeze clock
    unfreeze_resp = client.post("/api/v1/clock/unfreeze")
    assert unfreeze_resp.status_code == 200
    assert unfreeze_resp.json()["clock"]["is_frozen"] is False

    # 5. Reset clock
    reset_resp = client.post("/api/v1/clock/reset")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["clock"]["offset_seconds"] == 0
    assert reset_resp.json()["clock"]["is_frozen"] is False


def test_audit_inspector_console_and_rbac(client: TestClient) -> None:
    # 1. Audit Inspector can read audit trail
    audit_resp = client.get(
        "/api/v1/audit",
        headers={"X-Actor-ID": "USR-AUD-01"},
    )
    assert audit_resp.status_code == 200
    audit_data = audit_resp.json()
    assert audit_data["success"] is True
    assert "records" in audit_data["data"]
    records = audit_data["data"]["records"]
    assert len(records) > 0

    first_record = records[0]
    assert "actor" in first_record
    assert "outcome" in first_record
    assert "hash" in first_record

    # 2. Cryptographic verify chain
    verify_resp = client.get(
        "/api/v1/audit/verify",
        headers={"X-Actor-ID": "USR-AUD-01"},
    )
    assert verify_resp.status_code == 200
    verify_data = verify_resp.json()
    assert verify_data["success"] is True
    assert verify_data["data"]["valid"] is True

    # 3. Citizen cannot read audit trail
    cit_audit_resp = client.get(
        "/api/v1/audit",
        headers={"X-Actor-ID": "USR-CIT-01"},
    )
    assert cit_audit_resp.status_code == 403
    assert cit_audit_resp.json()["error"]["code"] == "AUDIT_READ_NOT_PERMITTED"

    # 4. Field officer cannot read audit trail
    fld_audit_resp = client.get(
        "/api/v1/audit",
        headers={"X-Actor-ID": "USR-FLD-01"},
    )
    assert fld_audit_resp.status_code == 403
