"""Administrative API routes for GovFix (User Management, Staff Provisioning, SLA Config)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from src.auth import (
    create_firebase_user,
    generate_password_reset_link,
    require_roles,
)
from src.database import connect
from src.models import SLA_HOURS, StaffCreateRequest, UserRole, UserStatusUpdateRequest
from src.services.email_service import dispatch_email_sync


router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def get_db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


AdminGuard = Annotated[dict[str, Any], Depends(require_roles("Admin"))]


@router.get("/users")
def list_users(
    admin: AdminGuard,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    """List all registered platform users with authoritative role and status."""
    rows = connection.execute(
        """
        SELECT id, name, role, email, department, status, is_verified, created_at
        FROM users
        ORDER BY created_at DESC, id ASC
        """
    ).fetchall()
    return {"success": True, "data": [dict(r) for r in rows]}


@router.post("/users", status_code=201)
def create_staff_user(
    payload: StaffCreateRequest,
    admin: AdminGuard,
    background_tasks: BackgroundTasks,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    """Admin provisions a staff account.
    
    Creates a Firebase user, allocates role-specific ID (e.g. USR-FLD-xx),
    inserts into users table, and sends an invitation email with a set-password link.
    """
    if payload.role == UserRole.CITIZEN:
        raise HTTPException(
            status_code=400,
            detail="Citizen accounts are self-service. Citizens must register via /signup.",
        )

    # 1. Check if email already registered
    existing = connection.execute(
        "SELECT id FROM users WHERE LOWER(email) = LOWER(?)",
        (payload.email.strip(),),
    ).fetchone()
    if existing:
        raise HTTPException(
            status_code=400,
            detail="An account with this email address is already registered.",
        )

    # 2. Determine prefix by role
    prefix_map = {
        UserRole.FIELD_OFFICER: "USR-FLD",
        UserRole.OFFICER: "USR-OFF",
        UserRole.SUPERVISOR: "USR-SUP",
        UserRole.AUDIT_INSPECTOR: "USR-AUD",
        UserRole.ADMIN: "USR-ADM",
    }
    prefix = prefix_map.get(payload.role, "USR-STF")

    # 3. Allocate next ID
    rows = connection.execute(
        f"SELECT id FROM users WHERE id LIKE '{prefix}-%' ORDER BY id DESC"
    ).fetchall()
    next_num = 1
    for r in rows:
        try:
            num = int(r["id"].split(f"{prefix}-")[-1])
            if num >= next_num:
                next_num = num + 1
        except Exception:
            pass
    staff_id = f"{prefix}-{next_num:02d}"

    # 4. Provision in Firebase
    temp_password = "GovFix" + datetime.now(UTC).strftime("%Y%m%d%H%M") + "!"
    firebase_uid = create_firebase_user(
        email=payload.email.strip(),
        password=temp_password,
        display_name=payload.name.strip(),
        role=payload.role.value,
        email_verified=True,
    )

    # Ensure unique firebase_uid in local users table
    existing_uid = connection.execute(
        "SELECT id FROM users WHERE firebase_uid = ?", (firebase_uid,)
    ).fetchone()
    if existing_uid:
        import uuid
        firebase_uid = f"{firebase_uid}-{uuid.uuid4().hex[:6]}"

    # 5. Insert into SQLite users
    now_text = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    connection.execute(
        """
        INSERT INTO users (id, name, role, email, firebase_uid, status, is_verified, department, created_at)
        VALUES (?, ?, ?, ?, ?, 'active', 1, ?, ?)
        """,
        (
            staff_id,
            payload.name.strip(),
            payload.role.value,
            payload.email.strip(),
            firebase_uid,
            payload.department,
            now_text,
        ),
    )
    connection.commit()

    # Sync to Cloud Firestore if enabled
    try:
        from src.firestore_db import save_user_to_firestore
        save_user_to_firestore({
            "id": staff_id,
            "name": payload.name.strip(),
            "role": payload.role.value,
            "email": payload.email.strip(),
            "firebase_uid": firebase_uid,
            "status": "active",
            "is_verified": 1,
            "department": payload.department,
            "created_at": now_text,
        })
    except Exception:
        pass

    # 6. Generate password setup invitation link & dispatch email
    set_password_link = generate_password_reset_link(payload.email.strip())
    background_tasks.add_task(
        dispatch_email_sync,
        recipient=payload.email.strip(),
        template="account_invitation",
        context={
            "name": payload.name.strip(),
            "role": payload.role.value,
            "link": set_password_link,
        },
        event_id=f"invite-{staff_id}",
    )

    return {
        "success": True,
        "data": {
            "id": staff_id,
            "name": payload.name.strip(),
            "role": payload.role.value,
            "email": payload.email.strip(),
            "department": payload.department,
            "status": "active",
            "is_verified": True,
            "message": f"Staff account {staff_id} provisioned. Invitation email sent.",
        },
    }


@router.post("/users/{user_id}/status")
def update_user_status(
    user_id: str,
    payload: UserStatusUpdateRequest,
    admin: AdminGuard,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    """Enable or disable a user account. Disabled users are rejected on next request."""
    user = connection.execute(
        "SELECT id, name, role, status FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if user["id"] == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot change status of your own admin account.")

    connection.execute(
        "UPDATE users SET status = ? WHERE id = ?",
        (payload.status, user_id),
    )
    connection.commit()

    # Sync to Cloud Firestore if enabled
    try:
        from src.firestore_db import save_user_to_firestore
        save_user_to_firestore({"id": user_id, "status": payload.status})
    except Exception:
        pass

    return {
        "success": True,
        "data": {
            "id": user_id,
            "status": payload.status,
            "message": f"User {user_id} status updated to {payload.status}.",
        },
    }


@router.get("/sla")
def get_sla_table(admin: AdminGuard) -> dict[str, Any]:
    """Return statutory SLA configurations for municipal categories."""
    return {
        "success": True,
        "data": [
            {"category": cat.value, "sla_hours": hours}
            for cat, hours in SLA_HOURS.items()
        ],
    }
