# Plan 02: Core Workflow Engine, Role Checks & Refusal Logging

## 1. Executive Summary & Objective
Plan 02 implements the deterministic workflow engine for GovFix. The engine enforces the municipal grievance state machine, validates every transition request against a strict 5-step validation pipeline, guarantees role authorization on the server, logs every attempt (accepted or refused) into the audit table, and commits state mutations atomically with audit entries.

---

## 2. Transition Rules Matrix & Invariants

The transition table is defined as immutable code configuration across the 9 canonical states (`SUBMITTED`, `ACKNOWLEDGED`, `NEEDS_INFO`, `ASSIGNED`, `IN_PROGRESS`, `RESOLVED`, `CLOSED`, `REOPENED`, `REJECTED`). Any state change outside this table is categorically refused.

| # | From State | To State | Permitted Roles | Invariants & Extra Conditions |
| :- | :--- | :--- | :--- | :--- |
| 1 | `(new)` | `SUBMITTED` | `Citizen` | Title, complaint text, and location must not be empty; set resolution and 2-hour triage deadlines. |
| 2 | `SUBMITTED` | `ACKNOWLEDGED` | `Officer`, `Supervisor` | Formal triage acknowledgement by municipal office. |
| 3 | `ACKNOWLEDGED` | `NEEDS_INFO` | `Officer`, `Supervisor` | Requires `info_request_note`; pauses SLA clock (`paused_at = now()`). |
| 4 | `NEEDS_INFO` | `ACKNOWLEDGED` | `Citizen` | Actor must be request owner (`citizen_id == actor.id`); requires `additional_info`; resumes clock, extends deadline by paused duration. |
| 5 | `ACKNOWLEDGED` | `ASSIGNED` | `Officer`, `Supervisor` | Must supply `assigned_to`; assignee role must be `Field Officer`. |
| 6 | `ACKNOWLEDGED` | `REJECTED` | `Officer`, `Supervisor` | Requires non-empty `rejection_reason`. Citizen may appeal. |
| 7 | `REJECTED` | `ACKNOWLEDGED` | `Supervisor` | Requires an existing Citizen appeal plus non-empty `appeal_approval_note`. |
| 8 | `ASSIGNED` | `IN_PROGRESS` | `Field Officer` | Actor must strictly match `assigned_to`. Starts field execution. |
| 9 | `IN_PROGRESS` | `RESOLVED` | `Field Officer` | Actor must match `assigned_to`; requires non-empty `resolution_notes` and non-empty `resolution_evidence_json`. |
| 10 | `RESOLVED` | `CLOSED` | `Citizen` | Actor must be request owner (`citizen_id == actor.id`); records `closed_at = now()`. |
| 11 | `RESOLVED` | `REOPENED` | `Citizen` | Actor must be request owner disputing resolution; requires non-empty `reopen_reason`; starts new SLA cycle. |
| 12 | `CLOSED` | `REOPENED` | `Citizen` | Request owner; `now() <= closed_at + 7 days`; requires non-empty `reopen_reason`; starts new SLA cycle. |
| 13 | `REOPENED` | `ASSIGNED` | `Supervisor` | Supervisor reassigns case to a Field Officer; requires `assigned_to` (`Field Officer`). |

> **Critical Invariant:** Direct `REOPENED → IN_PROGRESS` is strictly prohibited. Direct `SUBMITTED → ASSIGNED`, `IN_PROGRESS → CLOSED`, or any other skipped transitions are categorically blocked and logged as refused attempts in the audit trail.
> **Escalation Invariant:** Escalation is **not** a workflow state. It is an independent attribute (`escalation_level = 0, 1, 2`) with audit events, preserving the state machine.

Assignment, reassignment, and category confirmation are audited commands, not artificial state transitions. Reassignment preserves `ASSIGNED` or `IN_PROGRESS`; category confirmation preserves state and deterministically recalculates the current SLA deadline.

Citizen appeal submission and Field Officer progress updates are also audited non-state commands. An appeal keeps the request in `REJECTED` until Supervisor approval. A progress update keeps the request in `IN_PROGRESS` and stores its note/evidence in immutable audit details.

---

## 3. Strict 5-Step Validation Pipeline

Every incoming transition request is evaluated through the exact same sequence of 5 checks. If any step fails, the workflow engine **halts immediately**, writes a `REFUSED` entry to the audit log, and raises an authorization or validation error.

```
Incoming Transition Request
          │
          ▼
┌───────────────────────────────┐
│ 1. Actor Existence Check      │──(Fails)──► Log Refusal: "Actor does not exist" ──► Return 401
└───────────────────────────────┘
          │ (Passes)
          ▼
┌───────────────────────────────┐
│ 2. Transition Table Lookup    │──(Fails)──► Log Refusal: "Invalid transition from X to Y" ──► Return 422
└───────────────────────────────┘
          │ (Passes)
          ▼
┌───────────────────────────────┐
│ 3. Stored Role Verification   │──(Fails)──► Log Refusal: "Role R not permitted for transition" ──► Return 403
└───────────────────────────────┘
          │ (Passes)
          ▼
┌───────────────────────────────┐
│ 4. Ownership & Assignment     │──(Fails)──► Log Refusal: "Actor is not assigned field officer/owner" ──► Return 403
└───────────────────────────────┘
          │ (Passes)
          ▼
┌───────────────────────────────┐
│ 5. Required Payload Fields    │──(Fails)──► Log Refusal: "Missing required parameter" ──► Return 422
└───────────────────────────────┘
          │ (Passes)
          ▼
  Atomic DB Transaction:
  1. Compare `expected_version` with stored request version (409 on mismatch)
  2. Update `requests` state & attributes (and unpause clock if leaving NEEDS_INFO) and increment `version`
  3. Insert `audit` entry (outcome='ACCEPTED')
```

Every mutating command requires `expected_version`. A mismatch returns HTTP 409 and records a refused attempt without overwriting the newer request. Request mutation and hash-chain append run in one serialized SQLite transaction (`BEGIN IMMEDIATE`) so concurrent writers cannot reuse the same predecessor hash.

---

## 4. Refusal Logging & Structured Error Response

### 4.1 Audit Refusal Entry
When a transition fails, an entry is immediately committed to the `audit` table with:
- `outcome = 'REFUSED'`
- `action = 'TRANSITION_ATTEMPT'`
- `reason = <explicit human-readable diagnostic message>`
- `from_state = request.state`
- `to_state = target_state`

### 4.2 API Failure Contract (HTTP 403 / 409 / 422)
```json
{
  "success": false,
  "error": {
    "code": "INVALID_TRANSITION_OR_PERMISSION",
    "message": "Only the assigned field officer can move this complaint to In Progress.",
    "current_state": "ASSIGNED",
    "requested_state": "IN_PROGRESS",
    "permitted_next_states": ["IN_PROGRESS"],
    "actor_role": "Citizen",
    "audit_logged": true
  }
}
```

---

## 5. API Endpoints Specification

### 5.1 `POST /api/v1/requests`
- **Role:** Citizen
- **Payload:** `{"title": "...", "text": "...", "reported_category": "Water Leak" (optional), "location": "..."}`
- **Action:** Validates citizen identity and required location, stores the optional category hint, uses `Uncategorised` for the confirmed category and initial 72-hour resolution SLA, sets `triage_deadline = created_at + 2 hours`, inserts the request in `SUBMITTED`, and writes `SUBMIT_COMPLAINT`.

### 5.2 `POST /api/v1/requests/{id}/transition`
- **Role:** Resolved from DB via verified actor identity (`Citizen`, `Officer`, `Field Officer`, `Supervisor`)
- **Payload:**
  ```json
  {
    "to_state": "IN_PROGRESS",
    "expected_version": 3,
    "assigned_to": "USR-FLD-01",      // required for ASSIGNED
    "info_request_note": "...",       // required for NEEDS_INFO
    "additional_info": "...",         // required for NEEDS_INFO -> ACKNOWLEDGED
    "rejection_reason": "...",        // required for REJECTED
    "appeal_approval_note": "...",    // required for REJECTED -> ACKNOWLEDGED
    "resolution_notes": "...",        // required for RESOLVED
    "resolution_evidence_json": {      // required for RESOLVED
      "photos": ["evidence://repair-1"],
      "completion_summary": "..."
    },
    "reopen_reason": "..."            // required for REOPENED
  }
  ```
- **Action:** Runs 5-step validation, updates request state atomically with audit entry.
  - On `ACKNOWLEDGED -> NEEDS_INFO`: Sets `paused_at = now()`.
  - On `NEEDS_INFO -> ACKNOWLEDGED`: Computes `paused_seconds = now() - paused_at`, adds to `total_paused_seconds`, extends `deadline` and any still-pending `triage_deadline`, and clears `paused_at`.
  - On `ACKNOWLEDGED -> ASSIGNED`: Records `assigned_at` and sets `triaged_at` if this is the first triage outcome.
  - On `ACKNOWLEDGED -> REJECTED`: Sets `triaged_at` if this is the first triage outcome.
  - On `RESOLVED -> CLOSED`: Records `closed_at` for the 7-day reopening rule.
  - On `RESOLVED -> REOPENED` or valid `CLOSED -> REOPENED`: Increments `reopen_count` and `sla_cycle`, resets `sla_started_at` to now, clears pause state, recalculates `deadline`, and resets `escalation_level = 0`.

### 5.3 `POST /api/v1/requests/{id}/category`
- **Roles:** Officer or Supervisor.
- **Payload:** `{"category": "Water Leak", "expected_version": 3}`.
- **Action:** Validates configured category, preserves state, recomputes `deadline`, increments `version`, and writes `CONFIRM_CATEGORY` audit record.

### 5.4 `POST /api/v1/requests/{id}/reassign`
- **Role:** Supervisor.
- **Payload:** `{"assigned_to": "USR-FLD-02", "reason": "...", "expected_version": 4}`.
- **Allowed states:** `ASSIGNED` or `IN_PROGRESS`.
- **Action:** Validates new assignee is a `Field Officer`, requires non-empty reason, preserves current state (`ASSIGNED` or `IN_PROGRESS`), increments `version`, and writes `REASSIGN_REQUEST` audit record.

### 5.5 `POST /api/v1/requests/{id}/appeal`
- **Role:** Citizen owner only.
- **Payload:** `{"appeal_reason": "...", "expected_version": 4}`.
- **Allowed state:** `REJECTED`.
- **Action:** Requires a non-empty reason, stores `appeal_reason` and `appealed_at`, preserves `REJECTED`, increments `version`, and writes `SUBMIT_APPEAL`. The later `REJECTED -> ACKNOWLEDGED` transition is allowed only for a Supervisor and requires both this pending appeal and `appeal_approval_note`.

### 5.6 `POST /api/v1/requests/{id}/progress`
- **Role:** Assigned Field Officer only.
- **Payload:** `{"note": "...", "evidence_json": {...}, "expected_version": 5}`.
- **Allowed state:** `IN_PROGRESS`.
- **Action:** Preserves state, increments `version`, and stores the update in immutable audit details with `action = 'ADD_PROGRESS_UPDATE'`.

### 5.7 `GET /api/v1/requests/{id}`
- Returns request details, SLA countdown, paused status, overdue status, escalation level, and dynamic `permitted_actions`.

### 5.8 `GET /api/v1/requests`
- Filterable query parameters: `state`, `assigned_to`, `citizen_id`, `category`, `is_overdue`.
- **Server-Enforced Read Scopes:**
  - **Citizen:** Can read ONLY requests where `citizen_id == actor.id`.
  - **Officer (Triage):** Can read `SUBMITTED`, `ACKNOWLEDGED`, `NEEDS_INFO`, and unassigned requests across the municipality.
  - **Field Officer:** Can read ONLY requests where `assigned_to == actor.id`.
  - **Supervisor:** Can read all municipal requests.
  - **Audit Inspector:** Cannot read operational request endpoints; access is limited to the audit API.
  Client-supplied query filters can narrow but never expand server-enforced scopes.

### 5.9 `GET /api/v1/audit`
- **Role:** `Supervisor` and `Audit Inspector` (`USR-AUD-01`).
- Returns append-only audit records and cryptographic verification status; strictly read-only.

---

## 6. Implementation Deliverables
1. `src/workflow.py`: Deterministic state machine, 9-state transition table, and 5-step validator.
2. `src/routes/requests.py`: FastAPI route handlers for request creation, transitions, reassignment, category confirmation, citizen appeals, and field progress updates.
3. `src/services/audit_service.py`: Cryptographic hash-chained audit logging service.
4. `tests/test_workflow.py`: Exhaustive pytest suite testing:
   - Complete lifecycle: `SUBMITTED` -> `ACKNOWLEDGED` -> `ASSIGNED` -> `IN_PROGRESS` -> `RESOLVED` -> `CLOSED`.
   - `NEEDS_INFO` path: `ACKNOWLEDGED` -> `NEEDS_INFO` -> `ACKNOWLEDGED` with clock pause/resume.
   - Field Officer assignment: Only `Field Officer` can receive assignment and transition to `IN_PROGRESS`.
   - Reopen path: `RESOLVED` -> `REOPENED` -> `ASSIGNED` -> `IN_PROGRESS`.
   - Rejection and Citizen Appeal: `ACKNOWLEDGED` -> `REJECTED` -> `ACKNOWLEDGED` approved by Supervisor.
   - Closed reopen window: owner succeeds through day 7 and receives HTTP 422 after the window expires.
   - Resolution evidence: `IN_PROGRESS -> RESOLVED` is refused without structured evidence.
   - Progress updates: only the assigned Field Officer can append an update while `IN_PROGRESS`.
   - Unauthorized attempts: Blocked with HTTP 403, ticket unchanged, refusal logged.
   - Skipped transitions (`SUBMITTED -> ASSIGNED`, `REOPENED -> IN_PROGRESS`): Blocked with HTTP 422.
   - Concurrency conflicts: HTTP 409 on version mismatch.
   - Strict read-scope enforcement across Citizen, Officer, Field Officer, and Supervisor.

---

## 7. Confirmed Design Decisions
1. **5 Personas & Scoped Access:** Explicit segregation between Triage Officer (`/officer`) and Field Officer (`/field-officer`).
2. **Deterministic State Machine:** 9 canonical states with strict server-side validation.
3. **Clock Pausing on NEEDS_INFO:** SLA clock is paused while awaiting citizen input and resumed upon submission.
4. **Optimistic Concurrency:** Every write requires `expected_version` to prevent race conditions.
5. **No Force Closure:** Ticket closure remains citizen-confirmed; supervisors can reassign and approve appeals.
6. **Audit Inspector:** A seeded `Audit Inspector` role can read audit and verification endpoints only; it has no operational request permissions.
7. **Appeal Contract:** Citizen appeal submission is a versioned, audited command that preserves `REJECTED`; Supervisor approval is the state transition back to `ACKNOWLEDGED`.
