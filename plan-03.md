# Plan 03: Interface & Role-Based Workspaces (Five Distinct Personas)

## 1. Executive Summary & Objective
Plan 03 builds the web user interface for GovFix. The interface provides five dedicated role-based workspaces (Citizen, Officer — Triage, Field Officer — On-Ground Execution, Supervisor — Command Center, and Audit Inspector — Read-Only Verification) and an administrative control panel for simulated time and cryptographic audit chain inspection.

The UI maintains strict multi-page separation with full page reloads, a shared government-service visual theme (`base.html`), and persistent navigation that clearly reflects the active persona.

---

## 2. Frontend Architecture & Technology
- **Tech Stack:** Multi-page web interface (HTML5, Tailwind CSS via CDN, and Vanilla JS) served directly by FastAPI under dedicated routes (`/`, `/citizen`, `/officer`, `/field-officer`, `/supervisor`, `/audit`).
- **Route Structure (Confirmed):** Distinct server-rendered page routes with dedicated navigation, clear separation of concerns, and full page reload states. No single-page role-tab interface.
- **Shared Header Navigation (`base.html`):** Top navigation bar enabling seamless switching between the five persona routes:
  - `Citizen Portal (/citizen)` — Impersonating Ramesh Patel (`USR-CIT-01`) or Priya Sharma (`USR-CIT-02`)
  - `Officer Workspace (/officer)` — Impersonating Officer Anil Kumar (`USR-OFF-01`, Triage & Assignment)
  - `Field Officer Workspace (/field-officer)` — Impersonating Field Officer Vikram Singh (`USR-FLD-01`) or Sunita Rao (`USR-FLD-02`)
  - `Supervisor Command Center (/supervisor)` — Impersonating Supervisor Rajesh Gupta (`USR-SUP-01`)
  - `Audit Inspector Console (/audit)` — Read-only verification and security log
- **Persona Indicator:** Persistent top bar widget showing the currently active persona, name, role badge, and department.

---

## 3. Role-Based Views & Component Specifications

### 3.1 Persona 1: Citizen Portal (`/citizen`)
- **Complaint Submission:**
  - Form fields: Title, Description / Complaint Text, Optional Category hint (`Water Leak`, `Garbage Collection`, `Streetlight`, `Pothole`), and Location details.
  - Generates a unique tracking token / complaint ID (e.g., `REQ-2026-0001`).
  - Live AI advisory preview: non-intrusive preview showing detected category and extracted location cues.
- **My Complaints Tracker:**
  - Lists **only** complaints owned by the active citizen (`citizen_id == actor.id`).
  - Status Timeline & Visual Stepper across canonical states:
    `SUBMITTED` ➔ `ACKNOWLEDGED` ➔ `ASSIGNED` ➔ `IN_PROGRESS` ➔ `RESOLVED` ➔ `CLOSED`
  - Badges for status, assigned department/officer, SLA countdown timer, and overdue/escalation indicators.
- **Citizen Action Bar:**
  - **Provide Missing Information:** Active when status is `NEEDS_INFO`. Opens an input modal for `additional_info`, triggering `NEEDS_INFO → ACKNOWLEDGED`, resuming the SLA clock and extending the deadline.
  - **Confirm Resolution:** Active when status is `RESOLVED`. Citizen confirms work, triggering `RESOLVED → CLOSED`.
  - **Dispute Resolution & Reopen:** Active when status is `RESOLVED`. Citizen provides a mandatory `reopen_reason`, triggering `RESOLVED → REOPENED` and initiating a new SLA cycle.
  - **Reopen Closed Complaint:** Active for `CLOSED` complaints within the 7-day post-closure window.
- **Strict Invariants:** Citizens cannot assign cases, mark field work complete, or arbitrarily modify status.

### 3.2 Persona 2: Officer Workspace — Triage & Assignment (`/officer`)
- **Intake & Triage Queue:**
  - Displays newly submitted complaints (`SUBMITTED`) and items awaiting review after citizen response (`ACKNOWLEDGED`).
  - Displays complaint ID, category, location, citizen details permitted by system, and 2-hour triage SLA indicator.
  - **Review & Acknowledge:** `SUBMITTED → ACKNOWLEDGED`. Indicates formal receipt by municipal desk.
- **Triage Actions:**
  - **Request Missing Info:** `ACKNOWLEDGED → NEEDS_INFO` with mandatory `info_request_note`. Pauses the SLA clock.
  - **Assign Field Officer:** `ACKNOWLEDGED → ASSIGNED`. Dropdown of available seeded Field Officers (`USR-FLD-01`, `USR-FLD-02`). Upon assignment, the complaint disappears from the intake queue and appears in the chosen Field Officer's queue.
  - **Reject Complaint:** `ACKNOWLEDGED → REJECTED` with mandatory recorded `rejection_reason`.
- **AI Advisory Sidebar:**
  - Suggested Category (with confidence score) + "Confirm Category" button.
  - Extracted Location & Urgency cues.
  - AI Draft Response preview.
- **Strict Invariants:** Triage Officers must not mark field work complete or close complaints. AI never modifies operational fields autonomously.

### 3.3 Persona 3: Field Officer Workspace — On-Ground Execution (`/field-officer`)
- **Assigned Tasks Queue:**
  - Displays **only** complaints assigned to the active Field Officer (`assigned_to == actor.id`).
  - Displays Complaint ID, category, location, description, citizen evidence, assignment timestamp, SLA countdown, and escalation warnings (`Level 1`, `Level 2`).
- **On-Ground Workflow Actions:**
  - **Start Work:** `ASSIGNED → IN_PROGRESS`. Acknowledges field execution start.
  - **Add Progress Notes:** Allows recording incremental field updates.
  - **Submit Resolution:** `IN_PROGRESS → RESOLVED`. Requires mandatory, non-empty `resolution_notes` and completion details.
- **Feedback & Safeguards:**
  - Clear error feedback whenever an action is refused by the backend.
  - Prominent overdue and escalation alerts.
- **Strict Invariants:** Field Officers cannot assign or reassign complaints, change assignees, reject complaints, approve appeals, close complaints, or edit deadlines. The backend strictly forbids accessing another Field Officer's cases.

### 3.4 Persona 4: Supervisor Command Center (`/supervisor`)
- **Municipal Overview & Triage Monitoring:**
  - Comprehensive view across all municipal operations.
  - Review unassigned intake cases, acknowledged cases, and active assignments.
  - **Reassign Work:** Reassign eligible `ASSIGNED` or `IN_PROGRESS` cases to another Field Officer with mandatory recorded reason, preserving current state.
  - **Approve Rejection Appeals:** Review citizen appeals for `REJECTED` tickets and approve transition: `REJECTED → ACKNOWLEDGED`.
  - **Reassign Reopened Cases:** Move `REOPENED → ASSIGNED` to a Field Officer.
- **Overdue & Escalations Board:**
  - Real-time SLA monitoring:
    - `At Risk`: 75% SLA elapsed.
    - `Level 1 Escalation`: 100% SLA elapsed (Supervisor alert).
    - `Level 2 Escalation`: 150% SLA elapsed (Municipal Head alert).
  - Escalation history with responsible-officer tracking.
- **Cases Awaiting Confirmation:**
  - Resolution overview tracking tickets in `RESOLVED` awaiting citizen confirmation.
- **Simulated Clock Widget:**
  - Header controller displaying current simulated timestamp.
  - Fast-forward buttons: `+1 Hour`, `+12 Hours`, `+24 Hours`, `+72 Hours`, and `Reset to Real Time`. Triggers immediate SLA evaluation.
- **Strict Invariants:** No unrestricted Force Closure; closure remains citizen-confirmed.

### 3.5 Persona 5: Audit Inspector Console (`/audit`)
- **Read-Only Verification Surface:**
  - Append-only audit trail displaying `timestamp` (wall-clock UTC), `simulated_at`, `actor`, `actor_role`, `action`, `request_id`, `from_state`, `to_state`, `outcome` (`ACCEPTED` / `REFUSED`), `reason`, `details_json`, and SHA-256 `hash`.
  - Color-coded outcomes: Refused actions highlighted in red to demonstrate security policy enforcement.
- **Interactive Inspection Tools:**
  - Filters: by Complaint ID, Actor ID, Action, Outcome, and Date.
  - **"Verify Audit Chain" Button:** Invokes backend `/api/v1/audit/verify` and displays verified count or first compromised record ID.
  - **Tamper Demonstration Panel:** Clear notice detailing that `scripts/tamper_demo.py` operates on a disposable database copy, while the normal application provides verification only.
- **Strict Invariants:** Strictly read-only; zero mutation controls or trigger-bypass mechanisms.

---

## 4. Implementation Deliverables
1. `src/templates/base.html`: Shared government-service layout with 5-persona navigation and active persona banner.
2. `src/templates/citizen.html`: Citizen complaint submission, tracking timeline, info response, and resolution confirmation.
3. `src/templates/officer.html`: Triage intake queue, Field Officer assignment, info requests, and AI advisory widget.
4. `src/templates/field_officer.html`: On-ground execution queue, work start, progress notes, and resolution submission.
5. `src/templates/supervisor.html`: Municipal overview, reassignment, appeal approvals, SLA escalations, and simulated clock.
6. `src/templates/audit.html`: Read-only audit log table, filtering, and cryptographic chain verification.
7. `src/static/app.js`: Shared API helpers, actor identity header handling (`X-Actor-ID`), and modal handlers.
8. `src/static/styles.css`: Government portal design tokens and Tailwind utility classes.
9. `src/routes/ui.py`: FastAPI routes serving `/citizen`, `/officer`, `/field-officer`, `/supervisor`, and `/audit`.

---

## 5. Confirmed Decisions & Implementation Notes
1. **Five Explicit Personas:** Segregation between Triage Officer and Field Officer ensures distinct responsibilities.
2. **Multi-Page Architecture:** Each persona has a dedicated URL and template with full page reload state.
3. **Backend Enforcement:** Hiding buttons in the frontend is never relied upon; the backend enforces all permissions, ownership, and valid states.
4. **Deterministic Closure:** Tickets in `RESOLVED` require citizen confirmation to close, preventing unilateral officer/supervisor closure.
5. **Simulated Clock Control:** Fast-forwarding time triggers immediate backend SLA evaluation and persists across page reloads.

