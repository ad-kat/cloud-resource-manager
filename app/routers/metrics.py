"""
metrics.py — Prometheus metrics endpoint

Five lines of setup gets you a /metrics endpoint in Prometheus scrape format.
This is what SRE teams plug into Grafana dashboards.
Not that anyone is scraping this right now, but the point is it's there.

Library used: prometheus-fastapi-instrumentator (free, MIT license)
Adds: request count, latency histograms, in-progress requests — all standard stuff.
"""

import logging
from prometheus_fastapi_instrumentator import Instrumentator

logger = logging.getLogger(__name__)


def setup_metrics(app):
    """
    Call this once at app startup, passing the FastAPI app instance.
    Exposes /metrics in Prometheus text format.
    """
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")
    logger.info("Prometheus metrics endpoint live at /metrics")
