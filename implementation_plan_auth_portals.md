# GovFix Auth, Portals & Email Notifications Implementation Plan

## 1. Overview & Fallbacks
- **Architecture**: Real Firebase Authentication (email + password), database-authoritative RBAC, separate portals for Citizen vs Staff, async outbox-backed email notifications.
- **Reference Codebase**: `C:\Users\niket\Documents\Hackathon-106\Google-Code-For-Communities-` (Firebase Auth SDK patterns, token verification, staff account seeding, templates).
- **Fallbacks**:
  - Firebase Auth: Firebase Auth Emulator fallback (`FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099`).
  - Email Provider: `LocalOutboxProvider` (`EMAIL_PROVIDER=local_outbox`), saving `.eml` / HTML files into gitignored `.outbox/` and exposing dev `/dev/outbox` page.
  - Test Auth Override: `GOVFIX_TEST_AUTH_OVERRIDE=1` for existing automated test suite backward compatibility.

---

## 2. Database Schema Migrations
1. **`users` Table Upgrade**:
   - Add `firebase_uid TEXT UNIQUE`
   - Add `status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled'))`
   - Add `is_verified INTEGER NOT NULL DEFAULT 0 CHECK(is_verified IN (0, 1))`
   - Expand `role` CHECK constraint: `'Citizen', 'Officer', 'Field Officer', 'Supervisor', 'Audit Inspector', 'Admin'`.
   - Migration logic in `src/database.py` dynamically applies missing columns and checks on existing tables.
2. **`email_events` Table (Dedicated, separate from hash-chained audit)**:
   - `id INTEGER PRIMARY KEY AUTOINCREMENT`
   - `recipient TEXT NOT NULL`
   - `template TEXT NOT NULL`
   - `event_id TEXT UNIQUE` (for idempotency: one email per workflow / escalation event)
   - `status TEXT NOT NULL CHECK(status IN ('sent', 'failed'))`
   - `error TEXT`
   - `sent_at TEXT NOT NULL`
   - `metadata_json TEXT NOT NULL DEFAULT '{}'`

---

## 3. Route Map

### Auth & Public Endpoints
- `GET /login`: Public login page.
- `GET /signup`: Citizen self-registration page.
- `GET /verify-email`: Email verification pending page.
- `GET /forgot-password`: Password reset request page.
- `POST /api/v1/auth/signup`: Citizen signup endpoint (creates Firebase account + DB record with `USR-CIT-xx`).
- `GET /api/v1/auth/me`: Current authenticated user profile with verified DB role.
- `POST /api/v1/auth/send-verification`: Trigger/resend email verification link.
- `POST /api/v1/auth/reset-password`: Trigger password reset email.
- `POST /api/v1/auth/session`: Sets auth cookie for browser navigation.
- `POST /api/v1/auth/logout`: Clears session cookie and signs out.

### Citizen Portal (Light Theme, Mobile-First, Friendly)
- `GET /citizen`: Public citizen dashboard (strictly citizen requests, own stats, lodge complaint form, status timeline).
- Forbidden to staff roles; unauthenticated redirects to `/login`.

### Staff Workspaces (Dark/Slate Theme, Dense, Work-Focused Sidebar Layout)
- `GET /staff/triage`: Triage Officer workspace (unrouted/intake queue, AI suggestions, category confirm, acknowledge, dispatch).
- `GET /staff/field`: Field Officer workspace (assigned work orders ordered by deadline, start work, progress notes, submit resolution).
- `GET /staff/supervisor`: Supervisor workspace (overdue & escalated queues, reassign, reject, review & decide appeals, close).
- `GET /staff/audit`: Audit Inspector workspace (read-only audit log table, filters, cryptographic SHA-256 chain verification).
- `GET /admin`: Administrator console (user management, staff creation, SLA configuration, simulated clock controls).
- Cross-role route access redirects or raises 403 Forbidden.

### Email & Dev Tools
- `GET /dev/outbox`: Dev outbox UI showing dispatched emails.
- `GET /api/v1/dev/outbox`: JSON list and content of outbox messages.

### Request & Workflow API Scoping
- `GET /api/v1/requests`: Scoped on server by DB role:
  - Citizen: strictly `citizen_id = current_user.id`. Internal staff IDs (`assigned_to`) stripped / replaced with team/officer display name.
  - Officer (Triage): `state IN ('SUBMITTED', 'ACKNOWLEDGED', 'NEEDS_INFO')` or `assigned_to IS NULL`.
  - Field Officer: strictly `assigned_to = current_user.id`.
  - Supervisor: all requests, escalations, appeals.
  - Audit Inspector: 403 Forbidden on operational requests.
  - Admin: 403 Forbidden on workflow transitions.
- `GET /api/v1/requests/{id}`:
  - Citizen: 403 if not request owner.
  - Serialized response for citizens sanitizes internal IDs (shows team or officer display name).
- `POST /api/v1/requests`: Requires verified citizen account.
- 403 Refusal Logging: Authenticated forbidden attempts logged to `audit` with verified actor identity.

---

## 4. Templates List
- `src/templates/login.html` & `frontend/templates/login.html`: Branded sign-in with role redirect.
- `src/templates/signup.html` & `frontend/templates/signup.html`: Citizen self-registration.
- `src/templates/verify_email.html` & `frontend/templates/verify_email.html`: Verification prompt.
- `src/templates/forgot_password.html` & `frontend/templates/forgot_password.html`: Password recovery.
- `src/templates/citizen_portal.html` (rebuilt `citizen.html`): Mobile-first, friendly, no clock, no role switcher, avatar menu, own complaints only.
- `src/templates/staff_triage.html`: Dense triage console with AI advisory cards.
- `src/templates/staff_field.html`: Dense field officer console with deadline-sorted work orders.
- `src/templates/staff_supervisor.html`: Supervisor console with escalation ladder (Level 1 & Level 2), appeals.
- `src/templates/staff_audit.html`: Inspector console, read-only with SHA-256 verification.
- `src/templates/admin.html`: User management, staff creation, SLA config, simulated clock.
- `src/templates/dev_outbox.html`: Local outbox viewer for testing emails without external services.

---

## 5. Email Templates & Notifications
- Branded HTML + Plain-text templates in `src/templates/emails/`:
  1. `account_verification` (Citizen verification link)
  2. `welcome` (Citizen welcome)
  3. `complaint_received` (With SLA deadline, ID, location)
  4. `acknowledged_categorised` (Category confirmed)
  5. `assigned` (Field team assigned - team name only)
  6. `status_update` (General status transitions)
  7. `resolved` (Resolved notice + confirm/reopen link)
  8. `rejected` (Rejection reason + how to appeal)
  9. `appeal_decision` (Supervisor appeal approval)
  10. `new_assignment` (Field officer notification with deadline)
  11. `reassignment` (Field officer reassignment)
  12. `escalation_level_1` (Supervisor alert - 100% SLA)
  13. `escalation_level_2` (Supervisor critical alert - 150% SLA)
  14. `account_invitation` (Staff set-password invitation link)
  15. `password_reset` (Password reset link)
- Background execution post-transaction commit.
- Idempotency via `email_events.event_id`.

---

## 6. Demo Accounts (All with password `GovFix2026!`)
1. Citizen 1: `citizen1@govfix.local` (`USR-CIT-01`, Ramesh Patel)
2. Citizen 2: `citizen2@govfix.local` (`USR-CIT-02`, Priya Sharma)
3. Triage Officer: `triage@govfix.local` (`USR-OFF-01`, Officer Anil Kumar)
4. Field Officer 1: `field1@govfix.local` (`USR-FLD-01`, Field Officer Vikram Singh)
5. Field Officer 2: `field2@govfix.local` (`USR-FLD-02`, Field Officer Sunita Rao)
6. Supervisor: `supervisor@govfix.local` (`USR-SUP-01`, Supervisor Rajesh Gupta)
7. Audit Inspector: `audit@govfix.local` (`USR-AUD-01`, Audit Inspector Meera Joshi)
8. Administrator: `admin@govfix.local` (`USR-ADM-01`, Admin User)
