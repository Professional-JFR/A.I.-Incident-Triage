.PHONY: install lint typecheck format test run docker clean

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

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
