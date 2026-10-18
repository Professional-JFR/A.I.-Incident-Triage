.PHONY: install lint typecheck format test run docker docker-build docker-scan audit codeql clean

install:
	python -m pip install -r requirements-dev.txt

lint:
	ruff check .

typecheck:
	mypy

format:
	ruff check --fix .
	ruff format .

test:
	pytest

run:
	uvicorn app.main:app --reload

docker:
	docker compose up --build

docker-build:
	docker build -t ai-incident-triage:local .

docker-scan: docker-build
	trivy image --severity HIGH,CRITICAL --exit-code 1 ai-incident-triage:local

audit:
	pip-audit -r requirements-dev.txt

codeql:
	gh workflow run codeql.yml --ref $$(git branch --show-current)

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
