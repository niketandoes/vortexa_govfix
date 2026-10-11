"""Tests for GovFix MongoDB Atlas integration."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from src.mongodb import (
    COLLECTION_USERS,
    COLLECTION_REQUESTS,
    COLLECTION_AUDIT,
    COLLECTION_CLOCK_STATE,
    COLLECTION_EMAIL_EVENTS,
    get_mongodb_uri,
    is_mongodb_enabled,
    init_mongodb_indexes,
    purge_mongodb_data,
    save_user_to_mongodb,
    get_user_from_mongodb,
    save_request_to_mongodb,
    get_request_from_mongodb,
    save_audit_event_to_mongodb,
    save_clock_state_to_mongodb,
    save_email_event_to_mongodb,
)


def test_mongodb_uri_builder_with_cluster(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.delenv("MONGO_URI", raising=False)
    monkeypatch.setenv("MONGO_USER", "niketoke101_db_user")
    monkeypatch.setenv("MONGO_PASSWORD", "DuOjSO09GUNCb6G1")
    monkeypatch.setenv("MONGO_CLUSTER", "gov-fix.example.mongodb.net")

    uri = get_mongodb_uri()
    assert uri is not None
    assert "mongodb+srv://niketoke101_db_user:DuOjSO09GUNCb6G1@gov-fix.example.mongodb.net/gov-fix" in uri
    assert "appName=gov-fix" in uri


def test_is_mongodb_enabled_flag(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_BACKEND", "mongodb")
    assert is_mongodb_enabled() is True

    monkeypatch.setenv("DATABASE_BACKEND", "sqlite")
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.delenv("MONGO_URI", raising=False)
    monkeypatch.delenv("MONGO_CLUSTER", raising=False)
    assert is_mongodb_enabled() is False


def test_mongodb_mock_operations():
    mock_db = MagicMock()
    mock_collection = MagicMock()
    mock_db.__getitem__.return_value = mock_collection

    # Test purge
    counts = purge_mongodb_data(mock_db)
    assert COLLECTION_USERS in counts
    assert COLLECTION_REQUESTS in counts
    assert COLLECTION_AUDIT in counts

    # Test indexes
    result = init_mongodb_indexes(mock_db)
    assert result is True

    # Test user save and get
    user_data = {"id": "USR-CIT-01", "name": "Ramesh Patel", "role": "Citizen", "email": "ramesh@citizen.local"}
    saved = save_user_to_mongodb(user_data, db=mock_db)
    assert saved is True
    mock_collection.replace_one.assert_called()

    # Test request save
    req_data = {"id": "REQ-2026-0001", "citizen_id": "USR-CIT-01", "title": "Pothole", "state": "SUBMITTED"}
    saved_req = save_request_to_mongodb(req_data, db=mock_db)
    assert saved_req is True

    # Test audit save
    audit_data = {"id": 1, "action": "SUBMIT_COMPLAINT", "outcome": "ACCEPTED", "hash": "abc"}
    saved_audit = save_audit_event_to_mongodb(audit_data, db=mock_db)
    assert saved_audit is True

    # Test clock save
    clock_data = {"id": 1, "offset_seconds": 3600, "is_frozen": 0}
    saved_clock = save_clock_state_to_mongodb(clock_data, db=mock_db)
    assert saved_clock is True

    # Test email save
    email_data = {"event_id": "evt-01", "recipient": "ramesh@citizen.local", "template": "welcome"}
    saved_email = save_email_event_to_mongodb(email_data, db=mock_db)
    assert saved_email is True
