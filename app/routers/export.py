"""
export.py — data export endpoints

Because at some point finance will ask for a spreadsheet.
They always ask for a spreadsheet.
"""

import csv
import io
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.routers.cost import _active_hours

logger = logging.getLogger(__name__)
router = APIRouter()

HOURS_PER_MONTH = 730


@router.get(
    "/cost-report",
    summary="Export cost report as CSV",
    response_class=StreamingResponse,
)
def export_cost_report(db: Session = Depends(get_db)):
    """
    Downloads a CSV of all resources with cost data.
    Finance will ask for this. Now you have it. You're welcome.

    Columns: id, name, type, status, owner, region, cost_centre,
             cost_per_hr, hours_alive, cost_accrued_usd, projected_monthly_usd
    """
    rows = db.execute(text("SELECT * FROM resources ORDER BY created_at DESC")).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)

    # header row
    writer.writerow([
        "id", "name", "type", "status", "owner", "region",
        "cost_centre", "env",
        "cost_per_hr_usd", "hours_alive", "cost_accrued_usd", "projected_monthly_usd",
        "ttl_hours", "created_at", "updated_at",
    ])

    now = datetime.now(timezone.utc)

    for row in rows:
        r = dict(row._mapping)

        tags = r.get("policy_tags", "{}")
        if isinstance(tags, str):
            tags = json.loads(tags)

        cost_centre = tags.get("cost-centre", "untagged")
        env         = tags.get("env", "unknown")

        hours   = _active_hours(r["created_at"], r["updated_at"], r["status"])
        accrued = round(float(r["cost_per_hr"]) * hours, 4)
        proj    = round(float(r["cost_per_hr"]) * HOURS_PER_MONTH, 2)

        writer.writerow([
            r["id"],
            r["name"],
            r["type"],
            r["status"],
            r["owner"],
            r["region"],
            cost_centre,
            env,
            float(r["cost_per_hr"]),
            round(hours, 2),
            accrued,
            proj,
            r.get("ttl_hours", ""),
            r["created_at"],
            r["updated_at"],
        ])

    output.seek(0)

    filename = f"cost-report-{now.strftime('%Y%m%d-%H%M%S')}.csv"
    logger.info("Exporting cost report: %s (%d rows)", filename, len(rows))

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get(
    "/audit-log",
    summary="Export full audit log as CSV",
    response_class=StreamingResponse,
)
def export_audit_log(db: Session = Depends(get_db)):
    """
    Full audit trail as a CSV. Every state change, in order.
    Compliance teams love this. Or so I'm told.
    """
    rows = db.execute(
        text("SELECT * FROM events ORDER BY occurred_at ASC")
    ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "resource_id", "action", "actor", "detail", "occurred_at"])

    for row in rows:
        r = dict(row._mapping)
        writer.writerow([
            r["id"],
            r["resource_id"],
            r["action"],
            r["actor"],
            r.get("detail", ""),
            r["occurred_at"],
        ])

    output.seek(0)
    now      = datetime.now(timezone.utc)
    filename = f"audit-log-{now.strftime('%Y%m%d-%H%M%S')}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
