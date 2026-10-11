"""HTTP adapters for the Plan 2 request workflow."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

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
from src.ai.local_classifier import LocalClassifier
from src.services.clock_service import ClockService
from src.workflow import WorkflowService


router = APIRouter(prefix="/api/v1", tags=["requests"])


def get_connection() -> Generator[sqlite3.Connection, None, None]:
    connection = connect()
    try:
        yield connection
    finally:
        connection.close()


def get_actor_id(
    x_actor_id: Annotated[str, Header(alias="X-Actor-ID", min_length=1)],
) -> str:
    return x_actor_id


Connection = Annotated[sqlite3.Connection, Depends(get_connection)]
ActorId = Annotated[str, Depends(get_actor_id)]


def get_workflow_service(connection: Connection) -> WorkflowService:
    clock = ClockService(connection=connection)
    return WorkflowService(connection, now_provider=clock.now)


Workflow = Annotated[WorkflowService, Depends(get_workflow_service)]


@router.post("/requests", status_code=201)
def create_request(
    command: RequestCreate, actor_id: ActorId, workflow: Workflow
) -> dict:
    data = workflow.create_request(actor_id=actor_id, command=command)
    return {"success": True, "data": data}


@router.post("/requests/{request_id}/transition")
def transition_request(
    request_id: str,
    command: TransitionCommand,
    actor_id: ActorId,
    workflow: Workflow,
) -> dict:
    data = workflow.transition(
        request_id=request_id, actor_id=actor_id, command=command
    )
    return {"success": True, "data": data}


@router.post("/requests/{request_id}/appeal")
def submit_request_appeal(
    request_id: str,
    command: AppealCommand,
    actor_id: ActorId,
    workflow: Workflow,
) -> dict:
    data = workflow.submit_appeal(
        request_id=request_id, actor_id=actor_id, command=command
    )
    return {"success": True, "data": data}


@router.post("/requests/{request_id}/progress")
def add_request_progress(
    request_id: str,
    command: ProgressCommand,
    actor_id: ActorId,
    workflow: Workflow,
) -> dict:
    data = workflow.add_progress(
        request_id=request_id, actor_id=actor_id, command=command
    )
    return {"success": True, "data": data}


@router.post("/requests/{request_id}/category")
def confirm_request_category(
    request_id: str,
    command: CategoryCommand,
    actor_id: ActorId,
    workflow: Workflow,
) -> dict:
    data = workflow.confirm_category(
        request_id=request_id, actor_id=actor_id, command=command
    )
    return {"success": True, "data": data}


@router.post("/requests/{request_id}/reassign")
def reassign_request(
    request_id: str,
    command: ReassignCommand,
    actor_id: ActorId,
    workflow: Workflow,
) -> dict:
    data = workflow.reassign(
        request_id=request_id, actor_id=actor_id, command=command
    )
    return {"success": True, "data": data}


@router.get("/requests/{request_id}")
def get_request(
    request_id: str, actor_id: ActorId, workflow: Workflow
) -> dict:
    data = workflow.get_request(
        request_id=request_id, actor_id=actor_id
    )
    return {"success": True, "data": data}


@router.get("/requests/{request_id}/history")
def get_request_history(
    request_id: str, actor_id: ActorId, workflow: Workflow
) -> dict:
    data = workflow.get_request_history(
        request_id=request_id, actor_id=actor_id
    )
    return {"success": True, "data": data}


@router.post("/advisory/preview")
def preview_advisory(
    command: AdvisoryPreviewRequest, actor_id: ActorId
) -> dict:
    engine = LocalClassifier()
    full_text = f"{command.title or ''} {command.text}".strip()
    suggestion = engine.suggest(full_text)
    return {"success": True, "data": suggestion.model_dump()}


@router.get("/requests")
def list_requests(
    actor_id: ActorId,
    workflow: Workflow,
    state: Annotated[RequestState | None, Query()] = None,
    assigned_to: Annotated[str | None, Query()] = None,
    citizen_id: Annotated[str | None, Query()] = None,
    category: Annotated[Category | None, Query()] = None,
    is_overdue: Annotated[bool | None, Query()] = None,
) -> dict:
    data = workflow.list_requests(
        actor_id=actor_id,
        state=state,
        assigned_to=assigned_to,
        citizen_id=citizen_id,
        category=category,
        is_overdue=is_overdue,
    )
    return {"success": True, "data": data}


@router.get("/audit")
def list_audit(actor_id: ActorId, workflow: Workflow) -> dict:
    data = workflow.list_audit(actor_id=actor_id)
    return {"success": True, "data": data}


@router.get("/audit/verify")
def verify_audit(actor_id: ActorId, workflow: Workflow) -> dict:
    data = workflow.verify_audit(actor_id=actor_id)
    return {"success": True, "data": data}
