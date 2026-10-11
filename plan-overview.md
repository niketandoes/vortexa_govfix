# GovFix — Implementation Master Plan & Phase Roadmap

## 1. System Overview & Problem Statement
GovFix is a **Municipal Grievance Workflow Engine** built for VORTEXA 3.0 (Problem Statement G-02, Government Systems & GovTech).
It enforces deterministic state transitions, server-side role authorization, SLA deadline compliance, multi-level escalation, tamper-evident cryptographic audit logging, and an isolated AI advisory layer with an offline local fallback.

**Guiding Architectural Invariant:**
> **AI suggests, deterministic rules decide, humans confirm, and everything is logged.**

---

## 2. Phase Breakdown & Plan Index

| Plan | Phase Title | Focus Areas | Key Output Artifacts |
| :--- | :--- | :--- | :--- |
| [**Plan 01**](./plan-01.md) | **Foundation & Data Architecture** | SQLite schema, database triggers for append-only audit, 5 persona roles (Citizen, Officer, Field Officer, Supervisor, Audit Inspector), SLA and triage-target configuration, seed data | `src/database.py`, `src/schema.sql`, `src/seed.py`, `tests/test_foundation.py` |
| [**Plan 02**](./plan-02.md) | **Core Workflow Engine & Refusal Logging** | State transitions (9 canonical states including NEEDS_INFO), role and ownership checks, concurrency, SLA clock pause/resume, refusal logging | `src/workflow.py`, `src/routes/requests.py`, `tests/test_workflow.py` |
| [**Plan 03**](./plan-03.md) | **Interface & Role-Based Workspaces** | Five distinct personas: Citizen (`/citizen`), Officer Triage (`/officer`), Field Officer (`/field-officer`), Supervisor (`/supervisor`), Audit Inspector (`/audit`) | `src/templates/`, `src/static/app.js`, `src/routes/ui.py` |
| [**Plan 04**](./plan-04.md) | **AI Advisory Layer & Structural Boundary** | Dual-path AI (Gemini + offline scikit-learn fallback), location extraction, reply drafting, capability isolation | `src/ai/gemini_engine.py`, `src/ai/local_classifier.py`, `tests/test_ai_boundary.py` |
| [**Plan 05**](./plan-05.md) | **Simulated Clock & Multi-Level Escalation** | Persisted simulated clock, SLA cycles, dynamic deadlines, clock pause during NEEDS_INFO, idempotent escalation | `src/services/clock_service.py`, `src/services/escalation_service.py`, `tests/test_escalation.py` |
| [**Plan 06**](./plan-06.md) | **Hardening, Chain Verification & Adversarial Demo** | SHA-256 hash chaining, bounded tamper claims, adversarial tests, 5-persona judging walkthrough | `src/services/verification_service.py`, `tests/test_adversarial.py`, `scripts/demo_walkthrough.py` |

---

## 3. Dependency & Execution Graph

```
┌───────────────────────────────────────────────┐
│     Plan 01: Foundation & Data Schema         │
└───────────────────────┬───────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────┐
│     Plan 02: Core Engine & Refusal Security   │
└───────────────┬───────────────────────────────┘
                │
        ┌───────┴────────────────────────┐
        ▼                                ▼
┌─────────────────────────┐    ┌─────────────────────────┐
│ Plan 04: AI Advisory    │    │ Plan 05: Clock, SLA &   │
│ & Isolated Fallback     │    │ Multi-Level Escalation  │
└───────┬─────────────────┘    └─────────┬───────────────┘
        │                                │
        └───────────────┬────────────────┘
                        ▼
┌───────────────────────────────────────────────┐
│     Plan 03: 5-Persona Web Interface & UX     │
│  (/citizen, /officer, /field-officer, etc.)   │
└───────────────────────┬───────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────┐
│     Plan 06: Hardening, Tamper Verification   │
│     & Adversarial Demo Suite                  │
└───────────────────────────────────────────────┘
```

---

## 4. Confirmed Cross-Plan Decisions

- **Architecture & Tech:** Pure FastAPI with `sqlite3` raw SQL for absolute trigger & WAL control.
- **Frontend Approach:** Multi-page server-rendered interface with five dedicated routes: `/citizen`, `/officer` (triage), `/field-officer` (on-ground execution), `/supervisor`, and `/audit` (read-only verification) with a shared base template and persistent navigation.
- **Role Taxonomy:** Five explicit personas: Citizen, Officer (Triage), Field Officer (Execution), Supervisor (Command Center), and Audit Inspector (Read-Only Verification).
- **Canonical Workflow States:** `SUBMITTED`, `ACKNOWLEDGED`, `NEEDS_INFO`, `ASSIGNED`, `IN_PROGRESS`, `RESOLVED`, `CLOSED`, `REOPENED`, `REJECTED`.
- **Clock Pausing:** The SLA resolution clock is paused during `NEEDS_INFO` and resumed with deadline extension when the citizen provides information.
- **AI Engine:** Gemini 1.5 Flash + Scikit-Learn TF-IDF local fallback with automatic offline fallback when no API key is provided. Strictly advisory; zero direct workflow mutations.
- **Demo Identity:** Seeded demo identities selected by the UI and sent through `X-Actor-ID`; server derives role from verified database record.
- **Category Confirmation:** Officers and Supervisors may confirm a category through a dedicated audited command.
- **Reassignment:** Supervisors may reassign active work (`ASSIGNED` or `IN_PROGRESS`) to another Field Officer while preserving state and providing a mandatory reason.
- **Reopening:** Reopening requires a reason, starts a new SLA and escalation cycle, and closed complaints may be reopened within a 7-day period.
- **Required Evidence:** Complaint submission requires location details; Field Officer resolution requires notes plus structured completion evidence.
- **Citizen Appeals:** A Citizen may submit an appeal while a request is `REJECTED`; the state remains `REJECTED` until a Supervisor approves it back to `ACKNOWLEDGED`.
- **Triage Target:** Every new complaint receives a separate 2-hour `triage_deadline`; citizen-waiting time in `NEEDS_INFO` extends both the resolution and pending triage deadlines.
- **Tamper Demonstration:** A standalone script modifies only a disposable database copy; the normal web application exposes verification but no trigger-bypass endpoint.

---

## 5. Repository-Level Deliverables

In addition to phase-specific files, implementation must include `src/main.py`, `pyproject.toml` (or a pinned requirements file), `.env.example`, database bootstrap instructions, and README commands for setup, tests, and demo startup.

