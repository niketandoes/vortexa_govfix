"""FastAPI entry point for GovFix.

Plan 1 initializes the database and exposes only a basic health endpoint. Workflow
routes are added in Plan 2.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.database import connect, initialize_database


@asynccontextmanager
async def lifespan(_: FastAPI):
    connection = connect()
    try:
        initialize_database(connection)
    finally:
        connection.close()
    yield


app = FastAPI(title="GovFix", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
