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
| [**Plan 01**](./plan-01.md) | **Foundation & Data Architecture** | SQLite schema, database triggers for append-only audit, user roles, SLA configuration, seed data | `src/database.py`, `src/schema.sql`, `src/seed.py`, `tests/test_foundation.py` |
| [**Plan 02**](./plan-02.md) | **Core Workflow Engine & Refusal Logging** | State transitions, category confirmation, reassignment, role and ownership checks, concurrency, refusal logging | `src/workflow.py`, `src/routes/requests.py`, `tests/test_workflow.py` |
| [**Plan 03**](./plan-03.md) | **Interface & Role-Based Workspaces** | Separate Citizen, Officer, Supervisor, and Audit pages with SLA countdowns | `src/templates/`, `src/static/app.js`, `src/routes/ui.py` |
| [**Plan 04**](./plan-04.md) | **AI Advisory Layer & Structural Boundary** | Dual-path AI (Gemini + offline scikit-learn fallback), location extraction, reply drafting, capability isolation | `src/ai/gemini_engine.py`, `src/ai/local_classifier.py`, `tests/test_ai_boundary.py` |
| [**Plan 05**](./plan-05.md) | **Simulated Clock & Multi-Level Escalation** | Persisted simulated clock, SLA cycles, dynamic deadlines, idempotent escalation | `src/services/clock_service.py`, `src/services/escalation_service.py`, `tests/test_escalation.py` |
| [**Plan 06**](./plan-06.md) | **Hardening, Chain Verification & Adversarial Demo** | SHA-256 hash chaining, bounded tamper claims, adversarial tests, valid judging walkthrough | `src/services/verification_service.py`, `tests/test_adversarial.py`, `scripts/demo_walkthrough.py` |

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
│     Plan 03: Unified Web Interface & UX       │
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
- **Frontend Approach:** Multi-page server-rendered interface with `/citizen`, `/officer`, `/supervisor`, and `/audit` routes and a shared base template.
- **AI Engine:** Gemini 1.5 Flash + Scikit-Learn TF-IDF local fallback with automatic offline fallback when no API key is provided.
- **Demo Identity:** Seeded demo identities selected by the UI and sent through one documented actor header; this is not production authentication.
- **Category Confirmation:** Officers and Supervisors may confirm a category through a dedicated audited command.
- **Reassignment:** Supervisors may reassign active work while preserving its state and providing a reason.
- **Reopening:** Reopening requires a reason and starts a new SLA and escalation cycle.
- **Tamper Demonstration:** A standalone script modifies only a disposable database copy; the normal web application exposes verification but no trigger-bypass endpoint.

---

## 5. Repository-Level Deliverables

In addition to phase-specific files, implementation must include `src/main.py`, `pyproject.toml` (or a pinned requirements file), `.env.example`, database bootstrap instructions, and README commands for setup, tests, and demo startup.
