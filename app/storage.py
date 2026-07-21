import json
import os
import time
import uuid
from datetime import datetime
from typing import Optional

import psycopg
import redis

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

redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)


def _db_connect():
    return psycopg.connect(DB_DSN)


def init_storage(retries: int = 20, delay_seconds: float = 1.5) -> None:
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
            time.sleep(delay_seconds)
    raise RuntimeError(f"Unable to initialize storage: {last_error}")


def ping_db() -> bool:
    try:
        with _db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception:
        return False


def ping_redis() -> bool:
    try:
        return bool(redis_client.ping())
    except Exception:
        return False


def _cache_incident(record: dict) -> None:
    redis_client.setex(f"incident:{record['incident_id']}", 3600, json.dumps(record, default=str))
    redis_client.lpush("recent_incidents", record["incident_id"])
    redis_client.ltrim("recent_incidents", 0, 49)


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

    _cache_incident(record)
    return record


def fetch_recent_incidents(limit: int = 20) -> list[dict]:
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

    return [{"incident_id": row[0], "title": row[1], "description": row[2]} for row in rows]


def get_incident(incident_id: str) -> Optional[dict]:
    cached = redis_client.get(f"incident:{incident_id}")
    if cached:
        return json.loads(cached)

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
