from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from app.database import init_db
from app.routers import resources, cost, dashboard

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
    # imported here to avoid circular import issues at module load time
    from app.scheduler import start_scheduler
    scheduler = start_scheduler()

    yield

    # graceful shutdown — wait=False so we don't block for up to 5 min
    scheduler.shutdown(wait=False)
    logger.info("Shutting down.")


app = FastAPI(
    title="Cloud Resource Lifecycle Manager",
    description="REST API for cloud resource lifecycle — provisioning, governance, cost tracking, drift detection.",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(resources.router, prefix="/resources", tags=["Resources"])
app.include_router(cost.router,      prefix="/cost",      tags=["Cost & Budgets"])
app.include_router(dashboard.router, prefix="/dashboard", tags=["Dashboard"])


@app.get("/", tags=["Health"])
def root():
    return {
        "service": "Cloud Resource Lifecycle Manager",
        "version": "2.0.0",
        "docs":    "/docs",
        "dashboard": "/dashboard/ui",
    }


@app.get("/health", tags=["Health"])
def health():
    return {"status": "healthy"}
