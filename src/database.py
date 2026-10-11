"""SQLite connection and schema management for GovFix."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "govfix.db"


def configured_database_path() -> Path:
    """Return the configured database path without opening it."""

    configured = os.getenv("GOVFIX_DATABASE_PATH")
    return Path(configured).expanduser() if configured else DEFAULT_DATABASE_PATH


def connect(database_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a configured SQLite connection with GovFix safety pragmas."""

    path = database_path if database_path is not None else configured_database_path()
    if str(path) != ":memory:":
        resolved = Path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        target = str(resolved)
    else:
        target = ":memory:"

    connection = sqlite3.connect(target, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    if target != ":memory:":
        connection.execute("PRAGMA journal_mode = WAL")
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    """Create all Plan 1 tables, indexes, and triggers."""

    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    connection.commit()


@contextmanager
def transaction(
    connection: sqlite3.Connection, *, immediate: bool = False
) -> Iterator[sqlite3.Connection]:
    """Commit a transaction on success and roll it back on failure."""

    connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield connection
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()


@contextmanager
def database_connection(
    database_path: str | Path | None = None,
) -> Iterator[sqlite3.Connection]:
    """Open and always close a GovFix database connection."""

    connection = connect(database_path)
    try:
        yield connection
    finally:
        connection.close()
