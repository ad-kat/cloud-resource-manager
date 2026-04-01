"""
Dashboard router — two endpoints:
  GET /dashboard/     → JSON snapshot (for programmatic consumers / future frontend)
  GET /dashboard/ui   → rendered HTML page (for humans, screenshots, LinkedIn demo)
"""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

HOURS_PER_MONTH = 730


def _build_snapshot(db: Session) -> dict:
    """
    Pulls everything needed for the dashboard in a few queries.
    Could be optimised with a single GROUP BY query later if this gets slow.
    """
    rows = db.execute(text("SELECT * FROM resources")).fetchall()

    resources = []
    for row in rows:
        r = dict(row._mapping)
        if isinstance(r["policy_tags"], str):
            r["policy_tags"] = json.loads(r["policy_tags"])
        resources.append(r)

    now = datetime.now(timezone.utc)

    by_type:   dict = {}
    by_status: dict = {}
    by_cc:     dict = {}
    total_monthly = 0.0
    stale_count   = 0

    for r in resources:
        by_type[r["type"]]     = by_type.get(r["type"], 0)     + 1
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

        # only count cost for resources that are still running
        if r["status"] not in ("deprovisioned", "stopped"):
            projected = float(r["cost_per_hr"]) * HOURS_PER_MONTH
            total_monthly += projected

            cc = r["policy_tags"].get("cost-centre", "untagged")
            by_cc[cc] = round(by_cc.get(cc, 0) + projected, 2)

        # stale = active resource that has blown past its TTL
        # scheduler should have caught this but the dashboard shows it anyway
        if r["ttl_hours"] and r["status"] == "active":
            created = r["created_at"]
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_h = (now - created).total_seconds() / 3600
            if age_h > r["ttl_hours"]:
                stale_count += 1

    # grab the 10 most recent audit events for the activity feed
    recent_events = db.execute(
        text("SELECT * FROM events ORDER BY occurred_at DESC LIMIT 10")
    ).fetchall()

    return {
        "total_resources":         len(resources),
        "by_type":                 by_type,
        "by_status":               by_status,
        "stale_resources":         stale_count,
        "total_projected_monthly": round(total_monthly, 2),
        "cost_by_cost_centre":     by_cc,
        "recent_events":           [dict(e._mapping) for e in recent_events],
    }


@router.get("/", summary="JSON operational snapshot")
def dashboard_json(db: Session = Depends(get_db)):
    return _build_snapshot(db)


@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
def dashboard_html(db: Session = Depends(get_db)):
    data = _build_snapshot(db)
    return _render_html(data)


def _render_html(data: dict) -> str:
    # build the card blocks for type/status breakdowns
    def card(label, count, color):
        return f"""
        <div class="card">
            <div class="card-label">{label}</div>
            <div class="card-value" style="color:{color}">{count}</div>
        </div>"""

    type_cards   = "".join(card(k, v, "#4A9EFF") for k, v in data["by_type"].items())
    status_cards = "".join(card(k, v, "#50C878") for k, v in data["by_status"].items())

    cc_rows = "".join(
        f"<tr><td>{cc}</td><td>${cost:.2f}</td></tr>"
        for cc, cost in data["cost_by_cost_centre"].items()
    ) or "<tr><td colspan='2' style='color:#94a3b8'>No active resources</td></tr>"

    event_rows = "".join(
        f"<tr><td>{e['action']}</td>"
        f"<td>{e['resource_id'][:8]}...</td>"
        f"<td>{e['actor']}</td>"
        f"<td>{str(e['occurred_at'])[:19]}</td></tr>"
        for e in data["recent_events"]
    ) or "<tr><td colspan='4' style='color:#94a3b8'>No events yet</td></tr>"

    stale_color = "#ef4444" if data["stale_resources"] > 0 else "#50C878"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cloud Resource Manager — Dashboard</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f1117;
    color: #e2e8f0;
    padding: 2rem;
  }}

  h1 {{ font-size: 1.6rem; margin-bottom: 0.25rem; }}

  .subtitle {{
    color: #94a3b8;
    font-size: 0.9rem;
    margin-bottom: 2rem;
  }}

  .section {{ margin-bottom: 2rem; }}

  .section h2 {{
    font-size: 1rem;
    color: #94a3b8;
    text-transform: uppercase;
    letter-spacing: .08em;
    margin-bottom: 0.75rem;
  }}

  .cards {{ display: flex; flex-wrap: wrap; gap: 1rem; }}

  .card {{
    background: #1e2130;
    border-radius: 8px;
    padding: 1rem 1.5rem;
    min-width: 130px;
  }}

  .card-label {{ font-size: 0.78rem; color: #94a3b8; margin-bottom: 0.3rem; }}
  .card-value  {{ font-size: 1.8rem; font-weight: 700; }}

  /* the three big headline numbers at the top */
  .highlight {{
    background: #1e2130;
    border-radius: 8px;
    padding: 1rem 1.5rem;
    display: inline-block;
    margin-right: 1rem;
    margin-bottom: 1rem;
  }}

  .hl-label {{ font-size: 0.78rem; color: #94a3b8; }}
  .hl-value  {{ font-size: 2rem; font-weight: 700; color: #f59e0b; }}

  table {{
    width: 100%;
    border-collapse: collapse;
    background: #1e2130;
    border-radius: 8px;
    overflow: hidden;
  }}

  th {{
    text-align: left;
    padding: 0.6rem 1rem;
    background: #2d3148;
    font-size: 0.8rem;
    color: #94a3b8;
    text-transform: uppercase;
  }}

  td {{ padding: 0.6rem 1rem; border-top: 1px solid #2d3148; font-size: 0.9rem; }}
  tr:hover td {{ background: #252840; }}
</style>
</head>
<body>

<h1>&#9729; Cloud Resource Manager</h1>
<p class="subtitle">Live operational snapshot &mdash; refreshes on page load</p>

<div class="section">
  <div class="cards">
    <div class="highlight">
      <div class="hl-label">Total Resources</div>
      <div class="hl-value">{data['total_resources']}</div>
    </div>
    <div class="highlight">
      <div class="hl-label">Projected Monthly Cost</div>
      <div class="hl-value">${data['total_projected_monthly']:.2f}</div>
    </div>
    <div class="highlight">
      <div class="hl-label">Stale Resources</div>
      <div class="hl-value" style="color:{stale_color}">{data['stale_resources']}</div>
    </div>
  </div>
</div>

<div class="section">
  <h2>By Type</h2>
  <div class="cards">{type_cards}</div>
</div>

<div class="section">
  <h2>By Status</h2>
  <div class="cards">{status_cards}</div>
</div>

<div class="section">
  <h2>Projected Monthly Cost by Cost-Centre</h2>
  <table>
    <thead><tr><th>Cost-Centre</th><th>Projected / Month</th></tr></thead>
    <tbody>{cc_rows}</tbody>
  </table>
</div>

<div class="section">
  <h2>Recent Audit Events</h2>
  <table>
    <thead>
      <tr><th>Action</th><th>Resource</th><th>Actor</th><th>Time (UTC)</th></tr>
    </thead>
    <tbody>{event_rows}</tbody>
  </table>
</div>

</body>
</html>"""
