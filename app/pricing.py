"""
pricing.py — live cloud cost rates via Azure Retail Pricing API
No account or API key required. Falls back to static rates if unreachable.
"""

import json, logging, threading, time, urllib.parse, urllib.request

logger = logging.getLogger(__name__)

FALLBACK_RATES = {
    "vm":       0.096,      # Azure D2s v3, East US, Linux on-demand
    "database": 0.3744,     # Azure SQL Database, General Purpose 2 vCores
    "bucket":   0.0000267,  # Azure Blob Storage LRS per GB/hr
    "function": 0.000016,   # Azure Functions per execution-second
}

_rate_cache: dict = {}
_cache_lock       = threading.Lock()
_last_refresh     = 0.0
REFRESH_INTERVAL  = 6 * 3600

_BASE = "https://prices.azure.com/api/retail/prices?api-version=2023-01-01-preview&$filter="
_FILTERS = {
    "vm":       "serviceName eq 'Virtual Machines' and armRegionName eq 'eastus' and skuName eq 'D2s v3' and priceType eq 'Consumption'",
    "database": "serviceName eq 'Azure SQL Database' and armRegionName eq 'eastus' and priceType eq 'Consumption'",
    "bucket":   "serviceName eq 'Storage' and armRegionName eq 'eastus' and skuName eq 'LRS Data Stored' and priceType eq 'Consumption'",
    "function": "serviceName eq 'Azure Functions' and armRegionName eq 'eastus' and priceType eq 'Consumption'",
}


def _fetch_azure_rates() -> dict:
    rates = {}
    for rtype, filt in _FILTERS.items():
        try:
            url = _BASE + urllib.parse.quote(filt)
            req = urllib.request.Request(url, headers={"User-Agent": "cloud-resource-manager/3.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                items = json.loads(resp.read()).get("Items", [])
                if items:
                    rates[rtype] = float(items[0]["retailPrice"])
                    logger.info("Azure live rate %-10s $%.6f/hr", rtype, rates[rtype])
                    continue
        except Exception as e:
            logger.warning("Azure pricing failed for %s: %s — using fallback", rtype, e)
        rates[rtype] = FALLBACK_RATES[rtype]
    return rates


def refresh_rates(force: bool = False) -> dict:
    global _last_refresh
    now = time.time()
    if not force and (now - _last_refresh) < REFRESH_INTERVAL:
        return _rate_cache
    with _cache_lock:
        if not force and (now - _last_refresh) < REFRESH_INTERVAL:
            return _rate_cache
        _rate_cache.update(_fetch_azure_rates())
        _last_refresh = time.time()
    return _rate_cache


def get_rate(resource_type: str) -> float:
    if not _rate_cache or (time.time() - _last_refresh) > REFRESH_INTERVAL:
        refresh_rates()
    return _rate_cache.get(resource_type, FALLBACK_RATES.get(resource_type, 0.0))


def get_all_rates() -> dict:
    if not _rate_cache:
        refresh_rates()
    return dict(_rate_cache)