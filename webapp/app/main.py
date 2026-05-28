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
  - /                  → portfolio (logged in) / landing (anon)  [buildings_web]
  - /login /logout     → auth flow                               [web routes]
  - /buildings/{id}/tile → live data fragment (HTMX)             [buildings_web]
  - /static/*          → CSS, images
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
from app.web.buildings_web import router as web_buildings_router
from app.web.routes import router as web_router

# Importing app.models registers ALL SQLAlchemy models on Base.metadata.
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
    """Boot / shutdown hooks."""
    logger.info(
        "Sparki webapp v%s starting in %s mode",
        __version__,
        settings.environment,
    )
    get_influx_client()
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
_STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
else:
    logger.warning("Static directory not found at %s — /static will 404", _STATIC_DIR)


# ─── Meta routes ─────────────────────────────────────────────────────
@app.get("/healthz", tags=["meta"])
async def healthz() -> JSONResponse:
    """Liveness probe — is the FastAPI process up?"""
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
    """Readiness probe — are Postgres and InfluxDB reachable?"""
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
    """JSON descriptor for the REST API (was at "/" before Phase 3)."""
    return {
        "message": "Sparki webapp is running.",
        "docs": "/docs",
        "health": "/healthz",
        "readiness": "/readyz",
        "dashboard": "/",
    }


# ─── Router registration ─────────────────────────────────────────────
# JSON API routers first — their /api/* paths are well-isolated.
app.include_router(auth_router)
app.include_router(buildings_router)
app.include_router(prices_router)

# HTML UI routers last — they own "/" and other browser-facing paths.
# buildings_web owns "/" (portfolio) and "/buildings/{id}/tile";
# web_router owns "/login", "/auth/callback", "/logout", "/dev/login".
app.include_router(web_buildings_router)
app.include_router(web_router)
