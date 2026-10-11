from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.database import connect
from src.models import (
    Category,
    CategoryCommand,
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
                assigned_to="USR-OFF-01",
            ),
        )
        request = self.service.transition(
            request_id=request["id"],
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.IN_PROGRESS, expected_version=3
            ),
        )
        request = self.service.transition(
            request_id=request["id"],
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.RESOLVED,
                expected_version=4,
                resolution_notes="Replaced the damaged pipe section.",
            ),
        )
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
        self.service.transition(
            request_id="REQ-2026-0002",
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.IN_PROGRESS, expected_version=1
            ),
        )
        self.service.transition(
            request_id="REQ-2026-0002",
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.RESOLVED,
                expected_version=2,
                resolution_notes="Lamp replaced.",
            ),
        )
        self.connection.execute(
            "UPDATE requests SET escalation_level = 2 WHERE id = 'REQ-2026-0002'"
        )
        self.connection.commit()
        self.clock.value = REFERENCE_TIME + timedelta(days=2)
        reopened = self.service.transition(
            request_id="REQ-2026-0002",
            actor_id="USR-CIT-02",
            command=TransitionCommand(
                to_state=RequestState.REOPENED,
                expected_version=3,
                reopen_reason="The replacement lamp failed again.",
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
            "The replacement lamp failed again.",
        )
        in_progress = self.service.transition(
            request_id="REQ-2026-0002",
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.IN_PROGRESS, expected_version=4
            ),
        )
        resolved = self.service.transition(
            request_id="REQ-2026-0002",
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.RESOLVED,
                expected_version=5,
                resolution_notes="Repaired the connection and tested the lamp.",
            ),
        )
        self.assertEqual(in_progress["state"], "IN_PROGRESS")
        self.assertEqual((resolved["state"], resolved["version"]), ("RESOLVED", 6))

    def test_skipped_state_is_422_and_logged(self) -> None:
        error = self.assert_workflow_error(
            422,
            lambda: self.service.transition(
                request_id="REQ-2026-0002",
                actor_id="USR-OFF-01",
                command=TransitionCommand(
                    to_state=RequestState.RESOLVED,
                    expected_version=1,
                    resolution_notes="Attempted early resolution.",
                ),
            ),
            "INVALID_TRANSITION",
        )
        self.assertEqual(error.permitted_next_states, ["IN_PROGRESS"])

    def test_valid_edge_by_wrong_role_is_403(self) -> None:
        self.service.transition(
            request_id="REQ-2026-0002",
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.IN_PROGRESS, expected_version=1
            ),
        )
        self.assert_workflow_error(
            403,
            lambda: self.service.transition(
                request_id="REQ-2026-0002",
                actor_id="USR-CIT-02",
                command=TransitionCommand(
                    to_state=RequestState.RESOLVED,
                    expected_version=2,
                    resolution_notes="Citizen cannot resolve.",
                ),
            ),
            "ROLE_NOT_PERMITTED",
        )

    def test_non_assigned_officer_is_refused(self) -> None:
        self.assert_workflow_error(
            403,
            lambda: self.service.transition(
                request_id="REQ-2026-0002",
                actor_id="USR-OFF-02",
                command=TransitionCommand(
                    to_state=RequestState.IN_PROGRESS, expected_version=1
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

    def test_resolution_notes_are_required(self) -> None:
        self.service.transition(
            request_id="REQ-2026-0002",
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.IN_PROGRESS, expected_version=1
            ),
        )
        self.assert_workflow_error(
            422,
            lambda: self.service.transition(
                request_id="REQ-2026-0002",
                actor_id="USR-OFF-01",
                command=TransitionCommand(
                    to_state=RequestState.RESOLVED, expected_version=2
                ),
            ),
            "MISSING_REQUIRED_FIELD",
        )

    def test_citizen_cannot_close_another_citizens_request(self) -> None:
        self.service.transition(
            request_id="REQ-2026-0002",
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.IN_PROGRESS, expected_version=1
            ),
        )
        self.service.transition(
            request_id="REQ-2026-0002",
            actor_id="USR-OFF-01",
            command=TransitionCommand(
                to_state=RequestState.RESOLVED,
                expected_version=2,
                resolution_notes="Lamp repaired.",
            ),
        )
        self.assert_workflow_error(
            403,
            lambda: self.service.transition(
                request_id="REQ-2026-0002",
                actor_id="USR-CIT-01",
                command=TransitionCommand(
                    to_state=RequestState.CLOSED, expected_version=3
                ),
            ),
            "OWNERSHIP_REQUIRED",
        )

    def test_assignment_requires_officer_and_assigned_to(self) -> None:
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
            request_id="REQ-2026-0002",
            actor_id="USR-SUP-01",
            command=ReassignCommand(
                assigned_to="USR-OFF-02",
                reason="Balance the active workload.",
                expected_version=1,
            ),
        )
        self.assertEqual(request["assigned_to"], "USR-OFF-02")
        self.assertEqual(request["state"], "ASSIGNED")
        self.assertEqual(request["version"], 2)

    def test_create_request_uses_uncategorised_as_authoritative(self) -> None:
        request = self.service.create_request(
            actor_id="USR-CIT-02",
            command=RequestCreate(
                title="Missed collection",
                text="Garbage was not collected on Lake Road.",
                reported_category=Category.GARBAGE_COLLECTION,
            ),
        )
        self.assertEqual(request["reported_category"], "Garbage Collection")
        self.assertEqual(request["category"], "Uncategorised")
        self.assertEqual(request["state"], "SUBMITTED")
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
            {"REQ-2026-0001", "REQ-2026-0003"},
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
        supervisor_rows = self.service.list_requests(actor_id="USR-SUP-01")
        self.assertEqual(len(supervisor_rows), 3)

    def test_citizen_cannot_read_another_citizens_request(self) -> None:
        with self.assertRaises(WorkflowError) as caught:
            self.service.get_request(
                request_id="REQ-2026-0002", actor_id="USR-CIT-01"
            )
        self.assertEqual(caught.exception.status_code, 403)

    def test_audit_access_is_supervisor_only(self) -> None:
        with self.assertRaises(WorkflowError) as caught:
            self.service.list_audit(actor_id="USR-OFF-01")
        self.assertEqual(caught.exception.status_code, 403)
        result = self.service.list_audit(actor_id="USR-SUP-01")
        self.assertEqual(result["verification"]["status"], "NOT_RUN")
        self.assertGreaterEqual(len(result["records"]), 3)

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
                            assigned_to="USR-OFF-01",
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


if __name__ == "__main__":
    unittest.main()
