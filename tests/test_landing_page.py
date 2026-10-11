"""Targeted tests for GovFix Premium Landing Page requirements."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_landing_page_renders_with_200(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "GovFix — Municipal Grievance & Civic Accountability Platform" in response.text


def test_landing_page_navigation_elements(client: TestClient) -> None:
    response = client.get("/")
    html = response.text

    # GovFix Brand Logo linking to /
    assert 'href="/"' in html
    assert "GovFix" in html

    # Citizen Login button
    assert "Citizen Login" in html
    assert 'href="/citizen"' in html
    assert "border-[#00C875]" in html or "text-[#00C875]" in html

    # Staff Login button
    assert "Staff Login" in html
    assert "btn-staff-login" in html
    assert "staff-dropdown" in html


def test_staff_dropdown_contains_all_four_workspaces(client: TestClient) -> None:
    response = client.get("/")
    html = response.text

    # 1. Officer — Triage & Assignment
    assert "/officer" in html
    assert "Officer — Triage &amp; Assignment" in html or "Officer — Triage & Assignment" in html

    # 2. Field Officer — On-Ground Execution
    assert "/field-officer" in html
    assert "Field Officer — On-Ground Execution" in html

    # 3. Supervisor — Command Center
    assert "/supervisor" in html
    assert "Supervisor — Command Center" in html

    # 4. Audit Inspector — Read-Only Verification
    assert "/audit" in html
    assert "Audit Inspector — Read-Only Verification" in html


def test_hero_content_and_call_to_actions(client: TestClient) -> None:
    response = client.get("/")
    html = response.text

    # Announcement Badge
    assert "PUBLIC SERVICE TECHNOLOGY" in html
    assert "BUILT FOR ACCOUNTABILITY" in html

    # Main Headline
    assert "Every civic complaint deserves action." in html

    # Supporting Description
    assert "One transparent platform to report municipal issues, track progress, and hold every action accountable" in html

    # Primary CTA
    assert "Report a Grievance" in html
    assert "hero-cta-btn" in html

    # Secondary Track Link
    assert "Already reported an issue?" in html
    assert "Track your complaint" in html


def test_bottom_capabilities_strip(client: TestClient) -> None:
    response = client.get("/")
    html = response.text

    assert "Transparent Complaint Tracking" in html
    assert "SLA Deadline Monitoring" in html
    assert "Accountable Officer Actions" in html
    assert "Verifiable Audit History" in html


def test_background_asset_served(client: TestClient) -> None:
    # Verify hero-bg.jpg is served correctly by FastAPI static mount
    response = client.get("/static/hero-bg.jpg")
    assert response.status_code == 200
    assert "image" in response.headers["content-type"]
    assert len(response.content) > 1000


def test_all_five_persona_pages_still_render(client: TestClient) -> None:
    # 1. Citizen Portal
    resp_cit = client.get("/citizen")
    assert resp_cit.status_code == 200
    assert "Citizen Grievance Portal" in resp_cit.text

    # 2. Officer Workspace
    resp_off = client.get("/officer")
    assert resp_off.status_code == 200
    assert "Municipal Grievance Triage" in resp_off.text

    # 3. Field Officer Workspace
    resp_fld = client.get("/field-officer")
    assert resp_fld.status_code == 200
    assert "Field Officer Execution Workspace" in resp_fld.text

    # 4. Supervisor Workspace
    resp_sup = client.get("/supervisor")
    assert resp_sup.status_code == 200
    assert "Supervisor Command Center" in resp_sup.text

    # 5. Audit Console
    resp_aud = client.get("/audit")
    assert resp_aud.status_code == 200
    assert "Audit Inspector Console" in resp_aud.text
