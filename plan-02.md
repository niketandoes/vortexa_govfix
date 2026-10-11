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
| 6 | `IN_PROGRESS` | `RESOLVED` | `Officer` | Actor must match `assigned_to` and supply non-empty `resolution_notes`. |
| 7 | `RESOLVED` | `CLOSED` | `Citizen`, `Supervisor` | If actor is Citizen, must be the request owner (`citizen_id == actor.id`). |
| 8 | `RESOLVED` | `REOPENED` | `Citizen` | Must be the request owner and supply a non-empty `reopen_reason`; starts a new SLA cycle. |
| 9 | `REOPENED` | `IN_PROGRESS` | `Officer` | Actor must strictly match `assigned_to`. |

> **Crucial Rule:** Escalation is **not** a state. It is a level attribute (`escalation_level = 0, 1, 2`) with audit events, keeping the state machine minimal and unambiguous.

Assignment, reassignment, and category confirmation are audited commands, not artificial state transitions. Reassignment preserves `ASSIGNED` or `IN_PROGRESS`; category confirmation preserves state and deterministically recalculates the current SLA deadline.

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
  1. Compare `expected_version` with the stored request version
  2. Update `requests` state & attributes and increment `version`
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
- **Payload:** `{"title": "...", "text": "...", "reported_category": "Water Leak" (optional)}`
- **Action:** Validates citizen identity, stores the optional value only as a citizen-reported hint, uses `Uncategorised` for the confirmed category and initial 72-hour SLA, inserts the request in `SUBMITTED`, and writes `SUBMIT_COMPLAINT`.

### 5.2 `POST /api/v1/requests/{id}/transition`
- **Role:** Citizen, Officer, or Supervisor (resolved from DB via actor identity)
- **Payload:**
  ```json
  {
    "to_state": "IN_PROGRESS",
    "expected_version": 3,
    "assigned_to": "USR-OFF-01",      // required for ASSIGNED
    "rejection_reason": "...",        // required for REJECTED
    "resolution_notes": "...",        // required for RESOLVED
    "reopen_reason": "..."            // required for REOPENED
  }
  ```
- **Action:** Runs 5-step validation, updates request state atomically with audit entry.

On `RESOLVED -> REOPENED`, the same transaction increments `reopen_count` and `sla_cycle`, sets `sla_started_at` to simulated now, recalculates `deadline`, resets `escalation_level` to 0, and records these values in audit details. The MVP permits repeated reopening; a policy limit can be added later without changing the state model.

### 5.3 `POST /api/v1/requests/{id}/category`

- **Roles:** Officer or Supervisor.
- **Payload:** `{"category": "Water Leak", "expected_version": 3}`.
- **Allowed states:** Any active state.
- **Action:** Validates the configured category, preserves state, recomputes `deadline` from `sla_started_at`, increments `version`, and writes one `CONFIRM_CATEGORY` audit record containing old/new category and deadline values.

### 5.4 `POST /api/v1/requests/{id}/reassign`

- **Role:** Supervisor.
- **Payload:** `{"assigned_to": "USR-OFF-02", "reason": "...", "expected_version": 4}`.
- **Allowed states:** `ASSIGNED` or `IN_PROGRESS`.
- **Action:** Validates the new assignee is an Officer, requires a non-empty reason, preserves state, increments `version`, and writes one `REASSIGN_REQUEST` audit record containing old/new assignee IDs.

### 5.5 `GET /api/v1/requests/{id}`
- Returns request details, current SLA deadline, overdue status, escalation level, and an array of `permitted_actions` calculated dynamically based on caller role.

### 5.6 `GET /api/v1/requests`
- Filterable query parameters: `state`, `assigned_to`, `citizen_id`, `category`, `is_overdue`.
- **Read scopes:** Citizens can read only their requests; Officers can read the `SUBMITTED` intake queue and their assigned requests; Supervisors can read all requests. Client filters may narrow, but never expand, the server-enforced scope.

### 5.7 `GET /api/v1/audit`

- **Role:** Supervisor in the seeded demo identity model.
- Returns audit records and verification status; it never exposes a mutation or trigger-bypass operation.

---

## 6. Implementation Deliverables
1. `src/workflow.py`: Deterministic state machine, transition table, and 5-step validator.
2. `src/routes/requests.py`: FastAPI route handlers for creating requests and executing transitions.
3. `src/services/audit_service.py`: Helper to record accepted and refused transitions.
4. `tests/test_workflow.py`: Exhaustive pytest test suite testing:
   - Happy path: `SUBMITTED` -> `ACKNOWLEDGED` -> `ASSIGNED` -> `IN_PROGRESS` -> `RESOLVED` -> `CLOSED`.
   - Reopen path: `RESOLVED` -> `REOPENED` -> `IN_PROGRESS` -> `RESOLVED`.
   - Skipped state refusal: `ASSIGNED` directly to `RESOLVED` (fails step 2).
   - Role violation refusal: Citizen attempting the valid `IN_PROGRESS` -> `RESOLVED` edge (fails step 3 with HTTP 403).
   - Non-assigned officer refusal: Officer B trying to start Officer A's ticket (fails step 4).
   - Missing fields refusal: Rejecting without a reason (fails step 5).
   - Category confirmation and reassignment command contracts.
   - Reopen SLA-cycle reset and audit details.
   - Stale `expected_version` conflict handling and concurrent writer behavior.
   - Read-scope enforcement and verification that all refusals are stored in `audit`.

---

## 7. Confirmed Design Decisions

1. **Category Confirmation:** Officers and Supervisors may confirm or change a category through the dedicated command.
2. **Reopening:** The MVP permits repeated reopening, requires a reason, and starts a new SLA/escalation cycle each time.
3. **Reassignment:** Supervisors may reassign `ASSIGNED` or `IN_PROGRESS` work; state is preserved and a reason is mandatory.
4. **Optimistic Concurrency:** Every mutation requires `expected_version`; stale requests return HTTP 409.
5. **Demo Identity:** `X-Actor-ID` identifies a seeded user and the server resolves its stored role. This is a demonstration mechanism, not authentication.
