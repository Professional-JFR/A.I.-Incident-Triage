# Contributing

## Development setup

Use Python 3.11 or newer. Create a virtual environment, install project and development dependencies, and start the local services:

```bash
python -m venv .venv
source .venv/bin/activate
make install
cp .env.example .env
docker compose up -d postgres redis
```

For the API on the host, set `POSTGRES_HOST=localhost` and `REDIS_URL=redis://localhost:6379/0`, run `alembic upgrade head`, then `make run`. Keep `.env` local; do not commit real credentials.

## Tests and checks

```bash
make lint
make typecheck
make test
python -m app.evaluation
make audit
make docker-scan
```

Unit and API tests mock external storage and the optional model endpoint. `make test` enforces at least 80% coverage. For integration coverage, start PostgreSQL and Redis and run `POSTGRES_HOST=localhost REDIS_URL=redis://localhost:6379/0 pytest -m integration`.

`make codeql` dispatches the repository's CodeQL workflow and requires the GitHub CLI (`gh`) to be authenticated. `make docker-scan` requires Docker and Trivy.

## Change guidelines

- Preserve existing endpoint contracts unless a change explicitly requires an additive field/header.
- Keep providers and persistence behind service boundaries; add regression tests for failure paths and config changes.
- Never make a network call in a unit test. Inject or monkeypatch the LLM request callable.
- Add an Alembic migration for database changes and update `.env.example` plus configuration documentation together.
- Run the checks above, review `git diff`, and do not include secrets or generated artifacts.
