"""Deterministic GovFix workflow engine and command service."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Callable, Mapping

from src.models import (
    AppealCommand,
    Category,
    CategoryCommand,
    ProgressCommand,
    ReassignCommand,
    RequestCreate,
    RequestState,
    TransitionCommand,
    UserRole,
)
from src.services.audit_service import GENESIS_HASH, append_audit, canonical_json, utc_iso


ACTIVE_STATES = frozenset(
    {
        RequestState.SUBMITTED,
        RequestState.ACKNOWLEDGED,
        RequestState.NEEDS_INFO,
        RequestState.ASSIGNED,
        RequestState.IN_PROGRESS,
        RequestState.REOPENED,
    }
)

SLA_HOURS: Mapping[Category, int] = MappingProxyType(
    {
        Category.WATER_LEAK: 24,
        Category.GARBAGE_COLLECTION: 24,
        Category.STREETLIGHT: 48,
        Category.POTHOLE: 72,
        Category.UNCATEGORISED: 72,
    }
)


@dataclass(frozen=True)
class TransitionRule:
    roles: frozenset[UserRole]
    owner_required_for_citizen: bool = False
    assigned_officer_required: bool = False


TRANSITION_RULES: Mapping[tuple[RequestState, RequestState], TransitionRule] = (
    MappingProxyType(
        {
            (RequestState.SUBMITTED, RequestState.ACKNOWLEDGED): TransitionRule(
                frozenset({UserRole.OFFICER, UserRole.SUPERVISOR})
            ),
            (RequestState.ACKNOWLEDGED, RequestState.NEEDS_INFO): TransitionRule(
                frozenset({UserRole.OFFICER, UserRole.SUPERVISOR})
            ),
            (RequestState.NEEDS_INFO, RequestState.ACKNOWLEDGED): TransitionRule(
                frozenset({UserRole.CITIZEN}), owner_required_for_citizen=True
            ),
            (RequestState.ACKNOWLEDGED, RequestState.ASSIGNED): TransitionRule(
                frozenset({UserRole.OFFICER, UserRole.SUPERVISOR})
            ),
            (RequestState.ACKNOWLEDGED, RequestState.REJECTED): TransitionRule(
                frozenset({UserRole.OFFICER, UserRole.SUPERVISOR})
            ),
            (RequestState.REJECTED, RequestState.ACKNOWLEDGED): TransitionRule(
                frozenset({UserRole.SUPERVISOR})
            ),
            (RequestState.ASSIGNED, RequestState.IN_PROGRESS): TransitionRule(
                frozenset({UserRole.FIELD_OFFICER}), assigned_officer_required=True
            ),
            (RequestState.IN_PROGRESS, RequestState.RESOLVED): TransitionRule(
                frozenset({UserRole.FIELD_OFFICER}), assigned_officer_required=True
            ),
            (RequestState.RESOLVED, RequestState.CLOSED): TransitionRule(
                frozenset({UserRole.CITIZEN}),
                owner_required_for_citizen=True,
            ),
            (RequestState.RESOLVED, RequestState.REOPENED): TransitionRule(
                frozenset({UserRole.CITIZEN}), owner_required_for_citizen=True
            ),
            (RequestState.CLOSED, RequestState.REOPENED): TransitionRule(
                frozenset({UserRole.CITIZEN}), owner_required_for_citizen=True
            ),
            (RequestState.REOPENED, RequestState.ASSIGNED): TransitionRule(
                frozenset({UserRole.SUPERVISOR})
            ),
        }
    )
)


class WorkflowError(Exception):
    """Domain error carrying the stable API error contract."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        current_state: str | None = None,
        requested_state: str | None = None,
        permitted_next_states: list[str] | None = None,
        actor_role: str | None = None,
        audit_logged: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.current_state = current_state
        self.requested_state = requested_state
        self.permitted_next_states = permitted_next_states or []
        self.actor_role = actor_role
        self.audit_logged = audit_logged

    def as_response(self) -> dict[str, Any]:
        return {
            "success": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "current_state": self.current_state,
                "requested_state": self.requested_state,
                "permitted_next_states": self.permitted_next_states,
                "actor_role": self.actor_role,
                "audit_logged": self.audit_logged,
            },
        }


class WorkflowService:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.connection = connection
        self.now_provider = now_provider or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self.now_provider()
        if value.tzinfo is None:
            raise ValueError("now_provider must return a timezone-aware datetime")
        return value.astimezone(UTC)

    def _begin(self) -> None:
        self.connection.execute("BEGIN IMMEDIATE")

    def _actor(self, actor_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM users WHERE id = ?", (actor_id,)
        ).fetchone()

    def _request(self, request_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM requests WHERE id = ?", (request_id,)
        ).fetchone()

    @staticmethod
    def _next_states(state: RequestState) -> list[str]:
        return sorted(
            target.value
            for source, target in TRANSITION_RULES
            if source is state
        )

    def _refuse(
        self,
        *,
        actor_id: str,
        actor_role: str,
        action: str,
        request_id: str,
        status_code: int,
        code: str,
        message: str,
        current_state: str | None = None,
        requested_state: str | None = None,
        permitted_next_states: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        now = self._now()
        append_audit(
            self.connection,
            actor=actor_id or "UNKNOWN",
            actor_role=actor_role,
            action=action,
            request_id=request_id,
            from_state=current_state,
            to_state=requested_state,
            outcome="REFUSED",
            reason=message,
            details=details,
            simulated_at=now,
        )
        self.connection.commit()
        raise WorkflowError(
            status_code=status_code,
            code=code,
            message=message,
            current_state=current_state,
            requested_state=requested_state,
            permitted_next_states=permitted_next_states,
            actor_role=None if actor_role == "System" else actor_role,
            audit_logged=True,
        )

    def _require_actor(
        self, actor_id: str, *, action: str, request_id: str
    ) -> sqlite3.Row:
        actor = self._actor(actor_id)
        if actor is None:
            self._refuse(
                actor_id=actor_id,
                actor_role="System",
                action=action,
                request_id=request_id,
                status_code=401,
                code="ACTOR_NOT_FOUND",
                message="Actor does not exist",
            )
        return actor

    def _require_request(
        self,
        request_id: str,
        *,
        actor: sqlite3.Row,
        action: str,
        requested_state: str | None = None,
    ) -> sqlite3.Row:
        request = self._request(request_id)
        if request is None:
            self._refuse(
                actor_id=actor["id"],
                actor_role=actor["role"],
                action=action,
                request_id=request_id,
                status_code=404,
                code="REQUEST_NOT_FOUND",
                message="Request does not exist",
                requested_state=requested_state,
            )
        return request

    def _check_version(
        self,
        *,
        actor: sqlite3.Row,
        request: sqlite3.Row,
        expected_version: int,
        action: str,
        requested_state: str | None = None,
    ) -> None:
        if request["version"] != expected_version:
            self._refuse(
                actor_id=actor["id"],
                actor_role=actor["role"],
                action=action,
                request_id=request["id"],
                status_code=409,
                code="VERSION_CONFLICT",
                message=(
                    f"Expected version {expected_version}, but the current version is "
                    f"{request['version']}"
                ),
                current_state=request["state"],
                requested_state=requested_state,
                permitted_next_states=self._next_states(RequestState(request["state"])),
                details={
                    "expected_version": expected_version,
                    "current_version": request["version"],
                },
            )

    def create_request(
        self, *, actor_id: str, command: RequestCreate
    ) -> dict[str, Any]:
        self._begin()
        try:
            year = self._now().year
            prefix = f"REQ-{year}-"
            rows = self.connection.execute(
                "SELECT id FROM requests WHERE id LIKE ?", (f"{prefix}%",)
            ).fetchall()
            existing_sequences = [
                int(suffix)
                for row in rows
                if (suffix := row["id"].removeprefix(prefix)).isdigit()
            ]
            sequence = max(existing_sequences, default=0) + 1
            request_id = f"{prefix}{sequence:04d}"
            actor = self._require_actor(
                actor_id, action="SUBMIT_COMPLAINT", request_id=request_id
            )
            if actor["role"] != UserRole.CITIZEN.value:
                self._refuse(
                    actor_id=actor_id,
                    actor_role=actor["role"],
                    action="SUBMIT_COMPLAINT",
                    request_id=request_id,
                    status_code=403,
                    code="ROLE_NOT_PERMITTED",
                    message="Only Citizens may submit complaints",
                )

            now = self._now()
            triage_deadline = now + timedelta(hours=2)
            deadline = now + timedelta(hours=SLA_HOURS[Category.UNCATEGORISED])
            self.connection.execute(
                """
                INSERT INTO requests (
                    id, citizen_id, title, text, location, reported_category, category,
                    state, sla_started_at, created_at, updated_at, triage_deadline, deadline
                ) VALUES (?, ?, ?, ?, ?, ?, 'Uncategorised', 'SUBMITTED', ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    actor_id,
                    command.title,
                    command.text,
                    command.location,
                    command.reported_category.value
                    if command.reported_category
                    else None,
                    utc_iso(now),
                    utc_iso(now),
                    utc_iso(now),
                    utc_iso(triage_deadline),
                    utc_iso(deadline),
                ),
            )
            append_audit(
                self.connection,
                actor=actor_id,
                actor_role=actor["role"],
                action="SUBMIT_COMPLAINT",
                request_id=request_id,
                outcome="ACCEPTED",
                to_state=RequestState.SUBMITTED.value,
                reason="Complaint submitted",
                details={
                    "location": command.location,
                    "reported_category": command.reported_category.value
                    if command.reported_category
                    else None,
                    "confirmed_category": Category.UNCATEGORISED.value,
                    "triage_deadline": utc_iso(triage_deadline),
                    "deadline": utc_iso(deadline),
                    "version": 1,
                },
                simulated_at=now,
            )
            self.connection.commit()
            return self._serialize_request(self._request(request_id), actor)
        except WorkflowError:
            raise
        except Exception:
            self.connection.rollback()
            raise

    def transition(
        self,
        *,
        request_id: str,
        actor_id: str,
        command: TransitionCommand,
    ) -> dict[str, Any]:
        self._begin()
        try:
            action = "TRANSITION_ATTEMPT"
            actor = self._require_actor(actor_id, action=action, request_id=request_id)
            request = self._require_request(
                request_id,
                actor=actor,
                action=action,
                requested_state=command.to_state.value,
            )
            current = RequestState(request["state"])
            rule = TRANSITION_RULES.get((current, command.to_state))
            permitted = self._next_states(current)
            if rule is None:
                self._refuse(
                    actor_id=actor_id,
                    actor_role=actor["role"],
                    action=action,
                    request_id=request_id,
                    status_code=422,
                    code="INVALID_TRANSITION",
                    message=(
                        f"Cannot transition from {current.value} to "
                        f"{command.to_state.value}"
                    ),
                    current_state=current.value,
                    requested_state=command.to_state.value,
                    permitted_next_states=permitted,
                )

            actor_role = UserRole(actor["role"])
            if actor_role not in rule.roles:
                self._refuse(
                    actor_id=actor_id,
                    actor_role=actor["role"],
                    action=action,
                    request_id=request_id,
                    status_code=403,
                    code="ROLE_NOT_PERMITTED",
                    message=(
                        f"Role {actor_role.value} is not permitted for "
                        f"{current.value} to {command.to_state.value}"
                    ),
                    current_state=current.value,
                    requested_state=command.to_state.value,
                    permitted_next_states=permitted,
                )

            if rule.owner_required_for_citizen and actor_role is UserRole.CITIZEN:
                if request["citizen_id"] != actor_id:
                    self._refuse(
                        actor_id=actor_id,
                        actor_role=actor["role"],
                        action=action,
                        request_id=request_id,
                        status_code=403,
                        code="OWNERSHIP_REQUIRED",
                        message="Citizen does not own this request",
                        current_state=current.value,
                        requested_state=command.to_state.value,
                        permitted_next_states=permitted,
                    )

            if rule.assigned_officer_required and request["assigned_to"] != actor_id:
                self._refuse(
                    actor_id=actor_id,
                    actor_role=actor["role"],
                    action=action,
                    request_id=request_id,
                    status_code=403,
                    code="ASSIGNMENT_REQUIRED",
                    message="Field Officer is not assigned to this request",
                    current_state=current.value,
                    requested_state=command.to_state.value,
                    permitted_next_states=permitted,
                )

            now = self._now()
            updates: dict[str, Any] = {"state": command.to_state.value}
            details: dict[str, Any] = {
                "old_version": request["version"],
                "new_version": request["version"] + 1,
            }
            if command.to_state is RequestState.NEEDS_INFO:
                info_note = command.info_request_note or command.rejection_reason
                if not info_note:
                    self._missing_field(actor, request, command, "info_request_note")
                updates["info_request_note"] = info_note
                updates["paused_at"] = utc_iso(now)
                details["info_request_note"] = info_note
                details["paused_at"] = utc_iso(now)
            elif current is RequestState.NEEDS_INFO and command.to_state is RequestState.ACKNOWLEDGED:
                if not command.additional_info:
                    self._missing_field(actor, request, command, "additional_info")
                paused_at_dt = (
                    self._parse_time(request["paused_at"])
                    if request["paused_at"]
                    else now
                )
                paused_seconds = max(0, int((now - paused_at_dt).total_seconds()))
                total_paused = (request["total_paused_seconds"] or 0) + paused_seconds
                paused_delta = timedelta(seconds=paused_seconds)
                new_deadline = self._parse_time(request["deadline"]) + paused_delta
                updates["additional_info"] = command.additional_info
                updates["paused_at"] = None
                updates["total_paused_seconds"] = total_paused
                updates["deadline"] = utc_iso(new_deadline)
                if request["triaged_at"] is None:
                    new_triage = self._parse_time(request["triage_deadline"]) + paused_delta
                    updates["triage_deadline"] = utc_iso(new_triage)
                    details["new_triage_deadline"] = utc_iso(new_triage)
                details.update(
                    {
                        "additional_info": command.additional_info,
                        "paused_seconds": paused_seconds,
                        "total_paused_seconds": total_paused,
                        "old_deadline": request["deadline"],
                        "new_deadline": utc_iso(new_deadline),
                    }
                )
            elif command.to_state is RequestState.ASSIGNED:
                if not command.assigned_to:
                    self._missing_field(actor, request, command, "assigned_to")
                assignee = self._actor(command.assigned_to)
                if assignee is None or assignee["role"] != UserRole.FIELD_OFFICER.value:
                    self._refuse(
                        actor_id=actor_id,
                        actor_role=actor["role"],
                        action=action,
                        request_id=request_id,
                        status_code=422,
                        code="INVALID_ASSIGNEE",
                        message="assigned_to must identify a Field Officer",
                        current_state=current.value,
                        requested_state=command.to_state.value,
                        permitted_next_states=permitted,
                    )
                updates["assigned_to"] = assignee["id"]
                updates["assigned_at"] = utc_iso(now)
                details["assigned_to"] = assignee["id"]
                details["assigned_at"] = utc_iso(now)
                if request["triaged_at"] is None:
                    updates["triaged_at"] = utc_iso(now)
                    details["triaged_at"] = utc_iso(now)
            elif command.to_state is RequestState.REJECTED:
                if not command.rejection_reason:
                    self._missing_field(actor, request, command, "rejection_reason")
                updates["rejection_reason"] = command.rejection_reason
                details["rejection_reason"] = command.rejection_reason
                if request["triaged_at"] is None:
                    updates["triaged_at"] = utc_iso(now)
                    details["triaged_at"] = utc_iso(now)
                updates["appeal_reason"] = None
                updates["appealed_at"] = None
                updates["appeal_approval_note"] = None
            elif current is RequestState.REJECTED and command.to_state is RequestState.ACKNOWLEDGED:
                if not request["appeal_reason"] or not request["appealed_at"]:
                    self._refuse(
                        actor_id=actor_id,
                        actor_role=actor["role"],
                        action=action,
                        request_id=request_id,
                        status_code=422,
                        code="APPEAL_REQUIRED",
                        message="Cannot approve appeal without a pending Citizen appeal",
                        current_state=current.value,
                        requested_state=command.to_state.value,
                        permitted_next_states=permitted,
                    )
                if not command.appeal_approval_note:
                    self._missing_field(actor, request, command, "appeal_approval_note")
                updates["appeal_approval_note"] = command.appeal_approval_note
                details["appeal_approval_note"] = command.appeal_approval_note
            elif command.to_state is RequestState.RESOLVED:
                if not command.resolution_notes:
                    self._missing_field(actor, request, command, "resolution_notes")
                if not command.resolution_evidence_json:
                    self._missing_field(actor, request, command, "resolution_evidence_json")
                updates["resolution_notes"] = command.resolution_notes
                updates["resolution_evidence_json"] = canonical_json(command.resolution_evidence_json)
                details["resolution_notes"] = command.resolution_notes
                details["resolution_evidence_json"] = command.resolution_evidence_json
            elif command.to_state is RequestState.CLOSED:
                updates["closed_at"] = utc_iso(now)
                details["closed_at"] = utc_iso(now)
            elif command.to_state is RequestState.REOPENED:
                if not command.reopen_reason:
                    self._missing_field(actor, request, command, "reopen_reason")
                if current is RequestState.CLOSED:
                    closed_at_str = request["closed_at"] or request["updated_at"]
                    closed_at_dt = self._parse_time(closed_at_str)
                    if now > closed_at_dt + timedelta(days=7):
                        self._refuse(
                            actor_id=actor_id,
                            actor_role=actor["role"],
                            action=action,
                            request_id=request_id,
                            status_code=422,
                            code="REOPEN_WINDOW_EXPIRED",
                            message="Cannot reopen request: 7-day post-closure window has expired",
                            current_state=current.value,
                            requested_state=command.to_state.value,
                            permitted_next_states=permitted,
                        )
                deadline = now + timedelta(hours=SLA_HOURS[Category(request["category"])])
                updates.update(
                    {
                        "reopen_reason": command.reopen_reason,
                        "sla_started_at": utc_iso(now),
                        "deadline": utc_iso(deadline),
                        "sla_cycle": request["sla_cycle"] + 1,
                        "reopen_count": request["reopen_count"] + 1,
                        "escalation_level": 0,
                        "paused_at": None,
                        "total_paused_seconds": 0,
                        "closed_at": None,
                    }
                )
                details.update(
                    {
                        "reopen_reason": command.reopen_reason,
                        "old_deadline": request["deadline"],
                        "new_deadline": utc_iso(deadline),
                        "old_escalation_level": request["escalation_level"],
                        "new_escalation_level": 0,
                        "sla_cycle": request["sla_cycle"] + 1,
                    }
                )

            self._check_version(
                actor=actor,
                request=request,
                expected_version=command.expected_version,
                action=action,
                requested_state=command.to_state.value,
            )
            now = self._now()
            updates["updated_at"] = utc_iso(now)
            assignments = ", ".join(f"{column} = ?" for column in updates)
            values = [*updates.values(), request_id, command.expected_version]
            cursor = self.connection.execute(
                f"UPDATE requests SET {assignments}, version = version + 1 "
                "WHERE id = ? AND version = ?",
                values,
            )
            if cursor.rowcount != 1:
                raise RuntimeError("request version changed inside serialized transaction")
            append_audit(
                self.connection,
                actor=actor_id,
                actor_role=actor["role"],
                action="TRANSITION",
                request_id=request_id,
                from_state=current.value,
                to_state=command.to_state.value,
                outcome="ACCEPTED",
                reason="Workflow transition accepted",
                details=details,
                simulated_at=now,
            )
            self.connection.commit()
            return self._serialize_request(self._request(request_id), actor)
        except WorkflowError:
            raise
        except Exception:
            self.connection.rollback()
            raise

    def _missing_field(
        self,
        actor: sqlite3.Row,
        request: sqlite3.Row,
        command: TransitionCommand,
        field: str,
    ) -> None:
        self._refuse(
            actor_id=actor["id"],
            actor_role=actor["role"],
            action="TRANSITION_ATTEMPT",
            request_id=request["id"],
            status_code=422,
            code="MISSING_REQUIRED_FIELD",
            message=f"{field} is required",
            current_state=request["state"],
            requested_state=command.to_state.value,
            permitted_next_states=self._next_states(RequestState(request["state"])),
            details={"missing_field": field},
        )

    def confirm_category(
        self, *, request_id: str, actor_id: str, command: CategoryCommand
    ) -> dict[str, Any]:
        self._begin()
        try:
            action = "CONFIRM_CATEGORY"
            actor = self._require_actor(actor_id, action=action, request_id=request_id)
            request = self._require_request(
                request_id, actor=actor, action=action
            )
            if actor["role"] not in {UserRole.OFFICER.value, UserRole.SUPERVISOR.value}:
                self._refuse(
                    actor_id=actor_id,
                    actor_role=actor["role"],
                    action=action,
                    request_id=request_id,
                    status_code=403,
                    code="ROLE_NOT_PERMITTED",
                    message="Only Officers and Supervisors may confirm categories",
                    current_state=request["state"],
                )
            if RequestState(request["state"]) not in ACTIVE_STATES:
                self._refuse(
                    actor_id=actor_id,
                    actor_role=actor["role"],
                    action=action,
                    request_id=request_id,
                    status_code=422,
                    code="INACTIVE_REQUEST",
                    message="Category can be changed only while a request is active",
                    current_state=request["state"],
                )
            self._check_version(
                actor=actor,
                request=request,
                expected_version=command.expected_version,
                action=action,
            )
            start = self._parse_time(request["sla_started_at"])
            paused = timedelta(seconds=request["total_paused_seconds"] or 0)
            deadline = start + timedelta(hours=SLA_HOURS[command.category]) + paused
            now = self._now()
            self.connection.execute(
                """
                UPDATE requests
                SET category = ?, deadline = ?, updated_at = ?, version = version + 1
                WHERE id = ? AND version = ?
                """,
                (
                    command.category.value,
                    utc_iso(deadline),
                    utc_iso(now),
                    request_id,
                    command.expected_version,
                ),
            )
            append_audit(
                self.connection,
                actor=actor_id,
                actor_role=actor["role"],
                action=action,
                request_id=request_id,
                from_state=request["state"],
                to_state=request["state"],
                outcome="ACCEPTED",
                reason="Category confirmed and deadline recalculated",
                details={
                    "old_category": request["category"],
                    "new_category": command.category.value,
                    "old_deadline": request["deadline"],
                    "new_deadline": utc_iso(deadline),
                    "old_version": request["version"],
                    "new_version": request["version"] + 1,
                },
                simulated_at=now,
            )
            self.connection.commit()
            return self._serialize_request(self._request(request_id), actor)
        except WorkflowError:
            raise
        except Exception:
            self.connection.rollback()
            raise

    def reassign(
        self, *, request_id: str, actor_id: str, command: ReassignCommand
    ) -> dict[str, Any]:
        self._begin()
        try:
            action = "REASSIGN_REQUEST"
            actor = self._require_actor(actor_id, action=action, request_id=request_id)
            request = self._require_request(
                request_id, actor=actor, action=action
            )
            if actor["role"] != UserRole.SUPERVISOR.value:
                self._refuse(
                    actor_id=actor_id,
                    actor_role=actor["role"],
                    action=action,
                    request_id=request_id,
                    status_code=403,
                    code="ROLE_NOT_PERMITTED",
                    message="Only Supervisors may reassign requests",
                    current_state=request["state"],
                )
            if RequestState(request["state"]) not in {
                RequestState.ASSIGNED,
                RequestState.IN_PROGRESS,
                RequestState.REOPENED,
            }:
                self._refuse(
                    actor_id=actor_id,
                    actor_role=actor["role"],
                    action=action,
                    request_id=request_id,
                    status_code=422,
                    code="INVALID_REASSIGNMENT_STATE",
                    message="Only ASSIGNED, IN_PROGRESS, or REOPENED requests may be reassigned",
                    current_state=request["state"],
                )
            assignee = self._actor(command.assigned_to)
            if assignee is None or assignee["role"] != UserRole.FIELD_OFFICER.value:
                self._refuse(
                    actor_id=actor_id,
                    actor_role=actor["role"],
                    action=action,
                    request_id=request_id,
                    status_code=422,
                    code="INVALID_ASSIGNEE",
                    message="assigned_to must identify a Field Officer",
                    current_state=request["state"],
                )
            self._check_version(
                actor=actor,
                request=request,
                expected_version=command.expected_version,
                action=action,
            )
            now = self._now()
            self.connection.execute(
                """
                UPDATE requests
                SET assigned_to = ?, assigned_at = ?, updated_at = ?, version = version + 1
                WHERE id = ? AND version = ?
                """,
                (
                    assignee["id"],
                    utc_iso(now),
                    utc_iso(now),
                    request_id,
                    command.expected_version,
                ),
            )
            append_audit(
                self.connection,
                actor=actor_id,
                actor_role=actor["role"],
                action=action,
                request_id=request_id,
                from_state=request["state"],
                to_state=request["state"],
                outcome="ACCEPTED",
                reason=command.reason,
                details={
                    "old_assigned_to": request["assigned_to"],
                    "new_assigned_to": assignee["id"],
                    "old_version": request["version"],
                    "new_version": request["version"] + 1,
                },
                simulated_at=now,
            )
            self.connection.commit()
            return self._serialize_request(self._request(request_id), actor)
        except WorkflowError:
            raise
        except Exception:
            self.connection.rollback()
            raise

    def submit_appeal(
        self, *, request_id: str, actor_id: str, command: AppealCommand
    ) -> dict[str, Any]:
        self._begin()
        try:
            action = "SUBMIT_APPEAL"
            actor = self._require_actor(actor_id, action=action, request_id=request_id)
            request = self._require_request(request_id, actor=actor, action=action)
            if actor["role"] != UserRole.CITIZEN.value:
                self._refuse(
                    actor_id=actor_id,
                    actor_role=actor["role"],
                    action=action,
                    request_id=request_id,
                    status_code=403,
                    code="ROLE_NOT_PERMITTED",
                    message="Only Citizens may submit appeals",
                    current_state=request["state"],
                )
            if request["citizen_id"] != actor_id:
                self._refuse(
                    actor_id=actor_id,
                    actor_role=actor["role"],
                    action=action,
                    request_id=request_id,
                    status_code=403,
                    code="OWNERSHIP_REQUIRED",
                    message="Citizen does not own this request",
                    current_state=request["state"],
                )
            if request["state"] != RequestState.REJECTED.value:
                self._refuse(
                    actor_id=actor_id,
                    actor_role=actor["role"],
                    action=action,
                    request_id=request_id,
                    status_code=422,
                    code="INVALID_STATE_FOR_APPEAL",
                    message="Appeals may only be submitted for REJECTED requests",
                    current_state=request["state"],
                )
            self._check_version(
                actor=actor,
                request=request,
                expected_version=command.expected_version,
                action=action,
            )
            now = self._now()
            self.connection.execute(
                """
                UPDATE requests
                SET appeal_reason = ?, appealed_at = ?, updated_at = ?, version = version + 1
                WHERE id = ? AND version = ?
                """,
                (
                    command.appeal_reason,
                    utc_iso(now),
                    utc_iso(now),
                    request_id,
                    command.expected_version,
                ),
            )
            append_audit(
                self.connection,
                actor=actor_id,
                actor_role=actor["role"],
                action=action,
                request_id=request_id,
                from_state=request["state"],
                to_state=request["state"],
                outcome="ACCEPTED",
                reason="Citizen submitted rejection appeal",
                details={
                    "appeal_reason": command.appeal_reason,
                    "appealed_at": utc_iso(now),
                    "old_version": request["version"],
                    "new_version": request["version"] + 1,
                },
                simulated_at=now,
            )
            self.connection.commit()
            return self._serialize_request(self._request(request_id), actor)
        except WorkflowError:
            raise
        except Exception:
            self.connection.rollback()
            raise

    def add_progress(
        self, *, request_id: str, actor_id: str, command: ProgressCommand
    ) -> dict[str, Any]:
        self._begin()
        try:
            action = "ADD_PROGRESS_UPDATE"
            actor = self._require_actor(actor_id, action=action, request_id=request_id)
            request = self._require_request(request_id, actor=actor, action=action)
            if actor["role"] != UserRole.FIELD_OFFICER.value:
                self._refuse(
                    actor_id=actor_id,
                    actor_role=actor["role"],
                    action=action,
                    request_id=request_id,
                    status_code=403,
                    code="ROLE_NOT_PERMITTED",
                    message="Only Field Officers may add progress updates",
                    current_state=request["state"],
                )
            if request["assigned_to"] != actor_id:
                self._refuse(
                    actor_id=actor_id,
                    actor_role=actor["role"],
                    action=action,
                    request_id=request_id,
                    status_code=403,
                    code="ASSIGNMENT_REQUIRED",
                    message="Field Officer is not assigned to this request",
                    current_state=request["state"],
                )
            if request["state"] != RequestState.IN_PROGRESS.value:
                self._refuse(
                    actor_id=actor_id,
                    actor_role=actor["role"],
                    action=action,
                    request_id=request_id,
                    status_code=422,
                    code="INVALID_STATE_FOR_PROGRESS",
                    message="Progress updates may only be added while IN_PROGRESS",
                    current_state=request["state"],
                )
            self._check_version(
                actor=actor,
                request=request,
                expected_version=command.expected_version,
                action=action,
            )
            now = self._now()
            self.connection.execute(
                """
                UPDATE requests
                SET updated_at = ?, version = version + 1
                WHERE id = ? AND version = ?
                """,
                (
                    utc_iso(now),
                    request_id,
                    command.expected_version,
                ),
            )
            append_audit(
                self.connection,
                actor=actor_id,
                actor_role=actor["role"],
                action=action,
                request_id=request_id,
                from_state=request["state"],
                to_state=request["state"],
                outcome="ACCEPTED",
                reason=command.note,
                details={
                    "note": command.note,
                    "evidence_json": command.evidence_json,
                    "old_version": request["version"],
                    "new_version": request["version"] + 1,
                },
                simulated_at=now,
            )
            self.connection.commit()
            return self._serialize_request(self._request(request_id), actor)
        except WorkflowError:
            raise
        except Exception:
            self.connection.rollback()
            raise

    def get_request(self, *, request_id: str, actor_id: str) -> dict[str, Any]:
        actor = self._actor(actor_id)
        if actor is None:
            raise WorkflowError(
                status_code=401,
                code="ACTOR_NOT_FOUND",
                message="Actor does not exist",
            )
        if actor["role"] == UserRole.AUDIT_INSPECTOR.value:
            raise WorkflowError(
                status_code=403,
                code="ROLE_NOT_PERMITTED",
                message="Audit Inspector cannot access operational requests",
                actor_role=actor["role"],
            )
        request = self._request(request_id)
        if request is None:
            raise WorkflowError(
                status_code=404,
                code="REQUEST_NOT_FOUND",
                message="Request does not exist",
                actor_role=actor["role"],
            )
        if not self._can_read(actor, request):
            raise WorkflowError(
                status_code=403,
                code="READ_NOT_PERMITTED",
                message="Actor may not read this request",
                current_state=request["state"],
                actor_role=actor["role"],
            )
        return self._serialize_request(request, actor)

    def get_request_history(
        self, *, request_id: str, actor_id: str
    ) -> list[dict[str, Any]]:
        actor = self._actor(actor_id)
        if actor is None:
            raise WorkflowError(
                status_code=401,
                code="ACTOR_NOT_FOUND",
                message="Actor does not exist",
            )
        request = self._request(request_id)
        if request is None:
            raise WorkflowError(
                status_code=404,
                code="REQUEST_NOT_FOUND",
                message="Request does not exist",
                actor_role=actor["role"],
            )
        if not self._can_read(actor, request):
            raise WorkflowError(
                status_code=403,
                code="READ_NOT_PERMITTED",
                message="Actor may not read this request",
                current_state=request["state"],
                actor_role=actor["role"],
            )
        rows = self.connection.execute(
            """
            SELECT id, timestamp, simulated_at, actor_role, action, from_state, to_state, reason, details_json
            FROM audit
            WHERE request_id = ? AND outcome = 'ACCEPTED'
            ORDER BY id ASC
            """,
            (request_id,),
        ).fetchall()
        import json
        events = []
        for row in rows:
            details = {}
            if row["details_json"]:
                try:
                    details = json.loads(row["details_json"])
                except Exception:
                    details = {}
            if actor["role"] == UserRole.CITIZEN.value and details.get("assigned_to"):
                officer = self.connection.execute(
                    "SELECT name, department FROM users WHERE id = ?",
                    (details["assigned_to"],),
                ).fetchone()
                if officer:
                    details["assigned_to"] = officer["name"]
                    details["assigned_team"] = officer["department"] or "Municipal Field Team"
                else:
                    details["assigned_to"] = "Field Operations Officer"
                    details["assigned_team"] = "Municipal Field Operations"

            events.append(
                {
                    "id": row["id"],
                    "timestamp": row["simulated_at"] or row["timestamp"],
                    "actor_role": row["actor_role"],
                    "action": row["action"],
                    "from_state": row["from_state"],
                    "to_state": row["to_state"],
                    "reason": row["reason"],
                    "details": details,
                }
            )
        return events

    def list_requests(
        self,
        *,
        actor_id: str,
        state: RequestState | None = None,
        assigned_to: str | None = None,
        citizen_id: str | None = None,
        category: Category | None = None,
        is_overdue: bool | None = None,
    ) -> list[dict[str, Any]]:
        actor = self._actor(actor_id)
        if actor is None:
            raise WorkflowError(
                status_code=401,
                code="ACTOR_NOT_FOUND",
                message="Actor does not exist",
            )
        if actor["role"] in {UserRole.AUDIT_INSPECTOR.value, UserRole.ADMIN.value}:
            raise WorkflowError(
                status_code=403,
                code="ROLE_NOT_PERMITTED",
                message=f"{actor['role']} cannot access operational requests",
                actor_role=actor["role"],
            )
        clauses: list[str] = []
        values: list[Any] = []
        if actor["role"] == UserRole.CITIZEN.value:
            clauses.append("citizen_id = ?")
            values.append(actor_id)
        elif actor["role"] == UserRole.FIELD_OFFICER.value:
            clauses.append("assigned_to = ?")
            values.append(actor_id)
        elif actor["role"] == UserRole.OFFICER.value:
            clauses.append("(state IN ('SUBMITTED', 'ACKNOWLEDGED', 'NEEDS_INFO') OR assigned_to IS NULL)")
        if state:
            clauses.append("state = ?")
            values.append(state.value)
        if assigned_to:
            clauses.append("assigned_to = ?")
            values.append(assigned_to)
        if citizen_id:
            clauses.append("citizen_id = ?")
            values.append(citizen_id)
        if category:
            clauses.append("category = ?")
            values.append(category.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(
            f"SELECT * FROM requests {where} ORDER BY created_at DESC, id DESC",
            values,
        ).fetchall()
        serialized = [self._serialize_request(row, actor) for row in rows]
        if is_overdue is not None:
            serialized = [
                row for row in serialized if row["is_overdue"] is is_overdue
            ]
        return serialized

    def list_audit(self, *, actor_id: str) -> dict[str, Any]:
        actor = self._actor(actor_id)
        if actor is None:
            raise WorkflowError(
                status_code=401,
                code="ACTOR_NOT_FOUND",
                message="Actor does not exist",
            )
        if actor["role"] not in {UserRole.SUPERVISOR.value, UserRole.AUDIT_INSPECTOR.value}:
            raise WorkflowError(
                status_code=403,
                code="AUDIT_READ_NOT_PERMITTED",
                message="Only Supervisors and Audit Inspectors may read the audit log",
                actor_role=actor["role"],
            )
        rows = self.connection.execute("SELECT * FROM audit ORDER BY id").fetchall()
        records = [dict(row) for row in rows]
        return {
            "records": records,
            "verification": {
                "status": "NOT_RUN",
                "planned_phase": 6,
                "record_count": len(records),
                "chain_head": records[-1]["hash"] if records else None,
            },
        }

    def verify_audit(self, *, actor_id: str) -> dict[str, Any]:
        actor = self._actor(actor_id)
        if actor is None:
            raise WorkflowError(
                status_code=401,
                code="ACTOR_NOT_FOUND",
                message="Actor does not exist",
            )
        if actor["role"] not in {UserRole.SUPERVISOR.value, UserRole.AUDIT_INSPECTOR.value}:
            raise WorkflowError(
                status_code=403,
                code="AUDIT_READ_NOT_PERMITTED",
                message="Only Supervisors and Audit Inspectors may verify the audit log",
                actor_role=actor["role"],
            )
        from src.services.audit_service import verify_audit_chain
        return verify_audit_chain(self.connection)

    @staticmethod
    def _parse_time(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _is_overdue(self, request: sqlite3.Row) -> bool:
        if (
            RequestState(request["state"]) not in ACTIVE_STATES
            or request["state"] == RequestState.NEEDS_INFO.value
            or request["paused_at"] is not None
        ):
            return False
        return self._now() > self._parse_time(request["deadline"])

    @staticmethod
    def _can_read(actor: sqlite3.Row, request: sqlite3.Row) -> bool:
        role = actor["role"]
        if role == UserRole.AUDIT_INSPECTOR.value:
            return False
        if role == UserRole.SUPERVISOR.value:
            return True
        if role == UserRole.CITIZEN.value:
            return request["citizen_id"] == actor["id"]
        if role == UserRole.OFFICER.value:
            return (
                request["state"] in ("SUBMITTED", "ACKNOWLEDGED", "NEEDS_INFO")
                or request["assigned_to"] is None
            )
        if role == UserRole.FIELD_OFFICER.value:
            return request["assigned_to"] == actor["id"]
        return False

    def _permitted_actions(
        self, request: sqlite3.Row, actor: sqlite3.Row
    ) -> list[str]:
        state = RequestState(request["state"])
        role = UserRole(actor["role"])
        actions: list[str] = []
        for (source, target), rule in TRANSITION_RULES.items():
            if source is not state or role not in rule.roles:
                continue
            if rule.owner_required_for_citizen and role is UserRole.CITIZEN:
                if request["citizen_id"] != actor["id"]:
                    continue
            if source is RequestState.CLOSED and target is RequestState.REOPENED:
                closed_at_str = request["closed_at"] or request["updated_at"]
                closed_at = self._parse_time(closed_at_str)
                if self._now() > closed_at + timedelta(days=7):
                    continue
            if source is RequestState.REJECTED and target is RequestState.ACKNOWLEDGED:
                if not request["appeal_reason"] or not request["appealed_at"]:
                    continue
            actions.append(f"TRANSITION:{target.value}")
        if state in ACTIVE_STATES and role in {UserRole.OFFICER, UserRole.SUPERVISOR}:
            actions.append("CONFIRM_CATEGORY")
        if state in {RequestState.ASSIGNED, RequestState.IN_PROGRESS, RequestState.REOPENED} and role is UserRole.SUPERVISOR:
            actions.append("REASSIGN")
        if state is RequestState.REJECTED and role is UserRole.CITIZEN and request["citizen_id"] == actor["id"]:
            actions.append("APPEAL")
        if state is RequestState.IN_PROGRESS and role is UserRole.FIELD_OFFICER and request["assigned_to"] == actor["id"]:
            actions.append("ADD_PROGRESS")
        return sorted(actions)

    def _serialize_request(
        self, request: sqlite3.Row | None, actor: sqlite3.Row
    ) -> dict[str, Any]:
        if request is None:
            raise RuntimeError("request disappeared after successful command")
        result = dict(request)
        if result.get("resolution_evidence_json") and isinstance(result["resolution_evidence_json"], str):
            try:
                result["resolution_evidence_json"] = json.loads(result["resolution_evidence_json"])
            except Exception:
                pass
        result["is_overdue"] = self._is_overdue(request)
        result["permitted_actions"] = self._permitted_actions(request, actor)
        if actor["role"] == UserRole.CITIZEN.value and result.get("assigned_to"):
            officer = self.connection.execute(
                "SELECT name, department FROM users WHERE id = ?",
                (result["assigned_to"],),
            ).fetchone()
            if officer:
                result["assigned_officer_name"] = officer["name"]
                result["assigned_team"] = officer["department"] or "Municipal Field Team"
                result["assigned_to"] = officer["name"]
            else:
                result["assigned_officer_name"] = "Field Operations Officer"
                result["assigned_team"] = "Municipal Field Operations"
                result["assigned_to"] = "Field Operations Officer"
        return result
