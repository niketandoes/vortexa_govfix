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
| [**Plan 01**](file:///c:/Users/niket/Documents/Hackathon-108/vortexa_govfix/plan-01.md) | **Foundation & Data Architecture** | SQLite schema, database triggers for append-only audit, user roles, SLA configuration, seed data | `src/database.py`, `src/schema.sql`, `src/seed.py`, `tests/test_foundation.py` |
| [**Plan 02**](file:///c:/Users/niket/Documents/Hackathon-108/vortexa_govfix/plan-02.md) | **Core Workflow Engine & Refusal Logging** | 5-step validation pipeline, immutable transition table, role & ownership checks, refusal audit logging, atomic transactions | `src/workflow.py`, `src/routes/requests.py`, `tests/test_workflow.py` |
| [**Plan 03**](file:///c:/Users/niket/Documents/Hackathon-108/vortexa_govfix/plan-03.md) | **Interface & Role-Based Workspaces** | Citizen portal, Officer workspace, Supervisor command center, SLA countdowns, role switcher | `src/templates/index.html`, `src/static/app.js`, `src/routes/ui.py` |
| [**Plan 04**](file:///c:/Users/niket/Documents/Hackathon-108/vortexa_govfix/plan-04.md) | **AI Advisory Layer & Structural Boundary** | Dual-path AI (Gemini + offline scikit-learn fallback), location extraction, reply drafting, AST boundary verification | `src/ai/gemini_engine.py`, `src/ai/local_classifier.py`, `tests/test_ai_boundary.py` |
| [**Plan 05**](file:///c:/Users/niket/Documents/Hackathon-108/vortexa_govfix/plan-05.md) | **Simulated Clock & Multi-Level Escalation** | Centralized simulated clock provider, dynamic SLA deadlines, idempotent escalation ladder (Level 1 & Level 2) | `src/services/clock_service.py`, `src/services/escalation_service.py`, `tests/test_escalation.py` |
| [**Plan 06**](file:///c:/Users/niket/Documents/Hackathon-108/vortexa_govfix/plan-06.md) | **Hardening, Chain Verification & Adversarial Demo** | SHA-256 cryptographic hash-chaining, tamper detection engine, adversarial test suite, 7-step judging walkthrough | `src/services/verification_service.py`, `tests/test_adversarial.py`, `scripts/demo_walkthrough.py` |

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

## 4. Summary of Key Questions for Alignment
Before proceeding with implementation, the key questions documented across each plan need confirmation to ensure zero hallucinations and absolute alignment:
- **Architecture & Tech:** Pure FastAPI with `sqlite3` raw SQL for absolute trigger & WAL control.
- **Frontend Approach:** Lightweight Single Page Dashboard with role switcher tab navigation.
- **AI Engine:** Gemini 1.5 Flash + Scikit-Learn TF-IDF local fallback with automatic offline fallback when no API key is provided.
- **Tamper Demonstration:** Built-in interactive demo toggle button in the supervisor UI + automated CLI script for judging.
