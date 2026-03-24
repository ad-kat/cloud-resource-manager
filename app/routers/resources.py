"""
Resource endpoints — the heart of the service.

Every function here maps to one HTTP endpoint.  FastAPI uses Python type
annotations to:
  - Deserialise & validate the incoming JSON body
  - Serialise the return value to JSON
  - Generate accurate OpenAPI documentation at /docs

Endpoint summary:
  POST   /resources                   → provision a new resource
  GET    /resources                   → list all (with optional filters)
  GET    /resources/{resource_id}     → get a single resource
  PATCH  /resources/{resource_id}/policy → update status / policy tags
  DELETE /resources/{resource_id}     → deprovision (soft-delete)
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.database import get_connection
from app.models import (
    MessageResponse,
    PolicyUpdate,
    ResourceCreate,
    ResourceResponse,
    ResourceStatus,
    ResourceType,
)

logger = logging.getLogger(__name__)

# APIRouter is like a Flask Blueprint — it groups related routes.
# main.py mounts this router under the /resources prefix.
router = APIRouter()


# ---------------------------------------------------------------------------
# Helper — fetch one resource row or raise 404
# ---------------------------------------------------------------------------

def _get_or_404(resource_id: str) -> dict:
    """
    Look up a resource by id.  Raises HTTPException(404) if not found.
    Returns a plain dict so callers don't have to think about sqlite3.Row.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM resources WHERE id = ?", (resource_id,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resource '{resource_id}' not found.",
        )
    return dict(row)


# ---------------------------------------------------------------------------
# POST /resources  — provision
# ---------------------------------------------------------------------------

@router.post(
    "/",
    response_model=ResourceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provision a new cloud resource",
    description=(
        "Creates a new resource record with status=active.  "
        "Simulates the provisioning step in a cloud control plane."
    ),
)
def provision_resource(body: ResourceCreate):
    """
    Steps:
    1. Generate a UUID for the new resource.
    2. Capture the current UTC time as created_at and updated_at.
    3. Serialise policy_tags dict → JSON string for SQLite storage.
    4. INSERT the row and return the full object.
    """
    resource_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    policy_tags_json = json.dumps(body.policy_tags)

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO resources
                (id, name, type, status, owner, policy_tags, region, created_at, updated_at)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resource_id,
                body.name,
                body.type.value,          # store the string value, not the enum
                ResourceStatus.ACTIVE.value,
                body.owner,
                policy_tags_json,
                body.region,
                now,
                now,
            ),
        )
        conn.commit()
        logger.info("Provisioned resource %s (type=%s, owner=%s)", resource_id, body.type, body.owner)
    finally:
        conn.close()

    # Fetch and return the freshly-created row
    return ResourceResponse(**_get_or_404(resource_id))


# ---------------------------------------------------------------------------
# GET /resources  — list (with optional filters)
# ---------------------------------------------------------------------------

@router.get(
    "/",
    response_model=List[ResourceResponse],
    summary="List all resources",
    description="Returns all resources, optionally filtered by type, status, or owner.",
)
def list_resources(
    # Query parameters — client adds them as ?type=vm&status=active etc.
    type:   Optional[ResourceType]   = Query(default=None, description="Filter by resource type"),
    status: Optional[ResourceStatus] = Query(default=None, description="Filter by lifecycle status"),
    owner:  Optional[str]            = Query(default=None, description="Filter by owner"),
):
    """
    Builds a dynamic WHERE clause from whichever filters the client provided.
    Using parameterised queries (?, ?) prevents SQL injection.
    """
    query  = "SELECT * FROM resources WHERE 1=1"  # 1=1 makes appending AND easy
    params: list = []

    if type is not None:
        query  += " AND type = ?"
        params.append(type.value)

    if status is not None:
        query  += " AND status = ?"
        params.append(status.value)

    if owner is not None:
        query  += " AND owner = ?"
        params.append(owner)

    query += " ORDER BY created_at DESC"

    conn = get_connection()
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    return [ResourceResponse(**dict(row)) for row in rows]


# ---------------------------------------------------------------------------
# GET /resources/{resource_id}  — get one
# ---------------------------------------------------------------------------

@router.get(
    "/{resource_id}",
    response_model=ResourceResponse,
    summary="Get a single resource",
)
def get_resource(resource_id: str):
    return ResourceResponse(**_get_or_404(resource_id))


# ---------------------------------------------------------------------------
# PATCH /resources/{resource_id}/policy  — update policy / status
# ---------------------------------------------------------------------------

@router.patch(
    "/{resource_id}/policy",
    response_model=ResourceResponse,
    summary="Update resource policy tags or status",
    description=(
        "Partial update: send only the fields you want to change.  "
        "Simulates a governance control-plane operation."
    ),
)
def update_policy(resource_id: str, body: PolicyUpdate):
    """
    1. Load current resource (→ 404 if missing).
    2. Merge any provided fields over the existing values.
    3. UPDATE the row with a new updated_at timestamp.
    4. Return the updated object.
    """
    current = _get_or_404(resource_id)

    # Guard: can't update a deprovisioned resource
    if current["status"] == ResourceStatus.DEPROVISIONED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot update a deprovisioned resource.",
        )

    # Decide new values — keep existing if client didn't send a replacement
    new_policy_tags = (
        json.dumps(body.policy_tags)
        if body.policy_tags is not None
        else current["policy_tags"]   # already a JSON string in the DB
    )
    new_status = body.status.value if body.status is not None else current["status"]
    now = datetime.now(timezone.utc)

    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE resources
               SET policy_tags = ?, status = ?, updated_at = ?
             WHERE id = ?
            """,
            (new_policy_tags, new_status, now, resource_id),
        )
        conn.commit()
        logger.info("Updated resource %s — status=%s", resource_id, new_status)
    finally:
        conn.close()

    return ResourceResponse(**_get_or_404(resource_id))


# ---------------------------------------------------------------------------
# DELETE /resources/{resource_id}  — deprovision (soft-delete)
# ---------------------------------------------------------------------------

@router.delete(
    "/{resource_id}",
    response_model=MessageResponse,
    summary="Deprovision a resource",
    description=(
        "Soft-deletes the resource by setting status=deprovisioned.  "
        "The record is kept for audit purposes — it is never hard-deleted."
    ),
)
def deprovision_resource(resource_id: str):
    """
    Real cloud platforms rarely hard-delete billing/audit records.
    We simulate this with a status transition to 'deprovisioned'.
    """
    current = _get_or_404(resource_id)

    if current["status"] == ResourceStatus.DEPROVISIONED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Resource is already deprovisioned.",
        )

    now = datetime.now(timezone.utc)

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE resources SET status = ?, updated_at = ? WHERE id = ?",
            (ResourceStatus.DEPROVISIONED.value, now, resource_id),
        )
        conn.commit()
        logger.info("Deprovisioned resource %s", resource_id)
    finally:
        conn.close()

    return MessageResponse(
        message=f"Resource '{current['name']}' has been deprovisioned.",
        resource_id=resource_id,
    )
