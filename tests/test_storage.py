from unittest.mock import MagicMock

import redis

from app import storage


def test_cache_read_failure_falls_back_to_database(monkeypatch) -> None:
    expected = (
        "INC-123",
        "Payment outage",
        "Checkout is unavailable",
        "payments",
        "monitoring",
        None,
        "critical",
        None,
        "RB-002",
        "triaged",
    )
    connection = MagicMock()
    cursor = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchone.return_value = expected
    monkeypatch.setattr(storage.redis_client, "get", MagicMock(side_effect=redis.ConnectionError))
    monkeypatch.setattr(storage, "_db_connect", lambda: connection)
    monkeypatch.setattr(storage, "_cache_incident", lambda record: None)

    record = storage.get_incident("INC-123")

    assert record["incident_id"] == "INC-123"
    assert record["predicted_severity"] == "critical"


def test_invalid_cache_entry_falls_back_to_database(monkeypatch) -> None:
    connection = MagicMock()
    cursor = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchone.return_value = (
        "INC-123",
        "Payment outage",
        "Checkout is unavailable",
        "payments",
        "monitoring",
        None,
        "critical",
        None,
        "RB-002",
        "triaged",
    )
    monkeypatch.setattr(storage.redis_client, "get", lambda key: '{"wrong":"incident"}')
    monkeypatch.setattr(storage, "_db_connect", lambda: connection)
    monkeypatch.setattr(storage, "_cache_incident", lambda record: None)

    record = storage.get_incident("INC-123")

    assert record["incident_id"] == "INC-123"


def test_cache_write_failure_does_not_raise(monkeypatch) -> None:
    monkeypatch.setattr(
        storage.redis_client,
        "setex",
        MagicMock(side_effect=redis.ConnectionError),
    )

    storage._cache_incident({"incident_id": "INC-123"})


def test_incident_write_is_persisted_when_cache_is_unavailable(monkeypatch) -> None:
    connection = MagicMock()
    cursor = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    monkeypatch.setattr(storage, "_db_connect", lambda: connection)
    monkeypatch.setattr(
        storage.redis_client,
        "setex",
        MagicMock(side_effect=redis.ConnectionError),
    )

    record = storage.create_incident_record(
        title="Payment outage",
        description="Checkout is unavailable",
        service="payments",
        source="monitoring",
        occurred_at=None,
        predicted_severity="critical",
        duplicate_of=None,
        runbook_suggestion="RB-002",
    )

    assert record["status"] == "triaged"
    cursor.execute.assert_called_once()
    connection.commit.assert_called_once()
