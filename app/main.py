from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from app.database import init_db
from app.routers import resources, cost, dashboard, auth, export
from app.routers.metrics import setup_metrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Database ready.")

    # start the background policy enforcement engine
    from app.scheduler import start_scheduler
    scheduler = start_scheduler()

    yield

    scheduler.shutdown(wait=False)
    logger.info("Shutting down.")


app = FastAPI(
    title="Cloud Resource Lifecycle Manager",
    description=(
        "REST API for cloud resource lifecycle — provisioning, governance, "
        "cost tracking (live Azure pricing), drift detection, and export. "
        "All free. All open source. No vendor lock-in."
    ),
    version="3.0.0",
    lifespan=lifespan,
)

setup_metrics(app)

app.include_router(resources.router, prefix="/resources", tags=["Resources"])
app.include_router(cost.router,      prefix="/cost",      tags=["Cost & Budgets"])
app.include_router(dashboard.router, prefix="/dashboard", tags=["Dashboard"])
app.include_router(auth.router,      prefix="/auth",      tags=["Auth"])
app.include_router(export.router,    prefix="/export",    tags=["Export"])


@app.api_route("/", methods=["GET", "HEAD"], tags=["Health"])
def root():
    html = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cloud Resource Lifecycle Manager</title>
<style>
:root{--c-canvas:251 247 239;--c-panel:255 252 245;--c-panel2:243 235 220;--c-line:215 205 189;--c-ink:40 27 53;--c-mute:102 91 108;--c-violet:91 60 136;--c-orchid:116 74 160;--c-pink:143 80 126;--c-plum:61 40 84;--c-gold:140 101 25;--c-teal:39 109 106;}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:rgb(var(--c-canvas));color:rgb(var(--c-ink));min-height:100vh;display:flex;align-items:center;justify-content:center;padding:2rem;transition:background .2s,color .2s}
.container{max-width:560px;width:100%}
h1{font-size:1.5rem;font-weight:700;color:rgb(var(--c-plum));margin-bottom:.3rem}
.version{font-size:.8rem;color:rgb(var(--c-mute));margin-bottom:2rem}
.card{background:rgb(var(--c-panel));border:1px solid rgb(var(--c-line));border-radius:10px;padding:1.5rem;margin-bottom:1rem}
.card p{font-size:.9rem;color:rgb(var(--c-mute));margin-bottom:1.25rem;line-height:1.5}
.links{display:flex;flex-direction:column;gap:.6rem}
.link-row{display:flex;align-items:center;gap:.75rem;padding:.65rem .9rem;background:rgb(var(--c-panel2));border-radius:7px;border:1px solid rgb(var(--c-line));text-decoration:none;color:rgb(var(--c-ink));font-size:.88rem;transition:.15s}
.link-row:hover{border-color:rgb(var(--c-violet));color:rgb(var(--c-violet))}
.link-icon{font-size:1rem;width:1.4rem;text-align:center;flex-shrink:0}
.link-label{font-weight:600;flex:1}
.link-path{font-size:.75rem;color:rgb(var(--c-mute));font-family:monospace}
.ref-label{font-size:.68rem;color:rgb(var(--c-mute));text-transform:uppercase;letter-spacing:.08em;margin-bottom:.5rem}
.chips{display:flex;flex-wrap:wrap;gap:.4rem}
.chip{background:rgb(var(--c-panel2));border:1px solid rgb(var(--c-line));border-radius:20px;padding:.2rem .7rem;font-size:.78rem;font-family:monospace;color:rgb(var(--c-violet))}
.ref-note{font-size:.78rem;color:rgb(var(--c-mute));margin-top:.5rem;line-height:1.5}
.ref-note code{background:rgb(var(--c-panel2));padding:.1rem .35rem;border-radius:4px;font-size:.75rem;color:rgb(var(--c-violet))}
</style>
</head>
<body>
<div class="container">
  <div style="margin-bottom:2rem">
    <h1>Cloud Resource Lifecycle Manager</h1>
  </div>
  <div class="card">
    <p>REST API for cloud resource lifecycle : provisioning, governance, cost tracking (live Azure pricing), drift detection, and export. Use the links below to navigate.</p>
    <div class="links">
      <a class="link-row" href="/dashboard/ui">
        <span class="link-icon">&#9783;</span>
        <span class="link-label">Dashboard</span>
        <span class="link-path">/dashboard/ui</span>
      </a>
      <a class="link-row" href="/docs">
        <span class="link-icon">&#128196;</span>
        <span class="link-label">API Docs</span>
        <span class="link-path">/docs</span>
      </a>
      <a class="link-row" href="/export/cost-report">
        <span class="link-icon">&#128202;</span>
        <span class="link-label">Cost Report Export</span>
        <span class="link-path">/export/cost-report</span>
      </a>
      <a class="link-row" href="/cost/rates">
        <span class="link-icon">&#128176;</span>
        <span class="link-label">Live Pricing Rates</span>
        <span class="link-path">/cost/rates</span>
      </a>
    </div>
  </div>

  <div class="card">
    <div class="ref-label">Resource Types</div>
    <div class="chips">
      <span class="chip">vm</span>
      <span class="chip">bucket</span>
      <span class="chip">database</span>
      <span class="chip">function</span>
    </div>

    <div class="ref-label" style="margin-top:1rem">Required Policy Tags</div>
    <div class="chips">
      <span class="chip">env</span>
      <span class="chip">cost-centre</span>
    </div>
    <div class="ref-note">Common values: <code>env</code> &rarr; <code>prod</code>, <code>staging</code>, <code>dev</code> &nbsp;&bull;&nbsp; <code>cost-centre</code> &rarr; any team identifier e.g. <code>eng-platform</code></div>

    <div class="ref-label" style="margin-top:1rem">Region</div>
    <div class="ref-note">Free-form string: use standard cloud region slugs e.g. <code>us-east-1</code>, <code>eu-west-2</code>, <code>ap-southeast-1</code></div>
  </div>
</div>
</body>
</html>"""
    return HTMLResponse(content=html)


@app.get("/health", tags=["Health"])
def health():
    return {"status": "healthy"}
