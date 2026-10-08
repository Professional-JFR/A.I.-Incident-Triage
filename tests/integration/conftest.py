"""Fixtures for tests against real PostgreSQL and Redis.

Run with ``pytest -m integration`` after starting both services
(e.g. ``docker compose up -d postgres redis``) and exporting POSTGRES_* / REDIS_URL
(use ``POSTGRES_HOST=localhost REDIS_URL=redis://localhost:6379/0`` for compose).
Tests are skipped when the services are unreachable.
"""

import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app.config import reset_settings_cache
from app.main import app
from app.persistence import cache, database

pytestmark = pytest.mark.integration


def pytest_collection_modifyitems(items):
    """Mark every test in this package as an integration test."""
    for item in items:
        if "tests/integration" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def pg_schema():
    """Create the schema in PostgreSQL (skip if unavailable) and drop it afterwards."""
    reset_settings_cache()
    if not database.ping():
        pytest.skip("PostgreSQL is not reachable")
    with database.connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                incident_id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
                service TEXT, source TEXT, occurred_at TIMESTAMPTZ,
                predicted_severity TEXT NOT NULL, duplicate_of TEXT,
                runbook_suggestion TEXT NOT NULL, status TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    yield


@pytest.fixture
def db(pg_schema):
    """Provide an empty incidents table for each test."""
    with database.connect() as conn:
        conn.execute("TRUNCATE incidents")
    yield
    with database.connect() as conn:
        conn.execute("TRUNCATE incidents")


@pytest.fixture
def redis_cache():
    """Provide a flushed Redis (skip if unavailable)."""
    reset_settings_cache()
    cache.get_client.cache_clear()
    if not cache.ping():
        pytest.skip("Redis is not reachable")
    cache.get_client().flushdb()
    yield cache.get_client()
    cache.get_client().flushdb()


@pytest.fixture
def api(db):
    """Test client wired to real PostgreSQL; Redis may be absent (degraded)."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def sample_incident():
    """Sample incident payload."""
    return {
        "title": "Payment API latency spike",
        "description": "Timeouts and degraded checkout responses",
        "service": "payments-api",
        "source": "monitoring",
    }


@pytest.fixture
def redis_down(monkeypatch):
    """Point the application at an unused Redis port to simulate an outage."""
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    reset_settings_cache()
    cache.get_client.cache_clear()
    yield
    monkeypatch.undo()
    reset_settings_cache()
    cache.get_client.cache_clear()
