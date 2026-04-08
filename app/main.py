from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
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
        "cost tracking (live AWS pricing), drift detection, and export. "
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


@app.get("/", tags=["Health"])
def root():
    return {
        "service":   "Cloud Resource Lifecycle Manager",
        "version":   "3.0.0",
        "docs":      "/docs",
        "dashboard": "/dashboard/ui",
        "export":    "/export/cost-report",
        "pricing":   "/cost/rates",
    }


@app.get("/health", tags=["Health"])
def health():
    return {"status": "healthy"}
