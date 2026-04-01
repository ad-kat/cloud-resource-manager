import os
import pytest
from fastapi.testclient import TestClient

# must be set before any app imports — database.py reads this at module load
os.environ["DATABASE_URL"] = "sqlite:///./test_resources.db"

from app.main import app   # noqa: E402
from app.database import engine, init_db
from sqlalchemy import text


@pytest.fixture(autouse=True)
def clean_db():
    # drop and recreate tables before each test rather than deleting the file —
    # deleting the file while sqlalchemy holds an open connection causes the
    # "readonly database" error we kept hitting
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS budget_alerts"))
        conn.execute(text("DROP TABLE IF EXISTS events"))
        conn.execute(text("DROP TABLE IF EXISTS resources"))
        conn.commit()

    init_db()
    yield
    # nothing to do on teardown — next test's setup drops everything


@pytest.fixture()
def client(clean_db):
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def vm(client):
    """A ready-to-use provisioned VM resource."""
    resp = client.post("/resources", json={
        "name":        "test-vm",
        "type":        "vm",
        "owner":       "bob@example.com",
        "policy_tags": {"env": "test", "cost-centre": "eng"},
    })
    assert resp.status_code == 201
    return resp.json()
