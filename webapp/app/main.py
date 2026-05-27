"""
Sparki webapp — FastAPI entrypoint.

Wires together:
  - configuration loading (via app.config)
  - lifespan events (engine dispose, InfluxDB client close)
  - middleware (added in later phases)
  - route mounting

Health endpoints:
  - /healthz  → liveness (200 if process can respond)
  - /readyz   → readiness (200 if Postgres + InfluxDB respond)

HTML UI:
  - /         → landing / dashboard (handled by app.web.routes)
  - /login    → login flow entry
  - /logout   → clear session
  - /static/* → CSS, images, etc.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.auth.keycloak import warm_jwks_cache
from app.auth.routes import router as auth_router
from app.buildings.routes import router as buildings_router
from app.config import settings
from app.core.healthz import collect_health
from app.database import dispose_engine
from app.influx import close_influx_client, get_influx_client
from app.prices.routes import router as prices_router
from app.web.routes import router as web_router

# Importing app.models registers ALL SQLAlchemy models on Base.metadata.
# Required for Alembic autogenerate and any future model introspection.
from app import models  # noqa: F401

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

    Boot:
      - Eager-init the InfluxDB client so its first ping doesn't pay the
        connection-setup cost on the user's request path.
    Shutdown:
      - Close InfluxDB HTTP pool.
      - Dispose SQLAlchemy engine + connection pool.
    """
    logger.info(
        "Sparki webapp v%s starting in %s mode",
        __version__,
        settings.environment,
    )
    # Eager-init Influx client (lazy by default, but warm it now)
    get_influx_client()
    # Pre-fetch Keycloak JWKS so first request doesn't pay the network cost
    await warm_jwks_cache()

    yield

    logger.info("Sparki webapp shutting down")
    await close_influx_client()
    await dispose_engine()


# ─── App factory ─────────────────────────────────────────────────────
app = FastAPI(
    title="Sparki Dashboarding API",
    version=__version__,
    description=(
        "Onafhankelijk dashboarding-platform voor Sigenergy-installaties. "
        "Multi-tenant, role-based, ENTSO-E prijsintegratie."
    ),
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)


# ─── Static files ────────────────────────────────────────────────────
# Resolve the static dir relative to this file so the mount works
# regardless of the working directory uvicorn is launched from.
# Inside the container the path is /app/static; in local dev it's
# webapp/static. The Path lookup handles both.
_STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
else:
    logger.warning("Static directory not found at %s — /static will 404", _STATIC_DIR)


# ─── Meta routes ─────────────────────────────────────────────────────
@app.get("/healthz", tags=["meta"])
async def healthz() -> JSONResponse:
    """Liveness probe — is the FastAPI process up?

    Always returns 200 if the process can respond. Used by:
      - Docker HEALTHCHECK
      - Caddy upstream check (later)
      - Kubernetes liveness probe (deployment-team's choice)
    """
    return JSONResponse(
        content={
            "status": "ok",
            "service": "sparki-webapp",
            "version": __version__,
            "environment": settings.environment,
        }
    )


@app.get("/readyz", tags=["meta"])
async def readyz() -> JSONResponse:
    """Readiness probe — are Postgres and InfluxDB reachable?

    Returns 200 if all downstream services pass, 503 if any check fails.
    Use this from load balancers to route around half-broken instances.
    """
    report = await collect_health()
    http_status = 200 if report["status"] == "ok" else 503
    return JSONResponse(
        content={
            "service": "sparki-webapp",
            "version": __version__,
            **report,
        },
        status_code=http_status,
    )


@app.get("/api", tags=["meta"])
async def api_root() -> dict[str, str]:
    """JSON descriptor for the REST API.

    Used to live at "/" before Phase 3; relocated to /api now that the
    HTML UI owns the site root. Keep it as a smoke-test endpoint
    reachable without a browser.
    """
    return {
        "message": "Sparki webapp is running.",
        "docs": "/docs",
        "health": "/healthz",
        "readiness": "/readyz",
        "dashboard": "/",
    }


# ─── Router registration ─────────────────────────────────────────────
# JSON API routers first — their /api/* paths are well-isolated from the
# HTML routes the web_router owns at "/".
app.include_router(auth_router)
app.include_router(buildings_router)
app.include_router(prices_router)

# HTML UI router last — it owns "/" and other browser-facing paths.
app.include_router(web_router)
