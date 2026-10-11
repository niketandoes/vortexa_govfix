"""UI routes serving server-rendered Jinja2 templates for the 5 GovFix personas."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

FRONTEND_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "frontend" / "templates"
TEMPLATES_DIR = (
    FRONTEND_TEMPLATES_DIR
    if FRONTEND_TEMPLATES_DIR.exists()
    else Path(__file__).resolve().parent.parent / "templates"
)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["UI"])

SEEDED_PERSONAS = [
    {
        "id": "USR-CIT-01",
        "name": "Ramesh Patel",
        "role": "Citizen",
        "badge": "Citizen",
        "department": "Civilian",
        "default_page": "citizen",
    },
    {
        "id": "USR-CIT-02",
        "name": "Priya Sharma",
        "role": "Citizen",
        "badge": "Citizen",
        "department": "Civilian",
        "default_page": "citizen",
    },
    {
        "id": "USR-OFF-01",
        "name": "Officer Anil Kumar",
        "role": "Officer",
        "badge": "Triage Officer",
        "department": "Public Works",
        "default_page": "officer",
    },
    {
        "id": "USR-FLD-01",
        "name": "Field Officer Vikram Singh",
        "role": "Field Officer",
        "badge": "Field Officer",
        "department": "Public Works",
        "default_page": "field-officer",
    },
    {
        "id": "USR-FLD-02",
        "name": "Field Officer Sunita Rao",
        "role": "Field Officer",
        "badge": "Field Officer",
        "department": "Sanitation",
        "default_page": "field-officer",
    },
    {
        "id": "USR-SUP-01",
        "name": "Supervisor Rajesh Gupta",
        "role": "Supervisor",
        "badge": "Supervisor / Head",
        "department": "Municipal Administration",
        "default_page": "supervisor",
    },
    {
        "id": "USR-AUD-01",
        "name": "Audit Inspector Meera Joshi",
        "role": "Audit Inspector",
        "badge": "Audit Inspector",
        "department": "Municipal Audit Commission",
        "default_page": "audit",
    },
]


@router.get("/", response_class=RedirectResponse, include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse(url="/citizen", status_code=307)


@router.get("/citizen", response_class=HTMLResponse)
def citizen_portal(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="citizen.html",
        context={
            "request": request,
            "active_page": "citizen",
            "page_title": "Citizen Grievance Portal",
            "page_subtitle": "Submit municipal complaints, track progress by token, and confirm verified resolution.",
            "default_actor_id": "USR-CIT-01",
            "default_persona_role": "Citizen",
            "personas": SEEDED_PERSONAS,
        },
    )


@router.get("/officer", response_class=HTMLResponse)
def officer_workspace(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="officer.html",
        context={
            "request": request,
            "active_page": "officer",
            "page_title": "Municipal Grievance Triage",
            "page_subtitle": "Review intake complaints, verify SLA timelines, request citizen info, and assign field officers.",
            "default_actor_id": "USR-OFF-01",
            "default_persona_role": "Officer",
            "personas": SEEDED_PERSONAS,
        },
    )


@router.get("/field-officer", response_class=HTMLResponse)
def field_officer_workspace(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="field_officer.html",
        context={
            "request": request,
            "active_page": "field-officer",
            "page_title": "Field Officer Execution Workspace",
            "page_subtitle": "On-ground task resolution, work order acceptance, field progress logs, and completion verification.",
            "default_actor_id": "USR-FLD-01",
            "default_persona_role": "Field Officer",
            "personas": SEEDED_PERSONAS,
        },
    )


@router.get("/supervisor", response_class=HTMLResponse)
def supervisor_command_center(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="supervisor.html",
        context={
            "request": request,
            "active_page": "supervisor",
            "page_title": "Supervisor Command Center",
            "page_subtitle": "Municipal operations oversight, emergency reassignments, SLA escalation ladders, and simulated clock control.",
            "default_actor_id": "USR-SUP-01",
            "default_persona_role": "Supervisor",
            "personas": SEEDED_PERSONAS,
        },
    )


@router.get("/audit", response_class=HTMLResponse)
def audit_inspector_console(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="audit.html",
        context={
            "request": request,
            "active_page": "audit",
            "page_title": "Audit Inspector Console",
            "page_subtitle": "Cryptographic SHA-256 hash-chain verification and append-only governance trail of accepted and refused attempts.",
            "default_actor_id": "USR-AUD-01",
            "default_persona_role": "Audit Inspector",
            "personas": SEEDED_PERSONAS,
        },
    )
