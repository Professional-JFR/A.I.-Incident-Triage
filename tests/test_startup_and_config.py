import pytest
import psycopg

from app import main
from app.config import get_settings, reset_settings_cache
from app.exceptions import (
    CacheConnectionError,
    CacheException,
    ConfigurationError,
    DatabaseConnectionError,
    DatabaseException,
    StorageException,
    TriageException,
    ValidationError,
)
from app.persistence import cache, database
from app.resilience import CircuitBreaker, CircuitOpenError, is_transient_db_error, retry_with_backoff


@pytest.fixture
def env(monkeypatch):
    def setter(**values):
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        reset_settings_cache()

    yield setter
    monkeypatch.undo()
    reset_settings_cache()


def test_invalid_config_raises_configuration_error_without_leaking_values(env) -> None:
    env(POSTGRES_PORT="99999", LOG_LEVEL="loud", MAX_REQUEST_BYTES="0", REDIS_URL="http://x")

    with pytest.raises(ConfigurationError) as info:
        get_settings()

    assert "POSTGRES_PORT" in info.value.message
    assert "LOG_LEVEL" in info.value.message
    assert "99999" not in info.value.message


def test_dsn_built_from_parts_and_override(env) -> None:
    env(POSTGRES_HOST="h", POSTGRES_PORT="5432", POSTGRES_PASSWORD="p@ss")
    dsn = get_settings().db_dsn
    assert dsn.startswith("postgresql://postgres:p%40ss")
    assert dsn.endswith("@h:5432/incident_triage")
    env(DATABASE_URL="postgresql://example/db")
    assert get_settings().db_dsn == "postgresql://example/db"


def test_startup_fails_clearly_on_bad_config(env) -> None:
    env(SKIP_STARTUP_INIT="0", STARTUP_DB_RETRIES="0")
    with pytest.raises(ConfigurationError):
        main.run_startup_checks()


def test_startup_database_failure_is_reported(env, monkeypatch) -> None:
    env(SKIP_STARTUP_INIT="0")
    error = DatabaseConnectionError("nope", transient=False)
    monkeypatch.setattr(database, "wait_for_database", lambda: (_ for _ in ()).throw(error))
    with pytest.raises(DatabaseConnectionError):
        main.run_startup_checks()


def test_startup_requires_schema(env, monkeypatch) -> None:
    env(SKIP_STARTUP_INIT="0")
    monkeypatch.setattr(database, "wait_for_database", lambda: None)
    monkeypatch.setattr(database, "schema_ready", lambda: False)
    with pytest.raises(DatabaseConnectionError, match="alembic upgrade head"):
        main.run_startup_checks()


@pytest.mark.parametrize("redis_ok", [True, False])
def test_startup_tolerates_redis(env, monkeypatch, redis_ok) -> None:
    env(SKIP_STARTUP_INIT="0")
    monkeypatch.setattr(database, "wait_for_database", lambda: None)
    monkeypatch.setattr(database, "schema_ready", lambda: True)
    monkeypatch.setattr(cache, "ping", lambda: redis_ok)
    main.run_startup_checks()


def test_wait_for_database_permanent_failure(env, monkeypatch) -> None:
    env(STARTUP_DB_RETRIES="3", STARTUP_DB_RETRY_DELAY_SECONDS="0")
    calls = []

    def fail():
        calls.append(1)
        raise psycopg.OperationalError("password authentication failed for user")

    monkeypatch.setattr(database, "connect", fail)
    with pytest.raises(DatabaseConnectionError) as info:
        database.wait_for_database()
    assert info.value.transient is False
    assert len(calls) == 1


def test_wait_for_database_transient_exhausts_retries(env, monkeypatch) -> None:
    env(STARTUP_DB_RETRIES="3", STARTUP_DB_RETRY_DELAY_SECONDS="0")
    calls = []

    def fail():
        calls.append(1)
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(database, "connect", fail)
    with pytest.raises(DatabaseConnectionError) as info:
        database.wait_for_database()
    assert info.value.transient is True
    assert len(calls) == 3


def test_retry_backoff_delays_and_success() -> None:
    delays, attempts = [], []

    def flaky():
        attempts.append(1)
        if len(attempts) < 4:
            raise ValueError("boom")
        return "ok"

    result = retry_with_backoff(flaky, retries=5, base_delay_seconds=1, sleep=delays.append)
    assert result == "ok"
    assert delays == [1, 2, 4]


def test_retry_does_not_retry_permanent_errors() -> None:
    with pytest.raises(ValueError):
        retry_with_backoff(
            lambda: (_ for _ in ()).throw(ValueError()), retries=5, base_delay_seconds=0,
            is_transient=lambda exc: False, sleep=lambda s: None,
        )


def test_circuit_breaker_opens_and_recovers() -> None:
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout_seconds=0.05)

    def fail():
        raise RuntimeError

    for _ in range(2):
        with pytest.raises(RuntimeError):
            breaker.call(fail)
    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: 1)
    import time

    time.sleep(0.06)
    assert breaker.call(lambda: 1) == 1
    assert not breaker.is_open


def test_transient_classification() -> None:
    assert is_transient_db_error(psycopg.OperationalError("connection refused"))
    assert not is_transient_db_error(psycopg.OperationalError("password authentication failed"))
    assert not is_transient_db_error(psycopg.errors.UndefinedTable("x"))


def test_exception_hierarchy() -> None:
    assert issubclass(CacheException, StorageException)
    assert issubclass(DatabaseException, StorageException)
    assert issubclass(DatabaseConnectionError, DatabaseException)
    assert issubclass(CacheConnectionError, CacheException)
    assert ValidationError().status_code == 400
    assert StorageException().status_code == 503
    assert issubclass(ConfigurationError, TriageException)
