# Plan 02: Core Workflow Engine, Role Checks & Refusal Logging

## 1. Executive Summary & Objective
Plan 02 implements the deterministic workflow engine for GovFix. The engine enforces the municipal grievance state machine, validates every transition request against a strict 5-step validation pipeline, guarantees role authorization on the server, logs every attempt (accepted or refused) into the audit table, and commits state mutations atomically with audit entries.

---

## 2. Transition Rules Matrix & Invariants

The transition table is defined as immutable code configuration. Any state change outside this table is categorically refused.

| # | From State | To State | Permitted Roles | Invariants & Extra Conditions |
| :- | :--- | :--- | :--- | :--- |
| 1 | `(new)` | `SUBMITTED` | `Citizen` | Title and complaint text must not be empty. |
| 2 | `SUBMITTED` | `ACKNOWLEDGED` | `Officer`, `Supervisor` | None. Indicates formal receipt by the municipal office. |
| 3 | `ACKNOWLEDGED` | `ASSIGNED` | `Supervisor` | Must supply `assigned_to` officer ID; assignee role must be `Officer`. |
| 4 | `ACKNOWLEDGED` | `REJECTED` | `Supervisor` | Must supply non-empty `rejection_reason`. |
| 5 | `ASSIGNED` | `IN_PROGRESS` | `Officer` | Actor must strictly match `assigned_to`. |
| 6 | `IN_PROGRESS` | `RESOLVED` | `Officer` | Actor must strictly match `assigned_to`. May supply `resolution_notes`. |
| 7 | `RESOLVED` | `CLOSED` | `Citizen`, `Supervisor` | If actor is Citizen, must be the request owner (`citizen_id == actor.id`). |
| 8 | `RESOLVED` | `REOPENED` | `Citizen` | Must be the request owner (`citizen_id == actor.id`). |
| 9 | `REOPENED` | `IN_PROGRESS` | `Officer` | Actor must strictly match `assigned_to`. |

> **Crucial Rule:** Escalation is **not** a state. It is a level attribute (`escalation_level = 0, 1, 2`) with audit events, keeping the state machine minimal and unambiguous.

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
│ 4. Ownership & Assignment     │──(Fails)──► Log Refusal: "Actor is not assigned officer/owner" ──► Return 403
└───────────────────────────────┘
          │ (Passes)
          ▼
┌───────────────────────────────┐
│ 5. Required Payload Fields    │──(Fails)──► Log Refusal: "Missing required parameter" ──► Return 422
└───────────────────────────────┘
          │ (Passes)
          ▼
  Atomic DB Transaction:
  1. Update `requests` state & attributes
  2. Insert `audit` entry (outcome='ACCEPTED')
```

---

## 4. Refusal Logging & Structured Error Response

### 4.1 Audit Refusal Entry
When a transition fails, an entry is immediately committed to the `audit` table with:
- `outcome = 'REFUSED'`
- `action = 'TRANSITION_ATTEMPT'`
- `reason = <explicit human-readable diagnostic message>`
- `from_state = request.state`
- `to_state = target_state`

### 4.2 API Failure Contract (HTTP 403 / 422)
```json
{
  "success": false,
  "error": {
    "code": "INVALID_TRANSITION_OR_PERMISSION",
    "message": "Cannot transition from ASSIGNED to RESOLVED. Permitted next state is IN_PROGRESS.",
    "current_state": "ASSIGNED",
    "requested_state": "RESOLVED",
    "permitted_next_states": ["IN_PROGRESS"],
    "actor_role": "Officer",
    "audit_logged": true
  }
}
```

---

## 5. API Endpoints Specification

### 5.1 `POST /api/v1/requests`
- **Role:** Citizen
- **Payload:** `{"title": "...", "text": "...", "category": "Water Leak" (optional)}`
- **Action:** Validates citizen identity, calculates initial deadline, inserts request in `SUBMITTED` state, and writes audit record `SUBMIT_COMPLAINT`.

### 5.2 `POST /api/v1/requests/{id}/transition`
- **Role:** Citizen, Officer, or Supervisor (resolved from DB via actor identity)
- **Payload:**
  ```json
  {
    "to_state": "IN_PROGRESS",
    "assigned_to": "USR-OFF-01",      // required for ASSIGNED
    "rejection_reason": "...",        // required for REJECTED
    "resolution_notes": "..."         // optional for RESOLVED
  }
  ```
- **Action:** Runs 5-step validation, updates request state atomically with audit entry.

### 5.3 `GET /api/v1/requests/{id}`
- Returns request details, current SLA deadline, overdue status, escalation level, and an array of `permitted_actions` calculated dynamically based on caller role.

### 5.4 `GET /api/v1/requests`
- Filterable query parameters: `state`, `assigned_to`, `citizen_id`, `category`, `is_overdue`.

---

## 6. Implementation Deliverables
1. `src/workflow.py`: Deterministic state machine, transition table, and 5-step validator.
2. `src/routes/requests.py`: FastAPI route handlers for creating requests and executing transitions.
3. `src/services/audit_service.py`: Helper to record accepted and refused transitions.
4. `tests/test_workflow.py`: Exhaustive pytest test suite testing:
   - Happy path: `SUBMITTED` -> `ACKNOWLEDGED` -> `ASSIGNED` -> `IN_PROGRESS` -> `RESOLVED` -> `CLOSED`.
   - Reopen path: `RESOLVED` -> `REOPENED` -> `IN_PROGRESS` -> `RESOLVED`.
   - Skipped state refusal: `ASSIGNED` directly to `RESOLVED` (fails step 2).
   - Role violation refusal: Citizen attempting `RESOLVED` (fails step 3).
   - Non-assigned officer refusal: Officer B trying to start Officer A's ticket (fails step 4).
   - Missing fields refusal: Rejecting without a reason (fails step 5).
   - Verifying all refusals are stored in `audit`.

---

## 7. Detailed Clarification Questions & Design Decisions
To prevent assumptions and hallucinations, please clarify the following points for Plan 02:
1. **Category Confirmation Role:** Who is allowed to confirm or change the category of a ticket: Supervisor only, or both Officer and Supervisor?
2. **Reopening Limits:** Can a citizen reopen a complaint indefinitely, or should there be a maximum reopen count (e.g. max 2 times) or a time window (e.g. within 7 days of resolution)?
3. **Reassignment Rules:** If a ticket is already `ASSIGNED` or `IN_PROGRESS`, can a Supervisor reassign it to a different Officer? (If so, does it stay in its current state or revert to `ASSIGNED`?)
4. **Optimistic Concurrency / Race Conditions:** Should requests have an `updated_at` or `version` integer check to prevent conflicting concurrent transitions?
