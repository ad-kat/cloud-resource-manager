"""
Cloud Resource Lifecycle Manager
A FastAPI service that simulates cloud resource lifecycle management.
"""

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from app.database import init_db
from app.routers import resources
import logging

# Configure logging so we can see what the service is doing
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# lifespan replaces the old @app.on_event("startup") pattern in modern FastAPI
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once at startup (before yield) and once at shutdown (after yield)."""
    logger.info("Starting up — initialising SQLite database...")
    init_db()
    logger.info("Database ready.")
    yield                         # <-- app runs while we are suspended here
    logger.info("Shutting down.")


app = FastAPI(
    title="Cloud Resource Lifecycle Manager",
    description=(
        "A cloud-native REST API that manages the full lifecycle of simulated "
        "cloud resources (VMs, buckets, functions). Demonstrates provisioning, "
        "policy management, and deprovisioning patterns used in real cloud platforms."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Mount the resources router — all endpoints live under /resources
app.include_router(resources.router, prefix="/resources", tags=["Resources"])


@app.get("/", tags=["Health"])
def root():
    """Health-check / welcome endpoint."""
    return {
        "service": "Cloud Resource Lifecycle Manager",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health", tags=["Health"])
def health():
    """Liveness probe — useful for container orchestrators."""
    return {"status": "healthy"}
