"""Retry with exponential backoff and a simple circuit breaker."""

import logging
import threading
import time
from typing import Callable, Optional, TypeVar

logger = logging.getLogger("app.resilience")

T = TypeVar("T")

# SQLSTATE classes that indicate permanent problems (auth, missing schema/db, bad config).
PERMANENT_SQLSTATE_PREFIXES = ("28", "3D", "42", "0A")


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the circuit breaker is open."""


class CircuitBreaker:
    """Open after consecutive failures and allow a trial call after a cooldown."""

    def __init__(self, failure_threshold: int = 5, reset_timeout_seconds: float = 30.0) -> None:
        """Configure the failure threshold and cooldown."""
        self.failure_threshold = failure_threshold
        self.reset_timeout_seconds = reset_timeout_seconds
        self._failures = 0
        self._opened_at: Optional[float] = None
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        """Return whether calls are currently being rejected."""
        if self._opened_at is None:
            return False
        return time.monotonic() - self._opened_at < self.reset_timeout_seconds

    def call(self, func: Callable[[], T]) -> T:
        """Run ``func`` unless the circuit is open, tracking success and failure."""
        if self.is_open:
            raise CircuitOpenError("circuit breaker is open")
        try:
            result = func()
        except Exception:
            with self._lock:
                self._failures += 1
                if self._failures >= self.failure_threshold:
                    self._opened_at = time.monotonic()
            raise
        with self._lock:
            self._failures = 0
            self._opened_at = None
        return result


def retry_with_backoff(
    func: Callable[[], T],
    *,
    retries: int,
    base_delay_seconds: float,
    max_delay_seconds: float = 30.0,
    is_transient: Callable[[Exception], bool] = lambda exc: True,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``func`` with exponential backoff, re-raising permanent errors at once.

    Args:
        func: Zero-argument callable to run.
        retries: Maximum number of attempts (at least 1).
        base_delay_seconds: Delay after the first failure; doubles each attempt.
        max_delay_seconds: Upper bound for a single delay.
        is_transient: Returns ``False`` for errors that must not be retried.
        sleep: Sleep function (injectable for tests).

    Raises:
        Exception: The permanent error, or the last transient error after all attempts.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return func()
        except Exception as exc:
            if not is_transient(exc) or attempt >= retries:
                raise
            delay = min(base_delay_seconds * (2 ** (attempt - 1)), max_delay_seconds)
            logger.warning(
                "Transient failure; retrying",
                extra={
                    "event": "retry.attempt_failed",
                    "attempt": attempt,
                    "max_attempts": retries,
                    "retry_in_seconds": delay,
                    "error_type": type(exc).__name__,
                },
            )
            sleep(delay)


def is_transient_db_error(exc: Exception) -> bool:
    """Classify a psycopg error as transient (connection/timeouts) or permanent."""
    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate and sqlstate.startswith(PERMANENT_SQLSTATE_PREFIXES):
        return False
    message = str(exc).lower()
    if "password authentication failed" in message or "does not exist" in message:
        return False
    return True
