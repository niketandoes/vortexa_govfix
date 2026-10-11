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

The Plan 1 application exposes `GET /health`. Workflow endpoints are implemented in Plan 2.
