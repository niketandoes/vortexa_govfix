"""Pydantic data contracts shared by GovFix services and routes."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserRole(StrEnum):
    CITIZEN = "Citizen"
    OFFICER = "Officer"
    FIELD_OFFICER = "Field Officer"
    SUPERVISOR = "Supervisor"
    AUDIT_INSPECTOR = "Audit Inspector"
    ADMIN = "Admin"


class ActorRole(StrEnum):
    CITIZEN = "Citizen"
    OFFICER = "Officer"
    FIELD_OFFICER = "Field Officer"
    SUPERVISOR = "Supervisor"
    AUDIT_INSPECTOR = "Audit Inspector"
    ADMIN = "Admin"
    SYSTEM = "System"


class Category(StrEnum):
    WATER_LEAK = "Water Leak"
    GARBAGE_COLLECTION = "Garbage Collection"
    STREETLIGHT = "Streetlight"
    POTHOLE = "Pothole"
    UNCATEGORISED = "Uncategorised"


SLA_HOURS: dict[Category, int] = {
    Category.WATER_LEAK: 24,
    Category.GARBAGE_COLLECTION: 24,
    Category.STREETLIGHT: 48,
    Category.POTHOLE: 72,
    Category.UNCATEGORISED: 72,
}


class RequestState(StrEnum):
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    NEEDS_INFO = "NEEDS_INFO"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    REOPENED = "REOPENED"


class SuggestionKind(StrEnum):
    CATEGORISATION = "categorisation"
    EXTRACTION = "extraction"
    REPLY_DRAFT = "reply_draft"
    DUPLICATE_DETECTION = "duplicate_detection"


class SuggestionSource(StrEnum):
    GEMINI = "gemini_llm"
    LOCAL = "local_fallback"


class AuditOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    REFUSED = "REFUSED"


class GovFixModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="after")
    @classmethod
    def require_timezone_for_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and value.tzinfo is None:
            raise ValueError("datetime values must include a timezone")
        return value


class UserCreate(GovFixModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    role: UserRole
    email: str = Field(min_length=3)
    firebase_uid: str | None = None
    status: str = Field(default="active")
    is_verified: bool = Field(default=False)
    department: str | None = None
    created_at: datetime


class UserRecord(GovFixModel):
    id: str
    name: str
    role: UserRole
    email: str
    firebase_uid: str | None = None
    status: str = "active"
    is_verified: bool = False
    department: str | None = None
    created_at: datetime


class CitizenSignupRequest(GovFixModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=6)


class StaffCreateRequest(GovFixModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=100)
    role: UserRole
    department: str | None = None


class UserStatusUpdateRequest(GovFixModel):
    status: str = Field(pattern=r"^(active|disabled)$")



class RequestCreate(GovFixModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=10_000)
    location: str = Field(default="Municipal Area", min_length=1, max_length=500)
    reported_category: Category | None = None

    @field_validator("reported_category")
    @classmethod
    def reported_category_must_be_specific(
        cls, value: Category | None
    ) -> Category | None:
        if value is Category.UNCATEGORISED:
            raise ValueError("reported_category must be a specific category or omitted")
        return value


class RequestRecord(GovFixModel):
    id: str
    citizen_id: str
    title: str
    text: str
    location: str
    reported_category: Category | None = None
    category: Category
    state: RequestState
    assigned_to: str | None = None
    assigned_at: datetime | None = None
    rejection_reason: str | None = None
    appeal_reason: str | None = None
    appealed_at: datetime | None = None
    appeal_approval_note: str | None = None
    resolution_notes: str | None = None
    resolution_evidence_json: dict[str, Any] | None = None
    reopen_reason: str | None = None
    info_request_note: str | None = None
    additional_info: str | None = None
    escalation_level: int = Field(ge=0, le=2)
    sla_started_at: datetime
    sla_cycle: int = Field(ge=1)
    reopen_count: int = Field(ge=0)
    paused_at: datetime | None = None
    total_paused_seconds: int = Field(default=0, ge=0)
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    triage_deadline: datetime
    triaged_at: datetime | None = None
    closed_at: datetime | None = None
    deadline: datetime


class VersionedCommand(GovFixModel):
    expected_version: int = Field(ge=1)


class TransitionCommand(VersionedCommand):
    to_state: RequestState
    assigned_to: str | None = None
    info_request_note: str | None = None
    additional_info: str | None = None
    rejection_reason: str | None = None
    appeal_approval_note: str | None = None
    resolution_notes: str | None = None
    resolution_evidence_json: dict[str, Any] | None = None
    reopen_reason: str | None = None


class AppealCommand(VersionedCommand):
    appeal_reason: str = Field(min_length=1)


class ProgressCommand(VersionedCommand):
    note: str = Field(min_length=1)
    evidence_json: dict[str, Any] | None = None


class CategoryCommand(VersionedCommand):
    category: Category

    @field_validator("category")
    @classmethod
    def category_must_be_specific(cls, value: Category) -> Category:
        if value is Category.UNCATEGORISED:
            raise ValueError("confirmed category must be specific")
        return value


class ReassignCommand(VersionedCommand):
    assigned_to: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class AISuggestionCreate(GovFixModel):
    id: str
    request_id: str
    kind: SuggestionKind
    payload: dict[str, Any]
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source: SuggestionSource
    created_at: datetime


class AuditEntryCreate(GovFixModel):
    timestamp: datetime
    simulated_at: datetime | None = None
    actor: str = Field(min_length=1)
    actor_role: ActorRole
    action: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    from_state: RequestState | None = None
    to_state: RequestState | None = None
    outcome: AuditOutcome
    reason: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str | None = None
    previous_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ClockState(GovFixModel):
    offset_seconds: int = 0
    is_frozen: bool = False
    frozen_at: datetime | None = None
    updated_at: datetime

    @field_validator("frozen_at")
    @classmethod
    def frozen_at_matches_state(
        cls, value: datetime | None, info: Any
    ) -> datetime | None:
        if info.data.get("is_frozen") and value is None:
            raise ValueError("frozen_at is required when the clock is frozen")
        return value


class AdvisoryPreviewRequest(GovFixModel):
    text: str = Field(min_length=1, max_length=10_000)
    title: str | None = Field(default=None, max_length=200)

