"""Tests for GovFix Google Authentication and Citizen Auto-Provisioning."""

from __future__ import annotations

import os
from typing import Generator
import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.database import connect, initialize_database
from src.seed import seed_database
from src.auth import verify_token_payload, _init_firebase_admin
from src.mongodb import get_user_from_mongodb, is_mongodb_enabled


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def setup_db():
    conn = connect()
    initialize_database(conn)
    seed_database(conn, include_extended=True)
    yield conn
    conn.close()


def test_auth_config_endpoint(client: TestClient) -> None:
    """GET /api/v1/auth/config returns Firebase public client configuration."""
    resp = client.get("/api/v1/auth/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["projectId"] == "gov-fix"
    assert data["googleAuthEnabled"] is True
    assert "apiKey" in data
    assert "authDomain" in data


def test_google_signin_new_citizen_auto_provisions(client: TestClient, setup_db) -> None:
    """When a new citizen signs in with Google, they are auto-provisioned as USR-CIT-xx with is_verified=1."""
    unique_email = "new.google.citizen@example.com"
    token = f"mock-token-{unique_email}"

    resp = client.post(
        "/api/v1/auth/google",
        json={
            "token": token,
            "email": unique_email,
            "name": "Ananya Sharma",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["is_new"] is True
    assert body["redirect_url"] == "/citizen"

    user = body["data"]
    assert user["role"] == "Citizen"
    assert user["email"] == unique_email
    assert user["name"] == "Ananya Sharma"
    assert user["is_verified"] is True
    assert user["id"].startswith("USR-CIT-")

    # Verify session cookie was set
    assert "govfix_token" in resp.cookies
    assert resp.cookies["govfix_token"].strip('"') == token

    # Verify user exists in SQLite database
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (unique_email,)).fetchone()
        assert row is not None
        assert row["role"] == "Citizen"
        assert row["is_verified"] == 1
    finally:
        conn.close()

    # Verify user synced to MongoDB if enabled
    if is_mongodb_enabled():
        mg_user = get_user_from_mongodb(unique_email)
        assert mg_user is not None
        assert mg_user["id"] == user["id"]
        assert mg_user["role"] == "Citizen"
        assert mg_user["is_verified"] == 1


def test_google_signin_existing_citizen_links_and_redirects(client: TestClient, setup_db) -> None:
    """Existing citizen signing in with Google links UID and redirects to /citizen."""
    existing_email = "citizen1@govfix.local"
    token = f"mock-token-{existing_email}"

    resp = client.post(
        "/api/v1/auth/google",
        json={
            "token": token,
            "email": existing_email,
            "name": "Ramesh Kumar",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["is_new"] is False
    assert body["redirect_url"] == "/citizen"
    assert body["data"]["id"] == "USR-CIT-01"
    assert body["data"]["role"] == "Citizen"
    assert body["data"]["is_verified"] is True


def test_google_signin_staff_member_redirects_to_staff_portal(client: TestClient, setup_db) -> None:
    """Staff accounts signing in with Google redirect to their dedicated staff dashboard."""
    staff_cases = [
        ("triage@govfix.local", "Officer", "/staff/triage"),
        ("field1@govfix.local", "Field Officer", "/staff/field"),
        ("supervisor@govfix.local", "Supervisor", "/staff/supervisor"),
        ("audit@govfix.local", "Audit Inspector", "/staff/audit"),
        ("admin@govfix.local", "Admin", "/admin"),
    ]

    for email, role, expected_dest in staff_cases:
        token = f"mock-token-{email}"
        resp = client.post(
            "/api/v1/auth/google",
            json={"token": token, "email": email},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["role"] == role
        assert body["redirect_url"] == expected_dest


def test_google_signin_disabled_account_refused(client: TestClient, setup_db) -> None:
    """Disabled accounts are refused with 403."""
    conn = connect()
    try:
        conn.execute("UPDATE users SET status = 'disabled' WHERE id = 'USR-CIT-01'")
        conn.commit()

        resp = client.post(
            "/api/v1/auth/google",
            json={"token": "mock-token-citizen1@govfix.local", "email": "citizen1@govfix.local"},
        )
        assert resp.status_code == 403
        assert "disabled" in resp.json()["detail"].lower()
    finally:
        conn.execute("UPDATE users SET status = 'active' WHERE id = 'USR-CIT-01'")
        conn.commit()
        conn.close()


def test_token_verification_support() -> None:
    """verify_token_payload properly decodes mock tokens, and rejects forged tokens."""
    payload = verify_token_payload("mock-token-testuser@gmail.com")
    assert payload["email"] == "testuser@gmail.com"
    assert payload["email_verified"] is True

    with pytest.raises(Exception):
        verify_token_payload("forged_garbage_token")


def test_firebase_admin_auto_discovery() -> None:
    """Firebase Admin SDK initializes or uses root service account."""
    _init_firebase_admin(force=False)
    # Shouldn't raise any exceptions
