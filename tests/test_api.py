def test_health_smoke(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"


def test_triage_smoke(client, monkeypatch) -> None:
    monkeypatch.setattr("app.main.fetch_recent_incidents", lambda limit=50: [])

    def fake_create_incident_record(**kwargs):
        return {
            "incident_id": "INC-TEST1234",
            "predicted_severity": kwargs["predicted_severity"],
            "duplicate_of": kwargs["duplicate_of"],
            "runbook_suggestion": kwargs["runbook_suggestion"],
            "status": "triaged",
        }

    monkeypatch.setattr("app.main.create_incident_record", fake_create_incident_record)

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
    monkeypatch.setattr("app.main.ping_db", lambda: True)
    monkeypatch.setattr("app.main.ping_redis", lambda: False)

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok", "redis": "degraded"}


def test_database_failure_has_stable_error_response(client, monkeypatch) -> None:
    import psycopg

    monkeypatch.setattr("app.main.fetch_recent_incidents", lambda limit=50: [])
    monkeypatch.setattr(
        "app.main.create_incident_record",
        lambda **kwargs: (_ for _ in ()).throw(psycopg.OperationalError("database unavailable")),
    )

    response = client.post(
        "/incidents/triage",
        json={"title": "Payment failure", "description": "Checkout request failed"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "database_unavailable"


def test_oversized_request_is_rejected(client) -> None:
    from app.main import MAX_REQUEST_BYTES

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
    monkeypatch.setattr("app.main.get_incident", lambda incident_id: None)

    response = client.get("/incidents/INC-MISSING")

    assert response.status_code == 404
    assert response.json() == {"detail": "Incident not found"}
