# GovFix — Municipal Grievance Workflow Engine

[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/framework-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![Test Suite](https://img.shields.io/badge/tests-147%20passed-success.svg)](./tests)
[![Architecture](https://img.shields.io/badge/architecture-6--Phase%20Engine-orange.svg)](./plan-overview.md)
[![Database](https://img.shields.io/badge/backends-SQLite%20%7C%20MongoDB%20%7C%20Firestore-blueviolet.svg)](./src/database.py)

> **VORTEXA 3.0 · Domain 7: Government Systems & GovTech · Problem Statement G-02**  
> *Track: Municipal Grievance Resolution Workflow*

---

## 1. Executive Summary & Guiding Principle

**GovFix** is an auditable, enterprise-grade municipal grievance resolution engine that manages citizen complaints from submission through triage, field dispatch, on-ground resolution, citizen confirmation, and closure.

Built with strict server-side permissions, deterministic state transitions, category-based SLA deadlines, multi-level automatic escalation, clock pausing, and an append-only cryptographic audit trail, GovFix enforces the core architectural invariant:

> 🛡️ **AI suggests, deterministic rules decide, humans confirm, and everything is logged.**

GovFix avoids generic dashboard clichés. It implements a complete, rule-governed municipal workflow verified through **147 automated tests** and operable across **five distinct persona workspaces** plus an administrative console.

---

## 2. Key System Capabilities

### ⚡ Deterministic State Machine (9 States, 13 Transitions)
Complaints progress strictly through canonical states:
`SUBMITTED` ➔ `ACKNOWLEDGED` ➔ `NEEDS_INFO` ➔ `ASSIGNED` ➔ `IN_PROGRESS` ➔ `RESOLVED` ➔ `CLOSED` ➔ `REOPENED` ➔ `REJECTED`.
- **No Skipped Transitions:** Skipping states (e.g. `SUBMITTED -> ASSIGNED` or `IN_PROGRESS -> CLOSED`) is categorically rejected.
- **Optimistic Concurrency:** All mutations require `expected_version` to prevent race conditions and overwrite conflicts (HTTP 409).
- **Refusal Logging:** Any unauthorized attempt or invalid transition triggers an explicit diagnostic message, leaves the ticket untouched, and records a permanent `REFUSED` entry in the cryptographic audit log.

### 👥 6-Role Server-Authoritative Access Control (RBAC)
- **Citizen:** Lodge grievances, track progress, provide missing details, dispute or confirm resolutions (`CLOSED`), reopen closed cases within 7 days, appeal rejections.
- **Triage Officer:** Intake triage, acknowledge within 2-hour SLA, request details (`NEEDS_INFO`), confirm AI categories, assign Field Officers, reject with mandatory reason.
- **Field Officer:** On-ground execution, accept dispatches (`IN_PROGRESS`), record progress notes, submit completion evidence (`RESOLVED`).
- **Supervisor:** Command center overview, Level 1 & Level 2 escalation queues, work reassignment, rejection appeal adjudication, simulated clock controls.
- **Audit Inspector:** Read-only console, complete immutable audit log inspection, SHA-256 cryptographic chain verification.
- **Administrator:** User management, staff account provisioning, SLA configuration, simulated clock controls.

### ⏱️ Statutory SLAs, Clock Pausing & Multi-Level Escalation
- **Category SLA Targets:**
  - 🚯 **Garbage and sanitation:** 24 Hours
  - 💧 **Water leakage:** 24 Hours
  - 💡 **Streetlight failure:** 48 Hours
  - 🚧 **Potholes and road damage:** 72 Hours
  - 📋 **Uncategorised (Default):** 72 Hours
  - ⏱️ **Triage SLA Target:** 2 Hours from submission for intake review.
- **SLA Clock Pausing:** When extra details are requested (`ACKNOWLEDGED -> NEEDS_INFO`), the SLA timer freezes. When the citizen replies, the clock resumes with the resolution and pending triage deadlines dynamically extended by the exact paused duration.
- **Multi-Level Automatic Escalation Ladder:**
  - **75% SLA Elapsed:** Flagged as `At Risk` (Amber indicator).
  - **100% SLA Elapsed:** `Level 1 Escalation` to municipal Supervisor; automated email alert dispatched.
  - **150% SLA Elapsed:** `Level 2 Critical Escalation` to Municipal / Department Head; high-priority alert dispatched.
  - **Idempotency:** Unique deduplication keys prevent redundant audit logs and duplicate email alerts upon repeated clock advances.

### 🔐 Tamper-Evident Cryptographic Audit Trail
- Every accepted and refused mutation generates an immutable record with SHA-256 hash chaining:
  $$\text{hash}_n = \text{SHA-256}(\text{hash}_{n-1} \,\|\, \text{canonical\_payload}_n)$$
- **Database Trigger Protection:** SQLite `BEFORE UPDATE` and `BEFORE DELETE` triggers unconditionally abort any manual alteration.
- **Cryptographic Verification:** Dedicated endpoint (`GET /api/v1/audit/verify`) validates chain continuity, reports broken links, and pinpoints exact tamper timestamps.

### 🤖 Isolated AI Advisory Layer (Dual-Path)
- Combines **Google Gemini 1.5 Flash** with an offline local **scikit-learn TF-IDF classifier** trained on synthetic Indian municipal complaints.
- Automatically falls back to the offline classifier when `GEMINI_API_KEY` is not provided.
- **Capability Isolation:** AI outputs are strictly confined to an isolated `ai_suggestions` table. The AI has **zero permission** to mutate ticket states, assignees, deadlines, or escalation levels. Humans must explicitly confirm recommendations.

### 🚪 Authentication, Google Sign-In & Segregated Portals
- **Real Firebase Authentication & Google OAuth2:** Citizens can register and sign in with email/password or use one-click **"Sign in with Google"** / **"Register with Google"**.
- **Self-Service Citizen Onboarding:** New Google accounts are automatically provisioned with a `USR-CIT-xx` identifier, marked active and verified, and synced to cloud storage.
- **Completely Segregated Frontends:**
  - Friendly, light-themed **Citizen Portal** (`/citizen`) with mobile navigation, personal ticket timelines, and preferences. Internal staff IDs (`USR-FLD-xx`) are privacy-masked as team display names.
  - Dark-slate, dense **Staff Workspaces** (`/staff/...`) with persistent navigation, queue metrics, and action modals.
- **Route Guards:** Server-side redirects route authenticated users directly to their designated workspace, eliminating cross-portal access leaks and redirect loops.

### 📬 Outbox-Backed Email Notification System
- Asynchronous local outbox provider (`EMAIL_PROVIDER=local_outbox`) saving rendered HTML and `.eml` emails to `.outbox/`.
- Integrated developer inspection console at `/dev/outbox` and API at `GET /api/v1/dev/outbox`.
- 15 branded email templates covering complaint submission, acknowledgement, assignment, progress, reassignment, needs-info, resolution, rejection, appeal decisions, escalation alerts, and account invitations.

### 🗄️ Pluggable Multi-Database Backend
Configure storage seamlessly via `DATABASE_BACKEND`:
- **`sqlite`:** Local database with WAL mode, foreign keys, and immutable triggers.
- **`mongodb`:** Cloud **MongoDB Atlas** cluster with indexed collections: `users`, `requests`, `audit`, `ai_suggestions`, `clock_state`, and `email_events`.
- **`firestore`:** **Google Cloud Firestore** integration with Google Application Credentials or Firebase Admin SDK.

---

## 3. Architecture & Directory Structure

```
vortexa_govfix/
├── .outbox/                 # Dispatched local development emails (.eml & .html)
├── data/
│   └── govfix.db            # Local SQLite database (WAL mode)
├── frontend/                # Frontend codebase
│   ├── static/
│   │   ├── app.js           # GovFix client library, API helpers & clock client
│   │   └── styles.css       # Design tokens, tricolor accent & layout styles
│   └── templates/           # Jinja2 multi-page templates
│       ├── base.html        # Layout with role-segregated navigation
│       ├── citizen.html     # Citizen Portal (/citizen)
│       ├── officer.html     # Triage Officer Workspace (/staff/triage)
│       ├── field_officer.html # Field Officer Execution (/staff/field)
│       ├── supervisor.html  # Supervisor Command Center (/staff/supervisor)
│       ├── audit.html       # Audit Inspector Console (/staff/audit)
│       ├── admin.html       # Administrator Console (/admin)
│       ├── login.html       # Sign-In with Google & Email
│       ├── signup.html      # Citizen Registration
│       └── dev_outbox.html  # Developer Outbox Viewer (/dev/outbox)
├── scripts/
│   ├── migrate_to_mongodb.py # Atlas migration, indexing & seeding script
│   └── seed_firestore.py    # Firestore collection synchronization script
├── src/                     # Core backend engine
│   ├── ai/                  # AI Advisory Layer
│   │   ├── gemini_engine.py # Gemini 1.5 Flash client
│   │   ├── local_classifier.py # Offline TF-IDF classifier & dataset
│   │   └── dataset.json     # 100 Indian municipal grievance training samples
│   ├── routes/              # FastAPI route controllers
│   │   ├── admin.py         # Staff management & SLA configuration
│   │   ├── auth.py          # Firebase Auth, Google Sign-In & sessions
│   │   ├── clock.py         # Simulated municipal clock controls
│   │   ├── dev.py           # Outbox inspection endpoints
│   │   ├── requests.py      # Core workflow transitions, appeals & audit API
│   │   └── ui.py            # Jinja2 server-rendered views & RBAC guards
│   ├── services/            # Business logic services
│   │   ├── audit_service.py # SHA-256 hash chaining & verification
│   │   ├── clock_service.py # Thread-safe simulated clock & pause tracker
│   │   ├── email_service.py # Outbox email renderer & event logger
│   │   ├── escalation_service.py # SLA evaluation & multi-level escalation
│   │   └── suggestion_service.py # AI triage recommendation runner
│   ├── auth.py              # Firebase Admin SDK & token validation
│   ├── database.py          # SQLite connection, schema triggers & migrations
│   ├── firestore_db.py      # Cloud Firestore collection adapters
│   ├── mongodb.py           # MongoDB Atlas client & index manager
│   ├── models.py            # Pydantic schemas, enums & SLA constants
│   ├── schema.sql           # Canonical SQLite schema with triggers
│   ├── seed.py              # Deterministic database seeder
│   └── workflow.py          # Authoritative 9-state municipal state machine
└── tests/                   # 15 test suites with 147 automated tests
    ├── test_foundation.py   # Schema, triggers & seed validation
    ├── test_workflow.py     # 13 transitions, permissions & edge cases
    ├── test_ai_boundary.py  # AI capability firewall & offline fallback
    ├── test_escalation.py   # SLA pause/resume & escalation ladder
    ├── test_auth_and_portals.py # Portals, route guards & privacy masking
    ├── test_google_auth.py  # Google Sign-In & citizen auto-provisioning
    ├── test_mongodb.py      # MongoDB Atlas connection & CRUD operations
    └── ...
```

---

## 4. Getting Started & Installation

### Prerequisites
- **Python 3.11** or higher
- PowerShell, Bash, or standard terminal

### Step 1: Clone and Set Up Virtual Environment
```powershell
# Clone repository
git clone https://github.com/niketandoes/vortexa_govfix.git
cd vortexa_govfix

# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1   # On Linux/macOS: source .venv/bin/activate

# Install dependencies in editable mode
python -m pip install -e ".[dev]"
```

### Step 2: Environment Configuration
Copy `.env.example` to `.env`:
```powershell
Copy-Item .env.example .env   # On Linux/macOS: cp .env.example .env
```

Review the configuration options in `.env`:
```ini
# Storage Backend: 'sqlite', 'mongodb', or 'firestore'
DATABASE_BACKEND=sqlite

# Local SQLite database path
GOVFIX_DATABASE_PATH=data/govfix.db

# Optional: MongoDB Atlas Cluster
# MONGODB_URI=mongodb+srv://<USER>:<PASS>@<CLUSTER>.mongodb.net/gov-fix?retryWrites=true&w=majority
# MONGO_DB_NAME=gov-fix

# Optional: Google Gemini API Key (defaults to offline TF-IDF classifier if empty)
# GEMINI_API_KEY=AIzaSy...

# Optional: Firebase Authentication / Google Cloud Credentials
# GOOGLE_APPLICATION_CREDENTIALS=gov-fix-firebase-adminsdk.json

# Development Email Outbox Provider
EMAIL_PROVIDER=local_outbox
```

---

## 5. Database Initialization & Seeding

### Option A: SQLite (Default Local Storage)
Seed the local SQLite database with reproducible test data:
```powershell
python -m src.seed --database data/govfix.db --reference-time 2026-10-11T12:00:00Z
```
*Creates 8 users across 6 roles, 6 sample requests across lifecycle states, 1 AI suggestion, and a valid SHA-256 cryptographic audit chain.*

### Option B: MongoDB Atlas
To populate your MongoDB Atlas cluster:
```powershell
python scripts/migrate_to_mongodb.py
```

### Option C: Cloud Firestore
To synchronize data with Google Cloud Firestore:
```powershell
python scripts/seed_firestore.py
```

---

## 6. Running the Application

Start the FastAPI application with Uvicorn:
```powershell
python -m uvicorn src.main:app --reload --port 8000
```

Open your browser and navigate to:
- **Public Entry & Sign-In:** [http://127.0.0.1:8000/login](http://127.0.0.1:8000/login)
- **Citizen Registration:** [http://127.0.0.1:8000/signup](http://127.0.0.1:8000/signup)
- **API Documentation (Swagger):** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **Developer Email Outbox:** [http://127.0.0.1:8000/dev/outbox](http://127.0.0.1:8000/dev/outbox)
- **System Health Check:** [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

---

## 7. Pre-Seeded Demo Accounts

All seed accounts are initialized with the default password: **`GovFix2026!`**

| Persona | Role | Email | Actor ID | Dedicated Portal |
| :--- | :--- | :--- | :--- | :--- |
| **Citizen 1** | `Citizen` | `citizen1@govfix.local` | `USR-CIT-01` | `/citizen` |
| **Citizen 2** | `Citizen` | `citizen2@govfix.local` | `USR-CIT-02` | `/citizen` |
| **Triage Officer** | `Officer` | `triage@govfix.local` | `USR-OFF-01` | `/staff/triage` |
| **Field Officer 1** | `Field Officer` | `field1@govfix.local` | `USR-FLD-01` | `/staff/field` |
| **Field Officer 2** | `Field Officer` | `field2@govfix.local` | `USR-FLD-02` | `/staff/field` |
| **Supervisor** | `Supervisor` | `supervisor@govfix.local` | `USR-SUP-01` | `/staff/supervisor` |
| **Audit Inspector** | `Audit Inspector`| `audit@govfix.local` | `USR-AUD-01` | `/staff/audit` |
| **Administrator** | `Admin` | `admin@govfix.local` | `USR-ADM-01` | `/admin` |

> 💡 **Quick Google Sign-In:** Citizens can also click **"Sign in with Google"** on `/login` or **"Register with Google"** on `/signup` for instant account creation and verification.

---

## 8. Running the Automated Test Suite

GovFix features an exhaustive test suite covering state transitions, role security, escalation cycles, AI firewalls, outbox notifications, and database backends:

```powershell
python -m pytest
```

Output:
```text
============================== 147 passed in 18.19s ==============================
```

To run individual test suites:
```powershell
# Core State Machine & Permissions
python -m pytest tests/test_workflow.py

# SLA Clock Pausing & Escalation Ladder
python -m pytest tests/test_escalation.py

# AI Boundary & Offline Fallback
python -m pytest tests/test_ai_boundary.py

# Authentication, Portals & Privacy Scoping
python -m pytest tests/test_auth_and_portals.py

# Google Sign-In & Instant Onboarding
python -m pytest tests/test_google_auth.py

# MongoDB Atlas Integration
python -m pytest tests/test_mongodb.py
```

---

## 9. Key API Endpoints

### Workflow Operations
| Method | Endpoint | Description | Permitted Roles |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/requests` | Lodge new grievance | `Citizen` |
| `POST` | `/api/v1/requests/{id}/transition` | Execute state transition | Role-dependent |
| `POST` | `/api/v1/requests/{id}/category` | Confirm / update category | `Officer`, `Supervisor` |
| `POST` | `/api/v1/requests/{id}/reassign` | Reassign active case | `Supervisor` |
| `POST` | `/api/v1/requests/{id}/appeal` | Submit rejection appeal | `Citizen` (Owner) |
| `POST` | `/api/v1/requests/{id}/progress` | Record progress note | `Field Officer` (Assignee) |
| `GET`  | `/api/v1/requests/{id}` | Get grievance details | Owner / Staff |
| `GET`  | `/api/v1/requests` | List grievances (role-scoped) | Citizen / Staff |
| `GET`  | `/api/v1/citizen/stats` | Retrieve citizen overview metrics | `Citizen` |

### Audit & Security
| Method | Endpoint | Description | Permitted Roles |
| :--- | :--- | :--- | :--- |
| `GET`  | `/api/v1/audit` | Query append-only audit trail | `Audit Inspector` |
| `GET`  | `/api/v1/audit/verify` | Cryptographic SHA-256 chain verification | `Audit Inspector` |

### Simulated Municipal Clock
| Method | Endpoint | Description | Permitted Roles |
| :--- | :--- | :--- | :--- |
| `GET`  | `/api/v1/clock` | Current simulated time & state | All |
| `POST` | `/api/v1/clock/advance` | Advance clock by hours | `Supervisor`, `Admin` |
| `POST` | `/api/v1/clock/freeze` | Freeze simulated time | `Supervisor`, `Admin` |
| `POST` | `/api/v1/clock/unfreeze` | Resume frozen clock smoothly | `Supervisor`, `Admin` |
| `POST` | `/api/v1/clock/reset` | Reset clock to current wall time | `Supervisor`, `Admin` |

### Authentication & Sessions
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET`  | `/api/v1/auth/config` | Public Firebase web client credentials |
| `POST` | `/api/v1/auth/google` | Google ID Token authentication & citizen provisioning |
| `POST` | `/api/v1/auth/signup` | Citizen email/password registration |
| `POST` | `/api/v1/auth/session` | Set session cookie for web portal navigation |
| `POST` | `/api/v1/auth/logout` | Clear session cookie and sign out |

---

## 10. Demonstration Walkthrough for Hackathon Judges

### Scenario 1: Complete Grievance Lifecycle
1. **Citizen Submission:** Sign in as `citizen1@govfix.local` on `/citizen`. Lodge a grievance (*"Severe road crater near Sector 4"*). Receive tracking code `REQ-2026-0007` with a 2-hour triage target.
2. **Officer Triage & AI Review:** Sign in as `triage@govfix.local` on `/staff/triage`. Observe AI recommendation. Click **Acknowledge**, confirm category (*Potholes and road damage - 72h SLA*), and assign to Field Officer `USR-FLD-01`.
3. **Field Officer Execution:** Sign in as `field1@govfix.local` on `/staff/field`. Click **Start Work** (`IN_PROGRESS`), add a progress note, and submit **Mark Resolved** (`RESOLVED`) with repair details and evidence.
4. **Citizen Confirmation:** Return to `/citizen` as `citizen1@govfix.local`. Verify completion and click **Confirm & Close** (`CLOSED`).

### Scenario 2: Enforced Security Boundary & Refusal Logging
1. Sign in as Field Officer `field1@govfix.local` on `/staff/field`.
2. Attempt an unauthorized action (e.g. attempting to close a case or reassign work).
3. The server blocks the request with an explicit diagnostic message.
4. Sign in as Audit Inspector `audit@govfix.local` on `/staff/audit`. Observe the refusal recorded with `outcome = REFUSED` highlighted in red, leaving the ticket completely unmodified.

### Scenario 3: SLA Clock Pausing During `NEEDS_INFO`
1. Triage Officer moves an acknowledged complaint to `NEEDS_INFO` requesting a landmark.
2. Observe that the SLA timer is immediately paused (`paused_at`).
3. Citizen provides the requested landmark on `/citizen`.
4. The ticket transitions back to `ACKNOWLEDGED`. The resolution deadline and pending triage deadline are dynamically extended by the exact duration the ticket spent waiting.

### Scenario 4: Fast-Forwarding Time & Automatic Escalation
1. Locate seeded 24-hour complaint `REQ-2026-0006` on `/staff/supervisor`.
2. Use the simulated clock widget to advance time by **+18 hours** (75% elapsed). Dashboard reflects `At Risk` (Amber badge).
3. Advance by **+7 additional hours** (25h total, 100% elapsed). Complaint enters `Level 1 Escalation`; supervisor alert email is dispatched.
4. Advance by **+11 additional hours** (36h total, 150% elapsed). Complaint triggers `Level 2 Critical Escalation`.
5. Check `/dev/outbox` to verify the dispatched email notifications and observe that no duplicate emails were generated.

### Scenario 5: Cryptographic Audit Verification
1. Navigate to `/staff/audit` as Inspector `audit@govfix.local`.
2. Click **Verify Audit Chain**.
3. The backend validates SHA-256 hashes from genesis block to tip and returns `status: VALID` with total verified blocks.

---

## 11. Implementation Deliverables & Roadmap Reference

GovFix was engineered and verified following the **6-Phase Implementation Roadmap**:
- [**Plan 01**](./plan-01.md): Foundation, SQLite Schema, Triggers, Seed Data, and Models.
- [**Plan 02**](./plan-02.md): Core Workflow Engine, Atomic Transactions, and Refusal Logging.
- [**Plan 03**](./plan-03.md): 5-Persona Interfaces, Server-Rendered Jinja2 Templates, and Workspaces.
- [**Plan 04**](./plan-04.md): AI Advisory Layer, Gemini 1.5 Flash, and Offline TF-IDF Fallback.
- [**Plan 05**](./plan-05.md): Simulated Municipal Clock, SLA Pausing, and Idempotent Escalation Ladder.
- [**Plan 06**](./plan-06.md): Hardening, SHA-256 Hash Chaining, Tamper Verification, and Security Tests.
- [**Auth & Portals Rebuild**](./implementation_plan_auth_portals.md): Firebase Authentication, Google Sign-In, Role Guards, and Local Outbox Email Notifications.
- [**Master Plan Overview**](./plan-overview.md): Architectural Invariants, Execution Dependency Graph, and Deliverables.
- [**GovFix Full Solution Report**](./GovFix%20Report.md): Complete domain analysis and hackathon evaluation report.

---

## 12. License & Team

Developed for **VORTEXA 3.0 Hackathon (Domain 7: Government Systems & GovTech)**.  
Open-source under the [MIT License](LICENSE).
