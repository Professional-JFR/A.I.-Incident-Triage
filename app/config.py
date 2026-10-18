"""Application settings, loaded and validated at startup rather than import time."""

from functools import lru_cache
from urllib.parse import quote

from pydantic import ValidationError as PydanticValidationError
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.exceptions import ConfigurationError


class Settings(BaseSettings):
    """Environment-based configuration with defaults and validation."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "incident_triage"
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    database_url: str | None = None
    redis_url: str = "redis://redis:6379/0"
    max_request_bytes: int = 1048576
    log_level: str = "INFO"
    cache_ttl_seconds: int = 3600
    recent_incident_limit: int = 50
    startup_db_retries: int = 20
    startup_db_retry_delay_seconds: float = 1.5
    skip_startup_init: bool = False
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10
    db_pool_timeout_seconds: float = 5.0
    db_connect_timeout_seconds: int = 5
    triage_provider: str = "rules"
    triage_llm_endpoint: str | None = None
    triage_llm_api_key: str | None = None
    triage_llm_model: str = "incident-triage"
    triage_llm_timeout_seconds: float = 5.0
    triage_llm_retries: int = 2
    triage_llm_retry_delay_seconds: float = 0.25
    api_key_auth_enabled: bool = False
    api_key: str | None = None
    auth_protect_public_endpoints: bool = False
    rate_limit_enabled: bool = False
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60

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

    @field_validator(
        "db_pool_min_size",
        "db_pool_max_size",
        "db_pool_timeout_seconds",
        "db_connect_timeout_seconds",
        "triage_llm_timeout_seconds",
        "rate_limit_requests",
        "rate_limit_window_seconds",
    )
    @classmethod
    def _positive_limits(cls, value: int | float) -> int | float:
        """Require positive pool, timeout, and rate-limit settings."""
        if value <= 0:
            raise ValueError("must be greater than 0")
        return value

    @field_validator("triage_llm_retry_delay_seconds")
    @classmethod
    def _non_negative_seconds(cls, value: float) -> float:
        """Disallow negative timeout and retry-delay values."""
        if value < 0:
            raise ValueError("must not be negative")
        return value

    @field_validator("triage_llm_retries")
    @classmethod
    def _valid_llm_retries(cls, value: int) -> int:
        """Bound LLM retry counts to a small finite number."""
        if not 0 <= value <= 5:
            raise ValueError("must be between 0 and 5")
        return value

    @field_validator("triage_provider")
    @classmethod
    def _triage_provider(cls, value: str) -> str:
        """Accept only configured triage provider names."""
        provider = value.lower()
        if provider not in {"rules", "llm"}:
            raise ValueError("must be one of rules, llm")
        return provider

    @model_validator(mode="after")
    def _cross_field_settings(self) -> "Settings":
        """Validate settings that depend on more than one field."""
        if self.db_pool_max_size < self.db_pool_min_size:
            raise ValueError("DB_POOL_MAX_SIZE must be at least DB_POOL_MIN_SIZE")
        if self.api_key_auth_enabled and not self.api_key:
            raise ValueError("API_KEY is required when API_KEY_AUTH_ENABLED is true")
        if self.triage_provider == "llm" and not self.triage_llm_endpoint:
            raise ValueError("TRIAGE_LLM_ENDPOINT is required when TRIAGE_PROVIDER is llm")
        return self

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
            "Invalid configuration: "
            + "; ".join(f"{p['variable']} {p['problem']}" for p in problems),
            details={"errors": problems},
        ) from exc


def reset_settings_cache() -> None:
    """Clear cached settings (used by tests after changing the environment)."""
    _load_settings.cache_clear()
