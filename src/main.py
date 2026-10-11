"""FastAPI entry point for GovFix.

Plan 1 initializes the database and exposes only a basic health endpoint. Workflow
routes are added in Plan 2.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.database import connect, initialize_database
from src.routes.requests import router as requests_router
from src.workflow import WorkflowError


@asynccontextmanager
async def lifespan(_: FastAPI):
    connection = connect()
    try:
        initialize_database(connection)
    finally:
        connection.close()
    yield


app = FastAPI(title="GovFix", version="0.1.0", lifespan=lifespan)
app.include_router(requests_router)


@app.exception_handler(WorkflowError)
async def workflow_error_handler(_: Request, error: WorkflowError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content=error.as_response())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
