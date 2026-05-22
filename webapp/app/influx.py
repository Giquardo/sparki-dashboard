"""
InfluxDB async client.

Provides:
  - `get_influx_client()` — returns a singleton InfluxDBClientAsync
  - `close_influx_client()` — graceful shutdown for lifespan
  - `ping_influx()`         — bool healthcheck

Why a singleton: the official client maintains an internal HTTP connection
pool. Creating a new client per request would be wasteful.
"""

from __future__ import annotations

import logging

from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync

from app.config import settings

logger = logging.getLogger("sparki.influx")

_client: InfluxDBClientAsync | None = None


def get_influx_client() -> InfluxDBClientAsync:
    """Return the singleton InfluxDB async client.

    Lazy-initialized so we don't create an HTTP pool at import time.
    """
    global _client
    if _client is None:
        _client = InfluxDBClientAsync(
            url=settings.influxdb_url,
            token=settings.influxdb_token.get_secret_value(),
            org=settings.influxdb_org,
            timeout=10_000,        # 10 seconds, in ms
            enable_gzip=True,
        )
        logger.info("InfluxDB client initialized: %s", settings.influxdb_url)
    return _client


async def close_influx_client() -> None:
    """Close the singleton client. Call from FastAPI lifespan shutdown."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None
        logger.info("InfluxDB client closed")


async def ping_influx() -> bool:
    """Return True if InfluxDB is reachable and the token is valid."""
    try:
        client = get_influx_client()
        return await client.ping()
    except Exception as exc:  # noqa: BLE001 — broad catch is intentional for healthcheck
        logger.warning("InfluxDB ping failed: %s", exc)
        return False
