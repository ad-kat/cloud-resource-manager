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
    rows = db.execute(text("SELECT * FROM resources")).fetchall()
    resources = []
    for row in rows:
        r = dict(row._mapping)
        if isinstance(r["policy_tags"], str):
            r["policy_tags"] = json.loads(r["policy_tags"])
        resources.append(r)

    now = datetime.now(timezone.utc)
    by_type: dict = {}
    by_status: dict = {}
    by_cc: dict = {}
    total_monthly = 0.0
    stale_count = 0

    for r in resources:
        by_type[r["type"]]     = by_type.get(r["type"], 0) + 1
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

        if r["status"] not in ("deprovisioned", "stopped"):
            projected = float(r["cost_per_hr"]) * HOURS_PER_MONTH
            total_monthly += projected
            cc = r["policy_tags"].get("cost-centre", "untagged")
            by_cc[cc] = round(by_cc.get(cc, 0) + projected, 2)

        if r["ttl_hours"] and r["status"] == "active":
            created = r["created_at"]
            if isinstance(created, str):
                created = datetime.fromisoformat(created.replace(" ", "T"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if (now - created).total_seconds() / 3600 > r["ttl_hours"]:
                stale_count += 1

    recent_events = db.execute(
        text("SELECT * FROM events ORDER BY occurred_at DESC LIMIT 10")
    ).fetchall()

    budget_rows   = db.execute(text("SELECT * FROM budget_alerts")).fetchall()
    budget_alerts = []
    for b in budget_rows:
        b         = dict(b._mapping)
        projected = by_cc.get(b["cost_centre"], 0)
        limit     = float(b["monthly_limit"])
        util      = projected / limit if limit > 0 else 0
        if util >= float(b["alert_threshold"]):
            budget_alerts.append({
                "cost_centre":     b["cost_centre"],
                "severity":        "critical" if util >= 1.0 else "warning",
                "utilization_pct": round(util * 100, 1),
                "limit":           limit,
                "projected":       round(projected, 2),
            })

    return {
        "total_resources":         len(resources),
        "by_type":                 by_type,
        "by_status":               by_status,
        "stale_resources":         stale_count,
        "total_projected_monthly": round(total_monthly, 2),
        "cost_by_cost_centre":     by_cc,
        "recent_events":           [dict(e._mapping) for e in recent_events],
        "budget_alerts":           budget_alerts,
    }


@router.get("/", summary="JSON operational snapshot")
def dashboard_json(db: Session = Depends(get_db)):
    return _build_snapshot(db)


@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
def dashboard_html(db: Session = Depends(get_db)):
    data = _build_snapshot(db)
    tl   = json.dumps(list(data["by_type"].keys()))
    tv   = json.dumps(list(data["by_type"].values()))
    sl   = json.dumps(list(data["by_status"].keys()))
    sv   = json.dumps(list(data["by_status"].values()))
    cl   = json.dumps(list(data["cost_by_cost_centre"].keys()))
    cv   = json.dumps(list(data["cost_by_cost_centre"].values()))
    active      = data["by_status"].get("active", 0)
    stale_color = "rgb(var(--c-pink))" if data["stale_resources"] > 0 else "rgb(var(--c-teal))"

    alerts_html = "".join(
        f'<div class="alert-row"><span class="badge badge-{a["severity"]}">{a["severity"].upper()}</span>'
        f'<strong>{a["cost_centre"]}</strong> — ${a["projected"]:.2f} of ${a["limit"]:.2f} ({a["utilization_pct"]}%)</div>'
        for a in data["budget_alerts"]
    ) or "<p class='muted'>No budget alerts configured.</p>"

    event_rows = "".join(
        f"<tr><td><code>{e['action']}</code></td><td class='muted'>{e['resource_id'][:8]}…</td>"
        f"<td>{e['actor']}</td><td class='muted'>{str(e['occurred_at'])[:19]}</td></tr>"
        for e in data["recent_events"]
    ) or "<tr><td colspan='4' class='muted' style='padding:1rem'>No events yet</td></tr>"

    from app.routers.cost import cost_forecast
    forecast = cost_forecast(days=90, db=db)
    f_labels = json.dumps(["Now", "7 days", "30 days", "90 days"])
    f_values = json.dumps([0, forecast["scenarios"]["7d"], forecast["scenarios"]["30d"], forecast["scenarios"]["90d"]])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cloud Resource Manager</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script>if(localStorage.getItem('theme')==='dark')document.documentElement.classList.add('dark');</script>
<style>
:root{{--c-canvas:251 247 239;--c-panel:255 252 245;--c-panel2:243 235 220;--c-line:215 205 189;--c-ink:40 27 53;--c-mute:102 91 108;--c-violet:91 60 136;--c-orchid:116 74 160;--c-pink:143 80 126;--c-plum:61 40 84;--c-gold:140 101 25;--c-gold-soft:218 182 93;--c-teal:39 109 106;}}
.dark{{--c-canvas:23 19 30;--c-panel:33 26 43;--c-panel2:46 36 59;--c-line:84 72 103;--c-ink:245 240 248;--c-mute:198 189 204;--c-violet:201 181 228;--c-orchid:214 183 230;--c-pink:224 169 207;--c-plum:185 161 213;--c-gold:228 191 104;--c-gold-soft:244 216 147;--c-teal:117 196 188;}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,sans-serif;background:rgb(var(--c-canvas));color:rgb(var(--c-ink));padding:2rem;transition:background .2s,color .2s}}
h1{{font-size:1.4rem;font-weight:700;margin-bottom:.2rem;color:rgb(var(--c-plum))}}
.sub{{color:rgb(var(--c-mute));font-size:.85rem;margin-bottom:2rem;display:flex;justify-content:space-between;align-items:center}}
section{{margin-bottom:2rem}}
h2{{font-size:.72rem;color:rgb(var(--c-mute));text-transform:uppercase;letter-spacing:.08em;margin-bottom:.75rem}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem;margin-bottom:2rem}}
.card{{background:rgb(var(--c-panel));border-radius:10px;padding:1.25rem;border:1px solid rgb(var(--c-line))}}
.kpi-label{{font-size:.7rem;color:rgb(var(--c-mute));text-transform:uppercase;letter-spacing:.06em}}
.kpi-value{{font-size:2rem;font-weight:700;margin-top:.2rem}}
.charts{{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1rem}}
.chart-wrap{{background:rgb(var(--c-panel));border-radius:10px;padding:1.25rem;margin-bottom:1rem;border:1px solid rgb(var(--c-line))}}
.ch{{position:relative;height:200px}}
table{{width:100%;border-collapse:collapse;background:rgb(var(--c-panel));border-radius:10px;overflow:hidden;border:1px solid rgb(var(--c-line))}}
th{{background:rgb(var(--c-panel2));padding:.6rem 1rem;text-align:left;font-size:.72rem;color:rgb(var(--c-mute));text-transform:uppercase}}
td{{padding:.55rem 1rem;border-top:1px solid rgb(var(--c-line));font-size:.85rem}}
tr:hover td{{background:rgb(var(--c-panel2))}}
code{{background:rgb(var(--c-panel2));padding:.1rem .4rem;border-radius:4px;font-size:.78rem;color:rgb(var(--c-violet))}}
.badge{{padding:.15rem .5rem;border-radius:4px;font-size:.72rem;font-weight:700;margin-right:.5rem}}
.badge-critical{{background:#fee2e2;color:#dc2626}}
.badge-warning{{background:#fef3c7;color:#d97706}}
.alert-row{{padding:.65rem .85rem;border-radius:6px;margin-bottom:.4rem;background:rgb(var(--c-panel));font-size:.85rem;border:1px solid rgb(var(--c-line))}}
.muted{{color:rgb(var(--c-mute))}}
.toggle{{background:rgb(var(--c-panel2));border:1px solid rgb(var(--c-line));color:rgb(var(--c-ink));padding:.3rem .8rem;border-radius:6px;cursor:pointer;font-size:.8rem;transition:.15s}}
.toggle:hover{{background:rgb(var(--c-violet));color:rgb(var(--c-canvas))}}
</style>
</head>
<body>
<h1>☁ Cloud Resource Manager</h1>
<div class="sub"><span>Live operational snapshot</span><div style="display:flex;gap:1rem;align-items:center"><span class="muted">Refreshing in <span id="cd">30</span>s</span><button class="toggle" onclick="toggleTheme()">🌓 Theme</button></div></div>

<div class="kpis">
  <div class="card"><div class="kpi-label">Total Resources</div><div class="kpi-value" style="color:rgb(var(--c-violet))">{data['total_resources']}</div></div>
  <div class="card"><div class="kpi-label">Active</div><div class="kpi-value" style="color:rgb(var(--c-teal))">{active}</div></div>
  <div class="card"><div class="kpi-label">Projected Monthly</div><div class="kpi-value" style="color:rgb(var(--c-gold))">${data['total_projected_monthly']:.2f}</div></div>
  <div class="card"><div class="kpi-label">Stale Resources</div><div class="kpi-value" style="color:{stale_color}">{data['stale_resources']}</div></div>
</div>

<div class="charts">
  <div class="chart-wrap"><h2>By Type</h2><div class="ch"><canvas id="typeChart"></canvas></div></div>
  <div class="chart-wrap"><h2>By Status</h2><div class="ch"><canvas id="statusChart"></canvas></div></div>
</div>
<div class="chart-wrap"><h2>Projected Monthly Cost by Cost-Centre (USD)</h2><div class="ch" style="height:250px"><canvas id="ccChart"></canvas></div></div>
<div class="chart-wrap"><h2>Cost Forecast: 90 Day Projection (USD)</h2><div class="ch" style="height:250px"><canvas id="forecastChart"></canvas></div></div>
<section style="margin-top:1rem">
  <h2>Budget Alerts</h2>
  {alerts_html}
</section>

<section>
  <h2>Recent Audit Events</h2>
  <table>
    <thead><tr><th>Action</th><th>Resource</th><th>Actor</th><th>Time (UTC)</th></tr></thead>
    <tbody>{event_rows}</tbody>
  </table>
</section>

<script>
function css(v){{return'rgb('+getComputedStyle(document.documentElement).getPropertyValue('--'+v).trim()+')'}}
let charts=[];
function mkChart(id,type,labels,data,colors,noLegend=false){{
  return new Chart(document.getElementById(id),{{
    type,
    data:{{labels,datasets:[{{data,backgroundColor:colors,borderWidth:0}}]}},
    options:{{
      maintainAspectRatio:false,
      plugins:{{legend:{{display:!noLegend,labels:{{color:css('c-mute'),boxWidth:12}}}}}},
      scales:type==='bar'?{{x:{{ticks:{{color:css('c-mute')}},grid:{{color:css('c-line')}}}},y:{{ticks:{{color:css('c-mute')}},grid:{{color:css('c-line')}}}}}}:{{}}
    }}
  }});
}}
function buildCharts(){{
  charts.forEach(c=>c.destroy());charts=[];
  const C=[css('c-violet'),css('c-teal'),css('c-gold'),css('c-pink')];
  charts.push(mkChart('typeChart',  'doughnut',{tl},{tv},C));
  charts.push(mkChart('statusChart','doughnut',{sl},{sv},C));
  charts.push(mkChart('ccChart',    'bar',     {cl},{cv},C,true));
  charts.push(new Chart(document.getElementById('forecastChart'),{{
    type:'line',
    data:{{
      labels:{f_labels},
      datasets:[{{
        label:'Projected Cost (USD)',
        data:{f_values},
        borderColor:css('c-gold'),
        backgroundColor:css('c-gold')+'22',
        borderWidth:2,
        pointBackgroundColor:css('c-gold'),
        fill:true,
        tension:0.4
      }}]
    }},
    options:{{
      maintainAspectRatio:false,
      plugins:{{legend:{{display:false}}}},
      scales:{{
        x:{{ticks:{{color:css('c-mute')}},grid:{{color:css('c-line')}}}},
        y:{{ticks:{{color:css('c-mute'),callback:(v)=>'$'+v}},grid:{{color:css('c-line')}}}}
      }}
    }}
  }}));
}}
function toggleTheme(){{
  document.documentElement.classList.toggle('dark');
  localStorage.setItem('theme',document.documentElement.classList.contains('dark')?'dark':'light');
  buildCharts();
}}
buildCharts();
let s=30;const cd=document.getElementById('cd');
setInterval(()=>{{cd.textContent=--s;if(s<=0)location.reload();}},1000);
</script>
</body>
</html>"""