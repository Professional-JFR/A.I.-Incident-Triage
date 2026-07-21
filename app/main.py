import os

from fastapi import FastAPI, HTTPException, status

from app.schemas import IncidentIn, TriageOut
from app.storage import (
    create_incident_record,
    fetch_recent_incidents,
    get_incident,
    init_storage,
    ping_db,
    ping_redis,
)
from app.triage import find_duplicate, predict_severity, suggest_runbook

app = FastAPI(title="A.I. Incident Triage Copilot", version="0.1.0")


@app.on_event("startup")
def startup_event() -> None:
    if os.getenv("SKIP_STARTUP_INIT", "").lower() in {"1", "true", "yes"}:
        return
    init_storage()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> tuple[dict, int] | dict:
    db_ok = ping_db()
    redis_ok = ping_redis()
    if db_ok and redis_ok:
        return {"status": "ready", "database": "ok", "redis": "ok"}

    detail = {
        "status": "not_ready",
        "database": "ok" if db_ok else "error",
        "redis": "ok" if redis_ok else "error",
    }
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


@app.post("/incidents/triage", response_model=TriageOut)
def triage_incident(payload: IncidentIn) -> TriageOut:
    recent = fetch_recent_incidents(limit=50)

    predicted_severity = predict_severity(payload.title, payload.description)
    duplicate_of = find_duplicate(payload.title, payload.description, recent)
    runbook = suggest_runbook(payload.title, payload.description, payload.service)

    record = create_incident_record(
        title=payload.title,
        description=payload.description,
        service=payload.service,
        source=payload.source,
        occurred_at=payload.timestamp,
        predicted_severity=predicted_severity,
        duplicate_of=duplicate_of,
        runbook_suggestion=runbook,
    )

    return TriageOut(
        incident_id=record["incident_id"],
        predicted_severity=record["predicted_severity"],
        duplicate_of=record["duplicate_of"],
        runbook_suggestion=record["runbook_suggestion"],
        status=record["status"],
    )


@app.get("/incidents/{incident_id}")
def read_incident(incident_id: str) -> dict:
    record = get_incident(incident_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return record
