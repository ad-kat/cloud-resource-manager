"""
pricing.py — AWS Pricing API integration

AWS publishes their pricing data as giant JSON blobs at a public URL.
No auth needed. No API key. Just a big ol' HTTP GET.
This is genuinely one of the more pleasant things AWS has done.

We fetch on startup, cache in memory, refresh every 6 hours because
AWS doesn't change prices that often and we're not monsters.

Supported: EC2 (vm), RDS (database), S3 (bucket), Lambda (function)

Too much for render's 512MB limit to handle...
"""

import logging
import threading
import time
from typing import Optional

import urllib.request
import json

logger = logging.getLogger(__name__)

# AWS pricing index URLs — public, no auth, free forever
# these return the "current" price list for each service in us-east-1
# other regions have slightly different prices but this is close enough for our purposes
AWS_PRICING_URLS = {
    "vm": (
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/"
        "AmazonEC2/current/us-east-1/index.json"
    ),
    "database": (
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/"
        "AmazonRDS/current/us-east-1/index.json"
    ),
    # S3 and Lambda pricing is structured differently (per-request, per-GB)
    # so we use sensible fixed rates for those two instead of parsing the full index
}

# fallback rates if the AWS pricing API is down / times out
# based on real AWS on-demand prices as of early 2026, good enough
FALLBACK_RATES = {
    "vm":       0.096,   # t3.medium on-demand
    "database": 0.115,   # db.t3.medium RDS postgres on-demand
    "bucket":   0.023,   # S3 standard storage per GB/month converted to hourly
    "function": 0.0000002,  # Lambda per-invocation is weird, this is a rough hourly equiv
}

# in-memory cache — {resource_type: hourly_rate_usd}
_rate_cache: dict = {}
_cache_lock  = threading.Lock()
_last_refresh = 0.0
REFRESH_INTERVAL_SECONDS = 6 * 3600  # 6 hours, don't hammer AWS


def _parse_ec2_rate(pricing_json: dict) -> Optional[float]:
    """
    EC2 pricing JSON is... a lot. Like, 500MB a lot.
    We just want the on-demand hourly rate for a t3.medium Linux instance.
    This is what I get for not paying for the AWS Cost Explorer API.
    """
    try:
        products = pricing_json.get("products", {})
        terms    = pricing_json.get("terms", {}).get("OnDemand", {})

        for sku, product in products.items():
            attrs = product.get("attributes", {})
            # filter to t3.medium, Linux, no pre-installed software, us-east-1
            if (
                attrs.get("instanceType") == "t3.medium"
                and attrs.get("operatingSystem") == "Linux"
                and attrs.get("preInstalledSw") == "NA"
                and attrs.get("tenancy") == "Shared"
                and attrs.get("capacitystatus") == "Used"
            ):
                # now dig into the pricing terms — nested dict hell incoming
                sku_terms = terms.get(sku, {})
                for offer_term in sku_terms.values():
                    for price_dim in offer_term.get("priceDimensions", {}).values():
                        rate_str = price_dim.get("pricePerUnit", {}).get("USD", "0")
                        rate = float(rate_str)
                        if rate > 0:
                            return rate
    except Exception as e:
        logger.warning("Failed to parse EC2 pricing: %s", e)

    return None


def _parse_rds_rate(pricing_json: dict) -> Optional[float]:
    """
    RDS pricing — same mess, different service.
    We want db.t3.medium, PostgreSQL, Single-AZ, on-demand.
    """
    try:
        products = pricing_json.get("products", {})
        terms    = pricing_json.get("terms", {}).get("OnDemand", {})

        for sku, product in products.items():
            attrs = product.get("attributes", {})
            if (
                attrs.get("instanceType") == "db.t3.medium"
                and attrs.get("databaseEngine") == "PostgreSQL"
                and attrs.get("deploymentOption") == "Single-AZ"
            ):
                sku_terms = terms.get(sku, {})
                for offer_term in sku_terms.values():
                    for price_dim in offer_term.get("priceDimensions", {}).values():
                        rate_str = price_dim.get("pricePerUnit", {}).get("USD", "0")
                        rate = float(rate_str)
                        if rate > 0:
                            return rate
    except Exception as e:
        logger.warning("Failed to parse RDS pricing: %s", e)

    return None


def _fetch_rate(resource_type: str) -> float:
    """
    Fetches live pricing from AWS for the given resource type.
    Falls back to hardcoded rates if anything goes wrong.
    AWS pricing JSON files are huge (100MB+ for EC2) so we set a timeout
    and bail fast if it's not responding.
    """
    # bucket and function don't have a simple pricing URL — use fallback
    if resource_type not in AWS_PRICING_URLS:
        return FALLBACK_RATES.get(resource_type, 0.0)

    url = AWS_PRICING_URLS[resource_type]

    try:
        logger.info("Fetching AWS pricing for '%s'... (this might take a sec)", resource_type)
        req = urllib.request.Request(url, headers={"User-Agent": "cloud-resource-manager/2.0"})

        # 15 second timeout — the EC2 index is big but should load in time
        with urllib.request.urlopen(req, timeout=15) as resp:
            # streaming parse would be smarter but this is a demo not Netflix
            raw = resp.read()
            data = json.loads(raw)

        if resource_type == "vm":
            rate = _parse_ec2_rate(data)
        elif resource_type == "database":
            rate = _parse_rds_rate(data)
        else:
            rate = None

        if rate and rate > 0:
            logger.info("Got live AWS rate for '%s': $%.6f/hr", resource_type, rate)
            return rate
        else:
            logger.warning("Parsed rate was None/zero for '%s', using fallback", resource_type)
            return FALLBACK_RATES.get(resource_type, 0.0)

    except Exception as e:
        # pricing API down? timeout? who knows. just use the fallback and move on.
        logger.warning(
            "AWS pricing fetch failed for '%s' (%s). Using fallback rate $%.6f/hr",
            resource_type, e, FALLBACK_RATES.get(resource_type, 0.0)
        )
        return FALLBACK_RATES.get(resource_type, 0.0)


def refresh_rates(force: bool = False) -> dict:
    """
    Refreshes the in-memory rate cache from AWS pricing API.
    Called on startup and every 6 hours by the scheduler.
    Thread-safe because multiple requests could trigger this simultaneously.
    """
    global _last_refresh

    now = time.time()
    if not force and (now - _last_refresh) < REFRESH_INTERVAL_SECONDS:
        return _rate_cache

    with _cache_lock:
        # double-check after acquiring lock — another thread might have refreshed
        if not force and (now - _last_refresh) < REFRESH_INTERVAL_SECONDS:
            return _rate_cache

        logger.info("Refreshing AWS pricing rates...")

        new_rates = {}
        # fetch vm and database from AWS, use fallback for the rest
        for rtype in ["vm", "database", "bucket", "function"]:
            new_rates[rtype] = _fetch_rate(rtype)

        _rate_cache.update(new_rates)
        _last_refresh = time.time()

        logger.info("Pricing cache updated: %s", {k: f"${v:.6f}" for k, v in _rate_cache.items()})

    return _rate_cache


def get_rate(resource_type: str) -> float:
    """
    Get the hourly rate for a resource type.
    Uses cached value if fresh, fetches if stale.
    This is what the rest of the app calls.
    """
    if not _rate_cache or (time.time() - _last_refresh) > REFRESH_INTERVAL_SECONDS:
        refresh_rates()

    return _rate_cache.get(resource_type, FALLBACK_RATES.get(resource_type, 0.0))


def get_all_rates() -> dict:
    """Returns the full rate cache. Useful for the /cost/rates endpoint."""
    if not _rate_cache:
        refresh_rates()
    return dict(_rate_cache)
