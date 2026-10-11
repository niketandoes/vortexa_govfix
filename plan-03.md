# Plan 03: Interface & Role-Based Workspaces

## 1. Executive Summary & Objective
Plan 03 builds the web user interface for GovFix. The UI provides three dedicated role-based views (Citizen, Officer, Supervisor) and an administrative control panel for simulated time and audit chain inspection. It demonstrates seamless interaction with the deterministic workflow engine and transparently surfaces AI suggestions as advisory widgets.

---

## 2. Frontend Architecture & Technology
- **Tech Stack:** Multi-page web interface (HTML5, Tailwind CSS via CDN, and Vanilla JS) served directly by FastAPI under dedicated routes (`/`, `/citizen`, `/officer`, `/supervisor`, `/audit`).
- **Route Structure (Confirmed):** Distinct server-rendered / static page routes (`/citizen`, `/officer`, `/supervisor`, `/audit`) with dedicated navigation, clear separation of concerns, and full page reload states.
- **Persistent Header Navigation:** Top navigation bar enabling seamless switching between the role routes:
  - `Citizen Portal (/citizen)` - Impersonating Ramesh Patel (`USR-CIT-01`) or Priya Sharma (`USR-CIT-02`)
  - `Officer Workspace (/officer)` - Impersonating Officer Anil Kumar (`USR-OFF-01`)
  - `Supervisor Command Center (/supervisor)` - Impersonating Supervisor Rajesh Gupta (`USR-SUP-01`)
  - `Audit & Tamper Inspection (/audit)` - Verification & Security Log

---

## 3. Role-Based Views & Component Specifications

### 3.1 Citizen Portal (`/citizen`)
- **Complaint Submission:**
  - Clean form: Complaint Title, Description / Complaint Text, Optional Category.
  - Live AI advisory preview: triggers a background suggestion preview showing proposed category and extracted location cues.
- **My Complaints Tracker:**
  - List of complaints owned by the active citizen.
  - Visual Progress Stepper:
    `SUBMITTED` ➔ `ACKNOWLEDGED` ➔ `ASSIGNED` ➔ `IN_PROGRESS` ➔ `RESOLVED` ➔ `CLOSED`
  - Current status badge (Color-coded: Blue for Submitted, Yellow for In Progress, Green for Resolved, Gray for Closed, Red for Rejected).
  - SLA Deadline Indicator showing remaining time.
- **Citizen Action Bar:**
  - **Confirm Closure:** Enabled only when request is in `RESOLVED` state. Transitions to `CLOSED`.
  - **Reopen Complaint:** Enabled only when in `RESOLVED` state. Opens a modal asking for reason, transitioning back to `REOPENED`.

### 3.2 Officer Workspace (`/officer`)
- **Assigned Task Queue:**
  - Cards displaying assigned grievances filtered to current officer (`assigned_to == actor.id`).
  - Badges indicating SLA countdown timer, overdue status, and escalation level (`Level 1`, `Level 2`).
- **Grievance Detail & Action Pane:**
  - Citizen complaint text and submission timestamp.
  - **AI Advisory Sidebar:**
    - Suggested Category (with confidence score) + "Confirm Category" button.
    - Extracted Location & Urgency cues.
    - AI Draft Response + "Copy into Resolution Notes" button.
  - **Workflow Actions:**
    - `Acknowledge`: Available when status is `SUBMITTED`.
    - `Start Work (IN_PROGRESS)`: Available when status is `ASSIGNED` or `REOPENED`.
    - `Mark Resolved (RESOLVED)`: Requires resolution notes.

### 3.3 Supervisor Command Center (`/supervisor`)
- **Intake & Triage Board:**
  - View all unassigned and newly submitted complaints.
  - Action to assign ticket to specific officers with an officer dropdown.
  - Action to reject ticket with mandatory `rejection_reason`.
- **Overdue & Escalations Board:**
  - Dedicated view showing tickets that have breached SLA thresholds:
    - `Level 1 Escalation`: Elapsed time >= 1.0x SLA (Highlighted Yellow).
    - `Level 2 Escalation`: Elapsed time >= 1.5x SLA (Highlighted Red, flagged for Department Head).
- **Force Closure:**
  - Ability for supervisor to administratively close disputed tickets in `RESOLVED` state.
- **Simulated Clock Widget:**
  - Floating/header controller displaying current simulated timestamp.
  - Fast-forward buttons: `+1 Hour`, `+12 Hours`, `+24 Hours`, `+72 Hours`, and `Reset to Real Time`.
- **Audit Viewer & Chain Verification:**
  - Real-time audit log table showing `timestamp`, `actor`, `action`, `from_state`, `to_state`, `outcome` (ACCEPTED/REFUSED), `reason`, and cryptographic `hash`.
  - Refusals highlighted in red to demonstrate enforced security.
  - **"Verify Audit Chain" Button:** Runs cryptographic verification across all entries and displays green checkmark or red tamper alert with compromised record ID.
  - **"Simulate Tamper" Demo Button:** Intentionally modifies a past database row to prove live tamper detection to judges.

---

## 4. Implementation Deliverables
1. `src/templates/index.html`: Responsive unified dashboard with tabs for Citizen, Officer, Supervisor, and Audit views.
2. `src/static/app.js`: Clean client-side state handling, API calls, role switching, and dynamic UI updates.
3. `src/static/styles.css`: Custom government portal styling complementing Tailwind CSS.
4. `src/routes/ui.py`: FastAPI routes serving HTML pages and static assets.

---

## 5. Confirmed Decisions & Implementation Notes
1. **Multi-Page Routes:** Confirmed separate distinct page routes (`/citizen`, `/officer`, `/supervisor`, `/audit`) with full page reloads and clean navigation bar.
2. **AI Suggestions Display:** AI suggestions are displayed in the Officer and Supervisor workspaces to assist decision making without mutating data.
3. **Audit Log Access:** Dedicated `/audit` route provides real-time audit inspection and chain verification.
4. **FastAPI Templating:** Served directly using `Jinja2Templates` and static files, requiring no external Node.js build tools.

