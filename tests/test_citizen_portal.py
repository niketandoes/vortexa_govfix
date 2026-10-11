"""Targeted tests for GovFix Citizen Portal requirements (Phase 2)."""

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


class CitizenPortalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = connect(":memory:")
        seed_database(self.connection, reference_time=REFERENCE_TIME)
        seed_extended_users(self.connection, reference_time=REFERENCE_TIME)
        self.clock = MutableClock(REFERENCE_TIME)
        self.service = WorkflowService(self.connection, now_provider=self.clock.now)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.connection.close()

    # 1. Citizen can submit a complaint with title, description, category, and location
    def test_01_citizen_can_submit_complaint(self) -> None:
        resp = self.client.post(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-CIT-01"},
            json={
                "title": "Severe Water Leak at Sector 5",
                "text": "Main water supply pipeline has burst and is flooding the road.",
                "location": "Sector 5, Near Water Tank",
                "reported_category": "Water Leak",
            },
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()["data"]
        self.assertTrue(data["id"].startswith("REQ-"))
        self.assertEqual(data["citizen_id"], "USR-CIT-01")
        self.assertEqual(data["title"], "Severe Water Leak at Sector 5")
        self.assertEqual(data["location"], "Sector 5, Near Water Tank")
        self.assertEqual(data["reported_category"], "Water Leak")
        self.assertEqual(data["state"], "SUBMITTED")
        self.assertEqual(data["version"], 1)

    # 2. Required fields are validated
    def test_02_required_fields_validated(self) -> None:
        # Missing title
        resp = self.client.post(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-CIT-01"},
            json={
                "text": "Description without a title.",
                "location": "Somewhere",
            },
        )
        self.assertEqual(resp.status_code, 422)

        # Missing text
        resp2 = self.client.post(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-CIT-01"},
            json={
                "title": "Title without description",
            },
        )
        self.assertEqual(resp2.status_code, 422)

    # 3. The created complaint persists and appears in the correct citizen's list
    def test_03_complaint_persists_in_citizen_list(self) -> None:
        create_resp = self.client.post(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-CIT-01"},
            json={
                "title": "Broken Streetlight on Park Street",
                "text": "The streetlight pole is dark causing safety issues.",
                "location": "Park Street, Pole 14",
            },
        )
        req_id = create_resp.json()["data"]["id"]

        list_resp = self.client.get(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-CIT-01"},
        )
        self.assertEqual(list_resp.status_code, 200)
        items = list_resp.json()["data"]
        ids = [item["id"] for item in items]
        self.assertIn(req_id, ids)

    # 4. A second citizen cannot view or mutate another citizen's complaint
    def test_04_citizen_isolation_enforced(self) -> None:
        created = self.service.create_request(
            actor_id="USR-CIT-01",
            command=RequestCreate(
                title="Pothole in Ward 2",
                text="Deep pothole causing vehicular damage.",
                location="Ward 2 Main Road",
            ),
        )
        req_id = created["id"]

        # Citizen 2 cannot read
        with self.assertRaises(WorkflowError) as caught:
            self.service.get_request(request_id=req_id, actor_id="USR-CIT-02")
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.code, "READ_NOT_PERMITTED")

        # Advance request to RESOLVED
        self.service.transition(
            request_id=req_id,
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.ACKNOWLEDGED, expected_version=1
            ),
        )
        self.service.transition(
            request_id=req_id,
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.ASSIGNED,
                expected_version=2,
                assigned_to="USR-FLD-01",
            ),
        )
        self.service.transition(
            request_id=req_id,
            actor_id="USR-FLD-01",
            command=TransitionCommand(
                to_state=RequestState.IN_PROGRESS, expected_version=3
            ),
        )
        self.service.transition(
            request_id=req_id,
            actor_id="USR-FLD-01",
            command=TransitionCommand(
                to_state=RequestState.RESOLVED,
                expected_version=4,
                resolution_notes="Fixed.",
                resolution_evidence_json={"photo": "fixed.jpg"},
            ),
        )

        # Citizen 2 cannot close Citizen 1's resolved complaint
        with self.assertRaises(WorkflowError) as caught2:
            self.service.transition(
                request_id=req_id,
                actor_id="USR-CIT-02",
                command=TransitionCommand(
                    to_state=RequestState.CLOSED,
                    expected_version=5,
                ),
            )
        self.assertEqual(caught2.exception.status_code, 403)
        self.assertEqual(caught2.exception.code, "OWNERSHIP_REQUIRED")

    # 5. Complaint details display actual persisted data and history
    def test_05_complaint_details_and_history(self) -> None:
        created = self.service.create_request(
            actor_id="USR-CIT-01",
            command=RequestCreate(
                title="Garbage Overflows",
                text="Community bin overflowing for 3 days.",
                location="Market Yard Gate 1",
                reported_category=Category.GARBAGE_COLLECTION,
            ),
        )
        req_id = created["id"]

        details = self.service.get_request(request_id=req_id, actor_id="USR-CIT-01")
        self.assertEqual(details["title"], "Garbage Overflows")
        self.assertEqual(details["reported_category"], "Garbage Collection")
        self.assertEqual(details["location"], "Market Yard Gate 1")

        # Check citizen-safe history
        history = self.service.get_request_history(
            request_id=req_id, actor_id="USR-CIT-01"
        )
        self.assertTrue(len(history) >= 1)
        self.assertEqual(history[0]["action"], "SUBMIT_COMPLAINT")
        self.assertEqual(history[0]["actor_role"], "Citizen")

    # 6. Status display handles every supported workflow state
    def test_06_status_transitions_all_states(self) -> None:
        created = self.service.create_request(
            actor_id="USR-CIT-01",
            command=RequestCreate(
                title="Workflow Lifecycle Test",
                text="Testing transitions across supported states.",
            ),
        )
        req_id = created["id"]

        # SUBMITTED -> ACKNOWLEDGED
        req = self.service.transition(
            request_id=req_id,
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.ACKNOWLEDGED, expected_version=1
            ),
        )
        self.assertEqual(req["state"], "ACKNOWLEDGED")

        # ACKNOWLEDGED -> NEEDS_INFO
        req = self.service.transition(
            request_id=req_id,
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.NEEDS_INFO,
                expected_version=2,
                info_request_note="Need house number",
            ),
        )
        self.assertEqual(req["state"], "NEEDS_INFO")

        # NEEDS_INFO -> ACKNOWLEDGED
        req = self.service.transition(
            request_id=req_id,
            actor_id="USR-CIT-01",
            command=TransitionCommand(
                to_state=RequestState.ACKNOWLEDGED,
                expected_version=3,
                additional_info="House #42 near temple",
            ),
        )
        self.assertEqual(req["state"], "ACKNOWLEDGED")

        # ACKNOWLEDGED -> ASSIGNED
        req = self.service.transition(
            request_id=req_id,
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.ASSIGNED,
                expected_version=4,
                assigned_to="USR-FLD-01",
            ),
        )
        self.assertEqual(req["state"], "ASSIGNED")

        # ASSIGNED -> IN_PROGRESS
        req = self.service.transition(
            request_id=req_id,
            actor_id="USR-FLD-01",
            command=TransitionCommand(
                to_state=RequestState.IN_PROGRESS, expected_version=5
            ),
        )
        self.assertEqual(req["state"], "IN_PROGRESS")

        # IN_PROGRESS -> RESOLVED
        req = self.service.transition(
            request_id=req_id,
            actor_id="USR-FLD-01",
            command=TransitionCommand(
                to_state=RequestState.RESOLVED,
                expected_version=6,
                resolution_notes="Repairs executed",
                resolution_evidence_json={"photo": "repaired.jpg"},
            ),
        )
        self.assertEqual(req["state"], "RESOLVED")

        # RESOLVED -> CLOSED
        req = self.service.transition(
            request_id=req_id,
            actor_id="USR-CIT-01",
            command=TransitionCommand(
                to_state=RequestState.CLOSED, expected_version=7
            ),
        )
        self.assertEqual(req["state"], "CLOSED")

    # 7. Citizen can submit missing information only in NEEDS_INFO
    def test_07_citizen_can_submit_missing_info_only_in_needs_info(self) -> None:
        created = self.service.create_request(
            actor_id="USR-CIT-01",
            command=RequestCreate(
                title="Streetlight Failure",
                text="Dark pole in lane 3.",
            ),
        )
        req_id = created["id"]

        # Attempt in SUBMITTED state -> fails with 403 ROLE_NOT_PERMITTED
        with self.assertRaises(WorkflowError) as caught:
            self.service.transition(
                request_id=req_id,
                actor_id="USR-CIT-01",
                command=TransitionCommand(
                    to_state=RequestState.ACKNOWLEDGED,
                    expected_version=1,
                    additional_info="Extra detail",
                ),
            )
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.code, "ROLE_NOT_PERMITTED")

        # Move to ACKNOWLEDGED then NEEDS_INFO
        self.service.transition(
            request_id=req_id,
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.ACKNOWLEDGED, expected_version=1
            ),
        )
        self.service.transition(
            request_id=req_id,
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.NEEDS_INFO,
                expected_version=2,
                info_request_note="Which pole number?",
            ),
        )

        # Submitting without additional_info fails
        with self.assertRaises(WorkflowError) as caught2:
            self.service.transition(
                request_id=req_id,
                actor_id="USR-CIT-01",
                command=TransitionCommand(
                    to_state=RequestState.ACKNOWLEDGED,
                    expected_version=3,
                    additional_info="",
                ),
            )
        self.assertEqual(caught2.exception.status_code, 422)

        # Submitting valid additional_info succeeds
        resumed = self.service.transition(
            request_id=req_id,
            actor_id="USR-CIT-01",
            command=TransitionCommand(
                to_state=RequestState.ACKNOWLEDGED,
                expected_version=3,
                additional_info="Pole #7 near community park",
            ),
        )
        self.assertEqual(resumed["state"], "ACKNOWLEDGED")
        self.assertEqual(
            resumed["additional_info"], "Pole #7 near community park"
        )
        self.assertIsNone(resumed["paused_at"])

    # 8. Citizen can confirm closure only in RESOLVED
    def test_08_citizen_can_confirm_closure_only_in_resolved(self) -> None:
        created = self.service.create_request(
            actor_id="USR-CIT-01",
            command=RequestCreate(
                title="Pothole Fix",
                text="Pothole repair needed urgently.",
            ),
        )
        req_id = created["id"]

        # In SUBMITTED, cannot close
        with self.assertRaises(WorkflowError) as caught:
            self.service.transition(
                request_id=req_id,
                actor_id="USR-CIT-01",
                command=TransitionCommand(
                    to_state=RequestState.CLOSED, expected_version=1
                ),
            )
        self.assertEqual(caught.exception.status_code, 422)

        # Advance to RESOLVED
        self.service.transition(
            request_id=req_id,
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.ACKNOWLEDGED, expected_version=1
            ),
        )
        self.service.transition(
            request_id=req_id,
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.ASSIGNED,
                expected_version=2,
                assigned_to="USR-FLD-01",
            ),
        )
        self.service.transition(
            request_id=req_id,
            actor_id="USR-FLD-01",
            command=TransitionCommand(
                to_state=RequestState.IN_PROGRESS, expected_version=3
            ),
        )
        self.service.transition(
            request_id=req_id,
            actor_id="USR-FLD-01",
            command=TransitionCommand(
                to_state=RequestState.RESOLVED,
                expected_version=4,
                resolution_notes="Asphalt patch completed.",
                resolution_evidence_json={"photo": "asphalt.jpg"},
            ),
        )

        # In RESOLVED, citizen closes
        closed = self.service.transition(
            request_id=req_id,
            actor_id="USR-CIT-01",
            command=TransitionCommand(
                to_state=RequestState.CLOSED, expected_version=5
            ),
        )
        self.assertEqual(closed["state"], "CLOSED")

    # 9. Resolution disputes require a reason and use the valid transition
    def test_09_resolution_dispute_requires_reason(self) -> None:
        created = self.service.create_request(
            actor_id="USR-CIT-01",
            command=RequestCreate(
                title="Garbage Pile Up",
                text="Overflowing bin on street.",
            ),
        )
        req_id = created["id"]
        self.service.transition(
            request_id=req_id,
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.ACKNOWLEDGED, expected_version=1
            ),
        )
        self.service.transition(
            request_id=req_id,
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.ASSIGNED,
                expected_version=2,
                assigned_to="USR-FLD-02",
            ),
        )
        self.service.transition(
            request_id=req_id,
            actor_id="USR-FLD-02",
            command=TransitionCommand(
                to_state=RequestState.IN_PROGRESS, expected_version=3
            ),
        )
        self.service.transition(
            request_id=req_id,
            actor_id="USR-FLD-02",
            command=TransitionCommand(
                to_state=RequestState.RESOLVED,
                expected_version=4,
                resolution_notes="Bin emptied.",
                resolution_evidence_json={"photo": "bin_empty.jpg"},
            ),
        )

        # Attempt dispute without reopen_reason -> fails
        with self.assertRaises(WorkflowError) as caught:
            self.service.transition(
                request_id=req_id,
                actor_id="USR-CIT-01",
                command=TransitionCommand(
                    to_state=RequestState.REOPENED,
                    expected_version=5,
                    reopen_reason="",
                ),
            )
        self.assertEqual(caught.exception.status_code, 422)

        # Valid dispute with reason
        reopened = self.service.transition(
            request_id=req_id,
            actor_id="USR-CIT-01",
            command=TransitionCommand(
                to_state=RequestState.REOPENED,
                expected_version=5,
                reopen_reason="Only half the waste was collected.",
            ),
        )
        self.assertEqual(reopened["state"], "REOPENED")
        self.assertEqual(reopened["reopen_count"], 1)

    # 10. Closed complaints can only be reopened within the permitted 7-day window
    def test_10_closed_complaint_reopen_within_and_outside_7_days(self) -> None:
        created = self.service.create_request(
            actor_id="USR-CIT-01",
            command=RequestCreate(
                title="Water Leakage",
                text="Pipe burst.",
            ),
        )
        req_id = created["id"]
        self.service.transition(
            request_id=req_id,
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.ACKNOWLEDGED, expected_version=1
            ),
        )
        self.service.transition(
            request_id=req_id,
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.ASSIGNED,
                expected_version=2,
                assigned_to="USR-FLD-01",
            ),
        )
        self.service.transition(
            request_id=req_id,
            actor_id="USR-FLD-01",
            command=TransitionCommand(
                to_state=RequestState.IN_PROGRESS, expected_version=3
            ),
        )
        self.service.transition(
            request_id=req_id,
            actor_id="USR-FLD-01",
            command=TransitionCommand(
                to_state=RequestState.RESOLVED,
                expected_version=4,
                resolution_notes="Pipe welded.",
                resolution_evidence_json={"photo": "weld.jpg"},
            ),
        )
        closed = self.service.transition(
            request_id=req_id,
            actor_id="USR-CIT-01",
            command=TransitionCommand(
                to_state=RequestState.CLOSED, expected_version=5
            ),
        )
        self.assertEqual(closed["state"], "CLOSED")

        # 3 days later (within 7-day window) -> permitted
        self.clock.advance(days=3)
        reopened = self.service.transition(
            request_id=req_id,
            actor_id="USR-CIT-01",
            command=TransitionCommand(
                to_state=RequestState.REOPENED,
                expected_version=6,
                reopen_reason="Weld cracked again 3 days later.",
            ),
        )
        self.assertEqual(reopened["state"], "REOPENED")

        # Now re-close it: Supervisor reassigns REOPENED -> ASSIGNED
        self.service.transition(
            request_id=req_id,
            actor_id="USR-SUP-01",
            command=TransitionCommand(
                to_state=RequestState.ASSIGNED,
                expected_version=7,
                assigned_to="USR-FLD-01",
            ),
        )
        self.service.transition(
            request_id=req_id,
            actor_id="USR-FLD-01",
            command=TransitionCommand(
                to_state=RequestState.IN_PROGRESS, expected_version=8
            ),
        )
        self.service.transition(
            request_id=req_id,
            actor_id="USR-FLD-01",
            command=TransitionCommand(
                to_state=RequestState.RESOLVED,
                expected_version=9,
                resolution_notes="Pipe replaced entirely.",
                resolution_evidence_json={"photo": "replacement.jpg"},
            ),
        )
        self.service.transition(
            request_id=req_id,
            actor_id="USR-CIT-01",
            command=TransitionCommand(
                to_state=RequestState.CLOSED, expected_version=10
            ),
        )

        # Advance clock by 8 days (> 7 days)
        self.clock.advance(days=8)

        # Reopen attempt should be refused
        with self.assertRaises(WorkflowError) as caught:
            self.service.transition(
                request_id=req_id,
                actor_id="USR-CIT-01",
                command=TransitionCommand(
                    to_state=RequestState.REOPENED,
                    expected_version=11,
                    reopen_reason="Late reopen request.",
                ),
            )
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(caught.exception.code, "REOPEN_WINDOW_EXPIRED")

    # 11. Invalid or unauthorized actions are refused by the backend and leave complaint unchanged
    def test_11_unauthorized_action_leaves_complaint_unchanged(self) -> None:
        created = self.service.create_request(
            actor_id="USR-CIT-01",
            command=RequestCreate(
                title="Test Complaint",
                text="Checking refusal atomicity.",
            ),
        )
        req_id = created["id"]

        with self.assertRaises(WorkflowError):
            self.service.transition(
                request_id=req_id,
                actor_id="USR-CIT-01",
                command=TransitionCommand(
                    to_state=RequestState.IN_PROGRESS, expected_version=1
                ),
            )

        # Verify complaint state unchanged
        fresh = self.service.get_request(
            request_id=req_id, actor_id="USR-CIT-01"
        )
        self.assertEqual(fresh["state"], "SUBMITTED")
        self.assertEqual(fresh["version"], 1)

    # 12. Refused actions are audited
    def test_12_refused_actions_are_audited(self) -> None:
        before_count = self.connection.execute(
            "SELECT COUNT(*) FROM audit WHERE outcome = 'REFUSED'"
        ).fetchone()[0]

        created = self.service.create_request(
            actor_id="USR-CIT-01",
            command=RequestCreate(
                title="Audit Test",
                text="Checking audit outcome.",
            ),
        )
        req_id = created["id"]

        with self.assertRaises(WorkflowError):
            self.service.transition(
                request_id=req_id,
                actor_id="USR-CIT-01",
                command=TransitionCommand(
                    to_state=RequestState.RESOLVED, expected_version=1
                ),
            )

        after_count = self.connection.execute(
            "SELECT COUNT(*) FROM audit WHERE outcome = 'REFUSED'"
        ).fetchone()[0]
        self.assertEqual(after_count, before_count + 1)

    # 13. API failures produce useful error feedback without fake success
    def test_13_api_failure_response_structure(self) -> None:
        resp = self.client.post(
            "/api/v1/requests/REQ-NONEXISTENT/transition",
            headers={"X-Actor-ID": "USR-CIT-01"},
            json={
                "to_state": "CLOSED",
                "expected_version": 1,
            },
        )
        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error"]["code"], "REQUEST_NOT_FOUND")

    # 14. SLA indicators reflect backend values and advisory preview works
    def test_14_sla_indicators_and_advisory_preview(self) -> None:
        # At creation, complaint is Uncategorised with 72h Triage SLA
        water_req = self.service.create_request(
            actor_id="USR-CIT-01",
            command=RequestCreate(
                title="Water Leakage",
                text="Leaking valve at junction.",
                reported_category=Category.WATER_LEAK,
            ),
        )
        start_time = datetime.fromisoformat(
            water_req["sla_started_at"].replace("Z", "+00:00")
        )
        initial_deadline = datetime.fromisoformat(
            water_req["deadline"].replace("Z", "+00:00")
        )
        self.assertEqual(initial_deadline - start_time, timedelta(hours=72))

        # Officer confirms category to Water Leak -> SLA recalculated to 24h
        confirmed = self.service.confirm_category(
            request_id=water_req["id"],
            actor_id="USR-OFF-01",
            command=CategoryCommand(
                category=Category.WATER_LEAK, expected_version=1
            ),
        )
        confirmed_deadline = datetime.fromisoformat(
            confirmed["deadline"].replace("Z", "+00:00")
        )
        self.assertEqual(confirmed_deadline - start_time, timedelta(hours=24))

        # Advisory preview endpoint
        preview_resp = self.client.post(
            "/api/v1/advisory/preview",
            headers={"X-Actor-ID": "USR-CIT-01"},
            json={
                "title": "Water Pipe Burst",
                "text": "Water pipeline burst near Central Market gate flooding road.",
            },
        )
        self.assertEqual(preview_resp.status_code, 200)
        pdata = preview_resp.json()["data"]
        self.assertIn("category", pdata)
        self.assertIn("urgency", pdata)
        self.assertIn("location", pdata)


if __name__ == "__main__":
    unittest.main()
