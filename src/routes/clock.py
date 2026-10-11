"""FastAPI Routes for Simulated Clock & Escalation Triggers.

Simulated clock controls are restricted to Administrator console.
When clock advances trigger SLA escalations, supervisor alert emails are dispatched idempotently.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Annotated, Any, Dict

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.auth import get_current_user
from src.database import DEFAULT_DB_PATH, connect
from src.services.clock_service import ClockService
from src.services.email_service import dispatch_email_sync
from src.services.escalation_service import EscalationService


router = APIRouter(prefix="/api/v1/clock", tags=["Simulated Clock"])


class AdvanceClockRequest(BaseModel):
    hours: float = Field(default=0.0, ge=0.0, description="Hours to advance simulated clock")
    minutes: float = Field(default=0.0, ge=0.0, description="Minutes to advance simulated clock")


def get_clock_service() -> ClockService:
    return ClockService(db_path=DEFAULT_DB_PATH)


def get_escalation_service(clock: ClockService = Depends(get_clock_service)) -> EscalationService:
    return EscalationService(db_path=DEFAULT_DB_PATH, clock=clock)


async def require_clock_admin(request: Request) -> dict[str, Any] | None:
    """Enforce Admin-only access to simulated clock in production mode.
    
    Permits unauthenticated calls only under explicit test override mode (GOVFIX_TEST_AUTH_OVERRIDE=1).
    """
    if os.getenv("GOVFIX_TEST_AUTH_OVERRIDE") == "1":
        # Allow test client calls through
        try:
            return await get_current_user(request)
        except Exception:
            return {"id": "SYSTEM", "role": "Admin"}

    user = await get_current_user(request)
    if user.get("role") != "Admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Clock controls are restricted exclusively to System Administrators.",
        )
    return user


@router.get("", summary="Get current simulated clock state")
def get_clock_state(clock: ClockService = Depends(get_clock_service)) -> Dict[str, Any]:
    """Retrieve current simulated UTC timestamp, offset, and freeze status."""
    return clock.get_state()


@router.post("/advance", summary="Advance simulated time and trigger escalations")
def advance_clock(
    payload: AdvanceClockRequest,
    background_tasks: BackgroundTasks,
    admin: Annotated[dict[str, Any] | None, Depends(require_clock_admin)],
    clock: ClockService = Depends(get_clock_service),
    escalation_svc: EscalationService = Depends(get_escalation_service),
) -> Dict[str, Any]:
    """Advance the simulated clock by hours/minutes and immediately run SLA escalation checks."""
    if payload.hours == 0.0 and payload.minutes == 0.0:
        raise HTTPException(status_code=400, detail="Must specify non-zero hours or minutes to advance.")

    new_time = clock.advance(hours=payload.hours, minutes=payload.minutes)
    triggered_escalations = escalation_svc.evaluate_and_escalate_all(current_time=new_time)
    return {
        "success": True,
        "clock": clock.get_state(),
        "escalations_triggered": triggered_escalations,
    }


@router.post("/freeze", summary="Freeze simulated clock")
def freeze_clock(
    admin: Annotated[dict[str, Any] | None, Depends(require_clock_admin)],
    clock: ClockService = Depends(get_clock_service),
) -> Dict[str, Any]:
    """Freeze simulated clock so subsequent queries return the exact same frozen timestamp."""
    clock.freeze()
    return {
        "success": True,
        "clock": clock.get_state(),
    }


@router.post("/unfreeze", summary="Resume simulated clock")
def unfreeze_clock(
    admin: Annotated[dict[str, Any] | None, Depends(require_clock_admin)],
    clock: ClockService = Depends(get_clock_service),
) -> Dict[str, Any]:
    """Unfreeze simulated clock and resume time progression."""
    clock.unfreeze()
    return {
        "success": True,
        "clock": clock.get_state(),
    }


@router.post("/reset", summary="Reset clock to real-world UTC")
def reset_clock(
    admin: Annotated[dict[str, Any] | None, Depends(require_clock_admin)],
    clock: ClockService = Depends(get_clock_service),
    escalation_svc: EscalationService = Depends(get_escalation_service),
) -> Dict[str, Any]:
    """Reset simulated offsets, clearing freeze and offset state."""
    clock.reset()
    return {
        "success": True,
        "clock": clock.get_state(),
    }
