"""Authentication routes for GovFix (Citizen self-registration, session, password recovery)."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from src.auth import (
    create_firebase_user,
    generate_email_verification_link,
    generate_password_reset_link,
    get_current_user,
    verify_token_payload,
)
from src.database import connect
from src.models import CitizenSignupRequest
from src.services.email_service import dispatch_email_sync


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class SessionPayload(BaseModel):
    token: str = Field(min_length=1)


class ForgotPasswordPayload(BaseModel):
    email: str = Field(min_length=3)


class VerifyEmailPayload(BaseModel):
    email: str = Field(min_length=3)


class GoogleAuthPayload(BaseModel):
    token: str = Field(min_length=1)
    email: str | None = None
    name: str | None = None
    photo_url: str | None = None


def get_db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@router.post("/signup", status_code=201)
def citizen_signup(
    payload: CitizenSignupRequest,
    background_tasks: BackgroundTasks,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    """Citizen self-registration.
    
    Creates a Firebase Auth user, allocates a USR-CIT-xx identifier,
    creates the database user with is_verified=0, and sends an email verification link.
    """
    # 1. Check if email already registered in DB
    existing = connection.execute(
        "SELECT id, email FROM users WHERE LOWER(email) = LOWER(?)",
        (payload.email.strip(),),
    ).fetchone()
    if existing:
        raise HTTPException(
            status_code=400,
            detail="An account with this email address is already registered.",
        )

    # 2. Allocate next USR-CIT-xx identifier
    rows = connection.execute(
        "SELECT id FROM users WHERE id LIKE 'USR-CIT-%' ORDER BY id DESC"
    ).fetchall()
    next_num = 1
    for r in rows:
        try:
            num = int(r["id"].split("USR-CIT-")[-1])
            if num >= next_num:
                next_num = num + 1
        except Exception:
            pass
    citizen_id = f"USR-CIT-{next_num:02d}"

    # 3. Create Firebase user
    firebase_uid = create_firebase_user(
        email=payload.email.strip(),
        password=payload.password,
        display_name=payload.name.strip(),
        role="Citizen",
        email_verified=False,
    )

    # Ensure unique firebase_uid in local users table
    existing_uid = connection.execute(
        "SELECT id FROM users WHERE firebase_uid = ?", (firebase_uid,)
    ).fetchone()
    if existing_uid:
        import uuid
        firebase_uid = f"{firebase_uid}-{uuid.uuid4().hex[:6]}"

    # 4. Insert into local SQLite users table
    now_text = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    connection.execute(
        """
        INSERT INTO users (id, name, role, email, firebase_uid, status, is_verified, department, created_at)
        VALUES (?, ?, 'Citizen', ?, ?, 'active', 0, NULL, ?)
        """,
        (citizen_id, payload.name.strip(), payload.email.strip(), firebase_uid, now_text),
    )
    connection.commit()

    # Sync to MongoDB and Cloud Firestore if enabled
    try:
        from src.mongodb import save_user_to_mongodb
        save_user_to_mongodb({
            "id": citizen_id,
            "name": payload.name.strip(),
            "role": "Citizen",
            "email": payload.email.strip(),
            "firebase_uid": firebase_uid,
            "status": "active",
            "is_verified": 0,
            "department": None,
            "created_at": now_text,
        })
    except Exception:
        pass

    try:
        from src.firestore_db import save_user_to_firestore
        save_user_to_firestore({
            "id": citizen_id,
            "name": payload.name.strip(),
            "role": "Citizen",
            "email": payload.email.strip(),
            "firebase_uid": firebase_uid,
            "status": "active",
            "is_verified": 0,
            "department": None,
            "created_at": now_text,
        })
    except Exception:
        pass

    # 5. Generate verification link & dispatch email in background
    verify_link = generate_email_verification_link(payload.email.strip())
    background_tasks.add_task(
        dispatch_email_sync,
        recipient=payload.email.strip(),
        template="account_verification",
        context={
            "name": payload.name.strip(),
            "link": verify_link,
        },
        event_id=f"verify-{citizen_id}",
    )

    return {
        "success": True,
        "data": {
            "id": citizen_id,
            "name": payload.name.strip(),
            "email": payload.email.strip(),
            "role": "Citizen",
            "is_verified": False,
            "message": "Account created. Please check your email to verify your account before lodging complaints.",
        },
    }


@router.post("/verify-email")
def verify_email_confirm(
    payload: VerifyEmailPayload,
    background_tasks: BackgroundTasks,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    """Mark citizen account verified upon clicking verification link or completing emulator flow."""
    user = connection.execute(
        "SELECT * FROM users WHERE LOWER(email) = LOWER(?)",
        (payload.email.strip(),),
    ).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User account not found.")

    connection.execute(
        "UPDATE users SET is_verified = 1 WHERE id = ?",
        (user["id"],),
    )
    connection.commit()

    # Sync verified status to MongoDB
    try:
        from src.mongodb import get_user_from_mongodb, save_user_to_mongodb
        mg_user = get_user_from_mongodb(user["id"]) or dict(user)
        mg_user["is_verified"] = 1
        save_user_to_mongodb(mg_user)
    except Exception:
        pass

    # Send welcome email
    background_tasks.add_task(
        dispatch_email_sync,
        recipient=user["email"],
        template="welcome",
        context={"name": user["name"], "link": "http://127.0.0.1:8000/citizen"},
        event_id=f"welcome-{user['id']}",
    )

    return {"success": True, "message": "Email verified successfully."}


@router.post("/resend-verification")
def resend_verification(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Resend email verification link for the logged in user."""
    if current_user.get("is_verified"):
        return {"success": True, "message": "Account is already verified."}

    verify_link = generate_email_verification_link(current_user["email"])
    background_tasks.add_task(
        dispatch_email_sync,
        recipient=current_user["email"],
        template="account_verification",
        context={"name": current_user["name"], "link": verify_link},
        event_id=f"verify-resend-{current_user['id']}-{int(datetime.now(UTC).timestamp())}",
    )
    return {"success": True, "message": "Verification email sent."}


@router.post("/forgot-password")
def forgot_password(
    payload: ForgotPasswordPayload,
    background_tasks: BackgroundTasks,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    """Initiate password reset email."""
    user = connection.execute(
        "SELECT * FROM users WHERE LOWER(email) = LOWER(?)",
        (payload.email.strip(),),
    ).fetchone()
    # Always return success to avoid email enumeration
    if user:
        reset_link = generate_password_reset_link(payload.email.strip())
        background_tasks.add_task(
            dispatch_email_sync,
            recipient=payload.email.strip(),
            template="password_reset",
            context={"name": user["name"], "link": reset_link},
            event_id=f"pwd-reset-{user['id']}-{int(datetime.now(UTC).timestamp())}",
        )
    return {
        "success": True,
        "message": "If an account exists with that email, a password reset link has been dispatched.",
    }


@router.get("/me")
def get_me(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    """Return verified profile of current authenticated user from authoritative database."""
    return {
        "success": True,
        "data": {
            "id": current_user["id"],
            "name": current_user["name"],
            "email": current_user["email"],
            "role": current_user["role"],
            "department": current_user.get("department"),
            "status": current_user.get("status", "active"),
            "is_verified": bool(current_user.get("is_verified")),
        },
    }


@router.post("/session")
def set_session_cookie(payload: SessionPayload, response: Response) -> dict[str, Any]:
    """Set the session cookie so full page loads can authenticate server-side."""
    response.set_cookie(
        key="govfix_token",
        value=payload.token,
        httponly=False,  # Allow client JS to check session existence
        samesite="lax",
        path="/",
        max_age=86400 * 7,  # 7 days
    )
    return {"success": True}


@router.post("/logout")
def logout(response: Response) -> dict[str, Any]:
    """Clear session cookie upon sign out."""
    response.delete_cookie(key="govfix_token", path="/")
    return {"success": True, "message": "Logged out successfully."}


@router.get("/config")
def get_auth_config() -> dict[str, Any]:
    """Expose public Firebase web client configuration for Google authentication."""
    from dotenv import load_dotenv
    load_dotenv()
    project_id = os.getenv("FIREBASE_PROJECT_ID", "gov-fix")
    api_key = os.getenv("FIREBASE_WEB_API_KEY", "")
    auth_domain = os.getenv("FIREBASE_AUTH_DOMAIN", f"{project_id}.firebaseapp.com")
    app_id = os.getenv("FIREBASE_APP_ID", "")
    messaging_sender_id = os.getenv("FIREBASE_MESSAGING_SENDER_ID", "")
    return {
        "projectId": project_id,
        "apiKey": api_key,
        "authDomain": auth_domain,
        "appId": app_id,
        "messagingSenderId": messaging_sender_id,
        "googleAuthEnabled": True,
        "emulator": bool(os.getenv("FIREBASE_AUTH_EMULATOR_HOST")),
    }


@router.post("/google", status_code=status.HTTP_200_OK)
@router.post("/google-signin", status_code=status.HTTP_200_OK)
def google_signin(
    payload: GoogleAuthPayload,
    response: Response,
    background_tasks: BackgroundTasks,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    """Citizen Google Sign-In and auto-provisioning.
    
    Verifies Google/Firebase ID token, locates or provisions the Citizen account (USR-CIT-xx),
    syncs with MongoDB, sets session cookie, and returns role-based redirect URL.
    """
    token_payload = verify_token_payload(payload.token)
    uid = token_payload.get("uid")
    email = (payload.email or token_payload.get("email") or "").strip()
    name = (payload.name or token_payload.get("name") or "").strip()

    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Valid email address is required for Google Sign-In.",
        )

    # 1. Lookup existing user by firebase_uid or email
    user = None
    if uid:
        user = connection.execute("SELECT * FROM users WHERE firebase_uid = ?", (uid,)).fetchone()
    if not user and email:
        user = connection.execute("SELECT * FROM users WHERE LOWER(email) = LOWER(?)", (email,)).fetchone()

    # Fallback lookup in MongoDB
    if not user:
        try:
            from src.mongodb import get_user_from_mongodb
            mg_user = get_user_from_mongodb(uid or email)
            if mg_user and mg_user.get("id"):
                now_str = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                connection.execute(
                    """
                    INSERT INTO users (id, name, email, role, department, firebase_uid, status, is_verified, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        firebase_uid = excluded.firebase_uid,
                        is_verified = excluded.is_verified
                    """,
                    (
                        mg_user["id"],
                        mg_user.get("name", name or "Citizen"),
                        mg_user.get("email", email),
                        mg_user.get("role", "Citizen"),
                        mg_user.get("department"),
                        mg_user.get("firebase_uid", uid),
                        mg_user.get("status", "active"),
                        mg_user.get("is_verified", 1),
                        mg_user.get("created_at", now_str),
                    ),
                )
                connection.commit()
                user = connection.execute("SELECT * FROM users WHERE id = ?", (mg_user["id"],)).fetchone()
        except Exception:
            pass

    now_text = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    is_new = False

    if user:
        user_dict = dict(user)
        if user_dict.get("status") == "disabled":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is disabled. Contact system administrator.",
            )

        # Update firebase_uid and ensure email is marked verified (Google accounts are pre-verified)
        updates = []
        params = []
        if uid and user_dict.get("firebase_uid") != uid:
            updates.append("firebase_uid = ?")
            params.append(uid)
            user_dict["firebase_uid"] = uid
        if not user_dict.get("is_verified"):
            updates.append("is_verified = 1")
            user_dict["is_verified"] = 1
        if updates:
            params.append(user_dict["id"])
            connection.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", tuple(params))
            connection.commit()
    else:
        # 2. Auto-provision new Citizen (USR-CIT-xx)
        is_new = True
        rows = connection.execute(
            "SELECT id FROM users WHERE id LIKE 'USR-CIT-%' ORDER BY id DESC"
        ).fetchall()
        next_num = 1
        for r in rows:
            try:
                num = int(r["id"].split("USR-CIT-")[-1])
                if num >= next_num:
                    next_num = num + 1
            except Exception:
                pass
        citizen_id = f"USR-CIT-{next_num:02d}"
        display_name = name or email.split("@")[0].title() or "Citizen"

        connection.execute(
            """
            INSERT INTO users (id, name, role, email, firebase_uid, status, is_verified, department, created_at)
            VALUES (?, ?, 'Citizen', ?, ?, 'active', 1, NULL, ?)
            """,
            (citizen_id, display_name, email, uid, now_text),
        )
        connection.commit()

        user_dict = {
            "id": citizen_id,
            "name": display_name,
            "role": "Citizen",
            "email": email,
            "firebase_uid": uid,
            "status": "active",
            "is_verified": 1,
            "department": None,
            "created_at": now_text,
        }

        # Dispatch welcome email in background
        background_tasks.add_task(
            dispatch_email_sync,
            recipient=email,
            template="welcome",
            context={"name": display_name, "link": "http://127.0.0.1:8000/citizen"},
            event_id=f"welcome-google-{citizen_id}",
        )

    # Sync user to MongoDB
    try:
        from src.mongodb import save_user_to_mongodb
        save_user_to_mongodb(user_dict)
    except Exception:
        pass

    # 3. Set session cookie
    response.set_cookie(
        key="govfix_token",
        value=payload.token,
        httponly=False,
        samesite="lax",
        path="/",
        max_age=86400 * 7,
    )

    # 4. Role-based redirect
    user_role = user_dict.get("role", "Citizen")
    dest = "/citizen"
    if user_role == "Citizen":
        dest = "/citizen"
    elif user_role == "Officer":
        dest = "/staff/triage"
    elif user_role == "Field Officer":
        dest = "/staff/field"
    elif user_role == "Supervisor":
        dest = "/staff/supervisor"
    elif user_role == "Audit Inspector":
        dest = "/staff/audit"
    elif user_role == "Admin":
        dest = "/admin"

    return {
        "success": True,
        "token": payload.token,
        "data": {
            "id": user_dict["id"],
            "name": user_dict["name"],
            "email": user_dict["email"],
            "role": user_dict["role"],
            "department": user_dict.get("department"),
            "status": user_dict.get("status", "active"),
            "is_verified": bool(user_dict.get("is_verified")),
        },
        "is_new": is_new,
        "redirect_url": dest,
    }
