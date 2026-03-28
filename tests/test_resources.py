"""
Tests for the /resources endpoints.

Covers happy paths, validation errors, and lifecycle conflict guards.
SQLite :memory: is used so each test run starts with a clean slate.
"""

import pytest


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------

def test_provision_returns_201(client):
    resp = client.post("/resources", json={
        "name": "web-server",
        "type": "vm",
        "owner": "alice@example.com",
        "policy_tags": {"env": "prod", "cost-centre": "eng"},
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "active"
    assert data["type"] == "vm"


def test_provision_missing_required_tags_returns_422(client):
    # Policy requires 'env' and 'cost-centre' — omitting them should be rejected
    resp = client.post("/resources", json={
        "name": "untagged",
        "type": "bucket",
        "owner": "alice@example.com",
        "policy_tags": {},
    })
    assert resp.status_code == 422
    assert "env" in resp.json()["detail"] or "cost-centre" in resp.json()["detail"]


def test_provision_database_type(client):
    resp = client.post("/resources", json={
        "name": "main-db",
        "type": "database",
        "owner": "alice@example.com",
        "policy_tags": {"env": "prod", "cost-centre": "eng"},
    })
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Listing + filtering
# ---------------------------------------------------------------------------

def test_list_all(client, vm):
    resp = client.get("/resources")
    assert resp.status_code == 200
    assert any(r["id"] == vm["id"] for r in resp.json())


def test_filter_by_type(client, vm):
    resp = client.get("/resources?type=vm")
    assert all(r["type"] == "vm" for r in resp.json())


def test_filter_by_owner(client, vm):
    resp = client.get(f"/resources?owner={vm['owner']}")
    assert all(r["owner"] == vm["owner"] for r in resp.json())


# ---------------------------------------------------------------------------
# Get single
# ---------------------------------------------------------------------------

def test_get_existing(client, vm):
    resp = client.get(f"/resources/{vm['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == vm["id"]


def test_get_missing_returns_404(client):
    resp = client.get("/resources/does-not-exist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Policy update
# ---------------------------------------------------------------------------

def test_update_policy_tags(client, vm):
    resp = client.patch(f"/resources/{vm['id']}/policy", json={
        "policy_tags": {"env": "staging", "cost-centre": "eng", "reviewed": "true"},
    })
    assert resp.status_code == 200
    assert resp.json()["policy_tags"]["env"] == "staging"


def test_stop_resource(client, vm):
    resp = client.patch(f"/resources/{vm['id']}/policy", json={"status": "stopped"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "stopped"


def test_update_deprovisioned_resource_returns_409(client, vm):
    client.delete(f"/resources/{vm['id']}")
    resp = client.patch(f"/resources/{vm['id']}/policy", json={"status": "stopped"})
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Deprovision
# ---------------------------------------------------------------------------

def test_deprovision(client, vm):
    resp = client.delete(f"/resources/{vm['id']}")
    assert resp.status_code == 200
    # Record is kept; status is now deprovisioned
    assert client.get(f"/resources/{vm['id']}").json()["status"] == "deprovisioned"


def test_deprovision_twice_returns_409(client, vm):
    client.delete(f"/resources/{vm['id']}")
    resp = client.delete(f"/resources/{vm['id']}")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

def test_events_recorded_after_lifecycle(client, vm):
    rid = vm["id"]
    client.patch(f"/resources/{rid}/policy", json={"status": "stopped"})
    client.delete(f"/resources/{rid}")

    events = client.get(f"/resources/{rid}/events").json()
    actions = [e["action"] for e in events]
    assert "provisioned" in actions
    assert "deprovisioned" in actions
