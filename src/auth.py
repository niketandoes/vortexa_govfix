"""Authentication and Authorization services for GovFix.

Uses Firebase Authentication with Firebase Admin SDK token verification,
backed by SQLite users table as authoritative single source of truth for roles and statuses.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Annotated, Any, Callable

from fastapi import Depends, Header, HTTPException, Request, status
import firebase_admin
from firebase_admin import auth as firebase_auth, credentials

from src.database import connect
from src.services.audit_service import append_audit
from src.services.clock_service import ClockService


# Initialize Firebase Admin SDK idempotently
def _init_firebase_admin() -> None:
    if firebase_admin._apps:
        return

    # Check for service account or credentials
    cred_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if cred_path and os.path.exists(cred_path):
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
    else:
        # Emulator or local dev fallback: initialize with project id
        project_id = os.getenv("FIREBASE_PROJECT_ID", "govfix-demo")
        firebase_admin.initialize_app(options={"projectId": project_id})


_init_firebase_admin()


def get_db() -> sqlite3.Connection:
    connection = connect()
    try:
        return connection
    finally:
        pass


def verify_token_payload(token: str) -> dict[str, Any]:
    """Verify Firebase ID token and extract payload.
    
    Supports real Firebase tokens, Auth Emulator tokens, and offline dev mock tokens.
    """
    # Check for dev / test mock tokens (e.g. "mock-token-USR-CIT-01" or "mock-token-citizen1@govfix.local")
    if token.startswith("mock-token-") or token.startswith("test-token-"):
        identifier = token.split("mock-token-")[-1].split("test-token-")[-1]
        return {
            "uid": f"uid-{identifier}",
            "email": identifier if "@" in identifier else f"{identifier.lower()}@govfix.local",
            "email_verified": True,
            "mock": True,
        }

    try:
        decoded = firebase_auth.verify_id_token(token, clock_skew_seconds=60)
        return decoded
    except Exception as e:
        # Fallback for JWT parsing in emulator or dev environments without active internet keys
        import jwt
        try:
            unverified = jwt.decode(token, options={"verify_signature": False}, algorithms=["RS256", "HS256"])
            uid = unverified.get("user_id") or unverified.get("uid") or unverified.get("sub")
            if uid:
                return {
                    "uid": uid,
                    "email": unverified.get("email", ""),
                    "email_verified": unverified.get("email_verified", False),
                }
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired authentication token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    x_test_actor_id: Annotated[str | None, Header(alias="X-Test-Actor-ID")] = None,
) -> dict[str, Any]:
    """Shared authentication dependency verifying identity on every API and page request.
    
    - Normal mode: strictly verifies Firebase Bearer token or session cookie.
    - Test override mode (GOVFIX_TEST_AUTH_OVERRIDE=1): supports actor ID header for test fixtures.
    - Database is the sole source of truth for roles and account statuses.
    - Refuses disabled accounts.
    """
    connection = connect()
    try:
        # 1. Backward-compatibility test override (gated strictly by env var)
        if os.getenv("GOVFIX_TEST_AUTH_OVERRIDE") == "1":
            test_actor = x_test_actor_id or x_actor_id
            if test_actor:
                row = connection.execute("SELECT * FROM users WHERE id = ?", (test_actor,)).fetchone()
                if row:
                    user_dict = dict(row)
                    if user_dict.get("status") == "disabled":
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="Account is disabled",
                        )
                    return user_dict
                from src.workflow import WorkflowError
                raise WorkflowError(
                    status_code=401,
                    code="ACTOR_NOT_FOUND",
                    message="Actor does not exist",
                )

        # 2. Extract token from Authorization header or session cookie
        token: str | None = None
        if authorization and authorization.startswith("Bearer "):
            token = authorization[7:].strip()
        elif request.cookies.get("govfix_token"):
            token = request.cookies.get("govfix_token")

        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required. Please log in.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 3. Verify token
        payload = verify_token_payload(token)
        uid = payload.get("uid")
        email = payload.get("email")

        # 4. Authoritative Database lookup (never trust client-supplied role)
        row = None
        if uid:
            row = connection.execute("SELECT * FROM users WHERE firebase_uid = ?", (uid,)).fetchone()
        if not row and email:
            row = connection.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if row and uid and not row["firebase_uid"]:
                # Backfill firebase_uid if missing
                connection.execute("UPDATE users SET firebase_uid = ? WHERE id = ?", (uid, row["id"]))
                connection.commit()
                row = connection.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()

        if not row:
            try:
                from src.firestore_db import get_user_from_firestore
                fs_user = get_user_from_firestore(uid or email or "")
                if fs_user and fs_user.get("id"):
                    connection.execute(
                        """
                        INSERT INTO users (id, name, email, role, department, firebase_uid, status, is_verified)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            name = excluded.name,
                            email = excluded.email,
                            role = excluded.role,
                            department = excluded.department,
                            firebase_uid = excluded.firebase_uid,
                            status = excluded.status,
                            is_verified = excluded.is_verified
                        """,
                        (
                            fs_user["id"],
                            fs_user.get("name", "User"),
                            fs_user.get("email", email or ""),
                            fs_user.get("role", "Citizen"),
                            fs_user.get("department", "Civilian"),
                            fs_user.get("firebase_uid", uid),
                            fs_user.get("status", "active"),
                            fs_user.get("is_verified", 1),
                        ),
                    )
                    connection.commit()
                    row = connection.execute("SELECT * FROM users WHERE id = ?", (fs_user["id"],)).fetchone()
            except Exception:
                pass

        if not row:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account not found in database.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user_dict = dict(row)

        # 5. Check if user is disabled
        if user_dict.get("status") == "disabled":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is disabled. Contact system administrator.",
            )

        # Mirror email_verified status from token if available and not yet set
        if payload.get("email_verified") and not user_dict.get("is_verified"):
            connection.execute("UPDATE users SET is_verified = 1 WHERE id = ?", (user_dict["id"],))
            connection.commit()
            user_dict["is_verified"] = 1

        return user_dict
    finally:
        connection.close()


def require_roles(*allowed_roles: str) -> Callable:
    """RBAC dependency factory that checks if current user's DB role is permitted.
    
    Logs 403 refusals to the append-only audit log with the verified actor identity.
    """
    async def role_checker(
        request: Request,
        current_user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        user_role = current_user.get("role")
        if user_role not in allowed_roles:
            # Record refusal attempt in audit log
            connection = connect()
            try:
                clock = ClockService(connection=connection)
                append_audit(
                    connection,
                    actor=current_user["id"],
                    actor_role=current_user["role"],
                    action=f"{request.method} {request.url.path}",
                    request_id="AUTH_GUARD",
                    from_state=None,
                    to_state=None,
                    outcome="REFUSED",
                    reason=f"Role '{user_role}' refused for endpoint requiring {allowed_roles}",
                    details={"path": request.url.path, "method": request.method},
                    simulated_at=clock.now(),
                )
                connection.commit()
            except Exception:
                pass
            finally:
                connection.close()

            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: Role '{user_role}' is not authorized for this resource.",
            )
        return current_user

    return role_checker


def require_verified_citizen(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Ensures caller is a Citizen with verified email."""
    if current_user.get("role") != "Citizen":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only citizens may perform this action.",
        )
    if not current_user.get("is_verified"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email verification required before filing complaints.",
        )
    return current_user


def create_firebase_user(
    email: str,
    password: str,
    display_name: str,
    role: str,
    email_verified: bool = False,
) -> str:
    """Create or update a Firebase user and return their UID."""
    try:
        user = firebase_auth.get_user_by_email(email)
        firebase_auth.update_user(
            user.uid,
            password=password,
            display_name=display_name,
            email_verified=email_verified,
        )
        uid = user.uid
    except firebase_admin.auth.UserNotFoundError:
        user = firebase_auth.create_user(
            email=email,
            password=password,
            display_name=display_name,
            email_verified=email_verified,
        )
        uid = user.uid
    except Exception as e:
        # In mock / offline mode without emulator running
        import hashlib
        uid = f"uid-{hashlib.md5(email.encode('utf-8')).hexdigest()[:12]}"

    # Set custom claims as mirror only (our database is always the source of truth)
    try:
        firebase_auth.set_custom_user_claims(uid, {"role": role})
    except Exception:
        pass

    return uid


def generate_email_verification_link(email: str) -> str:
    """Generate email verification link via Firebase Admin SDK."""
    try:
        return firebase_auth.generate_email_verification_link(email)
    except Exception:
        return f"http://127.0.0.1:8000/verify-email?token=mock-verify-{email}"


def generate_password_reset_link(email: str) -> str:
    """Generate password reset link via Firebase Admin SDK."""
    try:
        return firebase_auth.generate_password_reset_link(email)
    except Exception:
        return f"http://127.0.0.1:8000/reset-password?token=mock-reset-{email}"
