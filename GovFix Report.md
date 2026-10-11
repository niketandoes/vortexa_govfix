# GovFix — Municipal Grievance Workflow Engine: Solution Report

VORTEXA 3.0 · Problem Statement G-02 (Government Systems & GovTech) · Track: Municipal Grievance Resolution

## 1. Summary

GovFix is a workflow engine that takes a municipal complaint from submission to closure under explicit, checkable rules. Every complaint moves through a fixed set of states. Each move is allowed only for specific roles, is enforced on the server, and is written to a tamper-evident audit history, whether it is accepted or refused. Every complaint carries a deadline derived from its category. Requests that pass their deadline are detected and escalated automatically, and the escalation is visible on the record. AI assists by categorising complaints, extracting key details, and drafting replies. These outputs are shown as suggestions only. They cannot change a request's status, assignment, or deadline.

The design follows the pattern that runs through the whole VORTEXA booklet: AI suggests, deterministic rules decide, humans confirm, and everything is logged.

## 2. Problem Addressed

Public institutions handle a constant stream of service requests. When these run on email and spreadsheets, requests stall, deadlines pass unnoticed, and nobody can say who changed a status or when. Citizens hear nothing for weeks, officers cannot see which cases are overdue, and the institution cannot demonstrate fair treatment.

The G-02 statement requires one complete workflow implemented end to end, not a generic dashboard across all five tracks. GovFix implements the municipal grievance track.

## 3. Requirements and How the Solution Meets Them

| Requirement from the problem statement | How GovFix meets it |
| --- | --- |
| Define states and allowed transitions; refuse skipped or invalid moves with a clear reason | A single transition table defines every permitted move. Any other move is refused with a reason naming the current state and the allowed next states |
| Restrict status changes to permitted roles, enforced in the system, with refused attempts recorded | The role is read from the user record on the server, never trusted from the request. Refusals return an authorization error and are written to the audit log |
| Track-appropriate deadline, overdue identification, visible escalation | Deadlines come from a per-category SLA table. An escalation job raises the level of overdue requests and logs each escalation |
| Audit history of changes, refusals, assignments, escalations (who, what, when) | Append-only, hash-chained audit table with a chain-verification function |
| AI may categorise, extract, and draft; it must not change status, assignment, or deadline | AI output is stored in a separate suggestions table. The AI module has no access path to the workflow service |

Common booklet rules are also met: a working live MVP, meaningful AI use, explicit rules making consequential decisions, fictional data only, and a local fallback so the core demonstration does not depend on any external AI service.

## 4. Workflow Design

### 4.1 States

SUBMITTED, ACKNOWLEDGED, ASSIGNED, IN\_PROGRESS, RESOLVED, CLOSED, REJECTED, and REOPENED.

### 4.2 Transition table

The table is stored as data and is the single source of truth for what is allowed.

| From | To | Permitted role | Extra condition |
| --- | --- | --- | --- |
| (new) | SUBMITTED | Citizen | Complaint text required |
| SUBMITTED | ACKNOWLEDGED | Officer, Supervisor | None |
| ACKNOWLEDGED | ASSIGNED | Supervisor | Assignee must be an officer |
| ACKNOWLEDGED | REJECTED | Supervisor | Reason required |
| ASSIGNED | IN\_PROGRESS | Officer | Must be the assigned officer |
| IN\_PROGRESS | RESOLVED | Officer | Must be the assigned officer |
| RESOLVED | CLOSED | Citizen, Supervisor | Citizen must be the request owner |
| RESOLVED | REOPENED | Citizen | Citizen must be the request owner |
| REOPENED | IN\_PROGRESS | Officer | Must be the assigned officer |

Escalation is not a state. It is a level field on the request combined with an audit event, which keeps the state machine small and unambiguous.

### 4.3 Validation order

Every attempted move is checked in the same order, and every failure is recorded:

1. The actor must exist.
2. The move must appear in the transition table.
3. The actor's stored role must be permitted for that move.
4. Ownership or assignment conditions must hold.
5. Required fields (assignee, rejection reason) must be present.

Only after all checks pass is the state updated, and the state change and its audit entry are committed together.

### 4.4 Roles

- **Citizen:** submits requests, tracks them, confirms closure, or reopens a resolved request they own.
- **Officer:** acknowledges requests, works the ones assigned to them, and marks them resolved.
- **Supervisor:** assigns and reassigns work, rejects invalid requests, confirms categories, force-closes, and receives escalations.

## 5. Deadlines and Escalation

### 5.1 Deadline rule

Each category has a service-level time. Illustrative values: water leak 24 hours, garbage collection 48 hours, streetlight 72 hours, pothole 7 days, with a default for uncategorised requests. A request's deadline is its creation time plus the SLA of its category. The deadline is always computed by this rule and never by AI.

When an officer or supervisor confirms a category, the deadline is recomputed by the same rule and the change is recorded in the audit log with the old and new values.

### 5.2 Overdue detection

A request is overdue when the current time is past its deadline and it is still in an active state (SUBMITTED, ACKNOWLEDGED, ASSIGNED, IN\_PROGRESS, REOPENED). Requests in RESOLVED, CLOSED, or REJECTED are not overdue.

### 5.3 Escalation ladder

| Elapsed time against SLA | Escalation level | Recipient |
| --- | --- | --- |
| 1.0× | Level 1 | Supervisor |
| 1.5× | Level 2 | Department head |

The escalation job is idempotent: a request is raised only when its required level exceeds its current level. Each escalation is written to the audit log as a system action and shown on the request.

### 5.4 Simulated clock

All time-dependent logic reads from a clock object rather than the system clock. An administrative control advances the simulated time so that overdue detection and escalation can be demonstrated within seconds and reproduced exactly.

## 6. Audit History

The audit table records accepted changes, refused attempts, assignments, category confirmations, and escalations.

**Fields per entry:** timestamp, actor, actor role, action, request identifier, from-state, to-state, outcome (accepted or refused), reason, previous hash, and entry hash.

**Integrity properties:**

- The table is append-only. Database triggers abort any UPDATE or DELETE.
- Each entry's hash is the SHA-256 of the previous entry's hash combined with the entry's canonical content, forming a chain.
- A verification function recomputes the chain from the start. Any edited, removed, or reordered entry breaks verification and identifies the first bad entry.

Because refusals are recorded alongside successes, the history shows attempted misuse as well as normal activity, which supports the institution's ability to demonstrate fair treatment.

## 7. AI Layer

### 7.1 Functions

- **Categorisation:** proposes a category with a confidence value.
- **Extraction:** pulls out location and urgency cues from the complaint text.
- **Reply drafting:** produces a suggested response for the officer.
- **Optional extension:** similarity-based detection of duplicate complaints about the same issue.

### 7.2 Boundary

- Outputs are written only to an AI suggestions table, each tagged with the path that produced it (LLM or local fallback).
- The AI module has no import path to the workflow service. An automated test asserts this, so the boundary holds structurally and not only by convention.
- A suggestion has effect only when a person acts on it. For example, confirming a suggested category is an officer action that is audited and then triggers the deadline rule.

### 7.3 Local fallback

The core demonstration does not depend on any external service. The fallback uses TF-IDF features with logistic regression, trained on roughly 100 synthetic complaints written for the project, together with pattern matching for locations. An external model such as Gemini can sit behind the same interface as an optional upgrade. If it is unavailable or returns unusable output, the fallback produces the suggestion and the suggestion records which path was used.

## 8. Data Model

| Table | Purpose | Key fields |
| --- | --- | --- |
| users | Identity and role, the server-side source of truth for permissions | id, name, role |
| requests | The complaint and its current workflow data | id, citizen\_id, text, category, state, assigned\_to, created\_at, deadline, escalation\_level |
| ai\_suggestions | Advisory output, isolated from workflow data | id, request\_id, kind, payload, source, created\_at |
| audit | Append-only, hash-chained history | id, timestamp, actor, role, action, request\_id, from\_state, to\_state, outcome, reason, previous hash, hash |

## 9. System Architecture

- **Workflow service:** holds the transition table, role and ownership checks, deadline rule, and escalation job. It is the only component that can change a request's state, assignment, or deadline.
- **Audit service:** writes hash-chained entries inside the same transaction as the change it records, and exposes chain verification.
- **AI service:** reads complaint text and writes only to the suggestions table.
- **API layer:** authenticates the caller, resolves the role from the database, and passes requests to the workflow service. Refusals map to authorization or validation errors.
- **User interface:** a citizen view for submission and tracking, an officer queue, and a supervisor dashboard showing overdue and escalated requests, the audit viewer, and chain verification.
- **Technology:** Python with FastAPI and SQLite, a lightweight web interface, and scikit-learn for the local classifier.

## 10. Demonstrated Behaviours

The live system shows the following, each reproducible with the seeded fictional data:

1. A citizen submits a complaint and the AI suggestion appears beside it.
2. A direct API call by the citizen to mark the request RESOLVED is refused with an authorization error, and the refusal appears in the audit log.
3. An officer attempting ASSIGNED to RESOLVED is refused because IN\_PROGRESS was skipped, with the reason shown.
4. A supervisor assigns the request, the officer works it, and the citizen closes it, with every step audited.
5. Advancing the simulated clock makes a request overdue and raises its escalation level, visible on the record.
6. The audit trail is displayed and chain verification succeeds. After a row is altered directly in the database, verification fails and identifies the entry.
7. With the AI component disabled, all workflow behaviour is unchanged and the suggestion panel states that the fallback was used.

## 11. Implementation Phases

| Phase | Content |
| --- | --- |
| Foundation | Schema, transition table, SLA configuration, seeded users and complaints |
| Core engine | Workflow service, role checks, audit log, API, and automated tests for invalid moves, wrong roles, and logged refusals |
| Interface | Citizen, officer, and supervisor views |
| AI | Categorisation, extraction, and reply drafting with the local fallback |
| Time and escalation | Simulated clock, overdue view, escalation job |
| Hardening | Audit viewer, chain verification, adversarial tests, clear error messages, demo data |

## 12. Limitations

- The local classifier is trained on a small synthetic set and is suitable for demonstration, not production accuracy.
- Authentication uses seeded users with tokens. Phone and OTP login are not part of this solution.
- SLA values and escalation recipients are illustrative configuration and would be set by the institution.
- The hash chain makes tampering detectable; it does not prevent a database administrator from deleting the whole database. Anchoring the latest hash outside the system would be required for stronger guarantees.
