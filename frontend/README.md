# GovFix Frontend

This directory contains the entire frontend codebase for the GovFix Municipal Grievance Workflow System.

---

## Architecture Overview

GovFix implements a server-rendered Jinja2 multi-page architecture powered by FastAPI, Tailwind CSS, and vanilla JavaScript (`GovFix` client).

There are **5 distinct persona workspaces**:

| Persona | URL Route | Template | Primary Role | Seeded Identity |
| :--- | :--- | :--- | :--- | :--- |
| **Citizen** | `/citizen` | [templates/citizen.html](file:///c:/Document/GitHub/vortexa_govfix/frontend/templates/citizen.html) | Lodge complaints, track status by token, appeal rejections, confirm resolutions, reopen closed cases | `USR-CIT-01` (Ramesh Patel), `USR-CIT-02` (Priya Sharma) |
| **Triage Officer** | `/officer` | [templates/officer.html](file:///c:/Document/GitHub/vortexa_govfix/frontend/templates/officer.html) | Intake queue triage, acknowledge receipt within 2h SLA, request missing info, assign Field Officers, reject invalid complaints, confirm AI categories | `USR-OFF-01` (Officer Anil Kumar) |
| **Field Officer** | `/field-officer` | [templates/field_officer.html](file:///c:/Document/GitHub/vortexa_govfix/frontend/templates/field_officer.html) | On-ground work execution, accept task dispatches, add progress notes, submit resolution evidence | `USR-FLD-01` (Vikram Singh), `USR-FLD-02` (Sunita Rao) |
| **Supervisor** | `/supervisor` | [templates/supervisor.html](file:///c:/Document/GitHub/vortexa_govfix/frontend/templates/supervisor.html) | Command center overview, SLA escalation monitoring, task reassignment, appeal adjudication, simulated clock control | `USR-SUP-01` (Supervisor Rajesh Gupta) |
| **Audit Inspector** | `/audit` | [templates/audit.html](file:///c:/Document/GitHub/vortexa_govfix/frontend/templates/audit.html) | Read-only governance inspection, cryptographic SHA-256 tamper-evident hash chain verification | `USR-AUD-01` (Inspector Meera Joshi) |

---

## Directory Structure

```
frontend/
├── static/
│   ├── app.js       # Shared GovFix JavaScript API client, header controls, simulated clock, and formatting helpers
│   └── styles.css   # Shared styling, tricolor accent bar, modal animations, and typography tokens
└── templates/
    ├── base.html          # Shared Jinja2 master layout with 5-persona persistent navigation bar and demo identity switcher
    ├── citizen.html       # Citizen Portal (Lodge, track, verify, appeal, reopen)
    ├── officer.html       # Officer Workspace (Triage queue, SLA badges, 2h target indicator, assignment, AI advisory copilot)
    ├── field_officer.html # Field Officer Execution Workspace (Dispatches, progress notes, resolution submission)
    ├── supervisor.html    # Supervisor Command Center (Reassignment, appeals, simulated clock controller)
    └── audit.html         # Audit Inspector Console (Immutable log, SHA-256 chain verification)
```

---

## Key Frontend Conventions

1. **Shared Layout:** All persona templates extend `base.html`, providing consistent header navigation, persona switcher (`X-Actor-ID`), and simulated municipal clock.
2. **Statutory SLA Badges:** authoritatively bound to backend timestamps (`deadline`, `triage_deadline`, `triaged_at`) via `GovFix.formatSLARemaining` and `GovFix.formatTriageRemaining`.
3. **No Mock Data:** All queues, cards, and modal actions bind directly to live FastAPI endpoints (`/api/v1/requests`, `/api/v1/audit`, etc.).
4. **Optimistic Concurrency:** State transition modals submit `expected_version` to prevent race conditions and concurrent write conflicts.
