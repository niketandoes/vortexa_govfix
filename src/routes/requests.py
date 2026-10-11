"""HTTP adapters for the Plan 2 request workflow."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from src.database import connect
from src.models import (
    Category,
    CategoryCommand,
    ReassignCommand,
    RequestCreate,
    RequestState,
    TransitionCommand,
)
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


@router.post("/requests", status_code=201)
def create_request(
    command: RequestCreate, connection: Connection, actor_id: ActorId
) -> dict:
    data = WorkflowService(connection).create_request(
        actor_id=actor_id, command=command
    )
    return {"success": True, "data": data}


@router.post("/requests/{request_id}/transition")
def transition_request(
    request_id: str,
    command: TransitionCommand,
    connection: Connection,
    actor_id: ActorId,
) -> dict:
    data = WorkflowService(connection).transition(
        request_id=request_id, actor_id=actor_id, command=command
    )
    return {"success": True, "data": data}


@router.post("/requests/{request_id}/category")
def confirm_request_category(
    request_id: str,
    command: CategoryCommand,
    connection: Connection,
    actor_id: ActorId,
) -> dict:
    data = WorkflowService(connection).confirm_category(
        request_id=request_id, actor_id=actor_id, command=command
    )
    return {"success": True, "data": data}


@router.post("/requests/{request_id}/reassign")
def reassign_request(
    request_id: str,
    command: ReassignCommand,
    connection: Connection,
    actor_id: ActorId,
) -> dict:
    data = WorkflowService(connection).reassign(
        request_id=request_id, actor_id=actor_id, command=command
    )
    return {"success": True, "data": data}


@router.get("/requests/{request_id}")
def get_request(
    request_id: str, connection: Connection, actor_id: ActorId
) -> dict:
    data = WorkflowService(connection).get_request(
        request_id=request_id, actor_id=actor_id
    )
    return {"success": True, "data": data}


@router.get("/requests")
def list_requests(
    connection: Connection,
    actor_id: ActorId,
    state: Annotated[RequestState | None, Query()] = None,
    assigned_to: Annotated[str | None, Query()] = None,
    citizen_id: Annotated[str | None, Query()] = None,
    category: Annotated[Category | None, Query()] = None,
    is_overdue: Annotated[bool | None, Query()] = None,
) -> dict:
    data = WorkflowService(connection).list_requests(
        actor_id=actor_id,
        state=state,
        assigned_to=assigned_to,
        citizen_id=citizen_id,
        category=category,
        is_overdue=is_overdue,
    )
    return {"success": True, "data": data}


@router.get("/audit")
def list_audit(connection: Connection, actor_id: ActorId) -> dict:
    data = WorkflowService(connection).list_audit(actor_id=actor_id)
    return {"success": True, "data": data}


@router.get("/audit/verify")
def verify_audit(connection: Connection, actor_id: ActorId) -> dict:
    data = WorkflowService(connection).verify_audit(actor_id=actor_id)
    return {"success": True, "data": data}
