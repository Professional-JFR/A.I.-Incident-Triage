"""Prometheus metrics and helpers for automatic tracking."""

import functools
import time
from typing import Callable, TypeVar

from prometheus_client import Counter, Histogram

T = TypeVar("T")

TRIAGE_DECISIONS = Counter(
    "triage_decisions_total", "Triage decisions by predicted severity.", ["severity"]
)
INCIDENTS_TRIAGED = Counter("incidents_triaged_total", "Total incidents triaged.")
DUPLICATES_DETECTED = Counter(
    "duplicates_detected_total", "Incidents flagged as duplicates of an earlier incident."
)
CACHE_OPERATIONS = Counter(
    "cache_operations_total", "Cache lookups by result (hit, miss, error).", ["result"]
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ["method", "path", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
DB_LATENCY = Histogram(
    "db_query_duration_seconds",
    "Database operation latency in seconds.",
    ["operation"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)
VALIDATION_ERRORS = Counter(
    "validation_errors_total", "Request validation errors by field.", ["field"]
)


def track_db_latency(operation: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator recording the duration of a database operation.

    Example:
        >>> @track_db_latency("select_incident")
        ... def load(): ...
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            started = time.monotonic()
            try:
                return func(*args, **kwargs)
            finally:
                DB_LATENCY.labels(operation=operation).observe(time.monotonic() - started)

        return wrapper

    return decorator
