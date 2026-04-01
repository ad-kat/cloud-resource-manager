import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

# standard billing month — AWS/GCP both use 730h
HOURS_PER_MONTH = 730


def _active_hours(created_at: datetime, updated_at: datetime, res_status: str) -> float:
    """
    How many hours has this resource been billable?
    Once stopped/deprovisioned the clock stops — we use updated_at as the end time.
    """
    if res_status in ("deprovisioned", "stopped"):
        end = updated_at
    else:
        end = datetime.now(timezone.utc)

    # sqlite returns naive datetimes, postgres returns tz-aware — handle both
    start = created_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    delta = end - start
    return max(delta.total_seconds() / 3600, 0)


@router.get("/estimate/{resource_id}")
def estimate_resource_cost(resource_id: str, db: Session = Depends(get_db)):
    """
    Returns actual cost accrued so far + projected monthly cost for one resource.
    """
    row = db.execute(
        text("SELECT * FROM resources WHERE id = :id"),
        {"id": resource_id}
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Resource '{resource_id}' not found.")

    r = dict(row._mapping)
    hours_alive      = _active_hours(r["created_at"], r["updated_at"], r["status"])
    cost_so_far      = round(float(r["cost_per_hr"]) * hours_alive, 4)
    projected_monthly = round(float(r["cost_per_hr"]) * HOURS_PER_MONTH, 2)

    return {
        "resource_id":       resource_id,
        "name":              r["name"],
        "type":              r["type"],
        "status":            r["status"],
        "cost_per_hr":       float(r["cost_per_hr"]),
        "hours_alive":       round(hours_alive, 2),
        "cost_accrued_usd":  cost_so_far,
        "projected_monthly": projected_monthly,
        "currency":          "USD",
    }


@router.get("/by-cost-centre")
def cost_by_cost_centre(
    cost_centre: Optional[str] = Query(default=None, description="Filter to a specific cost-centre tag"),
    db: Session = Depends(get_db),
):
    """
    Aggregated cost breakdown grouped by cost-centre tag.
    Good for chargeback reports — finance teams love this kind of view.
    """
    rows = db.execute(
        text("SELECT * FROM resources WHERE status != 'deprovisioned'")
    ).fetchall()

    breakdown: dict = {}

    for row in rows:
        r = dict(row._mapping)

        tags = r["policy_tags"]
        if isinstance(tags, str):
            tags = json.loads(tags)

        cc = tags.get("cost-centre", "untagged")

        if cost_centre and cc != cost_centre:
            continue

        hours     = _active_hours(r["created_at"], r["updated_at"], r["status"])
        accrued   = float(r["cost_per_hr"]) * hours
        projected = float(r["cost_per_hr"]) * HOURS_PER_MONTH

        if cc not in breakdown:
            breakdown[cc] = {
                "cost_centre":             cc,
                "resource_count":          0,
                "total_cost_accrued_usd":  0.0,
                "total_projected_monthly": 0.0,
                "resources":               [],
            }

        breakdown[cc]["resource_count"]          += 1
        breakdown[cc]["total_cost_accrued_usd"]  += accrued
        breakdown[cc]["total_projected_monthly"] += projected
        breakdown[cc]["resources"].append({
            "id":          r["id"],
            "name":        r["name"],
            "type":        r["type"],
            "cost_per_hr": float(r["cost_per_hr"]),
            "accrued_usd": round(accrued, 4),
        })

    # round after accumulation, not during — avoids floating point drift
    for cc_data in breakdown.values():
        cc_data["total_cost_accrued_usd"]  = round(cc_data["total_cost_accrued_usd"],  2)
        cc_data["total_projected_monthly"] = round(cc_data["total_projected_monthly"], 2)

    return {"cost_centres": list(breakdown.values()), "currency": "USD"}


@router.post("/budgets", status_code=201)
def set_budget(
    cost_centre:     str,
    monthly_limit:   float,
    alert_threshold: float = 0.80,   # default: alert at 80% of limit
    db: Session = Depends(get_db),
):
    """
    Set a monthly spend limit for a cost-centre.
    Uses upsert so you can call this repeatedly to update the limit.
    """
    if not (0.0 < alert_threshold <= 1.0):
        raise HTTPException(status_code=422, detail="alert_threshold must be between 0 and 1.")

    now       = datetime.now(timezone.utc)
    budget_id = str(uuid.uuid4())

    db.execute(
        text("""
            INSERT INTO budget_alerts
                (id, cost_centre, monthly_limit, alert_threshold, created_at, updated_at)
            VALUES (:id, :cc, :limit, :threshold, :now, :now)
            ON CONFLICT (cost_centre) DO UPDATE
                SET monthly_limit = :limit, alert_threshold = :threshold, updated_at = :now
        """),
        {
            "id":        budget_id,
            "cc":        cost_centre,
            "limit":     monthly_limit,
            "threshold": alert_threshold,
            "now":       now,
        }
    )
    db.commit()

    return {
        "cost_centre":     cost_centre,
        "monthly_limit":   monthly_limit,
        "alert_threshold": alert_threshold,
        "message":         f"Budget set for '{cost_centre}'.",
    }


@router.get("/budgets/alerts")
def check_budget_alerts(db: Session = Depends(get_db)):
    """
    Scans all cost-centres that have a budget configured and returns
    which ones are approaching or over their monthly limit.
    """
    budgets = db.execute(text("SELECT * FROM budget_alerts")).fetchall()

    if not budgets:
        return {"alerts": [], "message": "No budgets configured."}

    alerts = []

    for b in budgets:
        budget = dict(b._mapping)
        cc     = budget["cost_centre"]

        # reuse the by-cost-centre endpoint logic rather than duplicating the query
        cost_resp = cost_by_cost_centre(cost_centre=cc, db=db)
        cc_data   = next(
            (x for x in cost_resp["cost_centres"] if x["cost_centre"] == cc),
            None
        )

        if cc_data is None:
            continue

        projected   = cc_data["total_projected_monthly"]
        limit       = float(budget["monthly_limit"])
        threshold   = float(budget["alert_threshold"])
        utilization = projected / limit if limit > 0 else 0

        if utilization >= threshold:
            alerts.append({
                "cost_centre":         cc,
                "monthly_limit_usd":   limit,
                "projected_spend_usd": projected,
                "utilization_pct":     round(utilization * 100, 1),
                # critical if already over, warning if approaching
                "severity": "critical" if utilization >= 1.0 else "warning",
                "message": (
                    f"OVER BUDGET: {cc} is projecting ${projected} against a ${limit} limit."
                    if utilization >= 1.0
                    else f"WARNING: {cc} is at {round(utilization * 100, 1)}% of its ${limit} limit."
                ),
            })

    return {"alerts": alerts, "triggered": len(alerts)}
