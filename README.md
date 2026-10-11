# GovFix

GovFix is a municipal grievance workflow engine for VORTEXA 3.0. Plan 1 provides the SQLite foundation, validation models, fictional seed data, simulated-clock state, and append-only audit storage.

## Local setup

Requires Python 3.11 or newer.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

## Initialize demo data

The seed command recreates only the selected GovFix database. Use the fixed reference time when an exactly reproducible dataset is needed.

```powershell
python -m src.seed --database data/govfix.db --reference-time 2026-10-11T12:00:00Z
```

The fictional seed contains five users, three complaints, one AI suggestion, a clock-state row, and a valid seed audit chain.

## Run tests

```powershell
python -m pytest
```

The foundation suite is also compatible with the standard library:

```powershell
python -m unittest discover -s tests -v
```

## Start the application

```powershell
python -m uvicorn src.main:app --reload
```

The application exposes `GET /health` plus the Plan 2 workflow API.

## Workflow API

Every request uses a seeded demo identity header. Roles are always loaded from the database.

```text
X-Actor-ID: USR-CIT-01
```

Main endpoints:

```text
POST /api/v1/requests
POST /api/v1/requests/{id}/transition
POST /api/v1/requests/{id}/category
POST /api/v1/requests/{id}/reassign
GET  /api/v1/requests/{id}
GET  /api/v1/requests
GET  /api/v1/audit
```

All mutations after creation require `expected_version`. Accepted actions update the request and audit record atomically. Refused workflow attempts are also committed to the append-only audit history.

Example transition:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/requests/REQ-2026-0001/transition `
  -Headers @{ 'X-Actor-ID' = 'USR-OFF-01' } `
  -ContentType 'application/json' `
  -Body '{"to_state":"ACKNOWLEDGED","expected_version":1}'
```
