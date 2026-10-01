import json
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Optional

import psycopg
import redis

logger = logging.getLogger("app.storage")

PG_HOST = os.getenv("POSTGRES_HOST", "postgres")
PG_PORT = os.getenv("POSTGRES_PORT", "5432")
PG_DB = os.getenv("POSTGRES_DB", "incident_triage")
PG_USER = os.getenv("POSTGRES_USER", "postgres")
PG_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")
DB_DSN = os.getenv(
    "DATABASE_URL",
    f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CACHE_TTL_SECONDS = 3600
RECENT_INCIDENT_CACHE_LIMIT = 50

redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)


def _db_connect():
    """Open a PostgreSQL connection using the configured database URL."""
    return psycopg.connect(DB_DSN)


def init_storage(retries: int = 20, delay_seconds: float = 1.5) -> None:
    """Create required tables, retrying transient startup connection failures.

    Args:
        retries: Maximum number of attempts to create the incidents table.
        delay_seconds: Pause between failed attempts.

    Raises:
        RuntimeError: If the table cannot be created after all attempts.
    """
    last_error: Optional[Exception] = None
    for _ in range(retries):
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS incidents (
                            incident_id TEXT PRIMARY KEY,
                            title TEXT NOT NULL,
                            description TEXT NOT NULL,
                            service TEXT,
                            source TEXT,
                            occurred_at TIMESTAMPTZ,
                            predicted_severity TEXT NOT NULL,
                            duplicate_of TEXT,
                            runbook_suggestion TEXT NOT NULL,
                            status TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                conn.commit()
            return
        except Exception as exc:  # pragma: no cover - startup retry path
            last_error = exc
            logger.warning(
                "Storage initialization attempt failed",
                extra={"event": "storage.startup_retry", "error_type": type(exc).__name__},
            )
            time.sleep(delay_seconds)
    message = (
        f"Unable to initialize storage after {retries} attempts "
        f"({type(last_error).__name__}: {last_error})"
    )
    logger.error(
        message,
        extra={
            "event": "storage.startup_failed",
            "error_type": type(last_error).__name__ if last_error else "UnknownError",
        },
    )
    raise RuntimeError(message) from last_error


def ping_db() -> bool:
    """Return whether PostgreSQL responds to a basic query."""
    try:
        with _db_connect() as conn:
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


def ping_redis() -> bool:
    """Return whether Redis responds to a ping."""
    try:
        return bool(redis_client.ping())
    except redis.RedisError as exc:
        logger.warning(
            "Redis health check failed",
            extra={"event": "storage.cache_unavailable", "error_type": type(exc).__name__},
        )
        return False


def _cache_incident(record: dict) -> None:
    """Cache a persisted incident without changing the result of a database write.

    Args:
        record: Incident data already persisted to PostgreSQL.
    """
    try:
        redis_client.setex(
            f"incident:{record['incident_id']}",
            CACHE_TTL_SECONDS,
            json.dumps(record, default=str),
        )
        redis_client.lpush("recent_incidents", record["incident_id"])
        redis_client.ltrim("recent_incidents", 0, RECENT_INCIDENT_CACHE_LIMIT - 1)
    except redis.RedisError as exc:
        logger.warning(
            "Incident cache write failed",
            extra={
                "event": "cache.write_failed",
                "incident_id": record["incident_id"],
                "error_type": type(exc).__name__,
            },
        )


def create_incident_record(
    title: str,
    description: str,
    service: Optional[str],
    source: Optional[str],
    occurred_at: Optional[datetime],
    predicted_severity: str,
    duplicate_of: Optional[str],
    runbook_suggestion: str,
) -> dict:
    """Persist a triaged incident and best-effort cache its returned record.

    Args:
        title: Incident summary.
        description: Incident details.
        service: Optional affected service name.
        source: Optional incident source.
        occurred_at: Optional time at which the incident occurred.
        predicted_severity: Severity selected by the triage rules.
        duplicate_of: Optional ID of a similar incident.
        runbook_suggestion: Recommended response runbook.

    Returns:
        The persisted incident record.

    Raises:
        psycopg.Error: If the incident cannot be persisted.
    """
    incident_id = f"INC-{uuid.uuid4().hex[:8].upper()}"
    record = {
        "incident_id": incident_id,
        "title": title,
        "description": description,
        "service": service,
        "source": source,
        "timestamp": occurred_at.isoformat() if occurred_at else None,
        "predicted_severity": predicted_severity,
        "duplicate_of": duplicate_of,
        "runbook_suggestion": runbook_suggestion,
        "status": "triaged",
    }

    try:
        with _db_connect() as conn:
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
    except psycopg.Error as exc:
        logger.exception(
            "Incident persistence failed",
            extra={
                "event": "storage.incident_write_failed",
                "incident_id": incident_id,
                "error_type": type(exc).__name__,
            },
        )
        raise

    _cache_incident(record)
    logger.info(
        "Incident persisted",
        extra={"event": "storage.incident_persisted", "incident_id": incident_id},
    )
    return record


def fetch_recent_incidents(limit: int = 20) -> list[dict]:
    """Return the newest stored incidents used for duplicate detection.

    Args:
        limit: Maximum number of records to return.

    Returns:
        Incident IDs, titles, and descriptions ordered newest first.

    Raises:
        psycopg.Error: If PostgreSQL cannot return the records.
    """
    try:
        with _db_connect() as conn:
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
    except psycopg.Error as exc:
        logger.exception(
            "Recent incident lookup failed",
            extra={"event": "storage.recent_lookup_failed", "error_type": type(exc).__name__},
        )
        raise

    return [{"incident_id": row[0], "title": row[1], "description": row[2]} for row in rows]


def get_incident(incident_id: str) -> Optional[dict]:
    """Retrieve an incident from Redis or fall back to PostgreSQL.

    Args:
        incident_id: ID of the incident to retrieve.

    Returns:
        The incident record, or ``None`` when no record has that ID.

    Raises:
        psycopg.Error: If the cache misses and PostgreSQL cannot be queried.
    """
    try:
        cached = redis_client.get(f"incident:{incident_id}")
    except redis.RedisError as exc:
        cached = None
        logger.warning(
            "Incident cache lookup failed; falling back to PostgreSQL",
            extra={
                "event": "cache.read_failed",
                "incident_id": incident_id,
                "error_type": type(exc).__name__,
            },
        )
    if cached:
        try:
            record = json.loads(cached)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "Incident cache entry was invalid; falling back to PostgreSQL",
                extra={
                    "event": "cache.invalid_entry",
                    "incident_id": incident_id,
                    "error_type": type(exc).__name__,
                },
            )
        else:
            if isinstance(record, dict) and record.get("incident_id") == incident_id:
                logger.info(
                    "Incident cache hit",
                    extra={"event": "cache.hit", "incident_id": incident_id},
                )
                return record
            logger.warning(
                "Incident cache entry was invalid; falling back to PostgreSQL",
                extra={"event": "cache.invalid_entry", "incident_id": incident_id},
            )

    logger.info(
        "Incident cache miss",
        extra={"event": "cache.miss", "incident_id": incident_id},
    )

    try:
        with _db_connect() as conn:
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
    except psycopg.Error as exc:
        logger.exception(
            "Incident database lookup failed",
            extra={
                "event": "storage.incident_lookup_failed",
                "incident_id": incident_id,
                "error_type": type(exc).__name__,
            },
        )
        raise

    if not row:
        return None

    record = {
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
    _cache_incident(record)
    return record
