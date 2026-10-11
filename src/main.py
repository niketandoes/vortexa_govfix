"""FastAPI entry point for GovFix.

Plan 1 initializes the database and exposes only a basic health endpoint. Workflow
routes are added in Plan 2.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from dotenv import load_dotenv
load_dotenv()

from src.database import connect, initialize_database
from src.routes.requests import router as requests_router
from src.routes.clock import router as clock_router
from src.routes.ui import router as ui_router
from src.routes.auth import router as auth_router
from src.routes.admin import router as admin_router
from src.routes.dev import router as dev_router
from src.workflow import WorkflowError


@asynccontextmanager
async def lifespan(_: FastAPI):
    connection = connect()
    try:
        initialize_database(connection)
        user_count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if user_count == 0:
            from src.seed import seed_database
            seed_database(connection, include_extended=True)
        else:
            from src.seed import seed_extended_users
            seed_extended_users(connection)
    finally:
        connection.close()
    yield


app = FastAPI(title="GovFix", version="0.1.0", lifespan=lifespan)

FRONTEND_STATIC_DIR = Path(__file__).resolve().parents[1] / "frontend" / "static"
STATIC_DIR = (
    FRONTEND_STATIC_DIR
    if FRONTEND_STATIC_DIR.exists()
    else Path(__file__).resolve().parent / "static"
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(requests_router)
app.include_router(clock_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(dev_router)
app.include_router(ui_router)



@app.exception_handler(WorkflowError)
async def workflow_error_handler(_: Request, error: WorkflowError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content=error.as_response())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
