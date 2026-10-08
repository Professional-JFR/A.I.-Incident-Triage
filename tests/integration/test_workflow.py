import concurrent.futures

import pytest

from app.config import reset_settings_cache
from app.persistence import database


def test_triage_persists_and_can_be_retrieved(api, sample_incident) -> None:
    created = api.post("/incidents/triage", json=sample_incident)
    assert created.status_code == 200
    incident_id = created.json()["incident_id"]

    fetched = api.get(f"/incidents/{incident_id}")

    assert fetched.status_code == 200
    assert fetched.json()["title"] == sample_incident["title"]
    assert database.fetch_incident(incident_id)["predicted_severity"] == "high"


def test_duplicate_is_detected_against_persisted_incident(api, sample_incident) -> None:
    first = api.post("/incidents/triage", json=sample_incident).json()
    second = api.post("/incidents/triage", json=sample_incident).json()

    assert second["duplicate_of"] == first["incident_id"]


def test_cache_hit_and_miss(api, redis_cache, sample_incident) -> None:
    incident_id = api.post("/incidents/triage", json=sample_incident).json()["incident_id"]
    assert redis_cache.get(f"incident:{incident_id}") is not None

    redis_cache.flushdb()
    assert api.get(f"/incidents/{incident_id}").status_code == 200  # miss -> database
    assert redis_cache.get(f"incident:{incident_id}") is not None  # repopulated


def test_cache_degradation_when_redis_unavailable(api, redis_down, sample_incident) -> None:
    created = api.post("/incidents/triage", json=sample_incident)
    assert created.status_code == 200
    assert api.get(f"/incidents/{created.json()['incident_id']}").status_code == 200
    ready = api.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["redis"] == "degraded"


def test_database_failure_and_recovery(api, sample_incident, monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_PORT", "1")
    reset_settings_cache()
    failed = api.post("/incidents/triage", json=sample_incident)
    assert failed.status_code == 503
    assert failed.json()["detail"]["code"] == "database_unavailable"
    assert api.get("/ready").status_code == 503

    monkeypatch.undo()
    reset_settings_cache()
    assert api.post("/incidents/triage", json=sample_incident).status_code == 200


def test_concurrent_ingestion(api, sample_incident) -> None:
    def submit(i: int) -> str:
        payload = {**sample_incident, "title": f"Concurrent incident {i}"}
        response = api.post("/incidents/triage", json=payload)
        assert response.status_code == 200
        return response.json()["incident_id"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(submit, range(20)))

    assert len(set(ids)) == 20
    with database.connect() as conn:
        assert conn.execute("SELECT count(*) FROM incidents").fetchone()[0] == 20


@pytest.mark.parametrize(
    "title,description",
    [
        ("t" * 200, "d" * 5000),
        ("Ünïcödé 支付 失败 🔥", "Привет мир — обслуживание недоступно"),
        ("Robert'); DROP TABLE incidents;--", "<script>alert(1)</script> \"quotes\" 'single'"),
    ],
)
def test_edge_case_payloads_round_trip(api, title, description) -> None:
    created = api.post("/incidents/triage", json={"title": title, "description": description})
    assert created.status_code == 200

    stored = api.get(f"/incidents/{created.json()['incident_id']}").json()

    assert stored["title"] == title
    assert stored["description"] == description


def test_over_length_title_is_rejected(api) -> None:
    response = api.post("/incidents/triage", json={"title": "t" * 201, "description": "d"})
    assert response.status_code == 422


def test_schema_is_available(pg_schema) -> None:
    with database.connect() as conn:
        assert conn.execute("SELECT to_regclass('public.incidents')").fetchone()[0]
