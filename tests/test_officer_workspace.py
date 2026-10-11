"""Targeted tests for GovFix Officer Workspace requirements (Phase 3)."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from src.database import connect
from src.main import app
from src.models import (
    Category,
    CategoryCommand,
    RequestCreate,
    RequestState,
    TransitionCommand,
)
from src.seed import seed_database, seed_extended_users
from src.workflow import WorkflowError, WorkflowService


REFERENCE_TIME = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value

    def advance(self, **kwargs) -> None:
        self.value += timedelta(**kwargs)


class OfficerWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = connect(":memory:")
        seed_database(self.connection, reference_time=REFERENCE_TIME)
        seed_extended_users(self.connection, reference_time=REFERENCE_TIME)
        self.clock = MutableClock(REFERENCE_TIME)
        self.service = WorkflowService(self.connection, now_provider=self.clock.now)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.connection.close()

    # 1. The Officer page renders and uses the correct seeded Officer identity
    def test_01_officer_page_renders_with_seeded_identity(self) -> None:
        resp = self.client.get("/officer")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("Municipal Grievance Triage", resp.text)
        self.assertIn("USR-OFF-01", resp.text)
        self.assertIn("Configured Triage SLA Target: 2 Hours", resp.text)
        self.assertIn("Intake Queue", resp.text)
        self.assertIn("OfficerWorkspace", resp.text)

    # 2. SUBMITTED complaints appear in the intake queue
    def test_02_submitted_complaints_appear_in_intake_queue(self) -> None:
        # Create a new complaint via API
        create_resp = self.client.post(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-CIT-01"},
            json={
                "title": "Flooded Roadway near Metro Gate 3",
                "text": "Water pipeline burst creating a large flood on the road.",
                "location": "Metro Gate 3, Ring Road",
                "reported_category": "Water Leak",
            },
        )
        self.assertEqual(create_resp.status_code, 201)
        req_id = create_resp.json()["data"]["id"]

        # Officer lists queue
        list_resp = self.client.get(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-OFF-01"},
        )
        self.assertEqual(list_resp.status_code, 200)
        items = list_resp.json()["data"]
        matching = [r for r in items if r["id"] == req_id]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["state"], "SUBMITTED")
        self.assertIsNotNone(matching[0]["triage_deadline"])

    # 3. Acknowledgement performs the correct backend transition
    def test_03_acknowledgement_performs_correct_backend_transition(self) -> None:
        create_resp = self.client.post(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-CIT-01"},
            json={
                "title": "Broken Streetlight on 4th Cross",
                "text": "Dark street dangerous at night.",
                "location": "4th Cross, Indiranagar",
                "reported_category": "Streetlight",
            },
        )
        req_id = create_resp.json()["data"]["id"]
        version = create_resp.json()["data"]["version"]

        # Officer acknowledges complaint
        ack_resp = self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={
                "to_state": "ACKNOWLEDGED",
                "expected_version": version,
            },
        )
        self.assertEqual(ack_resp.status_code, 200)
        data = ack_resp.json()["data"]
        self.assertEqual(data["state"], "ACKNOWLEDGED")
        self.assertEqual(data["version"], 2)

    # 4. Unassigned ACKNOWLEDGED complaints are available for assignment
    def test_04_unassigned_acknowledged_complaints_available_for_assignment(self) -> None:
        create_resp = self.client.post(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-CIT-01"},
            json={
                "title": "Uncollected Garbage Heap",
                "text": "Garbage has not been collected for 4 days.",
                "location": "Sector 2 Market",
                "reported_category": "Garbage Collection",
            },
        )
        req_id = create_resp.json()["data"]["id"]
        version = create_resp.json()["data"]["version"]

        self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={
                "to_state": "ACKNOWLEDGED",
                "expected_version": version,
            },
        )

        # Check filter by state=ACKNOWLEDGED
        filter_resp = self.client.get(
            "/api/v1/requests?state=ACKNOWLEDGED",
            headers={"X-Actor-ID": "USR-OFF-01"},
        )
        self.assertEqual(filter_resp.status_code, 200)
        matching = [r for r in filter_resp.json()["data"] if r["id"] == req_id]
        self.assertEqual(len(matching), 1)
        self.assertIsNone(matching[0]["assigned_to"])

    # 5. Assigning USR-FLD-01 makes complaint visible to that Field Officer and removes from unassigned queue
    def test_05_assigning_field_officer_makes_visible_and_removes_from_queue(self) -> None:
        create_resp = self.client.post(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-CIT-01"},
            json={
                "title": "Pothole on Main Arterial",
                "text": "Deep pothole causing vehicle damage.",
                "location": "Arterial Road 12",
                "reported_category": "Pothole",
            },
        )
        req_id = create_resp.json()["data"]["id"]

        # Acknowledge
        self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={
                "to_state": "ACKNOWLEDGED",
                "expected_version": 1,
            },
        )

        # Assign to USR-FLD-01
        assign_resp = self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={
                "to_state": "ASSIGNED",
                "assigned_to": "USR-FLD-01",
                "expected_version": 2,
            },
        )
        self.assertEqual(assign_resp.status_code, 200)
        data = assign_resp.json()["data"]
        self.assertEqual(data["state"], "ASSIGNED")
        self.assertEqual(data["assigned_to"], "USR-FLD-01")
        self.assertIsNotNone(data["triaged_at"])

        # Check Officer queue: assigned complaint is no longer in Officer's triage scope
        officer_queue = self.client.get(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-OFF-01"},
        ).json()["data"]
        officer_ids = [r["id"] for r in officer_queue]
        self.assertNotIn(req_id, officer_ids)

        # Check Field Officer USR-FLD-01 list: complaint IS visible
        fld1_list = self.client.get(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-FLD-01"},
        ).json()["data"]
        fld1_ids = [r["id"] for r in fld1_list]
        self.assertIn(req_id, fld1_ids)

    # 6. The Field Officer cannot access another Field Officer's assignments
    def test_06_field_officer_cannot_access_other_field_officer_assignments(self) -> None:
        # Create and assign to USR-FLD-01
        create_resp = self.client.post(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-CIT-01"},
            json={
                "title": "Water Leak on 2nd Ave",
                "text": "Water valve leak.",
                "location": "2nd Ave",
                "reported_category": "Water Leak",
            },
        )
        req_id = create_resp.json()["data"]["id"]
        self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={"to_state": "ACKNOWLEDGED", "expected_version": 1},
        )
        self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={"to_state": "ASSIGNED", "assigned_to": "USR-FLD-01", "expected_version": 2},
        )

        # USR-FLD-02 listing: should NOT include req_id
        fld2_list = self.client.get(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-FLD-02"},
        ).json()["data"]
        self.assertNotIn(req_id, [r["id"] for r in fld2_list])

        # USR-FLD-02 direct read: should fail with 403 Forbidden
        direct_resp = self.client.get(
            f"/api/v1/requests/{req_id}",
            headers={"X-Actor-ID": "USR-FLD-02"},
        )
        self.assertEqual(direct_resp.status_code, 403)

    # 7. Missing-information requests require a non-empty note
    def test_07_missing_information_requests_require_non_empty_note(self) -> None:
        create_resp = self.client.post(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-CIT-01"},
            json={
                "title": "Vague Complaint",
                "text": "Something is wrong on the street.",
                "location": "Somewhere",
                "reported_category": "Streetlight",
            },
        )
        req_id = create_resp.json()["data"]["id"]
        self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={"to_state": "ACKNOWLEDGED", "expected_version": 1},
        )

        # Empty note attempt
        fail_resp = self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={
                "to_state": "NEEDS_INFO",
                "expected_version": 2,
                "info_request_note": "",
            },
        )
        self.assertEqual(fail_resp.status_code, 422)

        # Valid note attempt
        success_resp = self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={
                "to_state": "NEEDS_INFO",
                "expected_version": 2,
                "info_request_note": "Please specify exact pole number or landmark.",
            },
        )
        self.assertEqual(success_resp.status_code, 200)
        data = success_resp.json()["data"]
        self.assertEqual(data["state"], "NEEDS_INFO")
        self.assertEqual(data["info_request_note"], "Please specify exact pole number or landmark.")
        self.assertIsNotNone(data["paused_at"])

    # 8. Rejection requires a reason when the action is permitted
    def test_08_rejection_requires_reason(self) -> None:
        create_resp = self.client.post(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-CIT-01"},
            json={
                "title": "Private Garden Drainage",
                "text": "Private garden pipe leaking.",
                "location": "Private Villa #5",
                "reported_category": "Water Leak",
            },
        )
        req_id = create_resp.json()["data"]["id"]
        self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={"to_state": "ACKNOWLEDGED", "expected_version": 1},
        )

        # Missing reason
        fail_resp = self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={
                "to_state": "REJECTED",
                "expected_version": 2,
                "rejection_reason": "",
            },
        )
        self.assertEqual(fail_resp.status_code, 422)

        # Valid reason
        rej_resp = self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={
                "to_state": "REJECTED",
                "expected_version": 2,
                "rejection_reason": "Private property maintenance not covered by municipal works.",
            },
        )
        self.assertEqual(rej_resp.status_code, 200)
        data = rej_resp.json()["data"]
        self.assertEqual(data["state"], "REJECTED")
        self.assertEqual(
            data["rejection_reason"],
            "Private property maintenance not covered by municipal works.",
        )

    # 9. Category confirmation uses backend and current request version
    def test_09_category_confirmation_uses_backend_and_version(self) -> None:
        create_resp = self.client.post(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-CIT-01"},
            json={
                "title": "Street Cleaning Needed",
                "text": "Pothole filled with rubbish.",
                "location": "Main Square",
                "reported_category": "Pothole",
            },
        )
        req_id = create_resp.json()["data"]["id"]
        version = create_resp.json()["data"]["version"]

        # Confirm category to Garbage Collection
        cat_resp = self.client.post(
            f"/api/v1/requests/{req_id}/category",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={
                "category": "Garbage Collection",
                "expected_version": version,
            },
        )
        self.assertEqual(cat_resp.status_code, 200)
        data = cat_resp.json()["data"]
        self.assertEqual(data["category"], "Garbage Collection")
        self.assertEqual(data["version"], version + 1)

    # 10. Invalid or unauthorized transitions are refused and audited
    def test_10_invalid_or_unauthorized_transitions_refused_and_audited(self) -> None:
        create_resp = self.client.post(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-CIT-01"},
            json={
                "title": "Water Pipe Issue",
                "text": "Water valve leak.",
                "location": "Sector 9",
                "reported_category": "Water Leak",
            },
        )
        req_id = create_resp.json()["data"]["id"]

        # Attempt illegal direct transition SUBMITTED -> ASSIGNED without ACKNOWLEDGED
        illegal_resp = self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={
                "to_state": "ASSIGNED",
                "assigned_to": "USR-FLD-01",
                "expected_version": 1,
            },
        )
        self.assertEqual(illegal_resp.status_code, 422)
        err = illegal_resp.json()["error"]
        self.assertEqual(err["code"], "INVALID_TRANSITION")
        self.assertTrue(err["audit_logged"])

        # Attempt to assign to non-Field Officer (e.g. self USR-OFF-01)
        self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={"to_state": "ACKNOWLEDGED", "expected_version": 1},
        )
        self_assign_resp = self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={
                "to_state": "ASSIGNED",
                "assigned_to": "USR-OFF-01",
                "expected_version": 2,
            },
        )
        self.assertEqual(self_assign_resp.status_code, 422)
        self.assertEqual(self_assign_resp.json()["error"]["code"], "INVALID_ASSIGNEE")

    # 11. A stale request version produces appropriate conflict feedback
    def test_11_stale_request_version_produces_conflict(self) -> None:
        create_resp = self.client.post(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-CIT-01"},
            json={
                "title": "Concurrency Test Complaint",
                "text": "Testing optimistic concurrency lock.",
                "location": "Tech Park",
                "reported_category": "Streetlight",
            },
        )
        req_id = create_resp.json()["data"]["id"]

        # Advance to version 2
        self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={"to_state": "ACKNOWLEDGED", "expected_version": 1},
        )

        # Send stale version 1
        stale_resp = self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={
                "to_state": "ASSIGNED",
                "assigned_to": "USR-FLD-01",
                "expected_version": 1,
            },
        )
        self.assertEqual(stale_resp.status_code, 409)
        self.assertEqual(stale_resp.json()["error"]["code"], "VERSION_CONFLICT")

    # 12. API failures never produce false success or fabricated complaints
    def test_12_api_failures_never_produce_false_success(self) -> None:
        # Non-existent complaint
        resp = self.client.get(
            "/api/v1/requests/REQ-NONEXISTENT",
            headers={"X-Actor-ID": "USR-OFF-01"},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["success"], False)

        # Invalid actor
        resp2 = self.client.get(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-INVALID-999"},
        )
        self.assertEqual(resp2.status_code, 401)
        self.assertEqual(resp2.json()["success"], False)

    # 13. The Officer cannot resolve or close a complaint through UI or by bypassing UI
    def test_13_officer_cannot_resolve_or_close_complaint(self) -> None:
        create_resp = self.client.post(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-CIT-01"},
            json={
                "title": "Resolution Guardrail Test",
                "text": "Officer should never resolve or close.",
                "location": "Indiranagar",
                "reported_category": "Water Leak",
            },
        )
        req_id = create_resp.json()["data"]["id"]
        self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={"to_state": "ACKNOWLEDGED", "expected_version": 1},
        )
        self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={"to_state": "ASSIGNED", "assigned_to": "USR-FLD-01", "expected_version": 2},
        )

        # Officer attempts RESOLVED
        res_resp = self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={"to_state": "RESOLVED", "expected_version": 3},
        )
        self.assertIn(res_resp.status_code, (403, 422))

        # Officer attempts CLOSED
        close_resp = self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={"to_state": "CLOSED", "expected_version": 3},
        )
        self.assertIn(close_resp.status_code, (403, 422))

    # 14. AI recommendations remain advisory and do not mutate workflow fields autonomously
    def test_14_ai_recommendations_remain_advisory(self) -> None:
        create_resp = self.client.post(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-CIT-01"},
            json={
                "title": "Water Pipe Bursting",
                "text": "Massive high pressure water flood on road.",
                "location": "MG Road Corner",
                "reported_category": "Water Leak",
            },
        )
        req_id = create_resp.json()["data"]["id"]

        # Call AI suggestions endpoint
        sugg_resp = self.client.get(
            f"/api/v1/requests/{req_id}/suggestions",
            headers={"X-Actor-ID": "USR-OFF-01"},
        )
        self.assertEqual(sugg_resp.status_code, 200)
        data = sugg_resp.json()["data"]
        self.assertIn("recommendation", data)
        rec = data["recommendation"]
        self.assertEqual(rec["category"], "Water Leak")
        self.assertGreater(rec["confidence"], 0.0)
        self.assertIsNotNone(rec["draft_reply"])

        # Verify complaint state in DB did NOT mutate
        check_resp = self.client.get(
            f"/api/v1/requests/{req_id}",
            headers={"X-Actor-ID": "USR-OFF-01"},
        )
        complaint = check_resp.json()["data"]
        self.assertEqual(complaint["state"], "SUBMITTED")
        self.assertEqual(complaint["version"], 1)
        self.assertIsNone(complaint["assigned_to"])
