"""
Deep healthcheck logic.

Two levels of health are reported:
  - "live"  — the FastAPI process is up (always true if we can respond)
  - "ready" — all downstream services (Postgres, InfluxDB) are reachable

A failing downstream check makes the endpoint return HTTP 503 so that
load balancers / Caddy can route around the broken instance.
"""

from __future__ import annotations

import asyncio
from typing import Literal, TypedDict

from app.database import ping_db
from app.influx import ping_influx


class CheckResult(TypedDict):
    status: Literal["ok", "fail"]


class HealthReport(TypedDict):
    status: Literal["ok", "degraded"]
    checks: dict[str, CheckResult]


async def collect_health() -> HealthReport:
    """Run all health checks in parallel and aggregate the report."""
    db_ok, influx_ok = await asyncio.gather(
        ping_db(),
        ping_influx(),
    )

    checks: dict[str, CheckResult] = {
        "postgres": {"status": "ok" if db_ok else "fail"},
        "influxdb": {"status": "ok" if influx_ok else "fail"},
    }
    overall: Literal["ok", "degraded"] = "ok" if (db_ok and influx_ok) else "degraded"

    return {"status": overall, "checks": checks}
