# GovFix — Municipal Grievance Workflow Engine: Solution Report

Domain 7 · Government Systems & GovTech · 10-Hour Hackathon MVP
Problem Statement G-02 · Track: Municipal Grievance Resolution Workflow

---

## 1. Executive Summary & Core Directives

GovFix is an auditable municipal grievance resolution workflow engine that takes complaints from citizen submission to department assignment, on-ground field-level resolution, citizen confirmation, and closure — with strictly enforced server-side permissions, category-based SLA deadlines, multi-level escalation, clock pausing, and a permanent cryptographic audit trail.

**Guiding Architectural Invariant:**
> **AI suggests, deterministic rules decide, humans confirm, and everything is logged.**

GovFix does not build a generic government dashboard. It implements one complete, rule-driven municipal grievance workflow that judges can test from beginning to end across five distinct personas.

---

## 2. Municipal Grievance Scope & SLA Targets

The system focuses on four core municipal service categories with explicit service-specific deadlines, accompanied by an initial 2-hour triage target for all newly submitted complaints:

| Category | Target Resolution SLA | Scope & Description |
| :--- | :--- | :--- |
| **Garbage and sanitation** | 24 Hours | Overflowing bins, missed waste collection, illegal dumping |
| **Water leakage** | 24 Hours | Leaking public pipelines, main bursts, water wastage |
| **Streetlight failure** | 48 Hours | Non-functional public streetlights, electrical faults |
| **Potholes and road damage** | 72 Hours | Damaged road surfaces requiring municipal asphalt repair |
| **Uncategorised** | 72 Hours | Default SLA before category is confirmed by municipal officer |

*Initial Triage & Assignment Target:* 2 hours from submission for municipal triage review.

---

## 3. Workflow Design & State Transitions

### 3.1 Nine Canonical Workflow States
`SUBMITTED`, `ACKNOWLEDGED`, `NEEDS_INFO`, `ASSIGNED`, `IN_PROGRESS`, `RESOLVED`, `CLOSED`, `REOPENED`, `REJECTED`.

### 3.2 Transition Rules & Role Permissions Matrix
The transition table is immutable server-side configuration:

| Current State | Allowed Next State | Permitted Role | Mandatory Invariants & Extra Conditions |
| :--- | :--- | :--- | :--- |
| `(new)` | `SUBMITTED` | `Citizen` | Title, complaint text, and location details required. |
| `SUBMITTED` | `ACKNOWLEDGED` | `Triage Officer`, `Supervisor` | Formal triage acknowledgement by municipal desk. |
| `ACKNOWLEDGED` | `NEEDS_INFO` | `Triage Officer`, `Supervisor` | Requires `info_request_note`; pauses SLA resolution clock. |
| `NEEDS_INFO` | `ACKNOWLEDGED` | `Citizen` | Request owner provides `additional_info`; resumes clock and extends deadline. |
| `ACKNOWLEDGED` | `ASSIGNED` | `Triage Officer`, `Supervisor` | Assigns case to a Field Officer; requires `assigned_to` (`Field Officer`). |
| `ACKNOWLEDGED` | `REJECTED` | `Triage Officer`, `Supervisor` | Requires mandatory recorded `rejection_reason`. |
| `REJECTED` | `ACKNOWLEDGED` | `Supervisor` | Supervisor approves citizen appeal; requires `appeal_approval_note`. |
| `ASSIGNED` | `IN_PROGRESS` | `Field Officer` | Must strictly match `assigned_to`. Starts on-ground field execution. |
| `IN_PROGRESS` | `RESOLVED` | `Field Officer` | Must match `assigned_to`; requires mandatory `resolution_notes` and evidence. |
| `RESOLVED` | `CLOSED` | `Citizen` | Request owner confirms completion of work. |
| `RESOLVED` | `REOPENED` | `Citizen` | Request owner disputes resolution; requires `reopen_reason`; starts new SLA cycle. |
| `CLOSED` | `REOPENED` | `Citizen` | Allowed within 7 days of closure; requires `reopen_reason`; starts new SLA cycle. |
| `REOPENED` | `ASSIGNED` | `Supervisor` | Supervisor reassigns case to a Field Officer; requires `assigned_to`. |

### 3.3 Critical Workflow Invariants
- Direct `REOPENED ? IN_PROGRESS` is strictly prohibited. Reopened cases must be reviewed and assigned by a Supervisor.
- Direct `SUBMITTED ? ASSIGNED`, `IN_PROGRESS ? CLOSED`, or any skipped transitions are categorically blocked.
- Citizens cannot assign cases, mark field work complete, or arbitrarily edit complaint status.
- Field Officers cannot reassign cases, change assignees, reject complaints, approve appeals, or close cases.
- Supervisors handle reassignment for `ASSIGNED` or `IN_PROGRESS` cases with a mandatory recorded reason, preserving current state.
- No unrestricted Force Closure; closure remains citizen-confirmed.
- Refused attempts show explicit failure diagnostics (e.g. *"Only the assigned field officer can move this complaint to In Progress"*) and leave ticket data untouched.

---

## 4. Five Distinct Role-Based Personas

GovFix implements five dedicated multi-page workspaces with persistent navigation and active persona indicators:

1. **Persona 1: Citizen Portal (`/citizen`)**
   - Submit complaint with title, description, category hint, and location details.
   - Unique tracking token / complaint ID (e.g., `REQ-2026-0001`).
   - Scoped view: lists strictly complaints owned by the active citizen.
   - Visual progress stepper, SLA countdown timer, overdue warnings, and status history.
   - Provide missing information during `NEEDS_INFO` (resumes SLA clock).
   - Confirm resolution (`RESOLVED ? CLOSED`), dispute resolution (`RESOLVED ? REOPENED`), and reopen closed cases within 7 days.

2. **Persona 2: Triage Officer Workspace (`/officer`)**
   - Review incoming `SUBMITTED` complaints in the intake queue.
   - Acknowledge complaints (`SUBMITTED ? ACKNOWLEDGED`).
   - Request missing details (`ACKNOWLEDGED ? NEEDS_INFO`).
   - Assign acknowledged complaints to available seeded Field Officers (`ACKNOWLEDGED ? ASSIGNED`). Assigned cases disappear from intake and appear in the field officer's queue.
   - Reject invalid complaints with mandatory recorded reason (`ACKNOWLEDGED ? REJECTED`).
   - Review AI category and location recommendations without allowing autonomous mutations.

3. **Persona 3: Field Officer Workspace (`/field-officer`)**
   - Dedicated on-ground execution workspace.
   - Scoped view: displays strictly cases assigned to the active Field Officer (`assigned_to == actor.id`).
   - Accept and start work (`ASSIGNED ? IN_PROGRESS`).
   - Record progress notes.
   - Submit resolution details and completion evidence (`IN_PROGRESS ? RESOLVED`).
   - Overdue alerts and escalation warnings prominently displayed.

4. **Persona 4: Supervisor Command Center (`/supervisor`)**
   - Municipal overview across unassigned, acknowledged, and in-progress complaints.
   - Reassign active cases (`ASSIGNED` or `IN_PROGRESS`) with mandatory reason.
   - Review and approve citizen rejection appeals (`REJECTED ? ACKNOWLEDGED`).
   - Reassign reopened cases (`REOPENED ? ASSIGNED`).
   - SLA monitoring: 75% At Risk, 100% Level 1 Overdue, and 150% / +8h Level 2 Critical Overdue.
   - Administrative simulated clock controller: `+1h`, `+12h`, `+24h`, `+72h`, and `Reset to Real Time`.

5. **Persona 5: Audit Inspector Console (`/audit`)**
   - Read-only inspection console.
   - Append-only audit log displaying accepted and refused actions, timestamps, actors, roles, states, reasons, details JSON, and SHA-256 hashes.
   - Refused actions highlighted in red to demonstrate enforced security boundaries.
   - "Verify Audit Chain" button validating cryptographic SHA-256 hash chaining.
   - Strictly read-only; zero mutation or bypass controls.

---

## 5. Deadlines, Clock Pausing, and Automatic Escalation

### 5.1 Deterministic SLA Deadlines
Deadlines are deterministically derived from the complaint category, never selected arbitrarily by AI or officers:
$$\text{deadline} = \text{sla\_started\_at} + \text{SLA}[\text{category}] + \text{total\_paused\_seconds}$$

### 5.2 Clock Pausing During `NEEDS_INFO`
When waiting for missing details from a citizen:
1. The resolution clock is paused (`paused_at = now()`).
2. While paused, elapsed SLA time stops accumulating and overdue evaluation is suspended.
3. When the citizen submits info (`NEEDS_INFO ? ACKNOWLEDGED`), paused duration is added to `total_paused_seconds`, the deadline is extended, and the clock resumes.

### 5.3 Escalation Rules
1. **Approaching Deadline (75% Elapsed):** Flagged as `At Risk` with an Amber warning indicator in officer and supervisor dashboards.
2. **Deadline Exceeded (100% Elapsed):** Flagged as `Overdue` and escalated to Supervisor (`Level 1 Escalation`). Records deadline, escalation timestamp, and responsible officer.
3. **Still Unresolved (+8 Hours / 150% Elapsed):** Escalated to the designated Department / Municipal Head (`Level 2 Escalation`).
4. **Invariant:** Escalation is an independent level attribute (`escalation_level = 0, 1, 2`) and audit event, **never** an arbitrary status change. An overdue complaint remains `IN_PROGRESS`.
5. **Idempotency:** Unique deduplication keys prevent duplicate escalation events upon repeated clock advances or dashboard refreshes.

---

## 6. Audit Trail & Cryptographic Verification

Every accepted or refused transition, assignment, reassignment, appeal, category confirmation, and escalation creates an immutable audit record:

```sql
CREATE TABLE audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    simulated_at TEXT,
    actor TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    action TEXT NOT NULL,
    request_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    outcome TEXT NOT NULL CHECK(outcome IN ('ACCEPTED', 'REFUSED')),
    reason TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    dedupe_key TEXT UNIQUE,
    previous_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
```

- **Trigger Immutability:** SQLite `BEFORE UPDATE` and `BEFORE DELETE` triggers unconditionally abort any mutation.
- **SHA-256 Hash Chaining:** Every entry hashes the previous entry's hash combined with canonical sorted JSON payload.
- **Refusal Logging:** Failed attempts (unauthorized roles, state skips, missing notes) are recorded with status `REFUSED`, leaving the complaint unchanged.
- **Tamper Demonstration:** Demonstrated via `scripts/tamper_demo.py` on a disposable database copy, proving the chain detects tampered records while the production database remains untouched.

---

## 7. AI Advisory Layer & Structural Boundary

- **Advisory Capabilities:** Suggests category (with confidence), extracts location and urgency cues, and drafts citizen response templates.
- **Structural Boundary:** AI writes strictly to an isolated `ai_suggestions` table.
- **Zero Direct Mutations:** AI can never modify ticket status, assignee, deadline, or escalation level.
- **Dual-Path Strategy:** Primary Google Gemini 1.5 Flash with automatic silent fallback to a bundled offline `scikit-learn` TF-IDF classifier.

---

## 8. Four Core Demonstration Scenarios for Hackathon Judges

1. **Complete Complaint Lifecycle:**
   Citizen submits a pothole complaint on `/citizen` and receives tracking token `REQ-2026-0001`. Triage Officer reviews on `/officer` and assigns to Field Officer `USR-FLD-01`. Field Officer accepts work (`IN_PROGRESS`), records repairs, and marks resolved (`RESOLVED`). Citizen confirms resolution (`CLOSED`).
2. **Invalid Action Blocked & Refusal Logged:**
   An unauthorized user attempts to resolve a complaint directly, or Field Officer attempts to skip `ASSIGNED ? RESOLVED`. The backend rejects the action with explicit diagnostic reason, leaves the ticket unchanged, and logs `outcome = REFUSED` in `/audit`.
3. **Automatic Multi-Level SLA Escalation:**
   Inspect seeded active 24h complaint `REQ-2026-0006`. Advance simulated clock by 25 hours on `/supervisor` widget to trigger `Level 1` Overdue escalation to Supervisor. Advance 11 additional hours (36h total, 1.5x) to trigger `Level 2` escalation to Municipal Head. Verify audit trail logs zero duplicate events.
4. **Resolution Dispute & Supervisor Reassignment:**
   Citizen disputes a completed resolution (`RESOLVED ? REOPENED` with mandatory reason). Supervisor inspects case on `/supervisor` and reassigns to Field Officer `USR-FLD-02` (`REOPENED ? ASSIGNED`). Field Officer accepts work and resolves.
