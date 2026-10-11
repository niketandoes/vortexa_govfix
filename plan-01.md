# Plan 01: Foundation & Data Architecture

## 1. Executive Summary & Objective
Plan 01 lays the bedrock for **GovFix (Municipal Grievance Workflow Engine)**. It establishes the relational database schema, database-level security triggers, explicit state & role definitions, SLA duration mappings, and deterministic fictional seed data to support reproducible testing and demonstration.

---

## 2. Architecture & Tech Stack Decisions
- **Database Engine:** SQLite (stored locally as `govfix.db` with WAL mode enabled for concurrent reads/writes).
- **Backend Runtime:** Python 3.11+ / FastAPI.
- **Data Access:** Lightweight repository pattern using `sqlite3` or `aiosqlite` with strict connection context managers to guarantee transactional commits.
- **Append-Only Enforcement:** SQLite database triggers on the `audit` table that unconditionally raise an abort error on any `UPDATE` or `DELETE` attempt.

---

## 3. Detailed Data Models & Schema

### 3.1 Table: `users`
Represents verified municipal identities. Serves as the single server-side source of truth for role authorization.
```sql
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,               -- e.g. 'USR-CIT-01', 'USR-OFF-01', 'USR-FLD-01', 'USR-SUP-01', 'USR-AUD-01'
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('Citizen', 'Officer', 'Field Officer', 'Supervisor', 'Audit Inspector')),
    email TEXT UNIQUE NOT NULL,
    department TEXT,
    created_at TEXT NOT NULL             -- canonical UTC ISO-8601
);
```

### 3.2 Table: `requests`
Represents the municipal complaint throughout its lifecycle across the 9 canonical states.
```sql
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY,               -- e.g. 'REQ-2026-0001'
    citizen_id TEXT NOT NULL,
    title TEXT NOT NULL CHECK(length(trim(title)) > 0),
    text TEXT NOT NULL CHECK(length(trim(text)) > 0),
    location TEXT NOT NULL CHECK(length(trim(location)) > 0),
    reported_category TEXT CHECK(reported_category IS NULL OR reported_category IN (
        'Water Leak', 'Garbage Collection', 'Streetlight', 'Pothole'
    )),                                  -- optional citizen-provided hint
    category TEXT NOT NULL DEFAULT 'Uncategorised' CHECK(category IN (
        'Water Leak', 'Garbage Collection', 'Streetlight', 'Pothole', 'Uncategorised'
    )),                                  -- authorized confirmed category
    state TEXT NOT NULL CHECK(state IN (
        'SUBMITTED', 'ACKNOWLEDGED', 'NEEDS_INFO', 'ASSIGNED', 
        'IN_PROGRESS', 'RESOLVED', 'CLOSED', 
        'REJECTED', 'REOPENED'
    )),
    assigned_to TEXT,                  -- foreign key to users(id) when role = 'Field Officer'
    assigned_at TEXT,
    rejection_reason TEXT,
    appeal_reason TEXT,
    appealed_at TEXT,
    appeal_approval_note TEXT,
    resolution_notes TEXT,
    resolution_evidence_json TEXT CHECK(
        resolution_evidence_json IS NULL OR json_valid(resolution_evidence_json)
    ),                                  -- canonical JSON metadata/paths for completion evidence
    reopen_reason TEXT,
    info_request_note TEXT,            -- note from officer requesting missing info
    additional_info TEXT,              -- citizen-supplied additional info
    escalation_level INTEGER NOT NULL DEFAULT 0 CHECK(escalation_level IN (0, 1, 2)),
    sla_started_at TEXT NOT NULL,        -- start of current SLA cycle
    sla_cycle INTEGER NOT NULL DEFAULT 1 CHECK(sla_cycle >= 1),
    reopen_count INTEGER NOT NULL DEFAULT 0 CHECK(reopen_count >= 0),
    paused_at TEXT,                    -- UTC timestamp when SLA clock was paused (NEEDS_INFO)
    total_paused_seconds INTEGER NOT NULL DEFAULT 0 CHECK(total_paused_seconds >= 0),
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    created_at TEXT NOT NULL,            -- simulated UTC ISO-8601 timestamp
    updated_at TEXT NOT NULL,
    triage_deadline TEXT NOT NULL,        -- created_at + 2h + applicable pause extensions
    triaged_at TEXT,                     -- set on first ASSIGNED or REJECTED outcome
    closed_at TEXT,                      -- required to enforce the 7-day closed-reopen window
    deadline TEXT NOT NULL,              -- sla_started_at + SLA(category) + total_paused_seconds
    FOREIGN KEY(citizen_id) REFERENCES users(id),
    FOREIGN KEY(assigned_to) REFERENCES users(id)
);
```

### 3.3 Table: `ai_suggestions`
Isolated advisory storage. AI engines receive only a narrow suggestion-insert capability and never receive the request repository or a general database connection.
```sql
CREATE TABLE IF NOT EXISTS ai_suggestions (
    id TEXT PRIMARY KEY,               -- e.g. 'SUG-0001'
    request_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('categorisation', 'extraction', 'reply_draft', 'duplicate_detection')),
    payload TEXT NOT NULL,             -- JSON string of structured suggestion
    confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0),
    source TEXT NOT NULL CHECK(source IN ('gemini_llm', 'local_fallback')),
    created_at TEXT NOT NULL,
    FOREIGN KEY(request_id) REFERENCES requests(id) ON DELETE CASCADE
);
```

### 3.4 Table: `audit`
Tamper-evident, hash-chained audit trail recording every state transition attempt (both accepted and refused), assignments, category confirmations, and system escalations across all 5 personas.
```sql
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,             -- recorded wall-clock UTC time
    simulated_at TEXT,                   -- business time when relevant
    actor TEXT NOT NULL,               -- user ID or 'SYSTEM'
    actor_role TEXT NOT NULL CHECK(actor_role IN ('Citizen', 'Officer', 'Field Officer', 'Supervisor', 'Audit Inspector', 'System')),
    action TEXT NOT NULL,              -- e.g. 'SUBMIT_COMPLAINT', 'TRANSITION', 'CONFIRM_CATEGORY', 'REASSIGN_REQUEST', 'ESCALATE'
    request_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    outcome TEXT NOT NULL CHECK(outcome IN ('ACCEPTED', 'REFUSED')),
    reason TEXT,                         -- human-readable explanation
    details_json TEXT NOT NULL DEFAULT '{}', -- canonical action-specific JSON
    dedupe_key TEXT UNIQUE,              -- optional idempotency key, used by escalation
    previous_hash TEXT NOT NULL,       -- SHA-256 of previous audit record
    hash TEXT NOT NULL                 -- SHA-256(previous_hash + canonical_content)
);
```

`details_json` stores structured values such as assignee changes, category changes, old/new deadlines, reopen reason, SLA cycle, and escalation recipient. `dedupe_key` is null for ordinary events and uses `escalation:{request_id}:{sla_cycle}:{target_level}` for escalation. Both fields are included in the audit hash.

### 3.5 Table: `clock_state`

Persists the singleton simulated-clock configuration used by all time-dependent services.

```sql
CREATE TABLE IF NOT EXISTS clock_state (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    offset_seconds INTEGER NOT NULL DEFAULT 0,
    is_frozen INTEGER NOT NULL DEFAULT 0 CHECK(is_frozen IN (0, 1)),
    frozen_at TEXT,
    updated_at TEXT NOT NULL
);
```

Audit ordering is defined by the monotonically increasing audit `id`; resetting simulated time must never alter or reorder audit records.

### 3.6 Database Triggers for Append-Only Integrity
```sql
CREATE TRIGGER IF NOT EXISTS abort_audit_update
BEFORE UPDATE ON audit
BEGIN
    SELECT RAISE(ABORT, 'Audit log is append-only: updates forbidden');
END;

CREATE TRIGGER IF NOT EXISTS abort_audit_delete
BEFORE DELETE ON audit
BEGIN
    SELECT RAISE(ABORT, 'Audit log is append-only: deletions forbidden');
END;
```

---

## 4. SLA & Transition Configuration Data

### 4.1 Category SLA Mapping
Aligned with the municipal service guidelines:
| Category | SLA Duration (Hours) | Description |
| :--- | :--- | :--- |
| `Water Leak` | 24 | Leaking public pipelines and water wastage |
| `Garbage Collection` | 24 | Overflowing bins, missed collection, waste accumulation |
| `Streetlight` | 48 | Non-functional public streetlights |
| `Pothole` | 72 | Damaged road surfaces requiring municipal repair |
| `Uncategorised` | 72 | Default SLA before category is confirmed by officer |

Additionally, an initial **2-hour triage & assignment target** applies to all newly submitted complaints. It is stored as `triage_deadline = created_at + 2 hours`, is satisfied when the request first reaches `ASSIGNED` or `REJECTED`, and is tracked separately from the category resolution deadline.

---

## 5. Seed Data Specification
Pre-seeded identities to test all **five distinct personas** immediately without manual registration:
- **Persona 1 — Citizens:**
  - `USR-CIT-01`: "Ramesh Patel" (`ramesh@citizen.local`)
  - `USR-CIT-02`: "Priya Sharma" (`priya@citizen.local`)
- **Persona 2 — Officer (Triage & Assignment):**
  - `USR-OFF-01`: "Officer Anil Kumar" (`anil.kumar@municipality.gov`) — Triage Desk / Public Works
- **Persona 3 — Field Officers (On-Ground Execution):**
  - `USR-FLD-01`: "Field Officer Vikram Singh" (`vikram.singh@municipality.gov`) — Roads & Public Works
  - `USR-FLD-02`: "Field Officer Sunita Rao" (`sunita.rao@municipality.gov`) — Sanitation & Streetlights
- **Persona 4 — Supervisor (Command Center):**
  - `USR-SUP-01`: "Supervisor Rajesh Gupta" (`rajesh.gupta@municipality.gov`) — Municipal Head
- **Persona 5 — Audit Inspector Console (`/audit`):**
  - `USR-AUD-01`: "Audit Inspector Meera Joshi" (`meera.joshi@audit.gov`) — read-only audit verification identity.

### Pre-seeded Complaints:
1. `REQ-2026-0001` (`SUBMITTED`): New pothole complaint awaiting Triage Officer review in `/officer` intake queue.
2. `REQ-2026-0002` (`NEEDS_INFO`): Water leak complaint where officer requested landmark details; clock paused.
3. `REQ-2026-0003` (`ASSIGNED`): Garbage overflow assigned to `USR-FLD-01`, ready for `ASSIGNED → IN_PROGRESS`.
4. `REQ-2026-0004` (`IN_PROGRESS`): Streetlight failure assigned to `USR-FLD-01`, ready for resolution notes.
5. `REQ-2026-0005` (`RESOLVED`): Pothole repair completed, awaiting citizen confirmation in `/citizen`.
6. `REQ-2026-0006` (`IN_PROGRESS`, Escalation Demo): 24-hour Water Leak seeded at 0h elapsed at the reset reference time, allowing the demo to advance to 18h (At Risk), 25h (Level 1), and 36h (Level 2) deterministically.

---

## 6. Implementation Deliverables
1. `src/database.py`: SQLite connection factory with WAL mode and foreign key enforcement.
2. `src/models.py`: Pydantic schemas for data validation, optimistic versions, and API input/output.
3. `src/schema.sql`: Complete DDL schema including tables, indexes, and triggers.
4. `src/seed.py`: Deterministic seed script resetting and populating initial data for all 5 personas.
5. `tests/test_foundation.py`: Automated tests verifying triggers, constraints, clock state, canonical timestamps, and seed data.
6. Repository bootstrap: `src/main.py`, `pyproject.toml` (or pinned requirements), `.env.example`, and README setup/start/test commands.

---

## 7. Confirmed Decisions & Implementation Notes
1. **Data Access Layer:** Confirmed **Raw SQLite** via Python `sqlite3`/`aiosqlite` for direct, transparent control over triggers, transactions, and WAL mode without ORM overhead.
2. **Identity Model:** The demo UI selects from the seeded personas and passes `X-Actor-ID`; the server resolves the role from `users` and never accepts a client-supplied role.
3. **5 Explicit Personas:** Database roles strictly support `Citizen`, `Officer`, `Field Officer`, `Supervisor`, and read-only `Audit Inspector`, with dedicated workspaces and scoped access.
4. **Database Security:** Enforced by SQLite `BEFORE UPDATE` and `BEFORE DELETE` triggers aborting any mutation on `audit`.
5. **Categories:** 4 core municipal categories (`Water Leak` 24h, `Garbage Collection` 24h, `Streetlight` 48h, `Pothole` 72h) + `Uncategorised` (72h).
6. **SLA Clock Pausing:** `paused_at` and `total_paused_seconds` columns pause the SLA clock during `NEEDS_INFO` and extend deadlines upon receipt of info.
7. **Concurrency:** Every request mutation supplies `expected_version` and increments `version` in the same transaction as its audit entry.
8. **Audit Details:** Action-specific values are stored as canonical JSON and protected by the hash chain.
9. **Submission & Resolution Evidence:** `location` is mandatory at submission; `resolution_notes` and `resolution_evidence_json` are mandatory when moving to `RESOLVED`.
10. **Appeals & Closed Reopening:** Rejected complaints store a Citizen appeal separately from Supervisor approval. `closed_at` is stored when a Citizen closes a resolved complaint and is the source of truth for the 7-day reopen window.

