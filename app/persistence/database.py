"""PostgreSQL operations. Schema is managed by Alembic migrations."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

import psycopg
from psycopg_pool import ConnectionPool

from app.config import get_settings
from app.exceptions import DatabaseConnectionError
from app.metrics import track_db_latency
from app.resilience import is_transient_db_error, retry_with_backoff

logger = logging.getLogger("app.database")
_pool: ConnectionPool | None = None


def connect() -> psycopg.Connection:
    """Open a PostgreSQL connection using the configured database URL."""
    settings = get_settings()
    return psycopg.connect(settings.db_dsn, connect_timeout=settings.db_connect_timeout_seconds)


def open_pool() -> None:
    """Initialize the shared PostgreSQL connection pool."""
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = ConnectionPool(
            conninfo=settings.db_dsn,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            timeout=settings.db_pool_timeout_seconds,
            kwargs={"connect_timeout": settings.db_connect_timeout_seconds},
            open=True,
        )


def close_pool() -> None:
    """Close the PostgreSQL pool during graceful shutdown."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Borrow a pooled connection, using direct connections before startup."""
    if _pool is None:
        with connect() as conn:
            yield conn
    else:
        with _pool.connection() as conn:
            yield conn


def ping() -> bool:
    """Return whether PostgreSQL responds to a basic query."""
    try:
        with connection() as conn:
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
def insert_incident(
    record: dict, occurred_at: datetime | None, idempotency_key: str | None = None
) -> dict:
    """Insert an incident row.

    Raises:
        psycopg.Error: If the incident cannot be persisted.
    """
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO incidents (
                    incident_id, title, description, service, source, occurred_at,
                    predicted_severity, duplicate_of, runbook_suggestion, status, idempotency_key
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING incident_id, title, description, service, source, occurred_at,
                          predicted_severity, duplicate_of, runbook_suggestion, status
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
                    idempotency_key,
                ),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    """
                    SELECT incident_id, title, description, service, source, occurred_at,
                           predicted_severity, duplicate_of, runbook_suggestion, status
                    FROM incidents WHERE idempotency_key = %s
                    """,
                    (idempotency_key,),
                )
                row = cur.fetchone()
        conn.commit()
    if row is None:
        return record
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


@track_db_latency("select_recent_incidents")
def fetch_recent_incidents(limit: int = 20) -> list[dict]:
    """Return the newest stored incidents (ID, title, description), newest first."""
    with connection() as conn:
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
def fetch_incident(incident_id: str) -> dict | None:
    """Return one incident by ID, or ``None`` if it does not exist."""
    with connection() as conn:
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
    with connection() as conn:
        row = conn.execute("SELECT to_regclass('public.incidents')").fetchone()
    return bool(row and row[0])
