# Security policy

## Reporting a vulnerability

Please do not report exploitable vulnerabilities in public issues. Use GitHub's private vulnerability reporting for this repository, or contact the maintainers privately through GitHub. Include affected versions, impact, and minimal reproduction details. Do not include real incident data or credentials.

## Deployment guidance

- Replace the local PostgreSQL credentials and use a secrets manager for `API_KEY` and `TRIAGE_LLM_API_KEY`.
- Enable `API_KEY_AUTH_ENABLED` when exposing incident operations; configure a high-entropy key. Health/readiness/metrics are unauthenticated by default and can be protected with `AUTH_PROTECT_PUBLIC_ENDPOINTS`.
- Put a shared rate limiter, TLS termination, and request filtering at a trusted reverse proxy. The optional app limiter is process-local and does not coordinate across workers.
- Keep `TRIAGE_PROVIDER=rules` unless an approved OpenAI-compatible endpoint is configured. Set a bounded timeout and retry count; model input includes incident text and recent incident context.
- Restrict `/metrics` at the network boundary if operational data should not be public. Configure the DB pool with the total worker count and PostgreSQL connection limit in mind.
- Keep `SKIP_STARTUP_INIT` disabled outside tests, run migrations before serving traffic, and do not expose PostgreSQL or Redis ports publicly.

## Automated checks

Pull requests run the unit/API suite, dependency audit, CodeQL analysis, and Trivy image scan. Report any scanner false positive with a concise rationale; do not suppress findings without review.
