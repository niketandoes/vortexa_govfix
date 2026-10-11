"""HTTP adapters for the GovFix request workflow with authoritative RBAC and email triggers."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, status

from src.ai.local_classifier import LocalClassifier
from src.auth import get_current_user
from src.database import connect
from src.models import (
    AdvisoryPreviewRequest,
    AppealCommand,
    Category,
    CategoryCommand,
    ProgressCommand,
    ReassignCommand,
    RequestCreate,
    RequestState,
    TransitionCommand,
)
from src.services.clock_service import ClockService
from src.services.email_service import dispatch_email_sync
from src.workflow import WorkflowError, WorkflowService


router = APIRouter(prefix="/api/v1", tags=["requests"])


def get_connection() -> Generator[sqlite3.Connection, None, None]:
    connection = connect()
    try:
        yield connection
    finally:
        connection.close()


CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]
Connection = Annotated[sqlite3.Connection, Depends(get_connection)]


def get_actor_id(current_user: CurrentUser) -> str:
    """Extract authoritative actor ID from verified user identity."""
    return current_user["id"]


ActorId = Annotated[str, Depends(get_actor_id)]


def get_workflow_service(connection: Connection) -> WorkflowService:
    clock = ClockService(connection=connection)
    return WorkflowService(connection, now_provider=clock.now)


Workflow = Annotated[WorkflowService, Depends(get_workflow_service)]


@router.post("/requests", status_code=201)
def create_request(
    command: RequestCreate,
    current_user: CurrentUser,
    workflow: Workflow,
    background_tasks: BackgroundTasks,
    connection: Connection,
) -> dict[str, Any]:
    """Lodge a new grievance.
    
    Restricted to verified Citizens. Dispatches complaint_received notification post-commit.
    """
    if current_user.get("role") != "Citizen":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only citizens may submit grievances.",
        )
    if not current_user.get("is_verified"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email verification required before lodging complaints.",
        )

    actor_id = current_user["id"]
    data = workflow.create_request(actor_id=actor_id, command=command)

    # Post-commit async email
    if current_user.get("email"):
        background_tasks.add_task(
            dispatch_email_sync,
            recipient=current_user["email"],
            template="complaint_received",
            context={
                "name": current_user["name"],
                "request_id": data["id"],
                "title": data["title"],
                "location": data["location"],
                "deadline": data.get("deadline", "Standard Statutory SLA"),
                "link": f"http://127.0.0.1:8000/citizen?view={data['id']}",
            },
            event_id=f"received-{data['id']}",
        )

    return {"success": True, "data": data}


@router.post("/requests/{request_id}/transition")
def transition_request(
    request_id: str,
    command: TransitionCommand,
    current_user: CurrentUser,
    workflow: Workflow,
    background_tasks: BackgroundTasks,
    connection: Connection,
) -> dict[str, Any]:
    """Execute a lifecycle state transition.
    
    Enforces transition rules, role authority, version check, and dispatches role-specific emails.
    """
    actor_id = current_user["id"]
    actor_role = current_user.get("role")

    if actor_role in ("Audit Inspector", "Admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{actor_role} accounts have no operational workflow transition powers.",
        )

    data = workflow.transition(
        request_id=request_id, actor_id=actor_id, command=command
    )

    # Post-commit async notifications
    to_state = command.to_state.value if hasattr(command.to_state, "value") else str(command.to_state)
    citizen_row = connection.execute(
        "SELECT u.name, u.email FROM users u JOIN requests r ON u.id = r.citizen_id WHERE r.id = ?",
        (request_id,),
    ).fetchone()

    if citizen_row and citizen_row["email"]:
        cit_email = citizen_row["email"]
        cit_name = citizen_row["name"]

        if to_state == "ACKNOWLEDGED":
            if command.appeal_approval_note or data.get("appeal_approval_note"):
                background_tasks.add_task(
                    dispatch_email_sync,
                    recipient=cit_email,
                    template="appeal_decision",
                    context={
                        "name": cit_name,
                        "request_id": request_id,
                        "appeal_approval_note": command.appeal_approval_note or data.get("appeal_approval_note", "Appeal approved upon administrative review."),
                        "link": f"http://127.0.0.1:8000/citizen?view={request_id}",
                    },
                    event_id=f"appeal-dec-{request_id}-{data.get('version', 1)}",
                )
            else:
                background_tasks.add_task(
                    dispatch_email_sync,
                    recipient=cit_email,
                    template="acknowledged_categorised",
                    context={
                        "name": cit_name,
                        "request_id": request_id,
                        "category": data.get("category", "General"),
                        "deadline": data.get("deadline", ""),
                    },
                    event_id=f"ack-{request_id}-{data.get('version', 1)}",
                )
        elif to_state == "NEEDS_INFO":
            background_tasks.add_task(
                dispatch_email_sync,
                recipient=cit_email,
                template="needs_info",
                context={
                    "name": cit_name,
                    "request_id": request_id,
                    "info_request_note": command.info_request_note or "Additional information required.",
                    "link": f"http://127.0.0.1:8000/citizen?view={request_id}",
                },
                event_id=f"needs-info-{request_id}-{data.get('version', 1)}",
            )
        elif to_state == "ASSIGNED":
            team_display = data.get("assigned_team") or "Municipal Field Operations"
            background_tasks.add_task(
                dispatch_email_sync,
                recipient=cit_email,
                template="assigned",
                context={
                    "name": cit_name,
                    "request_id": request_id,
                    "team": team_display,
                },
                event_id=f"assigned-cit-{request_id}-{data.get('version', 1)}",
            )
        elif to_state == "RESOLVED":
            background_tasks.add_task(
                dispatch_email_sync,
                recipient=cit_email,
                template="resolved",
                context={
                    "name": cit_name,
                    "request_id": request_id,
                    "notes": command.resolution_notes or "Work completed per municipal standards.",
                },
                event_id=f"resolved-{request_id}-{data.get('version', 1)}",
            )
        elif to_state == "REJECTED":
            background_tasks.add_task(
                dispatch_email_sync,
                recipient=cit_email,
                template="rejected",
                context={
                    "name": cit_name,
                    "request_id": request_id,
                    "reason": command.rejection_reason or "Does not meet municipal jurisdiction criteria.",
                },
                event_id=f"rejected-{request_id}-{data.get('version', 1)}",
            )

    # If assigned, also notify the assigned field officer
    if to_state == "ASSIGNED" and data.get("assigned_to"):
        officer_row = connection.execute(
            "SELECT name, email FROM users WHERE id = ?", (data["assigned_to"],)
        ).fetchone()
        if officer_row and officer_row["email"]:
            background_tasks.add_task(
                dispatch_email_sync,
                recipient=officer_row["email"],
                template="new_assignment",
                context={
                    "name": officer_row["name"],
                    "request_id": request_id,
                    "title": data.get("title", ""),
                    "location": data.get("location", ""),
                    "category": data.get("category", ""),
                    "deadline": data.get("deadline", ""),
                },
                event_id=f"assigned-fld-{request_id}-{data.get('version', 1)}",
            )

    return {"success": True, "data": data}


@router.post("/requests/{request_id}/appeal")
def submit_request_appeal(
    request_id: str,
    command: AppealCommand,
    current_user: CurrentUser,
    workflow: Workflow,
) -> dict[str, Any]:
    """Citizen appeals a rejected grievance."""
    if current_user.get("role") != "Citizen":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the affected citizen may appeal a rejection.",
        )
    data = workflow.submit_appeal(
        request_id=request_id, actor_id=current_user["id"], command=command
    )
    return {"success": True, "data": data}


@router.post("/requests/{request_id}/progress")
def add_request_progress(
    request_id: str,
    command: ProgressCommand,
    current_user: CurrentUser,
    workflow: Workflow,
) -> dict[str, Any]:
    """Field officer records on-site progress notes."""
    if current_user.get("role") != "Field Officer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only assigned field officers may record progress notes.",
        )
    data = workflow.add_progress(
        request_id=request_id, actor_id=current_user["id"], command=command
    )
    return {"success": True, "data": data}


@router.post("/requests/{request_id}/category")
def confirm_request_category(
    request_id: str,
    command: CategoryCommand,
    current_user: CurrentUser,
    workflow: Workflow,
) -> dict[str, Any]:
    """Triage Officer or Supervisor confirms category."""
    if current_user.get("role") not in ("Officer", "Supervisor"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only triage officers and supervisors may confirm categories.",
        )
    data = workflow.confirm_category(
        request_id=request_id, actor_id=current_user["id"], command=command
    )
    return {"success": True, "data": data}


@router.post("/requests/{request_id}/reassign")
def reassign_request(
    request_id: str,
    command: ReassignCommand,
    current_user: CurrentUser,
    workflow: Workflow,
    background_tasks: BackgroundTasks,
    connection: Connection,
) -> dict[str, Any]:
    """Supervisor reassigns case to another field officer."""
    if current_user.get("role") != "Supervisor":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only supervisors may reassign cases.",
        )
    data = workflow.reassign(
        request_id=request_id, actor_id=current_user["id"], command=command
    )

    # Notify newly assigned officer
    officer_row = connection.execute(
        "SELECT name, email FROM users WHERE id = ?", (command.assigned_to,)
    ).fetchone()
    if officer_row and officer_row["email"]:
        background_tasks.add_task(
            dispatch_email_sync,
            recipient=officer_row["email"],
            template="reassignment",
            context={
                "name": officer_row["name"],
                "request_id": request_id,
                "title": data.get("title", ""),
                "location": data.get("location", ""),
                "category": data.get("category", ""),
                "deadline": data.get("deadline", ""),
            },
            event_id=f"reassign-{request_id}-{data.get('version', 1)}",
        )

    return {"success": True, "data": data}


@router.get("/requests/{request_id}")
def get_request(
    request_id: str, current_user: CurrentUser, workflow: Workflow
) -> dict[str, Any]:
    """Fetch complaint details. Enforces isolation and strips staff IDs for citizens."""
    data = workflow.get_request(
        request_id=request_id, actor_id=current_user["id"]
    )
    return {"success": True, "data": data}


@router.get("/requests/{request_id}/history")
def get_request_history(
    request_id: str, current_user: CurrentUser, workflow: Workflow
) -> dict[str, Any]:
    """Fetch history timeline for a complaint."""
    data = workflow.get_request_history(
        request_id=request_id, actor_id=current_user["id"]
    )
    return {"success": True, "data": data}


@router.post("/advisory/preview")
def preview_advisory(
    command: AdvisoryPreviewRequest, current_user: CurrentUser
) -> dict[str, Any]:
    """Advisory category preview for complaint drafting."""
    engine = LocalClassifier()
    full_text = f"{command.title or ''} {command.text}".strip()
    suggestion = engine.suggest(full_text)
    return {"success": True, "data": suggestion.model_dump()}


@router.get("/requests/{request_id}/suggestions")
def get_request_suggestions(
    request_id: str,
    current_user: CurrentUser,
    workflow: Workflow,
    connection: Connection,
) -> dict[str, Any]:
    """AI advisory suggestions for triage."""
    from src.ai.local_classifier import LocalClassifier
    from src.services.suggestion_service import (
        SQLiteSuggestionRepository,
        SuggestionService,
    )

    req = workflow.get_request(request_id=request_id, actor_id=current_user["id"])
    repo = SQLiteSuggestionRepository(connection=connection)
    service = SuggestionService(repository=repo)
    stored = service.get_suggestions_for_request(request_id)

    full_text = f"{req.get('title', '')}. {req.get('text', '')}".strip()
    if not stored:
        suggestion = service.generate_and_store_suggestions(
            request_id=request_id,
            complaint_text=full_text,
        )
        stored = service.get_suggestions_for_request(request_id)
        recommendation = suggestion.model_dump()
    else:
        recommendation = LocalClassifier().suggest(full_text).model_dump()

    return {
        "success": True,
        "data": {
            "stored": stored,
            "recommendation": recommendation,
        },
    }


@router.get("/citizen/stats")
def get_citizen_stats(
    current_user: CurrentUser,
    connection: Connection,
) -> dict[str, Any]:
    """Compute stats strictly over the citizen's own grievances. Never platform-wide."""
    if current_user.get("role") != "Citizen":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Citizen stats are restricted to citizen users.",
        )

    cit_id = current_user["id"]
    total = connection.execute(
        "SELECT COUNT(*) FROM requests WHERE citizen_id = ?", (cit_id,)
    ).fetchone()[0]

    active = connection.execute(
        """
        SELECT COUNT(*) FROM requests 
        WHERE citizen_id = ? AND state IN ('SUBMITTED', 'ACKNOWLEDGED', 'NEEDS_INFO', 'ASSIGNED', 'IN_PROGRESS', 'REOPENED')
        """,
        (cit_id,),
    ).fetchone()[0]

    in_progress = connection.execute(
        """
        SELECT COUNT(*) FROM requests 
        WHERE citizen_id = ? AND state IN ('SUBMITTED', 'ACKNOWLEDGED', 'ASSIGNED', 'IN_PROGRESS')
        """,
        (cit_id,),
    ).fetchone()[0]

    action_needed = connection.execute(
        """
        SELECT COUNT(*) FROM requests 
        WHERE citizen_id = ? AND state IN ('NEEDS_INFO', 'RESOLVED')
        """,
        (cit_id,),
    ).fetchone()[0]

    resolved = connection.execute(
        "SELECT COUNT(*) FROM requests WHERE citizen_id = ? AND state IN ('RESOLVED', 'CLOSED')",
        (cit_id,),
    ).fetchone()[0]

    rejected = connection.execute(
        "SELECT COUNT(*) FROM requests WHERE citizen_id = ? AND state = 'REJECTED'",
        (cit_id,),
    ).fetchone()[0]

    return {
        "success": True,
        "data": {
            "total": total,
            "total_complaints": total,
            "active": active,
            "active_complaints": active,
            "in_progress": in_progress,
            "action_needed": action_needed,
            "resolved": resolved,
            "resolved_complaints": resolved,
            "resolved_closed": resolved,
            "rejected": rejected,
            "rejected_complaints": rejected,
        },
    }


@router.get("/requests")
def list_requests(
    current_user: CurrentUser,
    workflow: Workflow,
    state: Annotated[RequestState | None, Query()] = None,
    assigned_to: Annotated[str | None, Query()] = None,
    citizen_id: Annotated[str | None, Query()] = None,
    category: Annotated[Category | None, Query()] = None,
    is_overdue: Annotated[bool | None, Query()] = None,
) -> dict[str, Any]:
    """List grievances scoped by role on the server."""
    data = workflow.list_requests(
        actor_id=current_user["id"],
        state=state,
        assigned_to=assigned_to,
        citizen_id=citizen_id,
        category=category,
        is_overdue=is_overdue,
    )
    return {"success": True, "data": data}


@router.get("/audit")
def list_audit(current_user: CurrentUser, workflow: Workflow) -> dict[str, Any]:
    """Read-only audit log inspection for Supervisors and Audit Inspectors."""
    data = workflow.list_audit(actor_id=current_user["id"])
    return {"success": True, "data": data}


@router.get("/audit/verify")
def verify_audit(current_user: CurrentUser, workflow: Workflow) -> dict[str, Any]:
    """Cryptographic SHA-256 chain verification for Supervisors and Audit Inspectors."""
    data = workflow.verify_audit(actor_id=current_user["id"])
    return {"success": True, "data": data}
