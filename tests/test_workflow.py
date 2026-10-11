from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.database import connect
from src.models import (
    AppealCommand,
    Category,
    CategoryCommand,
    ProgressCommand,
    ReassignCommand,
    RequestCreate,
    RequestState,
    TransitionCommand,
)
from src.seed import seed_database
from src.workflow import WorkflowError, WorkflowService


REFERENCE_TIME = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = connect(":memory:")
        seed_database(self.connection, reference_time=REFERENCE_TIME)
        self.clock = MutableClock(REFERENCE_TIME)
        self.service = WorkflowService(
            self.connection, now_provider=self.clock.now
        )

    def tearDown(self) -> None:
        self.connection.close()

    def audit_count(self) -> int:
        return self.connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0]

    def assert_workflow_error(
        self, expected_status: int, callback, expected_code: str | None = None
    ) -> WorkflowError:
        before = self.audit_count()
        with self.assertRaises(WorkflowError) as caught:
            callback()
        error = caught.exception
        self.assertEqual(error.status_code, expected_status)
        self.assertTrue(error.audit_logged)
        self.assertEqual(self.audit_count(), before + 1)
        if expected_code:
            self.assertEqual(error.code, expected_code)
        return error

    def test_complete_happy_path(self) -> None:
        request = self.service.transition(
            request_id="REQ-2026-0001",
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.ACKNOWLEDGED, expected_version=1
            ),
        )
        self.assertEqual(request["state"], "ACKNOWLEDGED")
        request = self.service.transition(
            request_id=request["id"],
            actor_id="USR-SUP-01",
            command=TransitionCommand(
                to_state=RequestState.ASSIGNED,
                expected_version=2,
                assigned_to="USR-FLD-01",
            ),
        )
        self.assertEqual(request["state"], "ASSIGNED")
        request = self.service.transition(
            request_id=request["id"],
            actor_id="USR-FLD-01",
            command=TransitionCommand(
                to_state=RequestState.IN_PROGRESS, expected_version=3
            ),
        )
        self.assertEqual(request["state"], "IN_PROGRESS")
        request = self.service.transition(
            request_id=request["id"],
            actor_id="USR-FLD-01",
            command=TransitionCommand(
                to_state=RequestState.RESOLVED,
                expected_version=4,
                resolution_notes="Replaced the damaged pipe section.",
                resolution_evidence_json={"work_order": "WO-9912", "photos": ["evidence://pipe-fixed.jpg"]},
            ),
        )
        self.assertEqual(request["state"], "RESOLVED")
        request = self.service.transition(
            request_id=request["id"],
            actor_id="USR-CIT-01",
            command=TransitionCommand(
                to_state=RequestState.CLOSED, expected_version=5
            ),
        )
        self.assertEqual(request["state"], "CLOSED")
        self.assertEqual(request["version"], 6)
        accepted = self.connection.execute(
            """
            SELECT COUNT(*) FROM audit
            WHERE request_id = 'REQ-2026-0001'
              AND action = 'TRANSITION' AND outcome = 'ACCEPTED'
            """
        ).fetchone()[0]
        self.assertEqual(accepted, 5)

    def test_reopen_starts_fresh_sla_cycle(self) -> None:
        self.clock.value = REFERENCE_TIME + timedelta(days=2)
        reopened = self.service.transition(
            request_id="REQ-2026-0005",
            actor_id="USR-CIT-01",
            command=TransitionCommand(
                to_state=RequestState.REOPENED,
                expected_version=4,
                reopen_reason="The repair deteriorated after rain.",
            ),
        )
        self.assertEqual(reopened["sla_cycle"], 2)
        self.assertEqual(reopened["reopen_count"], 1)
        self.assertEqual(reopened["escalation_level"], 0)
        self.assertEqual(reopened["sla_started_at"], "2026-10-13T12:00:00.000000Z")
        self.assertEqual(reopened["deadline"], "2026-10-16T12:00:00.000000Z")
        audit = self.connection.execute(
            "SELECT details_json FROM audit ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(
            json.loads(audit["details_json"])["reopen_reason"],
            "The repair deteriorated after rain.",
        )
        assigned = self.service.transition(
            request_id="REQ-2026-0005",
            actor_id="USR-SUP-01",
            command=TransitionCommand(
                to_state=RequestState.ASSIGNED,
                expected_version=5,
                assigned_to="USR-FLD-01",
            ),
        )
        self.assertEqual(assigned["state"], "ASSIGNED")
        in_progress = self.service.transition(
            request_id="REQ-2026-0005",
            actor_id="USR-FLD-01",
            command=TransitionCommand(
                to_state=RequestState.IN_PROGRESS, expected_version=6
            ),
        )
        resolved = self.service.transition(
            request_id="REQ-2026-0005",
            actor_id="USR-FLD-01",
            command=TransitionCommand(
                to_state=RequestState.RESOLVED,
                expected_version=7,
                resolution_notes="Re-leveled and sealed properly.",
                resolution_evidence_json={"work_order": "WO-9913", "photos": ["evidence://pothole-sealed.jpg"]},
            ),
        )
        self.assertEqual(in_progress["state"], "IN_PROGRESS")
        self.assertEqual((resolved["state"], resolved["version"]), ("RESOLVED", 8))

    def test_skipped_state_is_422_and_logged(self) -> None:
        error = self.assert_workflow_error(
            422,
            lambda: self.service.transition(
                request_id="REQ-2026-0003",
                actor_id="USR-FLD-01",
                command=TransitionCommand(
                    to_state=RequestState.RESOLVED,
                    expected_version=2,
                    resolution_notes="Attempted early resolution.",
                    resolution_evidence_json={"work_order": "WO-1"},
                ),
            ),
            "INVALID_TRANSITION",
        )
        self.assertEqual(error.permitted_next_states, ["IN_PROGRESS"])

    def test_valid_edge_by_wrong_role_is_403(self) -> None:
        self.assert_workflow_error(
            403,
            lambda: self.service.transition(
                request_id="REQ-2026-0003",
                actor_id="USR-CIT-01",
                command=TransitionCommand(
                    to_state=RequestState.IN_PROGRESS, expected_version=2
                ),
            ),
            "ROLE_NOT_PERMITTED",
        )

    def test_non_assigned_officer_is_refused(self) -> None:
        self.assert_workflow_error(
            403,
            lambda: self.service.transition(
                request_id="REQ-2026-0003",
                actor_id="USR-FLD-02",
                command=TransitionCommand(
                    to_state=RequestState.IN_PROGRESS, expected_version=2
                ),
            ),
            "ASSIGNMENT_REQUIRED",
        )

    def test_required_payload_is_enforced(self) -> None:
        self.service.transition(
            request_id="REQ-2026-0001",
            actor_id="USR-SUP-01",
            command=TransitionCommand(
                to_state=RequestState.ACKNOWLEDGED, expected_version=1
            ),
        )
        self.assert_workflow_error(
            422,
            lambda: self.service.transition(
                request_id="REQ-2026-0001",
                actor_id="USR-SUP-01",
                command=TransitionCommand(
                    to_state=RequestState.REJECTED, expected_version=2
                ),
            ),
            "MISSING_REQUIRED_FIELD",
        )

    def test_resolution_notes_and_evidence_are_required(self) -> None:
        # Missing resolution notes
        self.assert_workflow_error(
            422,
            lambda: self.service.transition(
                request_id="REQ-2026-0004",
                actor_id="USR-FLD-01",
                command=TransitionCommand(
                    to_state=RequestState.RESOLVED,
                    expected_version=3,
                    resolution_evidence_json={"photos": ["done.jpg"]},
                ),
            ),
            "MISSING_REQUIRED_FIELD",
        )
        # Missing resolution evidence
        self.assert_workflow_error(
            422,
            lambda: self.service.transition(
                request_id="REQ-2026-0004",
                actor_id="USR-FLD-01",
                command=TransitionCommand(
                    to_state=RequestState.RESOLVED,
                    expected_version=3,
                    resolution_notes="Bulb replaced.",
                ),
            ),
            "MISSING_REQUIRED_FIELD",
        )

    def test_citizen_cannot_close_another_citizens_request(self) -> None:
        self.assert_workflow_error(
            403,
            lambda: self.service.transition(
                request_id="REQ-2026-0005",
                actor_id="USR-CIT-02",
                command=TransitionCommand(
                    to_state=RequestState.CLOSED, expected_version=4
                ),
            ),
            "OWNERSHIP_REQUIRED",
        )

    def test_assignment_requires_field_officer_and_assigned_to(self) -> None:
        self.service.transition(
            request_id="REQ-2026-0001",
            actor_id="USR-SUP-01",
            command=TransitionCommand(
                to_state=RequestState.ACKNOWLEDGED, expected_version=1
            ),
        )
        self.assert_workflow_error(
            422,
            lambda: self.service.transition(
                request_id="REQ-2026-0001",
                actor_id="USR-SUP-01",
                command=TransitionCommand(
                    to_state=RequestState.ASSIGNED, expected_version=2
                ),
            ),
            "MISSING_REQUIRED_FIELD",
        )
        self.assert_workflow_error(
            422,
            lambda: self.service.transition(
                request_id="REQ-2026-0001",
                actor_id="USR-SUP-01",
                command=TransitionCommand(
                    to_state=RequestState.ASSIGNED,
                    assigned_to="USR-OFF-01",
                    expected_version=2,
                ),
            ),
            "INVALID_ASSIGNEE",
        )

    def test_stale_version_is_409_and_does_not_mutate(self) -> None:
        self.assert_workflow_error(
            409,
            lambda: self.service.transition(
                request_id="REQ-2026-0001",
                actor_id="USR-OFF-01",
                command=TransitionCommand(
                    to_state=RequestState.ACKNOWLEDGED, expected_version=99
                ),
            ),
            "VERSION_CONFLICT",
        )
        request = self.connection.execute(
            "SELECT state, version FROM requests WHERE id = 'REQ-2026-0001'"
        ).fetchone()
        self.assertEqual((request["state"], request["version"]), ("SUBMITTED", 1))

    def test_category_confirmation_recalculates_deadline(self) -> None:
        request = self.service.confirm_category(
            request_id="REQ-2026-0001",
            actor_id="USR-OFF-01",
            command=CategoryCommand(
                category=Category.WATER_LEAK, expected_version=1
            ),
        )
        self.assertEqual(request["category"], "Water Leak")
        self.assertEqual(request["deadline"], "2026-10-12T12:00:00.000000Z")
        self.assertEqual(request["version"], 2)
        details = json.loads(
            self.connection.execute(
                "SELECT details_json FROM audit ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
        )
        self.assertEqual(details["old_category"], "Uncategorised")
        self.assertEqual(details["new_category"], "Water Leak")

    def test_category_confirmation_is_refused_for_citizen(self) -> None:
        self.assert_workflow_error(
            403,
            lambda: self.service.confirm_category(
                request_id="REQ-2026-0001",
                actor_id="USR-CIT-01",
                command=CategoryCommand(
                    category=Category.WATER_LEAK, expected_version=1
                ),
            ),
            "ROLE_NOT_PERMITTED",
        )

    def test_supervisor_can_reassign_without_changing_state(self) -> None:
        request = self.service.reassign(
            request_id="REQ-2026-0003",
            actor_id="USR-SUP-01",
            command=ReassignCommand(
                assigned_to="USR-FLD-02",
                reason="Balance the active workload.",
                expected_version=2,
            ),
        )
        self.assertEqual(request["assigned_to"], "USR-FLD-02")
        self.assertEqual(request["state"], "ASSIGNED")
        self.assertEqual(request["version"], 3)

    def test_create_request_uses_uncategorised_as_authoritative(self) -> None:
        request = self.service.create_request(
            actor_id="USR-CIT-02",
            command=RequestCreate(
                title="Missed collection",
                text="Garbage was not collected on Lake Road.",
                location="Lake Road, Sector 3",
                reported_category=Category.GARBAGE_COLLECTION,
            ),
        )
        self.assertEqual(request["reported_category"], "Garbage Collection")
        self.assertEqual(request["category"], "Uncategorised")
        self.assertEqual(request["state"], "SUBMITTED")
        self.assertEqual(request["location"], "Lake Road, Sector 3")
        self.assertEqual(request["version"], 1)

    def test_unknown_actor_refusal_is_logged(self) -> None:
        self.assert_workflow_error(
            401,
            lambda: self.service.transition(
                request_id="REQ-2026-0001",
                actor_id="USR-NOT-FOUND",
                command=TransitionCommand(
                    to_state=RequestState.ACKNOWLEDGED, expected_version=1
                ),
            ),
            "ACTOR_NOT_FOUND",
        )
        row = self.connection.execute(
            "SELECT actor, actor_role FROM audit ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual((row["actor"], row["actor_role"]), ("USR-NOT-FOUND", "System"))

    def test_request_not_found_refusal_is_logged(self) -> None:
        self.assert_workflow_error(
            404,
            lambda: self.service.transition(
                request_id="REQ-MISSING",
                actor_id="USR-SUP-01",
                command=TransitionCommand(
                    to_state=RequestState.ACKNOWLEDGED, expected_version=1
                ),
            ),
            "REQUEST_NOT_FOUND",
        )

    def test_read_scopes_cannot_be_expanded_by_filters(self) -> None:
        citizen_rows = self.service.list_requests(actor_id="USR-CIT-01")
        self.assertEqual(
            {row["id"] for row in citizen_rows},
            {"REQ-2026-0001", "REQ-2026-0003", "REQ-2026-0005", "REQ-2026-0006"},
        )
        self.assertEqual(
            self.service.list_requests(
                actor_id="USR-CIT-01", citizen_id="USR-CIT-02"
            ),
            [],
        )
        officer_rows = self.service.list_requests(actor_id="USR-OFF-01")
        self.assertEqual(
            {row["id"] for row in officer_rows},
            {"REQ-2026-0001", "REQ-2026-0002"},
        )
        field_officer_rows = self.service.list_requests(actor_id="USR-FLD-01")
        self.assertEqual(
            {row["id"] for row in field_officer_rows},
            {"REQ-2026-0003", "REQ-2026-0004", "REQ-2026-0006"},
        )
        supervisor_rows = self.service.list_requests(actor_id="USR-SUP-01")
        self.assertEqual(len(supervisor_rows), 6)
        with self.assertRaises(WorkflowError) as caught:
            self.service.list_requests(actor_id="USR-AUD-01")
        self.assertEqual(caught.exception.status_code, 403)

    def test_citizen_cannot_read_another_citizens_request(self) -> None:
        with self.assertRaises(WorkflowError) as caught:
            self.service.get_request(
                request_id="REQ-2026-0002", actor_id="USR-CIT-01"
            )
        self.assertEqual(caught.exception.status_code, 403)

    def test_audit_access_is_supervisor_and_inspector_only(self) -> None:
        with self.assertRaises(WorkflowError) as caught:
            self.service.list_audit(actor_id="USR-OFF-01")
        self.assertEqual(caught.exception.status_code, 403)
        result = self.service.list_audit(actor_id="USR-SUP-01")
        self.assertEqual(result["verification"]["status"], "NOT_RUN")
        self.assertGreaterEqual(len(result["records"]), 6)

        inspector_result = self.service.list_audit(actor_id="USR-AUD-01")
        self.assertEqual(inspector_result["verification"]["status"], "NOT_RUN")

        verification = self.service.verify_audit(actor_id="USR-AUD-01")
        self.assertTrue(verification["is_valid"])
        self.assertGreaterEqual(verification["verified_records_count"], 6)

    def test_second_connection_with_stale_version_gets_conflict(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "workflow.db"
            first = connect(path)
            second = connect(path)
            try:
                seed_database(first, reference_time=REFERENCE_TIME)
                first_service = WorkflowService(first, now_provider=self.clock.now)
                second_service = WorkflowService(second, now_provider=self.clock.now)
                first_service.transition(
                    request_id="REQ-2026-0001",
                    actor_id="USR-OFF-01",
                    command=TransitionCommand(
                        to_state=RequestState.ACKNOWLEDGED, expected_version=1
                    ),
                )
                with self.assertRaises(WorkflowError) as caught:
                    second_service.transition(
                        request_id="REQ-2026-0001",
                        actor_id="USR-SUP-01",
                        command=TransitionCommand(
                            to_state=RequestState.ASSIGNED,
                            assigned_to="USR-FLD-01",
                            expected_version=1,
                        ),
                    )
                self.assertEqual(caught.exception.status_code, 409)
                current = second.execute(
                    "SELECT state, version FROM requests WHERE id = 'REQ-2026-0001'"
                ).fetchone()
                self.assertEqual((current["state"], current["version"]), ("ACKNOWLEDGED", 2))
            finally:
                first.close()
                second.close()

    def test_needs_info_pause_and_resume_extends_deadlines(self) -> None:
        # REQ-2026-0001: SUBMITTED -> ACKNOWLEDGED
        ack = self.service.transition(
            request_id="REQ-2026-0001",
            actor_id="USR-OFF-01",
            command=TransitionCommand(to_state=RequestState.ACKNOWLEDGED, expected_version=1),
        )
        orig_deadline = ack["deadline"]

        # Officer pauses ticket with NEEDS_INFO
        paused = self.service.transition(
            request_id="REQ-2026-0001",
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.NEEDS_INFO,
                expected_version=2,
                info_request_note="Please specify the landmark.",
            ),
        )
        self.assertEqual(paused["state"], "NEEDS_INFO")
        self.assertIsNotNone(paused["paused_at"])

        # Advance clock by 3 hours (10800 seconds)
        self.clock.value = REFERENCE_TIME + timedelta(hours=3)

        # Citizen provides additional info -> ACKNOWLEDGED
        resumed = self.service.transition(
            request_id="REQ-2026-0001",
            actor_id="USR-CIT-01",
            command=TransitionCommand(
                to_state=RequestState.ACKNOWLEDGED,
                expected_version=3,
                additional_info="Opposite the post office gate.",
            ),
        )
        self.assertEqual(resumed["state"], "ACKNOWLEDGED")
        self.assertIsNone(resumed["paused_at"])
        self.assertEqual(resumed["total_paused_seconds"], 10800)
        self.assertEqual(resumed["additional_info"], "Opposite the post office gate.")

        # Deadline shifted forward by 3 hours
        expected_new_deadline = (
            datetime.fromisoformat(orig_deadline.replace("Z", "+00:00")) + timedelta(hours=3)
        ).isoformat(timespec="microseconds").replace("+00:00", "Z")
        self.assertEqual(resumed["deadline"], expected_new_deadline)

    def test_citizen_appeal_and_supervisor_appeal_approval(self) -> None:
        # Acknowledge REQ-2026-0001 then reject it
        self.service.transition(
            request_id="REQ-2026-0001",
            actor_id="USR-OFF-01",
            command=TransitionCommand(to_state=RequestState.ACKNOWLEDGED, expected_version=1),
        )
        rejected = self.service.transition(
            request_id="REQ-2026-0001",
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.REJECTED,
                expected_version=2,
                rejection_reason="Road belongs to state highway authority.",
            ),
        )
        self.assertEqual(rejected["state"], "REJECTED")

        # Citizen appeals
        appealed = self.service.submit_appeal(
            request_id="REQ-2026-0001",
            actor_id="USR-CIT-01",
            command=AppealCommand(
                expected_version=3,
                appeal_reason="Road was transferred to municipal ward 5 last year.",
            ),
        )
        self.assertEqual(appealed["state"], "REJECTED")
        self.assertEqual(appealed["version"], 4)
        self.assertEqual(
            appealed["appeal_reason"],
            "Road was transferred to municipal ward 5 last year.",
        )

        # Non-supervisor cannot approve appeal
        self.assert_workflow_error(
            403,
            lambda: self.service.transition(
                request_id="REQ-2026-0001",
                actor_id="USR-OFF-01",
                command=TransitionCommand(
                    to_state=RequestState.ACKNOWLEDGED,
                    expected_version=4,
                    appeal_approval_note="Approved",
                ),
            ),
            "ROLE_NOT_PERMITTED",
        )

        # Supervisor must provide appeal_approval_note
        self.assert_workflow_error(
            422,
            lambda: self.service.transition(
                request_id="REQ-2026-0001",
                actor_id="USR-SUP-01",
                command=TransitionCommand(
                    to_state=RequestState.ACKNOWLEDGED,
                    expected_version=4,
                ),
            ),
            "MISSING_REQUIRED_FIELD",
        )

        # Supervisor approves appeal
        re_ack = self.service.transition(
            request_id="REQ-2026-0001",
            actor_id="USR-SUP-01",
            command=TransitionCommand(
                to_state=RequestState.ACKNOWLEDGED,
                expected_version=4,
                appeal_approval_note="Verified municipal transfer agreement.",
            ),
        )
        self.assertEqual(re_ack["state"], "ACKNOWLEDGED")
        self.assertEqual(re_ack["version"], 5)

    def test_seven_day_post_closure_reopen_window(self) -> None:
        # REQ-2026-0005 is RESOLVED, closed by owner USR-CIT-01
        closed = self.service.transition(
            request_id="REQ-2026-0005",
            actor_id="USR-CIT-01",
            command=TransitionCommand(
                to_state=RequestState.CLOSED,
                expected_version=4,
            ),
        )
        self.assertEqual(closed["state"], "CLOSED")

        # Attempt reopen 8 days after closure (expired)
        self.clock.value = REFERENCE_TIME + timedelta(days=8)
        self.assert_workflow_error(
            422,
            lambda: self.service.transition(
                request_id="REQ-2026-0005",
                actor_id="USR-CIT-01",
                command=TransitionCommand(
                    to_state=RequestState.REOPENED,
                    expected_version=5,
                    reopen_reason="Failed too late.",
                ),
            ),
            "REOPEN_WINDOW_EXPIRED",
        )

        # Reset clock to 4 days after closure (within 7 days)
        self.clock.value = REFERENCE_TIME + timedelta(days=4)
        reopened = self.service.transition(
            request_id="REQ-2026-0005",
            actor_id="USR-CIT-01",
            command=TransitionCommand(
                to_state=RequestState.REOPENED,
                expected_version=5,
                reopen_reason="Recurred within one week.",
            ),
        )
        self.assertEqual(reopened["state"], "REOPENED")
        self.assertEqual(reopened["sla_cycle"], 2)

    def test_field_officer_progress_update(self) -> None:
        # REQ-2026-0004 is IN_PROGRESS assigned to USR-FLD-01, version 3
        updated = self.service.add_progress(
            request_id="REQ-2026-0004",
            actor_id="USR-FLD-01",
            command=ProgressCommand(
                expected_version=3,
                note="Wiring replaced, testing bulb circuitry.",
                evidence_json={"technician": "T-102", "tested_volts": 220},
            ),
        )
        self.assertEqual(updated["version"], 4)

        # Other field officer cannot add progress
        self.assert_workflow_error(
            403,
            lambda: self.service.add_progress(
                request_id="REQ-2026-0004",
                actor_id="USR-FLD-02",
                command=ProgressCommand(
                    expected_version=4,
                    note="Unauthorized update.",
                ),
            ),
            "ASSIGNMENT_REQUIRED",
        )

    def test_re_rejection_clears_prior_appeal_and_requires_fresh_appeal(self) -> None:
        # Acknowledge REQ-2026-0001 then reject it
        self.service.transition(
            request_id="REQ-2026-0001",
            actor_id="USR-OFF-01",
            command=TransitionCommand(to_state=RequestState.ACKNOWLEDGED, expected_version=1),
        )
        self.service.transition(
            request_id="REQ-2026-0001",
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.REJECTED,
                expected_version=2,
                rejection_reason="First rejection reason.",
            ),
        )
        # Citizen appeals
        self.service.submit_appeal(
            request_id="REQ-2026-0001",
            actor_id="USR-CIT-01",
            command=AppealCommand(
                expected_version=3,
                appeal_reason="First appeal reason.",
            ),
        )
        # Supervisor approves
        self.service.transition(
            request_id="REQ-2026-0001",
            actor_id="USR-SUP-01",
            command=TransitionCommand(
                to_state=RequestState.ACKNOWLEDGED,
                expected_version=4,
                appeal_approval_note="First approval.",
            ),
        )
        # Officer rejects again -> must clear appeal fields!
        second_rejected = self.service.transition(
            request_id="REQ-2026-0001",
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.REJECTED,
                expected_version=5,
                rejection_reason="Second rejection reason.",
            ),
        )
        self.assertEqual(second_rejected["state"], "REJECTED")
        self.assertIsNone(second_rejected["appeal_reason"])
        self.assertIsNone(second_rejected["appealed_at"])
        self.assertIsNone(second_rejected["appeal_approval_note"])

        # Supervisor cannot approve without a fresh appeal
        self.assert_workflow_error(
            422,
            lambda: self.service.transition(
                request_id="REQ-2026-0001",
                actor_id="USR-SUP-01",
                command=TransitionCommand(
                    to_state=RequestState.ACKNOWLEDGED,
                    expected_version=6,
                    appeal_approval_note="Attempted bypass.",
                ),
            ),
            "APPEAL_REQUIRED",
        )

        # Fresh citizen appeal allows approval
        self.service.submit_appeal(
            request_id="REQ-2026-0001",
            actor_id="USR-CIT-01",
            command=AppealCommand(
                expected_version=6,
                appeal_reason="Second appeal reason.",
            ),
        )
        re_approved = self.service.transition(
            request_id="REQ-2026-0001",
            actor_id="USR-SUP-01",
            command=TransitionCommand(
                to_state=RequestState.ACKNOWLEDGED,
                expected_version=7,
                appeal_approval_note="Second approval.",
            ),
        )
        self.assertEqual(re_approved["state"], "ACKNOWLEDGED")

    def test_reopen_clears_closed_at(self) -> None:
        # Close REQ-2026-0005
        closed = self.service.transition(
            request_id="REQ-2026-0005",
            actor_id="USR-CIT-01",
            command=TransitionCommand(
                to_state=RequestState.CLOSED,
                expected_version=4,
            ),
        )
        self.assertIsNotNone(closed["closed_at"])

        # Reopen within 7 days
        self.clock.value = REFERENCE_TIME + timedelta(days=2)
        reopened = self.service.transition(
            request_id="REQ-2026-0005",
            actor_id="USR-CIT-01",
            command=TransitionCommand(
                to_state=RequestState.REOPENED,
                expected_version=5,
                reopen_reason="Disputing resolution quality.",
            ),
        )
        self.assertEqual(reopened["state"], "REOPENED")
        self.assertIsNone(reopened["closed_at"])

    def test_seven_day_boundary_exact_vs_expired(self) -> None:
        closed = self.service.transition(
            request_id="REQ-2026-0005",
            actor_id="USR-CIT-01",
            command=TransitionCommand(
                to_state=RequestState.CLOSED,
                expected_version=4,
            ),
        )
        closed_at = datetime.fromisoformat(closed["closed_at"].replace("Z", "+00:00"))

        # Exact boundary: closed_at + exactly 7 days -> SUCCEEDS
        self.clock.value = closed_at + timedelta(days=7)
        reopened = self.service.transition(
            request_id="REQ-2026-0005",
            actor_id="USR-CIT-01",
            command=TransitionCommand(
                to_state=RequestState.REOPENED,
                expected_version=5,
                reopen_reason="Reopened at exact 7-day boundary.",
            ),
        )
        self.assertEqual(reopened["state"], "REOPENED")

    def test_seven_day_boundary_one_microsecond_expired(self) -> None:
        closed = self.service.transition(
            request_id="REQ-2026-0005",
            actor_id="USR-CIT-01",
            command=TransitionCommand(
                to_state=RequestState.CLOSED,
                expected_version=4,
            ),
        )
        closed_at = datetime.fromisoformat(closed["closed_at"].replace("Z", "+00:00"))

        # 1 microsecond after 7 days -> FAILS with 422
        self.clock.value = closed_at + timedelta(days=7, microseconds=1)
        self.assert_workflow_error(
            422,
            lambda: self.service.transition(
                request_id="REQ-2026-0005",
                actor_id="USR-CIT-01",
                command=TransitionCommand(
                    to_state=RequestState.REOPENED,
                    expected_version=5,
                    reopen_reason="Reopened after 7 days.",
                ),
            ),
            "REOPEN_WINDOW_EXPIRED",
        )

    def test_category_confirmation_during_needs_info_and_extension_on_resume(self) -> None:
        # Acknowledge REQ-2026-0001 (starts Uncategorised, 72h SLA, 2h triage)
        self.service.transition(
            request_id="REQ-2026-0001",
            actor_id="USR-OFF-01",
            command=TransitionCommand(to_state=RequestState.ACKNOWLEDGED, expected_version=1),
        )
        # Pause at T+1h
        self.clock.value = REFERENCE_TIME + timedelta(hours=1)
        paused = self.service.transition(
            request_id="REQ-2026-0001",
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.NEEDS_INFO,
                expected_version=2,
                info_request_note="Need pipe diameter.",
            ),
        )
        self.assertEqual(paused["state"], "NEEDS_INFO")

        # Advance by 5 hours while in NEEDS_INFO (to T+6h)
        self.clock.value = REFERENCE_TIME + timedelta(hours=6)

        # Confirm category as Water Leak (24h SLA) while paused
        cat_confirmed = self.service.confirm_category(
            request_id="REQ-2026-0001",
            actor_id="USR-SUP-01",
            command=CategoryCommand(
                category=Category.WATER_LEAK,
                expected_version=3,
            ),
        )
        self.assertEqual(cat_confirmed["category"], "Water Leak")
        self.assertEqual(cat_confirmed["state"], "NEEDS_INFO")
        self.assertIsNotNone(cat_confirmed["paused_at"])

        # Advance another 2 hours (to T+8h, total pause = 7 hours)
        self.clock.value = REFERENCE_TIME + timedelta(hours=8)

        # Citizen resumes
        resumed = self.service.transition(
            request_id="REQ-2026-0001",
            actor_id="USR-CIT-01",
            command=TransitionCommand(
                to_state=RequestState.ACKNOWLEDGED,
                expected_version=4,
                additional_info="6-inch cast iron pipe.",
            ),
        )
        self.assertEqual(resumed["state"], "ACKNOWLEDGED")
        self.assertIsNone(resumed["paused_at"])
        self.assertEqual(resumed["total_paused_seconds"], 7 * 3600)

        # SLA was 24h from sla_started_at (REFERENCE_TIME) + 7 hours pause = REFERENCE_TIME + 31 hours
        expected_deadline = (REFERENCE_TIME + timedelta(hours=24 + 7)).isoformat(timespec="microseconds").replace("+00:00", "Z")
        self.assertEqual(resumed["deadline"], expected_deadline)

        # Triage deadline was 2h + 7h pause = REFERENCE_TIME + 9 hours
        expected_triage = (REFERENCE_TIME + timedelta(hours=2 + 7)).isoformat(timespec="microseconds").replace("+00:00", "Z")
        self.assertEqual(resumed["triage_deadline"], expected_triage)

    def test_verify_audit_tamper_detection_includes_tampered_at(self) -> None:
        # In memory DB, tamper with an audit row
        self.connection.execute("DROP TRIGGER abort_audit_update")
        self.connection.execute("UPDATE audit SET outcome = 'REFUSED' WHERE id = 1")
        result = self.service.verify_audit(actor_id="USR-AUD-01")
        self.assertFalse(result["is_valid"])
        self.assertEqual(result["error"], "TAMPER_DETECTED")
        self.assertEqual(result["tampered_record_id"], 1)
        self.assertIn("tampered_at", result)
        self.assertIsNotNone(result["tampered_at"])


class RouteWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.conn = connect(self.db_path)
        seed_database(self.conn, reference_time=REFERENCE_TIME)
        self.conn.close()

        import os
        os.environ["GOVFIX_DATABASE_PATH"] = self.db_path

        from src.main import app
        from fastapi.testclient import TestClient
        self.client = TestClient(app)

    def tearDown(self) -> None:
        import os
        try:
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
        except OSError:
            pass
        os.environ.pop("GOVFIX_DATABASE_PATH", None)

    def test_http_api_create_and_transition(self) -> None:
        resp = self.client.post(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-CIT-01"},
            json={
                "title": "Leaking Pipe",
                "text": "Pipe burst on Main Street",
                "location": "Main Street, Ward 1",
            },
        )
        self.assertEqual(resp.status_code, 201)
        req_data = resp.json()["data"]
        req_id = req_data["id"]
        self.assertEqual(req_data["state"], "SUBMITTED")
        self.assertEqual(req_data["location"], "Main Street, Ward 1")

        # Officer acknowledges
        ack_resp = self.client.post(
            f"/api/v1/requests/{req_id}/transition",
            headers={"X-Actor-ID": "USR-OFF-01"},
            json={"to_state": "ACKNOWLEDGED", "expected_version": 1},
        )
        self.assertEqual(ack_resp.status_code, 200)
        self.assertEqual(ack_resp.json()["data"]["state"], "ACKNOWLEDGED")

    def test_http_api_audit_verify_for_inspector(self) -> None:
        resp = self.client.get(
            "/api/v1/audit/verify",
            headers={"X-Actor-ID": "USR-AUD-01"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertTrue(data["is_valid"])
        self.assertGreaterEqual(data["verified_records_count"], 6)

    def test_http_api_audit_inspector_blocked_from_requests(self) -> None:
        resp = self.client.get(
            "/api/v1/requests",
            headers={"X-Actor-ID": "USR-AUD-01"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"]["code"], "ROLE_NOT_PERMITTED")


if __name__ == "__main__":
    unittest.main()
