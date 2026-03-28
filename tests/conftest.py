import os
import pytest
from fastapi.testclient import TestClient

# Point at a throwaway DB before importing the app
os.environ["DB_PATH"] = ":memory:"

from app.main import app  # noqa: E402  (import after env var is set)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def vm(client):
    """A ready-to-use provisioned VM resource."""
    resp = client.post("/resources", json={
        "name": "test-vm",
        "type": "vm",
        "owner": "bob@example.com",
        "policy_tags": {"env": "test", "cost-centre": "eng"},
    })
    assert resp.status_code == 201
    return resp.json()
