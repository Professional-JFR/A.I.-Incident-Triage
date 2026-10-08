"""PostgreSQL operations. Schema is managed by Alembic migrations."""

import logging
from datetime import datetime
from typing import Optional

import psycopg

from app.config import get_settings
from app.exceptions import DatabaseConnectionError
from app.metrics import track_db_latency
from app.resilience import is_transient_db_error, retry_with_backoff

logger = logging.getLogger("app.database")


def connect() -> psycopg.Connection:
    """Open a PostgreSQL connection using the configured database URL."""
    return psycopg.connect(get_settings().db_dsn)


def ping() -> bool:
    """Return whether PostgreSQL responds to a basic query."""
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except psycopg.Error as exc:
        logger.warning(
            "PostgreSQL health check failed",
            extra={"event": "storage.database_unavailable", "error_type": type(exc).__name__},
        )
        return False


def wait_for_database() -> None:
    """Block until PostgreSQL accepts connections, retrying transient failures.

    Raises:
        DatabaseConnectionError: With ``transient=False`` for permanent errors
            (e.g. bad credentials) or ``transient=True`` once retries are exhausted.
    """
    settings = get_settings()

    def attempt() -> None:
        with connect() as conn:
            conn.execute("SELECT 1")

    try:
        retry_with_backoff(
            attempt,
            retries=settings.startup_db_retries,
            base_delay_seconds=settings.startup_db_retry_delay_seconds,
            is_transient=is_transient_db_error,
        )
    except psycopg.Error as exc:
        transient = is_transient_db_error(exc)
        raise DatabaseConnectionError(
            (
                f"Unable to connect to PostgreSQL ({type(exc).__name__}). "
                + (
                    f"Gave up after {settings.startup_db_retries} attempts; check that the "
                    "database is running and reachable."
                    if transient
                    else "This looks permanent: check credentials and database name."
                )
            ),
            details={
                "host": settings.postgres_host,
                "port": settings.postgres_port,
                "database": settings.postgres_db,
                "error_type": type(exc).__name__,
            },
            transient=transient,
        ) from exc


@track_db_latency("insert_incident")
def insert_incident(record: dict, occurred_at: Optional[datetime]) -> None:
    """Insert an incident row.

    Raises:
        psycopg.Error: If the incident cannot be persisted.
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO incidents (
                    incident_id, title, description, service, source, occurred_at,
                    predicted_severity, duplicate_of, runbook_suggestion, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record["incident_id"],
                    record["title"],
                    record["description"],
                    record["service"],
                    record["source"],
                    occurred_at,
                    record["predicted_severity"],
                    record["duplicate_of"],
                    record["runbook_suggestion"],
                    record["status"],
                ),
            )
        conn.commit()


@track_db_latency("select_recent_incidents")
def fetch_recent_incidents(limit: int = 20) -> list[dict]:
    """Return the newest stored incidents (ID, title, description), newest first."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT incident_id, title, description
                FROM incidents
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
    return [{"incident_id": row[0], "title": row[1], "description": row[2]} for row in rows]


@track_db_latency("select_incident")
def fetch_incident(incident_id: str) -> Optional[dict]:
    """Return one incident by ID, or ``None`` if it does not exist."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT incident_id, title, description, service, source, occurred_at,
                       predicted_severity, duplicate_of, runbook_suggestion, status
                FROM incidents
                WHERE incident_id = %s
                """,
                (incident_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {
        "incident_id": row[0],
        "title": row[1],
        "description": row[2],
        "service": row[3],
        "source": row[4],
        "timestamp": row[5].isoformat() if row[5] else None,
        "predicted_severity": row[6],
        "duplicate_of": row[7],
        "runbook_suggestion": row[8],
        "status": row[9],
    }


def schema_ready() -> bool:
    """Return whether the ``incidents`` table exists (i.e. migrations were applied)."""
    with connect() as conn:
        row = conn.execute("SELECT to_regclass('public.incidents')").fetchone()
    return bool(row and row[0])
