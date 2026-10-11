# GovFix Planning Review and Applied Corrections

Reviewed commit: `fb4f3fa` (`Planning`)

Scope: `plan-overview.md`, `plan-01.md` through `plan-06.md`, and `GovFix Report.md`.

Status: The corrections and defaults recorded below have now been applied to the planning package. This file remains the review rationale and traceability record; each historical **Required correction** paragraph describes the change now reflected in the plans.

## Review approach

This file records contradictions or missing contracts that would block a consistent implementation. Items that the plans already label as clarification questions are kept in a separate decision list rather than being presented as implementation mistakes. Production enhancements that are not necessary for the hackathon MVP are also separated from required corrections.

## Corrections applied before implementation

### 1. Repair the live demo sequence and identify its tickets

**Files:** `plan-06.md` section 5; `plan-01.md` section 5; `plan-02.md` sections 2 and 3.

The demo begins with a newly submitted complaint. The citizen then requests `RESOLVED`, which fails at the transition-table check and returns HTTP 422 under the documented validation order, not the promised HTTP 403. The officer's next attempt is also made while the request is still `SUBMITTED`, so it does not demonstrate the stated `ASSIGNED -> RESOLVED` skip. Finally, the happy path says the supervisor assigns the submitted request, but assignment is only valid after `ACKNOWLEDGED`.

Step 5 is also ambiguous: the complaint from Step 4 has already been closed and therefore cannot become overdue. Plan 01 does seed a separate near-overdue complaint, but Plan 06 does not say to switch to it.

**Required correction:** Name two demo records and their starting states.

- Complaint A: submit -> demonstrate a valid-edge role refusal -> acknowledge -> assign -> demonstrate `ASSIGNED -> RESOLVED` refusal -> start work -> resolve -> close.
- Complaint B: keep active with a 24-hour SLA, then use it for Level 1 and Level 2 escalation.

For the role-refusal demonstration, first place Complaint A in a state where `RESOLVED` is a valid target (for example `IN_PROGRESS`) and attempt it as a citizen. That reaches role validation and correctly returns HTTP 403. Otherwise, retain the original state and change the expected response to HTTP 422.

### 2. Make the multi-page frontend decision consistent everywhere

**Files:** `plan-overview.md` sections 2 and 4; `plan-03.md` sections 2, 4, and 5.

The confirmed decision is a multi-page interface with `/citizen`, `/officer`, `/supervisor`, and `/audit` routes and full page reloads. The overview still describes a single-page dashboard, while Plan 03's deliverables still specify one tabbed `index.html` and client-side role switching.

**Required correction:** Keep the confirmed multi-page design. Specify a shared base template plus route-specific templates, and describe `app.js` as page behavior rather than tab-based role switching.

### 3. Fix acknowledgement visibility in the Officer workspace

**File:** `plan-03.md` section 3.2.

The Officer queue is filtered to `assigned_to == actor.id`, but a `SUBMITTED` complaint has not yet been assigned. An Officer therefore cannot see the complaint on which the UI offers **Acknowledge**.

**Required correction:** Add an Officer intake queue for visible `SUBMITTED` complaints, or make acknowledgement a Supervisor-only action and update the transition matrix. The first option preserves the current role table.

### 4. Define category confirmation as a complete command

**Files:** `plan-02.md` sections 5 and 7; `plan-03.md` sections 3.2 and 5; `plan-05.md` section 3.2; `GovFix Report.md` sections 4.4 and 5.1.

Category confirmation changes the deadline and is exposed as a UI action, but no endpoint, payload, allowed states, or transaction contract is defined. The report and Plan 05 imply that both Officers and Supervisors may confirm it, while Plan 02 leaves the role unresolved.

**Required correction:** Confirm the role rule and document a dedicated audited command, for example `POST /api/v1/requests/{id}/category`, including category validation, allowed states, deadline recomputation, and atomic audit recording.

### 5. Add structured audit details for non-transition actions

**Files:** `plan-01.md` section 3.4; `plan-05.md` section 3.2; `GovFix Report.md` sections 5 and 6.

The audit schema can record states and a free-text reason, but Plan 05 requires `old_deadline` and `new_deadline`. Assignment, reassignment, category confirmation, escalation recipient, and reopen reason also need action-specific data. These fields do not belong reliably in an unstructured reason string.

**Required correction:** Add `details_json TEXT NOT NULL DEFAULT '{}'`, define a small JSON shape for each action, include its canonical representation in the entry hash, and keep `reason` for a human-readable explanation.

### 6. Align resolution notes across API and UI

**Files:** `plan-02.md` transition 6 and section 5.2; `plan-03.md` section 3.2.

Plan 02 makes resolution notes optional, while the Officer UI requires them.

**Required correction:** Make non-empty resolution notes mandatory for `IN_PROGRESS -> RESOLVED` in both the transition contract and UI. This is the stronger accountability rule and matches the existing UI intent.

### 7. Define reopen reason and reopen SLA behavior

**Files:** `plan-01.md` sections 3.2 and 3.4; `plan-02.md` transition 8; `plan-03.md` section 3.1; `plan-05.md` sections 3 and 4.

The UI collects a reopen reason, but the transition payload does not require or store it. In addition, escalation is calculated from the original `created_at`, and escalation levels remain elevated until resolution. A reopened request can therefore immediately inherit an expired deadline and an old escalation level.

**Required correction:** Require a non-empty `reopen_reason` and store it in audit details. Define a new SLA cycle on reopen using fields such as `sla_started_at` and `sla_cycle`; reset `escalation_level` to 0 atomically and calculate the new deadline from the new cycle start. Audit the reset and deadline.

### 8. Resolve reassignment and concurrency before claiming support

**Files:** `GovFix Report.md` sections 4.4 and 9; `plan-02.md` section 7.

The report says Supervisors can reassign work, but Plan 02 correctly lists reassignment as an unanswered question. The same section leaves optimistic concurrency unresolved even though simultaneous actions could otherwise overwrite one another.

**Required correction:** Until the choices are confirmed, remove reassignment from the report's committed behavior. Before implementation, decide whether reassignment is allowed and whether it preserves the current state. Add a request `version` check to mutating commands so the accepted behavior is deterministic.

### 9. Align the identity model and read authorization

**Files:** `plan-01.md` section 7; `plan-03.md` section 2; `plan-02.md` sections 5.3 and 5.4; `GovFix Report.md` section 12.

Plan 01 uses `X-Actor-ID` or query headers, Plan 03 exposes a demo identity selector through role routes, and the report claims seeded users have tokens even though no token field or token-validation design exists. The request-list endpoints also define filters without defining who may read which records.

**Required correction:** State that the hackathon build uses seeded demo identities and does not provide production authentication. Choose one actor transport for API calls and remove the unsupported token claim. Define read scopes: Citizens see their own requests, Officers see permitted intake plus their assignments, Supervisors see all requests, and audit access is restricted to the demo Supervisor/Admin surface.

### 10. Represent the Level 2 recipient consistently

**Files:** `plan-01.md` sections 3.1 and 5; `plan-05.md` section 4.2; `GovFix Report.md` section 5.3.

Level 2 escalation names a Department Head, but the role model contains only Citizen, Officer, and Supervisor. The seed Supervisor is described as Municipal Head, so the MVP can avoid adding another role.

**Required correction:** For the MVP, explicitly designate `USR-SUP-01` as both Supervisor and the Level 2 municipal/department-head recipient, while keeping authorization role `Supervisor`. If separate recipients are required later, add a notification-recipient model rather than changing workflow roles casually.

### 11. Add the persisted simulated-clock schema and precise semantics

**Files:** `plan-01.md` section 3; `plan-05.md` sections 2 and 5.

Plan 05 promises SQLite persistence, but Plan 01 defines no clock/configuration table. `base_wall_time + offset` is also unclear when the clock is frozen. The test says to advance by 25 hours and then by 37 hours; if both advances are relative, the total is 62 hours rather than the intended 1.5x point.

**Required correction:** Add a singleton `clock_state` table and define `now()`, advance, freeze, and reset behavior exactly. Update the test to say either "advance 11 additional hours, reaching 36 total" or "reset and advance 37 hours."

### 12. Narrow the audit-tamper claim to what the design proves

**Files:** `GovFix Report.md` sections 6 and 12; `plan-06.md` sections 2 and 3.

Triggers prevent normal updates and deletes. The hash chain detects modified content and broken internal links. However, an internally stored chain alone cannot prove that the final row was deleted, because the shortened chain still verifies without a trusted external record of the expected head or count. The report's limitations section already acknowledges the need for external anchoring.

**Required correction:** Say that verification detects content changes and internal chain breaks. Do not claim detection of every possible deletion unless the chain head and record count are anchored outside the database.

### 13. Use a safe, consistent tamper demo

**Files:** `plan-03.md` section 3.3; `plan-06.md` sections 3.2 and 7.

The UI promises a **Simulate Tamper** button, while Plan 06 leaves an endpoint versus script undecided. A normal update cannot work because the triggers reject it; an application endpoint capable of bypassing the audit protections would weaken the same security story the demo is meant to prove.

**Required correction:** Use `scripts/tamper_demo.py` only against a disposable copy of the demo database. The UI can verify and display the resulting broken chain, but the normal application should not expose a trigger-bypass endpoint.

### 14. Correct the AI dependency and isolation wording

**Files:** `plan-01.md` section 3.3; `plan-04.md` sections 1, 2, and 4.2; `GovFix Report.md` section 7.

The local fallback is offline and does not need an API key, but it is not zero-dependency because it uses scikit-learn. Also, SQLite does not provide table-level write permissions to code sharing a normal connection. An AST import test supports module separation, but it is not by itself proof of database permissions.

**Required correction:** Call it an "offline local fallback with bundled dependencies." Give the AI path a narrow suggestion repository/capability rather than a general database connection, and describe the boundary as application-level capability isolation backed by tests.

### 15. Fix portable links and complete the implementation deliverables

**Files:** `plan-overview.md` section 2; all implementation plans; `README.md`.

The plan index uses absolute links from another contributor's computer, so the links do not work in this checkout. The phase deliverables also omit the application entry point, dependency declaration, configuration example, and runnable setup instructions.

**Required correction:** Replace the links with relative paths such as `[Plan 01](./plan-01.md)`. Add `src/main.py`, a pinned dependency file or `pyproject.toml`, `.env.example`, database bootstrap/migration instructions, and README commands for setup, tests, and demo startup.

## Decisions adopted in the revised plans

The earlier plans identified these as open decisions rather than implementation errors. The revised package now uses the following explicit MVP defaults:

1. Officers and Supervisors may confirm categories through a dedicated command.
2. Reopening is unlimited in the MVP, requires a reason, and starts a new SLA cycle.
3. Supervisors may reassign `ASSIGNED` or `IN_PROGRESS` work while preserving state and recording a reason.
4. Every request mutation requires `expected_version`; stale writes return HTTP 409.
5. External audit anchoring is outside MVP scope, and the tail-deletion limitation is stated explicitly.
6. The interactive demo runner uses named records and validates each starting state and expected HTTP response.

## Optional hardening after the MVP contract is aligned

These improvements were reviewed as hardening. The revised plans adopt the first three and the relevant schema checks; the remaining test depth can be implemented after the core happy path:

- Serialize audit-chain appends and request mutations in one SQLite transaction (for example, `BEGIN IMMEDIATE`).
- Make escalation updates conditional and add a uniqueness key per request, level, and SLA cycle.
- Store canonical UTC timestamps and distinguish recorded wall time from simulated business time.
- Add stricter SQL checks for non-empty text and confidence range.
- Separate a citizen's reported category from the authorized confirmed category if that distinction is desired.
- Add race-condition and failure-injection tests after the core happy path is working.

## Recommended correction order

1. Freeze identity, read scopes, transition rules, and the non-transition commands.
2. Update the schema for audit details, request versioning, SLA cycles, and clock state.
3. Align API contracts and multi-page UI actions with those rules.
4. Rewrite the demo with named records and valid starting states.
5. Align security and AI claims with the guarantees actually implemented.
6. Add the missing project/bootstrap deliverables and then begin implementation.

## Acceptance checks for the revised plans

- Every UI action maps to a documented command or endpoint.
- Every command defines roles, allowed states, required data, audit data, and transaction behavior.
- Every demo action begins from a named valid state and produces the documented HTTP status.
- Escalation uses an active complaint, never the complaint already closed in the happy path.
- Reopening starts an explicitly defined SLA cycle.
- Identity and authorization descriptions are consistent across schema, API, UI, and report.
- Audit and AI claims do not exceed the mechanisms actually designed.
