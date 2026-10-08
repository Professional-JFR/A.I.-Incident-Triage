"""Application settings, loaded and validated at startup rather than import time."""

from functools import lru_cache
from typing import Optional
from urllib.parse import quote

from pydantic import ValidationError as PydanticValidationError
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.exceptions import ConfigurationError


class Settings(BaseSettings):
    """Environment-based configuration with defaults and validation."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "incident_triage"
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    database_url: Optional[str] = None
    redis_url: str = "redis://redis:6379/0"
    max_request_bytes: int = 1048576
    log_level: str = "INFO"
    cache_ttl_seconds: int = 3600
    recent_incident_limit: int = 50
    startup_db_retries: int = 20
    startup_db_retry_delay_seconds: float = 1.5
    skip_startup_init: bool = False

    @field_validator("postgres_port")
    @classmethod
    def _valid_port(cls, value: int) -> int:
        """Require a TCP port number."""
        if not 1 <= value <= 65535:
            raise ValueError("must be between 1 and 65535")
        return value

    @field_validator("max_request_bytes", "cache_ttl_seconds", "recent_incident_limit")
    @classmethod
    def _positive(cls, value: int) -> int:
        """Require strictly positive integers."""
        if value <= 0:
            raise ValueError("must be greater than 0")
        return value

    @field_validator("startup_db_retries")
    @classmethod
    def _retries(cls, value: int) -> int:
        """Require at least one startup connection attempt."""
        if value < 1:
            raise ValueError("must be at least 1")
        return value

    @field_validator("startup_db_retry_delay_seconds")
    @classmethod
    def _delay(cls, value: float) -> float:
        """Disallow negative retry delays."""
        if value < 0:
            raise ValueError("must not be negative")
        return value

    @field_validator("log_level")
    @classmethod
    def _log_level(cls, value: str) -> str:
        """Normalize and validate the logging level name."""
        level = value.upper()
        if level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError("must be one of CRITICAL, ERROR, WARNING, INFO, DEBUG")
        return level

    @field_validator("redis_url")
    @classmethod
    def _redis_url(cls, value: str) -> str:
        """Require a Redis URL scheme."""
        if not value.startswith(("redis://", "rediss://", "unix://")):
            raise ValueError("must start with redis://, rediss:// or unix://")
        return value

    @property
    def db_dsn(self) -> str:
        """Return the PostgreSQL connection URL."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql://{quote(self.postgres_user, safe='')}:{quote(self.postgres_password, safe='')}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def _load_settings() -> Settings:
    """Build settings once from the environment."""
    return Settings()


def get_settings() -> Settings:
    """Return validated settings.

    Raises:
        ConfigurationError: If environment values are invalid. Messages list the
            offending variables but never their values.
    """
    try:
        return _load_settings()
    except PydanticValidationError as exc:
        problems = [
            {"variable": ".".join(str(part) for part in err["loc"]).upper(), "problem": err["msg"]}
            for err in exc.errors()
        ]
        raise ConfigurationError(
            "Invalid configuration: " + "; ".join(f"{p['variable']} {p['problem']}" for p in problems),
            details={"errors": problems},
        ) from exc


def reset_settings_cache() -> None:
    """Clear cached settings (used by tests after changing the environment)."""
    _load_settings.cache_clear()
