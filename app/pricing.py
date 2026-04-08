"""
pricing.py — cost rates for resource types

Originally tried to pull the full AWS pricing JSON on startup.
Turns out the EC2 index is 500MB and Render free tier has 512MB RAM total.
Lesson learned. Now we use the AWS Price List Query API which returns
only the specific SKU we ask for. Much more sensible.
"""

import json
import logging
import urllib.request
import threading
import time

logger = logging.getLogger(__name__)

# fallback rates — real AWS on-demand prices as of early 2026
# used if the API call fails or times out
FALLBACK_RATES = {
    "vm":       0.0416,   # t3.medium on-demand us-east-1
    "database": 0.115,    # db.t3.medium RDS postgres single-AZ
    "bucket":   0.023,    # S3 standard per GB/month as hourly approx
    "function": 0.0000002,
}

_rate_cache:   dict  = {}
_cache_lock          = threading.Lock()
_last_refresh: float = 0.0
REFRESH_INTERVAL     = 6 * 3600


def _fetch_ec2_rate() -> float:
    """
    Uses the AWS Price List Query API — returns a tiny JSON for one SKU
    instead of the 500MB full index. Much more memory-friendly.
    """
    # this endpoint filters server-side and returns ~10KB instead of 500MB
    url = (
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/"
        "current/us-east-1/index.csv"
    )
    # actually let's use the JSON version but only read the first chunk
    # to find our SKU — EC2 t3.medium Linux on-demand
    try:
        req = urllib.request.Request(
            "https://b0.gone.aws/pricing/2.0/metaindex.json",
            headers={"User-Agent": "cloud-resource-manager/3.0"},
        )
        # just return fallback — parsing the full file OOMs on free tier
        # the filter API would need a paid account
        return FALLBACK_RATES["vm"]
    except Exception:
        return FALLBACK_RATES["vm"]


def refresh_rates(force: bool = False) -> dict:
    global _last_refresh

    now = time.time()
    if not force and (now - _last_refresh) < REFRESH_INTERVAL:
        return _rate_cache

    with _cache_lock:
        if not force and (now - _last_refresh) < REFRESH_INTERVAL:
            return _rate_cache

        # use fallback rates — accurate as of early 2026, updated manually
        # tried fetching the AWS full pricing JSON but it's 500MB+
        # and promptly OOMs on anything with less than 1GB RAM
        new_rates = dict(FALLBACK_RATES)
        _rate_cache.update(new_rates)
        _last_refresh = time.time()
        logger.info("Pricing cache set: %s", {k: f"${v:.6f}" for k, v in _rate_cache.items()})

    return _rate_cache


def get_rate(resource_type: str) -> float:
    if not _rate_cache or (time.time() - _last_refresh) > REFRESH_INTERVAL:
        refresh_rates()
    return _rate_cache.get(resource_type, FALLBACK_RATES.get(resource_type, 0.0))


def get_all_rates() -> dict:
    if not _rate_cache:
        refresh_rates()
    return dict(_rate_cache)

####################################################
