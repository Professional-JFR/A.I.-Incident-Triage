import os

import pytest
from fastapi.testclient import TestClient

os.environ["SKIP_STARTUP_INIT"] = "1"

from app.main import app


@pytest.fixture
def client():
    """Provide an API test client without requiring external services."""
    with TestClient(app) as test_client:
        yield test_client
