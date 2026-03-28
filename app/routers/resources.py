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
router = APIRouter()

REQUIRED_TAGS = {"env", "cost-centre"}


def _get_or_404(resource_id: str) -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM resources WHERE id = ?", (resource_id,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Resource '{resource_id}' not found.")
    return dict(row)


def _record_event(resource_id: str, action: str, actor: str, detail: str = None):
    # Fire-and-forget audit entry; failures here should not break the main operation
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO events (id, resource_id, action, actor, detail, occurred_at) VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), resource_id, action, actor, detail, datetime.now(timezone.utc)),
        )
        conn.commit()
        conn.close()
    except Exception:
        logger.exception("Failed to write audit event for %s", resource_id)


@router.post("/", response_model=ResourceResponse, status_code=status.HTTP_201_CREATED)
def provision_resource(body: ResourceCreate):
    missing = REQUIRED_TAGS - body.policy_tags.keys()
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Missing required policy tags: {sorted(missing)}",
        )

    resource_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO resources
                (id, name, type, status, owner, policy_tags, region, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (resource_id, body.name, body.type.value, ResourceStatus.ACTIVE.value,
             body.owner, json.dumps(body.policy_tags), body.region, now, now),
        )
        conn.commit()
    finally:
        conn.close()

    _record_event(resource_id, "provisioned", body.owner)
    logger.info("Provisioned %s (type=%s owner=%s)", resource_id, body.type, body.owner)
    return ResourceResponse(**_get_or_404(resource_id))


@router.get("/", response_model=List[ResourceResponse])
def list_resources(
    type:   Optional[ResourceType]   = Query(default=None),
    status: Optional[ResourceStatus] = Query(default=None),
    owner:  Optional[str]            = Query(default=None),
):
    query  = "SELECT * FROM resources WHERE 1=1"
    params: list = []

    if type is not None:
        query += " AND type = ?"
        params.append(type.value)
    if status is not None:
        query += " AND status = ?"
        params.append(status.value)
    if owner is not None:
        query += " AND owner = ?"
        params.append(owner)

    query += " ORDER BY created_at DESC"

    conn = get_connection()
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    return [ResourceResponse(**dict(row)) for row in rows]


@router.get("/{resource_id}", response_model=ResourceResponse)
def get_resource(resource_id: str):
    return ResourceResponse(**_get_or_404(resource_id))


@router.patch("/{resource_id}/policy", response_model=ResourceResponse)
def update_policy(resource_id: str, body: PolicyUpdate):
    current = _get_or_404(resource_id)

    if current["status"] == ResourceStatus.DEPROVISIONED.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Cannot update a deprovisioned resource.")

    new_tags = json.dumps(body.policy_tags) if body.policy_tags is not None else current["policy_tags"]
    new_status = body.status.value if body.status is not None else current["status"]
    now = datetime.now(timezone.utc)

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE resources SET policy_tags = ?, status = ?, updated_at = ? WHERE id = ?",
            (new_tags, new_status, now, resource_id),
        )
        conn.commit()
    finally:
        conn.close()

    _record_event(resource_id, "policy_updated", "api", f"status={new_status}")
    return ResourceResponse(**_get_or_404(resource_id))


@router.delete("/{resource_id}", response_model=MessageResponse)
def deprovision_resource(resource_id: str):
    current = _get_or_404(resource_id)

    if current["status"] == ResourceStatus.DEPROVISIONED.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Resource is already deprovisioned.")

    now = datetime.now(timezone.utc)
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE resources SET status = ?, updated_at = ? WHERE id = ?",
            (ResourceStatus.DEPROVISIONED.value, now, resource_id),
        )
        conn.commit()
    finally:
        conn.close()

    _record_event(resource_id, "deprovisioned", current["owner"])
    logger.info("Deprovisioned %s", resource_id)
    return MessageResponse(
        message=f"Resource '{current['name']}' has been deprovisioned.",
        resource_id=resource_id,
    )


@router.get("/{resource_id}/events")
def get_resource_events(resource_id: str):
    """Audit trail for a resource — all state changes in chronological order."""
    _get_or_404(resource_id)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM events WHERE resource_id = ? ORDER BY occurred_at ASC",
            (resource_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]