"""Incident persistence and retrieval orchestration (database + cache)."""

import logging
import uuid
from datetime import datetime
from typing import Optional

import psycopg

from app.persistence import cache, database

logger = logging.getLogger("app.incident_service")


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
    """Persist a triaged incident and best-effort cache its record.

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
        database.insert_incident(record, occurred_at)
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
    cache.cache_incident(record)
    logger.info("Incident persisted", extra={"event": "storage.incident_persisted", "incident_id": incident_id})
    return record


def fetch_recent_incidents(limit: int = 20) -> list[dict]:
    """Return the newest incidents used for duplicate detection.

    Raises:
        psycopg.Error: If PostgreSQL cannot return the records.
    """
    try:
        return database.fetch_recent_incidents(limit)
    except psycopg.Error as exc:
        logger.exception(
            "Recent incident lookup failed",
            extra={"event": "storage.recent_lookup_failed", "error_type": type(exc).__name__},
        )
        raise


def get_incident(incident_id: str) -> Optional[dict]:
    """Retrieve an incident from Redis, falling back to PostgreSQL.

    Returns:
        The incident record, or ``None`` when no record has that ID.

    Raises:
        psycopg.Error: If the cache misses and PostgreSQL cannot be queried.
    """
    cached = cache.get_cached_incident(incident_id)
    if cached is not None:
        return cached
    try:
        record = database.fetch_incident(incident_id)
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
    if record:
        cache.cache_incident(record)
    return record
