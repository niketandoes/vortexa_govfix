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
    id TEXT PRIMARY KEY,               -- e.g. 'USR-CIT-01', 'USR-OFF-01', 'USR-SUP-01'
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('Citizen', 'Officer', 'Supervisor')),
    email TEXT UNIQUE NOT NULL,
    department TEXT DEFAULT 'Public Works',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3.2 Table: `requests`
Represents the municipal complaint throughout its lifecycle.
```sql
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY,               -- e.g. 'REQ-2026-0001'
    citizen_id TEXT NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Uncategorised',
    state TEXT NOT NULL CHECK(state IN (
        'SUBMITTED', 'ACKNOWLEDGED', 'ASSIGNED', 
        'IN_PROGRESS', 'RESOLVED', 'CLOSED', 
        'REJECTED', 'REOPENED'
    )),
    assigned_to TEXT,                  -- foreign key to users(id) when role = 'Officer'
    rejection_reason TEXT,
    resolution_notes TEXT,
    escalation_level INTEGER DEFAULT 0 CHECK(escalation_level IN (0, 1, 2)),
    created_at TIMESTAMP NOT NULL,     -- simulated clock timestamp
    updated_at TIMESTAMP NOT NULL,
    deadline TIMESTAMP NOT NULL,       -- computed from created_at + SLA(category)
    FOREIGN KEY(citizen_id) REFERENCES users(id),
    FOREIGN KEY(assigned_to) REFERENCES users(id)
);
```

### 3.3 Table: `ai_suggestions`
Isolated advisory storage. The AI engine writes only to this table; it has zero write permissions to `requests`.
```sql
CREATE TABLE IF NOT EXISTS ai_suggestions (
    id TEXT PRIMARY KEY,               -- e.g. 'SUG-0001'
    request_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('categorisation', 'extraction', 'reply_draft', 'duplicate_detection')),
    payload TEXT NOT NULL,             -- JSON string of structured suggestion
    confidence REAL,                   -- 0.0 to 1.0 confidence score
    source TEXT NOT NULL CHECK(source IN ('gemini_llm', 'local_fallback')),
    created_at TIMESTAMP NOT NULL,
    FOREIGN KEY(request_id) REFERENCES requests(id) ON DELETE CASCADE
);
```

### 3.4 Table: `audit`
Tamper-evident, hash-chained audit trail recording every state transition attempt (both accepted and refused), assignments, category confirmations, and system escalations.
```sql
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP NOT NULL,
    actor TEXT NOT NULL,               -- user ID or 'SYSTEM'
    actor_role TEXT NOT NULL,          -- 'Citizen', 'Officer', 'Supervisor', 'System'
    action TEXT NOT NULL,              -- e.g. 'SUBMIT_COMPLAINT', 'TRANSITION', 'CONFIRM_CATEGORY', 'ESCALATE'
    request_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    outcome TEXT NOT NULL CHECK(outcome IN ('ACCEPTED', 'REFUSED')),
    reason TEXT,                       -- explicit explanation if refused or required justification
    previous_hash TEXT NOT NULL,       -- SHA-256 of previous audit record
    hash TEXT NOT NULL                 -- SHA-256(previous_hash + canonical_content)
);
```

### 3.5 Database Triggers for Append-Only Integrity
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
| Category | SLA Duration (Hours) | Description |
| :--- | :--- | :--- |
| `Water Leak` | 24 | Critical municipal utility leak |
| `Garbage Collection` | 48 | Sanitation / missed pickup |
| `Streetlight` | 72 | Electrical / streetlight maintenance |
| `Pothole` | 168 (7 Days) | Road repairs & asphalt maintenance |
| `Uncategorised` | 72 | Default SLA before category is confirmed |

---

## 5. Seed Data Specification
To allow instant local demonstration without manual user onboarding:
- **Citizens:**
  - `USR-CIT-01`: "Ramesh Patel" (`ramesh@citizen.local`)
  - `USR-CIT-02`: "Priya Sharma" (`priya@citizen.local`)
- **Officers:**
  - `USR-OFF-01`: "Officer Anil Kumar" (`anil.kumar@municipality.gov`) - Public Works
  - `USR-OFF-02`: "Officer Sunita Verma" (`sunita.verma@municipality.gov`) - Sanitation
- **Supervisor:**
  - `USR-SUP-01`: "Supervisor Rajesh Gupta" (`rajesh.gupta@municipality.gov`) - Municipal Head
- **Pre-seeded Complaints:**
  - 1 New complaint in `SUBMITTED` state.
  - 1 Complaint in `ASSIGNED` state assigned to `USR-OFF-01`.
  - 1 Overdue complaint close to escalation deadline to demonstrate clock advance.

---

## 6. Implementation Deliverables
1. `src/database.py`: SQLite connection factory with WAL mode and foreign key enforcement.
2. `src/models.py`: Pydantic schemas for data validation and API input/output.
3. `src/schema.sql`: Complete DDL schema including tables, indexes, and triggers.
4. `src/seed.py`: Deterministic seed script resetting and populating initial data.
5. `tests/test_foundation.py`: Automated tests verifying triggers, constraints, and seed data.

---

## 7. Confirmed Decisions & Implementation Notes
1. **Data Access Layer:** Confirmed **Raw SQLite** via Python `sqlite3`/`aiosqlite` for direct, transparent control over triggers, transactions, and WAL mode without ORM overhead.
2. **Identity Model:** Resolved server-side via `X-Actor-ID` / query headers matching the `users` table.
3. **Database Security:** Enforced by SQLite `BEFORE UPDATE` and `BEFORE DELETE` triggers aborting any mutation on `audit`.
4. **Categories:** 4 core municipal categories (`Water Leak`, `Garbage Collection`, `Streetlight`, `Pothole`) + `Uncategorised`.

