"""Domain exception hierarchy with stable error codes and HTTP status mapping."""

from typing import Any, Optional


class TriageException(Exception):
    """Base class for domain errors that map to structured API responses."""

    status_code: int = 500
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """Create the error with an optional public message and extra details."""
        self.message = message or type(self).message
        self.details = details or {}
        super().__init__(self.message)


class ValidationError(TriageException):
    """Raised when domain-level input validation fails."""

    status_code = 400
    code = "validation_error"
    message = "The request could not be validated."


class TriageDecisionError(TriageException):
    """Raised when triage processing cannot produce a decision."""

    status_code = 500
    code = "triage_failed"
    message = "Incident triage could not be completed."


class StorageException(TriageException):
    """Base class for database and cache failures."""

    status_code = 503
    code = "storage_unavailable"
    message = "Incident storage is temporarily unavailable."


class CacheException(StorageException):
    """Raised for Redis-specific failures."""

    code = "cache_unavailable"
    message = "The incident cache is temporarily unavailable."


class DatabaseException(StorageException):
    """Raised for PostgreSQL-specific failures."""

    code = "database_unavailable"
    message = "Incident storage is temporarily unavailable."


class ConfigurationError(TriageException):
    """Raised at startup when configuration is invalid (a permanent failure)."""

    code = "configuration_error"
    message = "Application configuration is invalid."


class DatabaseConnectionError(DatabaseException):
    """Raised when PostgreSQL cannot be reached or authenticated at startup."""

    code = "database_connection_failed"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        details: Optional[dict[str, Any]] = None,
        transient: bool = True,
    ) -> None:
        """Record whether the failure is transient (retryable) or permanent."""
        super().__init__(message, details=details)
        self.transient = transient


class CacheConnectionError(CacheException):
    """Raised when Redis cannot be reached; the application degrades gracefully."""

    code = "cache_connection_failed"
