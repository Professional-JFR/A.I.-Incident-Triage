def test_health_smoke(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"


def test_triage_smoke(client, monkeypatch) -> None:
    monkeypatch.setattr("app.services.incident_service.fetch_recent_incidents", lambda limit=50: [])

    def fake_create_incident_record(**kwargs):
        return {
            "incident_id": "INC-TEST1234",
            "predicted_severity": kwargs["predicted_severity"],
            "duplicate_of": kwargs["duplicate_of"],
            "runbook_suggestion": kwargs["runbook_suggestion"],
            "status": "triaged",
        }

    monkeypatch.setattr("app.services.incident_service.create_incident_record", fake_create_incident_record)

    payload = {
        "title": "Payment API latency spike",
        "description": "Timeout and degraded response time on checkout",
        "service": "payments-api",
    }

    response = client.post("/incidents/triage", json=payload)
    assert response.status_code == 200

    body = response.json()
    assert body["incident_id"] == "INC-TEST1234"
    assert body["status"] == "triaged"
    assert body["predicted_severity"] in {"low", "medium", "high", "critical"}


def test_ready_when_optional_cache_is_unavailable(client, monkeypatch) -> None:
    monkeypatch.setattr("app.persistence.database.ping", lambda: True)
    monkeypatch.setattr("app.persistence.cache.ping", lambda: False)

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok", "redis": "degraded"}


def test_database_failure_has_stable_error_response(client, monkeypatch) -> None:
    import psycopg

    monkeypatch.setattr("app.services.incident_service.fetch_recent_incidents", lambda limit=50: [])
    monkeypatch.setattr(
        "app.services.incident_service.create_incident_record",
        lambda **kwargs: (_ for _ in ()).throw(psycopg.OperationalError("database unavailable")),
    )

    response = client.post(
        "/incidents/triage",
        json={"title": "Payment failure", "description": "Checkout request failed"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "database_unavailable"


def test_oversized_request_is_rejected(client) -> None:
    from app.config import get_settings

    MAX_REQUEST_BYTES = get_settings().max_request_bytes

    response = client.post(
        "/incidents/triage",
        json={
            "title": "oversized",
            "description": "x" * MAX_REQUEST_BYTES,
        },
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "request_too_large"


def test_missing_incident_returns_not_found(client, monkeypatch) -> None:
    monkeypatch.setattr("app.services.incident_service.get_incident", lambda incident_id: None)

    response = client.get("/incidents/INC-MISSING")

    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "not_found", "message": "Incident not found"}}


def test_correlation_id_is_generated_and_echoed(client) -> None:
    generated = client.get("/health").headers["x-request-id"]
    assert len(generated) == 32

    echoed = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["x-request-id"] == "abc-123"

    replaced = client.get("/health", headers={"X-Request-ID": "bad id\twith spaces"})
    assert replaced.headers["x-request-id"] != "bad id\twith spaces"


def test_metrics_endpoint_exposes_application_metrics(client, monkeypatch) -> None:
    monkeypatch.setattr("app.services.incident_service.fetch_recent_incidents", lambda limit=50: [])
    monkeypatch.setattr(
        "app.services.incident_service.create_incident_record",
        lambda **kw: {
            "incident_id": "INC-1", "predicted_severity": kw["predicted_severity"],
            "duplicate_of": None, "runbook_suggestion": kw["runbook_suggestion"], "status": "triaged",
        },
    )
    client.post("/incidents/triage", json={"title": "Total outage", "description": "everything is down"})
    client.post("/incidents/triage", json={"title": "  ", "description": "x"})

    body = client.get("/metrics").text

    assert 'triage_decisions_total{severity="critical"}' in body
    assert 'validation_errors_total{field="title"}' in body
    assert "http_request_duration_seconds_bucket" in body


def test_ready_fails_when_database_is_down(client, monkeypatch) -> None:
    monkeypatch.setattr("app.persistence.database.ping", lambda: False)
    monkeypatch.setattr("app.persistence.cache.ping", lambda: True)

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == {"status": "not_ready", "database": "error", "redis": "ok"}


def test_domain_exception_returns_structured_error(client, monkeypatch) -> None:
    from app.exceptions import CacheException

    def boom(limit=50):
        raise CacheException(details={"x": 1})

    monkeypatch.setattr("app.services.incident_service.fetch_recent_incidents", boom)

    response = client.post("/incidents/triage", json={"title": "t", "description": "d"})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cache_unavailable"


def test_get_incident_uses_detail_model(client, monkeypatch) -> None:
    record = {
        "incident_id": "INC-1", "title": "t", "description": "d", "service": None, "source": None,
        "timestamp": None, "predicted_severity": "low", "duplicate_of": None,
        "runbook_suggestion": "RB-000", "status": "triaged",
    }
    monkeypatch.setattr("app.services.incident_service.get_incident", lambda incident_id: record)

    assert client.get("/incidents/INC-1").json() == record
    assert "IncidentDetailOut" in client.get("/openapi.json").text
