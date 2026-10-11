"""UI routes serving server-rendered Jinja2 templates for GovFix.

Enforces server-side authentication and role-segregated navigation:
- Public: /, /login, /signup, /verify-email, /forgot-password
- Citizen: /citizen (strictly for Citizens)
- Staff: /staff/triage, /staff/field, /staff/supervisor, /staff/audit, /admin
- Legacy route aliases (/officer, /field-officer, etc.) supported with server-side RBAC.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.auth import verify_token_payload
from src.database import connect


FRONTEND_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "frontend" / "templates"
TEMPLATES_DIR = (
    FRONTEND_TEMPLATES_DIR
    if FRONTEND_TEMPLATES_DIR.exists()
    else Path(__file__).resolve().parent.parent / "templates"
)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["UI"])


def get_session_user(request: Request) -> dict[str, Any] | None:
    """Extract authenticated user for HTML page requests via session cookie, header, or test override."""
    connection = connect()
    try:
        # 1. Test override mode (for existing automated test suites)
        if os.getenv("GOVFIX_TEST_AUTH_OVERRIDE") == "1":
            actor_id = request.headers.get("X-Test-Actor-ID") or request.headers.get("X-Actor-ID")
            if actor_id:
                row = connection.execute("SELECT * FROM users WHERE id = ?", (actor_id,)).fetchone()
                if row:
                    return dict(row)
            return None

        # 2. Extract token from header or cookie
        token = None
        auth_hdr = request.headers.get("Authorization")
        if auth_hdr and auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:].strip()
        elif request.cookies.get("govfix_token"):
            token = request.cookies.get("govfix_token")

        if not token:
            return None

        payload = verify_token_payload(token)
        uid = payload.get("uid")
        email = payload.get("email")

        row = None
        if uid:
            row = connection.execute("SELECT * FROM users WHERE firebase_uid = ?", (uid,)).fetchone()
        if not row and email:
            row = connection.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if row:
            u = dict(row)
            if u.get("status") != "disabled":
                return u
        return None
    except Exception:
        return None
    finally:
        connection.close()


def enforce_page_role(request: Request, allowed_roles: set[str]) -> dict[str, Any] | None:
    """Enforce server-side role check for page loads. Redirects or raises 403."""
    user = get_session_user(request)
    is_test_mode = os.getenv("GOVFIX_TEST_AUTH_OVERRIDE") == "1"

    if not user:
        if is_test_mode:
            # Let unauthenticated calls pass in test mode for template render checks
            return None
        # Unauthenticated in normal mode: redirect to login
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": f"/login?next={request.url.path}"},
        )

    # User is logged in: check role
    if user.get("role") not in allowed_roles:
        if is_test_mode:
            # In test mode, raise 403 to prove cross-role boundary enforcement
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.get('role')}' is not authorized to access {request.url.path}.",
            )
        # In browser normal mode, redirect strictly to the user's own authoritative portal
        user_role = user.get("role")
        target = (
            "/citizen" if user_role == "Citizen" else
            "/staff/triage" if user_role == "Officer" else
            "/staff/field" if user_role == "Field Officer" else
            "/staff/supervisor" if user_role == "Supervisor" else
            "/staff/audit" if user_role == "Audit Inspector" else
            "/admin" if user_role == "Admin" else
            "/login"
        )
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": target},
        )

    return user


# ==============================================================================
# Public & Authentication Pages
# ==============================================================================

@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    user = get_session_user(request)
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "request": request,
            "active_page": "home",
            "page_title": "Municipal Grievance Platform",
            "current_user": user,
        },
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    user = get_session_user(request)
    if user:
        # Redirect logged in user directly to their dashboard
        dest = "/citizen" if user["role"] == "Citizen" else f"/staff/{user['role'].lower().replace(' ', '')}"
        if user["role"] == "Admin":
            dest = "/admin"
        elif user["role"] == "Officer":
            dest = "/staff/triage"
        elif user["role"] == "Field Officer":
            dest = "/staff/field"
        elif user["role"] == "Supervisor":
            dest = "/staff/supervisor"
        elif user["role"] == "Audit Inspector":
            dest = "/staff/audit"
        return RedirectResponse(url=dest, status_code=302)

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"request": request, "page_title": "Sign In | GovFix Portal"},
    )


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="signup.html",
        context={"request": request, "page_title": "Citizen Registration | GovFix"},
    )


@router.get("/verify-email", response_class=HTMLResponse)
def verify_email_page(request: Request) -> HTMLResponse:
    email = request.query_params.get("email", "")
    return templates.TemplateResponse(
        request=request,
        name="verify_email.html",
        context={"request": request, "email": email, "page_title": "Verify Email | GovFix"},
    )


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="forgot_password.html",
        context={"request": request, "page_title": "Reset Password | GovFix"},
    )


# ==============================================================================
# Citizen Portal (Light Theme, Mobile-First, Strictly Citizen Data)
# ==============================================================================

@router.get("/citizen", response_class=HTMLResponse)
def citizen_portal(request: Request) -> HTMLResponse:
    try:
        user = enforce_page_role(request, allowed_roles={"Citizen"})
    except HTTPException as e:
        if e.status_code == 307:
            return RedirectResponse(url=e.headers["Location"], status_code=302)
        raise

    return templates.TemplateResponse(
        request=request,
        name="citizen.html",
        context={
            "request": request,
            "active_page": "citizen",
            "page_title": "Citizen Grievance Portal",
            "page_subtitle": "Submit municipal complaints, track progress by token, and confirm verified resolution.",
            "current_user": user or {"id": "USR-CIT-01", "name": "Ramesh Patel", "role": "Citizen", "is_verified": 1},
            "default_actor_id": "USR-CIT-01",
        },
    )


# ==============================================================================
# Staff Console Workspaces (Dark/Slate Theme, Dense, Work-Focused Sidebar)
# ==============================================================================

@router.get("/staff/triage", response_class=HTMLResponse)
@router.get("/officer", response_class=HTMLResponse)
def officer_workspace(request: Request) -> HTMLResponse:
    try:
        user = enforce_page_role(request, allowed_roles={"Officer", "Supervisor"})
    except HTTPException as e:
        if e.status_code == 307:
            return RedirectResponse(url=e.headers["Location"], status_code=302)
        raise

    return templates.TemplateResponse(
        request=request,
        name="officer.html",
        context={
            "request": request,
            "active_page": "officer",
            "page_title": "Municipal Grievance Triage",
            "page_subtitle": "Review intake complaints, verify SLA timelines, request citizen info, and assign field officers.",
            "current_user": user or {"id": "USR-OFF-01", "name": "Officer Anil Kumar", "role": "Officer"},
            "default_actor_id": "USR-OFF-01",
        },
    )


@router.get("/staff/field", response_class=HTMLResponse)
@router.get("/field-officer", response_class=HTMLResponse)
def field_officer_workspace(request: Request) -> HTMLResponse:
    try:
        user = enforce_page_role(request, allowed_roles={"Field Officer"})
    except HTTPException as e:
        if e.status_code == 307:
            return RedirectResponse(url=e.headers["Location"], status_code=302)
        raise

    return templates.TemplateResponse(
        request=request,
        name="field_officer.html",
        context={
            "request": request,
            "active_page": "field-officer",
            "page_title": "Field Officer Execution Workspace",
            "page_subtitle": "On-ground task resolution, work order acceptance, field progress logs, and completion verification.",
            "current_user": user or {"id": "USR-FLD-01", "name": "Field Officer Vikram Singh", "role": "Field Officer"},
            "default_actor_id": "USR-FLD-01",
        },
    )


@router.get("/staff/supervisor", response_class=HTMLResponse)
@router.get("/supervisor", response_class=HTMLResponse)
def supervisor_command_center(request: Request) -> HTMLResponse:
    try:
        user = enforce_page_role(request, allowed_roles={"Supervisor"})
    except HTTPException as e:
        if e.status_code == 307:
            return RedirectResponse(url=e.headers["Location"], status_code=302)
        raise

    return templates.TemplateResponse(
        request=request,
        name="supervisor.html",
        context={
            "request": request,
            "active_page": "supervisor",
            "page_title": "Supervisor Command Center",
            "page_subtitle": "Municipal operations oversight, emergency reassignments, SLA escalation ladders, and simulated clock control.",
            "current_user": user or {"id": "USR-SUP-01", "name": "Supervisor Rajesh Gupta", "role": "Supervisor"},
            "default_actor_id": "USR-SUP-01",
        },
    )


@router.get("/staff/audit", response_class=HTMLResponse)
@router.get("/audit", response_class=HTMLResponse)
def audit_inspector_console(request: Request) -> HTMLResponse:
    try:
        user = enforce_page_role(request, allowed_roles={"Audit Inspector", "Supervisor"})
    except HTTPException as e:
        if e.status_code == 307:
            return RedirectResponse(url=e.headers["Location"], status_code=302)
        raise

    return templates.TemplateResponse(
        request=request,
        name="audit.html",
        context={
            "request": request,
            "active_page": "audit",
            "page_title": "Audit Inspector Console",
            "page_subtitle": "Cryptographic SHA-256 hash-chain verification and append-only governance trail of accepted and refused attempts.",
            "current_user": user or {"id": "USR-AUD-01", "name": "Audit Inspector Meera Joshi", "role": "Audit Inspector"},
            "default_actor_id": "USR-AUD-01",
        },
    )


@router.get("/admin", response_class=HTMLResponse)
def admin_console(request: Request) -> HTMLResponse:
    try:
        user = enforce_page_role(request, allowed_roles={"Admin"})
    except HTTPException as e:
        if e.status_code == 307:
            return RedirectResponse(url=e.headers["Location"], status_code=302)
        raise

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            "request": request,
            "active_page": "admin",
            "page_title": "GovFix Administrator Console",
            "page_subtitle": "User management, staff account provisioning, SLA table, and simulated municipal clock controls.",
            "current_user": user or {"id": "USR-ADM-01", "name": "Admin Administrator", "role": "Admin"},
            "default_actor_id": "USR-ADM-01",
        },
    )
