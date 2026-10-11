from __future__ import annotations

import sqlite3
import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from src.database import connect, initialize_database, transaction
from src.models import Category, ClockState, RequestCreate
from src.seed import GENESIS_HASH, seed_database


REFERENCE_TIME = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


class FoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = connect(":memory:")
        seed_database(self.connection, reference_time=REFERENCE_TIME)

    def tearDown(self) -> None:
        self.connection.close()

    def test_seed_contains_expected_fictional_records(self) -> None:
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 5
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 3
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM ai_suggestions").fetchone()[0],
            1,
        )
        clock = self.connection.execute(
            "SELECT * FROM clock_state WHERE id = 1"
        ).fetchone()
        self.assertEqual(clock["offset_seconds"], 0)
        self.assertEqual(clock["is_frozen"], 0)

    def test_near_deadline_fixture_is_one_hour_from_breach(self) -> None:
        request = self.connection.execute(
            "SELECT * FROM requests WHERE id = 'REQ-2026-0003'"
        ).fetchone()
        self.assertEqual(request["category"], "Water Leak")
        self.assertEqual(request["state"], "ASSIGNED")
        self.assertEqual(request["deadline"], "2026-10-11T13:00:00.000000Z")

    def test_foreign_keys_are_enabled(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                """
                INSERT INTO requests (
                    id, citizen_id, title, text, category, state,
                    sla_started_at, created_at, updated_at, deadline
                ) VALUES (
                    'REQ-BAD-FK', 'MISSING', 'Title', 'Text', 'Uncategorised',
                    'SUBMITTED', '2026-10-11T12:00:00Z',
                    '2026-10-11T12:00:00Z', '2026-10-11T12:00:00Z',
                    '2026-10-14T12:00:00Z'
                )
                """
            )

    def test_assignee_must_be_an_officer(self) -> None:
        with self.assertRaisesRegex(sqlite3.IntegrityError, "must reference an Officer"):
            self.connection.execute(
                "UPDATE requests SET assigned_to = 'USR-SUP-01' WHERE id = 'REQ-2026-0001'"
            )

    def test_audit_rows_cannot_be_updated_or_deleted(self) -> None:
        with self.assertRaisesRegex(sqlite3.IntegrityError, "updates forbidden"):
            self.connection.execute("UPDATE audit SET outcome = 'REFUSED' WHERE id = 1")
        self.connection.rollback()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "deletions forbidden"):
            self.connection.execute("DELETE FROM audit WHERE id = 1")

    def test_audit_seed_chain_links_from_genesis(self) -> None:
        rows = self.connection.execute(
            "SELECT previous_hash, hash FROM audit ORDER BY id"
        ).fetchall()
        self.assertEqual(rows[0]["previous_hash"], GENESIS_HASH)
        for previous, current in zip(rows, rows[1:]):
            self.assertEqual(current["previous_hash"], previous["hash"])

    def test_audit_dedupe_key_is_unique(self) -> None:
        row = self.connection.execute("SELECT * FROM audit WHERE id = 1").fetchone()
        values = tuple(row[key] for key in row.keys())
        columns = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row.keys())
        mutable_values = list(values)
        mutable_values[0] = None
        dedupe_index = row.keys().index("dedupe_key")
        mutable_values[dedupe_index] = "escalation:REQ-2026-0001:1:1"
        self.connection.execute(
            f"INSERT INTO audit ({columns}) VALUES ({placeholders})", mutable_values
        )
        mutable_values[0] = None
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                f"INSERT INTO audit ({columns}) VALUES ({placeholders})", mutable_values
            )

    def test_transaction_rolls_back_all_changes(self) -> None:
        self.connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.connection, immediate=True):
                self.connection.execute(
                    """
                    INSERT INTO users (id, name, role, email, created_at)
                    VALUES ('USR-TEMP', 'Temporary User', 'Citizen',
                            'temp@example.test', '2026-10-11T12:00:00Z')
                    """
                )
                self.connection.execute(
                    """
                    INSERT INTO users (id, name, role, email, created_at)
                    VALUES ('USR-BAD', 'Bad Role', 'Administrator',
                            'bad@example.test', '2026-10-11T12:00:00Z')
                    """
                )
        count = self.connection.execute(
            "SELECT COUNT(*) FROM users WHERE id = 'USR-TEMP'"
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_schema_initialization_is_idempotent(self) -> None:
        initialize_database(self.connection)
        initialize_database(self.connection)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 5
        )


class ModelTests(unittest.TestCase):
    def test_request_create_strips_text_and_accepts_specific_category(self) -> None:
        model = RequestCreate(
            title="  Broken streetlight  ",
            text="  The lamp is dark.  ",
            reported_category=Category.STREETLIGHT,
        )
        self.assertEqual(model.title, "Broken streetlight")

    def test_request_create_rejects_blank_text(self) -> None:
        with self.assertRaises(ValidationError):
            RequestCreate(title=" ", text="Complaint")

    def test_uncategorised_cannot_be_a_citizen_reported_category(self) -> None:
        with self.assertRaises(ValidationError):
            RequestCreate(
                title="Issue",
                text="Details",
                reported_category=Category.UNCATEGORISED,
            )

    def test_frozen_clock_requires_frozen_at(self) -> None:
        with self.assertRaises(ValidationError):
            ClockState(
                offset_seconds=0,
                is_frozen=True,
                frozen_at=None,
                updated_at=REFERENCE_TIME,
            )


if __name__ == "__main__":
    unittest.main()
