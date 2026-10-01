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
Validates, triages, and stores an incident. Text is trimmed; whitespace-only values
are rejected. Maximum lengths are 200 characters for `title`, 5,000 for
`description`, and 100 each for optional `service` and `source`. Request bodies
are limited to 1 MiB by default.

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

### Error responses

Errors use FastAPI's JSON `detail` format. For example, invalid input returns
HTTP `422` with field-specific validation messages:

```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body", "title"],
      "msg": "Value error, must contain at least one non-whitespace character",
      "input": "   "
    }
  ]
}
```

Other expected responses:

| Status | Code / response | Meaning |
| --- | --- | --- |
| `404` | `{"detail":"Incident not found"}` | The incident ID does not exist. |
| `413` | `{"detail":{"code":"request_too_large","message":"Request body exceeds the configured size limit."}}` | The request body is larger than the configured limit. |
| `503` | `{"detail":{"code":"database_unavailable","message":"Incident storage is temporarily unavailable."}}` | PostgreSQL could not complete the operation. |

PostgreSQL is required to persist or retrieve incidents. Redis is only a cache:
when Redis reads or writes fail, reads fall back to PostgreSQL and successful
database writes are still returned. `/ready` reports `"redis":"degraded"` while
the database is available, and returns `503` only when PostgreSQL is unavailable.

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

Tests use mocked storage operations and do not require running PostgreSQL or Redis.

## Logging and deployment notes

The API writes one JSON log object per line. Set `LOG_LEVEL` to `DEBUG`, `INFO`,
`WARNING`, or `ERROR` to adjust verbosity; the default is `INFO`. Logs include
request outcomes, triage decisions, cache hits/misses, and storage failures.

The Docker image runs the API as a non-root `app` user. The Compose database
credentials are development defaults; replace them and avoid exposing database
ports when deploying outside a local development environment. `MAX_REQUEST_BYTES`
can be set to change the default 1 MiB request-body limit.

Rate limiting is intentionally placed at the reverse proxy or API gateway so it
can be shared across application workers. The following Nginx configuration is
commented out; enable and tune it in the proxy's `http` and `server` contexts
before exposing the service publicly:

```nginx
# http {
#     limit_req_zone $binary_remote_addr zone=triage:10m rate=10r/s;
#
#     server {
#         location = /incidents/triage {
#             limit_req zone=triage burst=20 nodelay;
#             proxy_pass http://api:8000;
#         }
#     }
# }
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
