PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    role TEXT NOT NULL CHECK(role IN ('Citizen', 'Officer', 'Supervisor')),
    email TEXT UNIQUE NOT NULL CHECK(length(trim(email)) > 0),
    department TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY,
    citizen_id TEXT NOT NULL,
    title TEXT NOT NULL CHECK(length(trim(title)) > 0),
    text TEXT NOT NULL CHECK(length(trim(text)) > 0),
    reported_category TEXT CHECK(
        reported_category IS NULL OR reported_category IN (
            'Water Leak', 'Garbage Collection', 'Streetlight', 'Pothole'
        )
    ),
    category TEXT NOT NULL DEFAULT 'Uncategorised' CHECK(
        category IN (
            'Water Leak', 'Garbage Collection', 'Streetlight', 'Pothole',
            'Uncategorised'
        )
    ),
    state TEXT NOT NULL CHECK(state IN (
        'SUBMITTED', 'ACKNOWLEDGED', 'ASSIGNED', 'IN_PROGRESS',
        'RESOLVED', 'CLOSED', 'REJECTED', 'REOPENED'
    )),
    assigned_to TEXT,
    rejection_reason TEXT,
    resolution_notes TEXT,
    escalation_level INTEGER NOT NULL DEFAULT 0 CHECK(escalation_level IN (0, 1, 2)),
    sla_started_at TEXT NOT NULL,
    sla_cycle INTEGER NOT NULL DEFAULT 1 CHECK(sla_cycle >= 1),
    reopen_count INTEGER NOT NULL DEFAULT 0 CHECK(reopen_count >= 0),
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deadline TEXT NOT NULL,
    FOREIGN KEY(citizen_id) REFERENCES users(id) ON DELETE RESTRICT,
    FOREIGN KEY(assigned_to) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS ai_suggestions (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN (
        'categorisation', 'extraction', 'reply_draft', 'duplicate_detection'
    )),
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0),
    source TEXT NOT NULL CHECK(source IN ('gemini_llm', 'local_fallback')),
    created_at TEXT NOT NULL,
    FOREIGN KEY(request_id) REFERENCES requests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    simulated_at TEXT,
    actor TEXT NOT NULL,
    actor_role TEXT NOT NULL CHECK(actor_role IN ('Citizen', 'Officer', 'Supervisor', 'System')),
    action TEXT NOT NULL CHECK(length(trim(action)) > 0),
    request_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    outcome TEXT NOT NULL CHECK(outcome IN ('ACCEPTED', 'REFUSED')),
    reason TEXT,
    details_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(details_json)),
    dedupe_key TEXT UNIQUE,
    previous_hash TEXT NOT NULL CHECK(length(previous_hash) = 64),
    hash TEXT NOT NULL CHECK(length(hash) = 64)
);

CREATE TABLE IF NOT EXISTS clock_state (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    offset_seconds INTEGER NOT NULL DEFAULT 0,
    is_frozen INTEGER NOT NULL DEFAULT 0 CHECK(is_frozen IN (0, 1)),
    frozen_at TEXT,
    updated_at TEXT NOT NULL,
    CHECK(
        (is_frozen = 0 AND frozen_at IS NULL)
        OR (is_frozen = 1 AND frozen_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_requests_citizen ON requests(citizen_id);
CREATE INDEX IF NOT EXISTS idx_requests_assignee ON requests(assigned_to);
CREATE INDEX IF NOT EXISTS idx_requests_state ON requests(state);
CREATE INDEX IF NOT EXISTS idx_requests_deadline ON requests(deadline);
CREATE INDEX IF NOT EXISTS idx_audit_request ON audit(request_id, id);
CREATE INDEX IF NOT EXISTS idx_ai_suggestions_request ON ai_suggestions(request_id);

CREATE TRIGGER IF NOT EXISTS require_officer_assignee_insert
BEFORE INSERT ON requests
WHEN NEW.assigned_to IS NOT NULL
BEGIN
    SELECT CASE
        WHEN NOT EXISTS (
            SELECT 1 FROM users
            WHERE id = NEW.assigned_to AND role = 'Officer'
        )
        THEN RAISE(ABORT, 'assigned_to must reference an Officer')
    END;
END;

CREATE TRIGGER IF NOT EXISTS require_officer_assignee_update
BEFORE UPDATE OF assigned_to ON requests
WHEN NEW.assigned_to IS NOT NULL
BEGIN
    SELECT CASE
        WHEN NOT EXISTS (
            SELECT 1 FROM users
            WHERE id = NEW.assigned_to AND role = 'Officer'
        )
        THEN RAISE(ABORT, 'assigned_to must reference an Officer')
    END;
END;

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
