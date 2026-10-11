"""Tests for GovFix Phase 1 UI routes, templates, and static assets."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_index_renders_landing_page(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "GovFix" in response.text
    assert "Every civic complaint deserves action." in response.text
    assert "Citizen Login" in response.text
    assert "Staff Login" in response.text
    assert "Report a Grievance" in response.text
    assert "Transparent Complaint Tracking" in response.text


def test_citizen_portal_renders(client: TestClient) -> None:
    response = client.get("/citizen")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Citizen Grievance Portal" in response.text
    assert "Lodge Municipal Grievance" in response.text
    assert "Registered Complaints" in response.text
    assert "USR-CIT-01" in response.text
    assert "/static/styles.css" in response.text
    assert "/static/app.js" in response.text


def test_officer_workspace_renders(client: TestClient) -> None:
    response = client.get("/officer")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Officer Triage Workspace" in response.text
    assert "Intake Queue" in response.text
    assert "USR-OFF-01" in response.text


def test_field_officer_workspace_renders(client: TestClient) -> None:
    response = client.get("/field-officer")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Field Officer Execution Workspace" in response.text
    assert "On-Ground Task Dispatches" in response.text


def test_supervisor_command_center_renders(client: TestClient) -> None:
    response = client.get("/supervisor")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Supervisor Command Center" in response.text
    assert "Simulated Municipal Clock Controller" in response.text
    assert "USR-SUP-01" in response.text


def test_audit_inspector_console_renders(client: TestClient) -> None:
    response = client.get("/audit")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Audit Inspector Console" in response.text
    assert "Immutable Governance Audit Log" in response.text
    assert "SHA-256" in response.text


def test_static_assets_served(client: TestClient) -> None:
    css_resp = client.get("/static/styles.css")
    assert css_resp.status_code == 200
    assert "gov-tricolor-strip" in css_resp.text

    js_resp = client.get("/static/app.js")
    assert js_resp.status_code == 200
    assert "GovFix" in js_resp.text
    assert "X-Actor-ID" in js_resp.text
