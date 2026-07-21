# A.I. Incident Triage Copilot

An **A.I.-assisted incident triage backend** that analyzes incident text, predicts severity, suggests likely duplicates, and recommends a runbook.  
Built for practical backend + AI workflow demonstration using **Python/FastAPI**, **PostgreSQL**, **Redis**, and **Docker**.

## Tech Stack
- **Python**
- **FastAPI**
- **PostgreSQL**
- **Redis**
- **Docker / Docker Compose**
- **Pytest**

## Features
- **Health & readiness checks** for operational validation
- **Incident triage endpoint** with A.I.-style decision support
- **Severity prediction** from incident title/description
- **Duplicate detection** against recent incidents
- **Runbook recommendation** based on incident context
- **Persistent incident storage** + retrieval by `incident_id`

## API Endpoints

### `GET /health`
Liveness check.

**Example response**
```json
{"status":"ok"}
```

### `GET /ready`
Readiness check for API dependencies.

**Example response**
```json
{"status":"ready","database":"ok","redis":"ok"}
```

### `POST /incidents/triage`
Triages an incident and stores result.

**Request body**
```json
{
  "title": "Payment API latency spike",
  "description": "Timeouts and degraded checkout responses",
  "service": "payments-api",
  "source": "monitoring"
}
```

**Example response**
```json
{
  "incident_id": "INC-3003131B",
  "predicted_severity": "high",
  "duplicate_of": "INC-7D962A80",
  "runbook_suggestion": "RB-002: Validate payment gateway and API error rates",
  "status": "triaged"
}
```

### `GET /incidents/{incident_id}`
Returns stored incident data + triage output.

---

## Quick Start

### 1) Clone
```bash
git clone https://github.com/Professional-JFR/A.I.-Incident-Triage.git
cd A.I.-Incident-Triage
```

### 2) Run with Docker
```bash
docker compose up --build -d
```

### 3) Validate service
```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

---

## PowerShell Demo Commands (Windows)

```powershell
$body = @{
  title       = "Payment API latency spike"
  description = "Timeouts and degraded checkout responses"
  service     = "payments-api"
  source      = "monitoring"
} | ConvertTo-Json

$response = Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8000/incidents/triage" `
  -ContentType "application/json" `
  -Body $body

$response

Invoke-RestMethod -Method Get -Uri "http://localhost:8000/incidents/$($response.incident_id)"
```

---

## Results & Metrics (Baseline Demo)

- **Service availability checks:** **2/2 successful**
  - `GET /health` → `{"status":"ok"}`
  - `GET /ready` → `{"status":"ready","database":"ok","redis":"ok"}`
- **Triage workflow success rate:** **100%** for demonstrated run
- **Incident retrieval success rate:** **100%** for demonstrated run
- **A.I. output coverage:** **3/3 fields produced**
  - `predicted_severity`, `duplicate_of`, `runbook_suggestion`
- **End-to-end flow completion:** **1/1 successful**
  - ingest → triage → persist → retrieve

These baseline metrics validate functional correctness of the **A.I.-assisted triage pipeline** in a local containerized environment.

---

## Demo Evidence

Add your screenshots under:
<img width="937" height="95" alt="AI Triage Screenshot 1" src="https://github.com/user-attachments/assets/ed34d60f-bc36-46e7-82be-2ab178aa8790" />
<img width="1012" height="457" alt="AI Triage Screenshot 2" src="https://github.com/user-attachments/assets/1de3ad86-c9ff-4cb0-a8f8-186fec68dccc" />
<img width="1602" height="307" alt="AI Triage Screenshot 3" src="https://github.com/user-attachments/assets/21df9004-396d-422f-b758-3593755a7eb7" />
<img width="1565" height="897" alt="AI Triage Screenshot 4" src="https://github.com/user-attachments/assets/d7453bd8-deb5-4c2c-b759-1785be56e562" />

---

## Testing

```bash
pytest -q
```

---

## Current Scope / Limitations
- Uses **baseline rule-based AI logic** (intentionally simple, transparent, and demo-friendly)
- Not yet optimized for high-throughput production scale
- No advanced model training/evaluation pipeline yet

---

## Project Goal
Show a practical, end-to-end backend project that combines **API engineering**, **data persistence**, and **A.I.-assisted incident decision support** in a clean, reproducible setup.

## Future Improvements

- Replace baseline rule-based triage with a trained NLP/LLM-assisted classification pipeline for higher severity/duplicate accuracy.
- Add authentication and role-based access control (RBAC) for secure incident operations.
- Introduce async queue-based processing (e.g., Celery/RQ) for higher-throughput triage workloads.
- Add observability stack (structured logs, metrics, tracing, dashboards, alerts) for production monitoring.
- Expand test coverage with integration/load tests and benchmark-driven performance targets.
