"""Redis cache operations. Redis is optional: failures never break database paths."""

import json
import logging
from functools import lru_cache
from typing import Optional

import redis

from app.config import get_settings
from app.metrics import CACHE_OPERATIONS

logger = logging.getLogger("app.cache")


@lru_cache(maxsize=1)
def get_client() -> redis.Redis:
    """Return the shared Redis client, created lazily from validated settings."""
    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)


def close_client() -> None:
    """Close the shared Redis client if one was created."""
    if get_client.cache_info().currsize:
        get_client().close()
        get_client.cache_clear()


def ping() -> bool:
    """Return whether Redis responds to a ping."""
    try:
        return bool(get_client().ping())
    except redis.RedisError as exc:
        logger.warning(
            "Redis health check failed",
            extra={"event": "storage.cache_unavailable", "error_type": type(exc).__name__},
        )
        return False


def cache_incident(record: dict) -> None:
    """Cache a persisted incident without changing the result of a database write."""
    settings = get_settings()
    try:
        client = get_client()
        client.setex(
            f"incident:{record['incident_id']}",
            settings.cache_ttl_seconds,
            json.dumps(record, default=str),
        )
        client.lpush("recent_incidents", record["incident_id"])
        client.ltrim("recent_incidents", 0, settings.recent_incident_limit - 1)
    except redis.RedisError as exc:
        logger.warning(
            "Incident cache write failed",
            extra={
                "event": "cache.write_failed",
                "incident_id": record["incident_id"],
                "error_type": type(exc).__name__,
            },
        )


def get_cached_incident(incident_id: str) -> Optional[dict]:
    """Return a valid cached incident, or ``None`` on miss, error, or invalid entry."""
    try:
        cached = get_client().get(f"incident:{incident_id}")
    except redis.RedisError as exc:
        CACHE_OPERATIONS.labels(result="error").inc()
        logger.warning(
            "Incident cache lookup failed; falling back to PostgreSQL",
            extra={
                "event": "cache.read_failed",
                "incident_id": incident_id,
                "error_type": type(exc).__name__,
            },
        )
        return None
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
                CACHE_OPERATIONS.labels(result="hit").inc()
                logger.info("Incident cache hit", extra={"event": "cache.hit", "incident_id": incident_id})
                return record
            logger.warning(
                "Incident cache entry was invalid; falling back to PostgreSQL",
                extra={"event": "cache.invalid_entry", "incident_id": incident_id},
            )
    CACHE_OPERATIONS.labels(result="miss").inc()
    logger.info(
        "Incident cache miss; falling back to PostgreSQL",
        extra={"event": "cache.miss", "incident_id": incident_id},
    )
    return None
