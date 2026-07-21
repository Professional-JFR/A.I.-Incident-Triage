import os

from fastapi.testclient import TestClient

os.environ["SKIP_STARTUP_INIT"] = "1"

from app.main import app


client = TestClient(app)


def test_health_smoke() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_triage_smoke(monkeypatch) -> None:
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
