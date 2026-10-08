from unittest.mock import MagicMock

import psycopg
import pytest
import redis

from app.persistence import cache, database
from app.services import incident_service

ROW = (
    "INC-123", "Payment outage", "Checkout is unavailable", "payments", "monitoring",
    None, "critical", None, "RB-002", "triaged",
)


def _mock_connection(row=ROW):
    connection = MagicMock()
    cursor = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchone.return_value = row
    cursor.fetchall.return_value = [("INC-1", "t", "d")]
    return connection


@pytest.fixture
def fake_redis(monkeypatch):
    client = MagicMock()
    client.get.return_value = None
    monkeypatch.setattr(cache, "get_client", lambda: client)
    return client


def test_cache_read_failure_falls_back_to_database(monkeypatch, fake_redis) -> None:
    fake_redis.get.side_effect = redis.ConnectionError
    monkeypatch.setattr(database, "connect", lambda: _mock_connection())

    record = incident_service.get_incident("INC-123")

    assert record["incident_id"] == "INC-123"
    assert record["predicted_severity"] == "critical"


@pytest.mark.parametrize("cached", ['{"wrong":"incident"}', "not-json"])
def test_invalid_cache_entry_falls_back_to_database(monkeypatch, fake_redis, cached) -> None:
    fake_redis.get.return_value = cached
    monkeypatch.setattr(database, "connect", lambda: _mock_connection())

    assert incident_service.get_incident("INC-123")["incident_id"] == "INC-123"


def test_cache_hit_skips_database(monkeypatch, fake_redis) -> None:
    fake_redis.get.return_value = '{"incident_id": "INC-123"}'
    monkeypatch.setattr(database, "connect", MagicMock(side_effect=AssertionError))

    assert incident_service.get_incident("INC-123") == {"incident_id": "INC-123"}


def test_missing_incident_returns_none(monkeypatch, fake_redis) -> None:
    monkeypatch.setattr(database, "connect", lambda: _mock_connection(row=None))

    assert incident_service.get_incident("INC-NOPE") is None


def test_database_lookup_failure_is_raised(monkeypatch, fake_redis) -> None:
    monkeypatch.setattr(database, "connect", MagicMock(side_effect=psycopg.OperationalError("down")))

    with pytest.raises(psycopg.Error):
        incident_service.get_incident("INC-123")


def test_cache_write_failure_does_not_raise(fake_redis) -> None:
    fake_redis.setex.side_effect = redis.ConnectionError

    cache.cache_incident({"incident_id": "INC-123"})


def test_incident_write_is_persisted_when_cache_is_unavailable(monkeypatch, fake_redis) -> None:
    connection = _mock_connection()
    monkeypatch.setattr(database, "connect", lambda: connection)
    fake_redis.setex.side_effect = redis.ConnectionError

    record = incident_service.create_incident_record(
        title="Payment outage", description="Checkout is unavailable", service="payments",
        source="monitoring", occurred_at=None, predicted_severity="critical",
        duplicate_of=None, runbook_suggestion="RB-002",
    )

    assert record["status"] == "triaged"
    connection.commit.assert_called_once()


def test_incident_write_failure_is_raised(monkeypatch, fake_redis) -> None:
    monkeypatch.setattr(database, "connect", MagicMock(side_effect=psycopg.OperationalError("down")))

    with pytest.raises(psycopg.Error):
        incident_service.create_incident_record(
            title="t", description="d", service=None, source=None, occurred_at=None,
            predicted_severity="low", duplicate_of=None, runbook_suggestion="RB-000",
        )


def test_fetch_recent_incidents(monkeypatch) -> None:
    monkeypatch.setattr(database, "connect", lambda: _mock_connection())

    assert incident_service.fetch_recent_incidents(5) == [
        {"incident_id": "INC-1", "title": "t", "description": "d"}
    ]


def test_fetch_recent_failure_is_raised(monkeypatch) -> None:
    monkeypatch.setattr(database, "connect", MagicMock(side_effect=psycopg.OperationalError("down")))

    with pytest.raises(psycopg.Error):
        incident_service.fetch_recent_incidents()


def test_pings(monkeypatch, fake_redis) -> None:
    monkeypatch.setattr(database, "connect", lambda: _mock_connection())
    assert database.ping() is True
    fake_redis.ping.return_value = True
    assert cache.ping() is True

    monkeypatch.setattr(database, "connect", MagicMock(side_effect=psycopg.OperationalError))
    fake_redis.ping.side_effect = redis.ConnectionError
    assert database.ping() is False
    assert cache.ping() is False
