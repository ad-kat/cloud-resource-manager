import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db
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

# rough hourly rates — mirrors real AWS on-demand pricing at order-of-magnitude level
# keeping this here rather than in a config file for now, can always move it later
HOURLY_RATES = {
    "vm":       0.096,
    "bucket":   0.023,
    "function": 0.0000002,  # functions are priced per-invocation in reality but this works for demo
    "database": 0.115,
}


def _get_or_404(resource_id: str, db: Session) -> dict:
    row = db.execute(
        text("SELECT * FROM resources WHERE id = :id"),
        {"id": resource_id}
    ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resource '{resource_id}' not found."
        )
    r = dict(row._mapping)
    # policy_tags comes back as a string from sqlite (used in tests), parse it
    if isinstance(r.get("policy_tags"), str):
        r["policy_tags"] = json.loads(r["policy_tags"])
    return r


def _record_event(resource_id: str, action: str, actor: str, db: Session, detail: str = None):
    # fire-and-forget — audit failures shouldn't take down the main operation
    try:
        db.execute(
            text("""
                INSERT INTO events (id, resource_id, action, actor, detail, occurred_at)
                VALUES (:id, :rid, :action, :actor, :detail, :ts)
            """),
            {
                "id":     str(uuid.uuid4()),
                "rid":    resource_id,
                "action": action,
                "actor":  actor,
                "detail": detail,
                "ts":     datetime.now(timezone.utc),
            }
        )
        db.commit()
    except Exception:
        logger.exception("Failed to write audit event for %s", resource_id)


@router.post("/", response_model=ResourceResponse, status_code=status.HTTP_201_CREATED)
def provision_resource(body: ResourceCreate, db: Session = Depends(get_db)):
    missing = REQUIRED_TAGS - body.policy_tags.keys()
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Missing required policy tags: {sorted(missing)}",
        )

    resource_id = str(uuid.uuid4())
    now  = datetime.now(timezone.utc)
    rate = HOURLY_RATES.get(body.type.value, 0.0)

    db.execute(
        text("""
            INSERT INTO resources
                (id, name, type, status, owner, policy_tags,
                 region, cost_per_hr, ttl_hours, created_at, updated_at)
            VALUES
                (:id, :name, :type, :status, :owner, :tags,
                 :region, :rate, :ttl, :now, :now)
        """),
        {
            "id":     resource_id,
            "name":   body.name,
            "type":   body.type.value,
            "status": ResourceStatus.ACTIVE.value,
            "owner":  body.owner,
            "tags":   json.dumps(body.policy_tags),
            "region": body.region,
            "rate":   rate,
            "ttl":    body.ttl_hours,
            "now":    now,
        }
    )
    db.commit()

    _record_event(resource_id, "provisioned", body.owner, db)
    logger.info("Provisioned %s (type=%s owner=%s)", resource_id, body.type, body.owner)
    return ResourceResponse(**_get_or_404(resource_id, db))


@router.get("/", response_model=List[ResourceResponse])
def list_resources(
    type:   Optional[ResourceType]   = Query(default=None),
    status: Optional[ResourceStatus] = Query(default=None),
    owner:  Optional[str]            = Query(default=None),
    db:     Session                  = Depends(get_db),
):
    query  = "SELECT * FROM resources WHERE 1=1"
    params: dict = {}

    if type is not None:
        query += " AND type = :type"
        params["type"] = type.value
    if status is not None:
        query += " AND status = :status"
        params["status"] = status.value
    if owner is not None:
        query += " AND owner = :owner"
        params["owner"] = owner

    query += " ORDER BY created_at DESC"

    rows = db.execute(text(query), params).fetchall()
    result = []
    for row in rows:
        r = dict(row._mapping)
        if isinstance(r.get("policy_tags"), str):
            r["policy_tags"] = json.loads(r["policy_tags"])
        result.append(ResourceResponse(**r))
    return result


@router.get("/{resource_id}", response_model=ResourceResponse)
def get_resource(resource_id: str, db: Session = Depends(get_db)):
    return ResourceResponse(**_get_or_404(resource_id, db))


@router.patch("/{resource_id}/policy", response_model=ResourceResponse)
def update_policy(resource_id: str, body: PolicyUpdate, db: Session = Depends(get_db)):
    current = _get_or_404(resource_id, db)

    if current["status"] == ResourceStatus.DEPROVISIONED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot update a deprovisioned resource."
        )

    # if not provided, keep whatever is already stored
    new_tags   = json.dumps(body.policy_tags) if body.policy_tags is not None else json.dumps(current["policy_tags"])
    new_status = body.status.value if body.status is not None else current["status"]
    now = datetime.now(timezone.utc)

    db.execute(
        text("""
            UPDATE resources
            SET policy_tags = :tags, status = :status, updated_at = :now
            WHERE id = :id
        """),
        {"tags": new_tags, "status": new_status, "now": now, "id": resource_id}
    )
    db.commit()

    _record_event(resource_id, "policy_updated", "api", db, f"status={new_status}")
    return ResourceResponse(**_get_or_404(resource_id, db))


@router.delete("/{resource_id}", response_model=MessageResponse)
def deprovision_resource(resource_id: str, db: Session = Depends(get_db)):
    current = _get_or_404(resource_id, db)

    if current["status"] == ResourceStatus.DEPROVISIONED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Resource is already deprovisioned."
        )

    now = datetime.now(timezone.utc)
    db.execute(
        text("UPDATE resources SET status = :s, updated_at = :now WHERE id = :id"),
        {"s": ResourceStatus.DEPROVISIONED.value, "now": now, "id": resource_id}
    )
    db.commit()

    _record_event(resource_id, "deprovisioned", current["owner"], db)
    logger.info("Deprovisioned %s", resource_id)
    return MessageResponse(
        message=f"Resource '{current['name']}' has been deprovisioned.",
        resource_id=resource_id,
    )


@router.get("/{resource_id}/events")
def get_resource_events(resource_id: str, db: Session = Depends(get_db)):
    """Audit trail for a resource — all state changes in chronological order."""
    _get_or_404(resource_id, db)
    rows = db.execute(
        text("SELECT * FROM events WHERE resource_id = :id ORDER BY occurred_at ASC"),
        {"id": resource_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]