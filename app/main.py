from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from app.database import init_db
from app.routers import resources

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Database ready.")
    yield
    logger.info("Shutting down.")


app = FastAPI(
    title="Cloud Resource Lifecycle Manager",
    description="REST API modelling cloud resource lifecycle — provisioning, governance, deprovisioning.",
    version="1.1.0",
    lifespan=lifespan,
)

app.include_router(resources.router, prefix="/resources", tags=["Resources"])


@app.get("/", tags=["Health"])
def root():
    return {"service": "Cloud Resource Lifecycle Manager", "status": "running", "docs": "/docs"}


@app.get("/health", tags=["Health"])
def health():
    return {"status": "healthy"}
