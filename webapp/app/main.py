"""
Sparki webapp — FastAPI entrypoint.

This is the boot file. It wires together:
  - configuration loading (via app.config)
  - lifespan events (DB connection pools, InfluxDB client, etc. — added later)
  - middleware (sessions, request logging, etc.)
  - route mounting (added in later phases)

For Phase 1, Step 2A we only expose `/healthz` and a placeholder `/`.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import __version__
from app.config import settings

# ─── Logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if not settings.is_production else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sparki")


# ─── Lifespan ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Boot / shutdown hooks.

    Phase 2 will wire up:
      - SQLAlchemy async engine + connection pool
      - InfluxDB async client
      - Keycloak JWKS cache
    For now we only log lifecycle events.
    """
    logger.info(
        "Sparki webapp v%s starting in %s mode",
        __version__,
        settings.environment,
    )
    yield
    logger.info("Sparki webapp shutting down")


# ─── App factory ─────────────────────────────────────────────────────
app = FastAPI(
    title="Sparki Dashboarding API",
    version=__version__,
    description=(
        "Onafhankelijk dashboarding-platform voor Sigenergy-installaties. "
        "Multi-tenant, role-based, ENTSO-E prijsintegratie."
    ),
    lifespan=lifespan,
    # Hide internal docs in production unless explicitly enabled later.
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)


# ─── Routes ──────────────────────────────────────────────────────────
@app.get("/healthz", tags=["meta"])
async def healthz() -> JSONResponse:
    """Liveness + readiness probe.

    Used by Docker HEALTHCHECK and (later) by Caddy upstream checks.
    Returns 200 as long as the FastAPI process can accept requests.
    Deeper checks (DB connectivity, etc.) come in Phase 2.
    """
    return JSONResponse(
        content={
            "status": "ok",
            "service": "sparki-webapp",
            "version": __version__,
            "environment": settings.environment,
        }
    )


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    """Placeholder root.

    Will be replaced by dashboard.routes in Phase 3.
    """
    return {
        "message": "Sparki webapp is running.",
        "docs": "/docs",
        "health": "/healthz",
    }
